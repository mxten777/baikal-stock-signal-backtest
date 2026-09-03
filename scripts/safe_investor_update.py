"""
Safe Investor Updater — Daily Operation 전용 안전 수급(외국인/기관) 갱신 layer.

기존 scripts/step1a_collect_investor_all.py, src/data_provider/naver_investor_provider.py
를 수정하지 않고, daily operation 전용 safety/orchestration layer 로 동작한다.

핵심 원칙:
    FETCH TO STAGING
    VALIDATE ALL TICKERS
    PRESERVE HISTORICAL ROWS
    APPEND NEW DATES ONLY
    NO PARTIAL PUBLISH
    ALIGN TO MARKET DATE POLICY

Signal 판단은 절대 하지 않는다. 데이터 갱신만 수행한다.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Protocol

import pandas as pd

REQUIRED_COLUMNS = ["date", "ticker", "foreign_net_buy", "institution_net_buy"]
NUMERIC_COLUMNS = ["foreign_net_buy", "institution_net_buy"]

STATUS_UPDATED = "UPDATED"
STATUS_NO_NEW_DATA = "NO_NEW_DATA"
STATUS_SOURCE_LAG = "SOURCE_LAG"
STATUS_FAILED = "FAILED"

# overlap 재조회 일수: 기존 max date 이전 며칠부터 source를 다시 받아
# 기존 날짜와 source 날짜가 일치하는지 검증한다.
OVERLAP_DAYS = 10


class InvestorDataSource(Protocol):
    """수급 데이터 source interface. production 구현은 naver_investor_provider."""

    def fetch(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        """date, foreign_net_buy, institution_net_buy 컬럼 DataFrame 반환.

        Raises on failure. 빈 DataFrame 반환은 '신규 데이터 없음' 후보가 된다.
        """
        ...


class NaverInvestorSource:
    """Production source — src/data_provider/naver_investor_provider 기반."""

    def fetch(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        root = Path(__file__).parent.parent
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from src.data_provider.naver_investor_provider import fetch_investor_flow

        return fetch_investor_flow(ticker, start, end)


@dataclass
class TickerResult:
    ticker: str
    ok: bool = False
    error: Optional[str] = None
    previous_max_date: Optional[str] = None
    source_max_date: Optional[str] = None
    new_rows: int = 0
    overlap_mismatches: List[str] = field(default_factory=list)


@dataclass
class UpdateResult:
    status: str
    run_timestamp: str
    market_target_date: Optional[str]
    ticker_count: int
    fetch_success_count: int
    fetch_failed_count: int
    previous_latest_date: Optional[str]
    source_latest_date: Optional[str]
    published_latest_date: Optional[str]
    gap_days: Optional[int]
    rows_added: int
    overlap_mismatches: Dict[str, List[str]]
    publish_status: str  # "PUBLISHED" | "SKIPPED_NO_NEW_DATA" | "NOT_PUBLISHED"
    source_lag_type: Optional[str]  # None | "UNIFORM" | "PARTIAL"
    failures: Dict[str, str]
    tickers: List[TickerResult]

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "run_timestamp": self.run_timestamp,
            "market_target_date": self.market_target_date,
            "ticker_count": self.ticker_count,
            "fetch_success_count": self.fetch_success_count,
            "fetch_failed_count": self.fetch_failed_count,
            "previous_investor_latest_date": self.previous_latest_date,
            "source_latest_date": self.source_latest_date,
            "published_investor_latest_date": self.published_latest_date,
            "gap_days": self.gap_days,
            "rows_added": self.rows_added,
            "overlap_mismatches": self.overlap_mismatches,
            "publish_status": self.publish_status,
            "source_lag_type": self.source_lag_type,
            "failed_tickers": self.failures,
        }


def compute_target_market_date(raw_dir: Path, tickers: Dict[str, str]) -> str:
    """data/raw 기준 operational market latest date를 계산한다.

    모든 configured ticker의 raw market CSV 중 최댓값을 사용한다.
    Raises ValueError on missing/empty raw data.
    """
    max_dates: List[pd.Timestamp] = []
    for ticker in tickers:
        path = raw_dir / f"{ticker}.csv"
        if not path.exists():
            raise ValueError(f"missing raw market data for {ticker}")
        df = pd.read_csv(path, parse_dates=["date"], usecols=["date"])
        if df.empty or df["date"].isna().all():
            raise ValueError(f"empty raw market data for {ticker}")
        max_dates.append(df["date"].max())
    if not max_dates:
        raise ValueError("no tickers configured")
    return max(max_dates).strftime("%Y-%m-%d")


def _normalize_schema(df: pd.DataFrame, ticker: str, label: str) -> pd.DataFrame:
    """source DataFrame을 production schema로 정규화한다.

    Raises ValueError on schema failure. Dedup/정렬은 하지 않는다 —
    이후 validate 단계에서 duplicate/order 위반으로 명시적으로 실패시킨다.
    """
    if df is None:
        raise ValueError(f"{label}: source returned None")
    missing = [c for c in ["date", *NUMERIC_COLUMNS] if c not in df.columns]
    if missing:
        raise ValueError(f"{label}: schema failure: missing columns {missing}")
    out = pd.DataFrame(index=df.index)
    out["date"] = pd.to_datetime(df["date"], errors="coerce")
    if out["date"].isna().any():
        raise ValueError(f"{label}: schema failure: invalid/null date in source")
    out["ticker"] = int(ticker)
    for col in NUMERIC_COLUMNS:
        out[col] = pd.to_numeric(df[col], errors="coerce")
    if out[NUMERIC_COLUMNS].isna().any().any():
        raise ValueError(f"{label}: schema failure: non-numeric investor value in source")
    return out[REQUIRED_COLUMNS]


def _validate_frame(df: pd.DataFrame, today: date, label: str) -> None:
    """candidate/production frame 검증. Raises ValueError on failure."""
    if list(df.columns) != REQUIRED_COLUMNS:
        raise ValueError(f"{label}: schema mismatch")
    if df["date"].isna().any():
        raise ValueError(f"{label}: null date")
    if df["date"].duplicated().any():
        raise ValueError(f"{label}: duplicate date")
    if not df["date"].is_monotonic_increasing:
        raise ValueError(f"{label}: dates not ascending")
    if (df["date"].dt.date > today).any():
        raise ValueError(f"{label}: future date present")


class SafeInvestorUpdater:
    """Daily operation 전용 safe investor updater."""

    def __init__(
        self,
        tickers: Dict[str, str],
        investor_dir: Path,
        raw_dir: Path,
        source: InvestorDataSource,
        staging_dir: Optional[Path] = None,
        today: Optional[date] = None,
        overlap_days: int = OVERLAP_DAYS,
    ):
        self.tickers = dict(tickers)
        self.investor_dir = Path(investor_dir)
        self.raw_dir = Path(raw_dir)
        self.source = source
        self.staging_dir = Path(staging_dir) if staging_dir else None
        self.today = today or date.today()
        self.overlap_days = overlap_days

    # ------------------------------------------------------------------
    # public entry point
    # ------------------------------------------------------------------
    def run(self) -> UpdateResult:
        run_ts = datetime.now(timezone.utc).isoformat()

        try:
            target_market_date = compute_target_market_date(self.raw_dir, self.tickers)
        except ValueError as e:
            return UpdateResult(
                status=STATUS_FAILED,
                run_timestamp=run_ts,
                market_target_date=None,
                ticker_count=len(self.tickers),
                fetch_success_count=0,
                fetch_failed_count=len(self.tickers),
                previous_latest_date=None,
                source_latest_date=None,
                published_latest_date=None,
                gap_days=None,
                rows_added=0,
                overlap_mismatches={},
                publish_status="NOT_PUBLISHED",
                source_lag_type=None,
                failures={"_batch": str(e)},
                tickers=[],
            )

        ticker_results: List[TickerResult] = []
        failures: Dict[str, str] = {}
        mismatch_report: Dict[str, List[str]] = {}

        # staging 영역 생성 — production glob과 분리된다.
        if self.staging_dir:
            staging_root = self.staging_dir
            if staging_root.exists():
                shutil.rmtree(staging_root)
            staging_root.mkdir(parents=True, exist_ok=True)
            staging_owned = False
        else:
            staging_root = Path(tempfile.mkdtemp(prefix="safe_investor_staging_"))
            staging_owned = True

        candidates: Dict[str, Path] = {}
        try:
            for ticker in self.tickers:
                tr = self._process_ticker(ticker, staging_root, candidates, target_market_date)
                ticker_results.append(tr)
                if not tr.ok:
                    failures[ticker] = tr.error or "unknown failure"
                if tr.overlap_mismatches:
                    mismatch_report[ticker] = tr.overlap_mismatches

            fetch_ok = sum(1 for t in ticker_results if t.ok)
            prev_latest = max(
                (t.previous_max_date for t in ticker_results if t.previous_max_date),
                default=None,
            )

            # STEP 7 — Batch Coverage Gate: 하나라도 실패하면 publish 0.
            if failures:
                return UpdateResult(
                    status=STATUS_FAILED,
                    run_timestamp=run_ts,
                    market_target_date=target_market_date,
                    ticker_count=len(self.tickers),
                    fetch_success_count=fetch_ok,
                    fetch_failed_count=len(failures),
                    previous_latest_date=prev_latest,
                    source_latest_date=None,
                    published_latest_date=None,
                    gap_days=None,
                    rows_added=0,
                    overlap_mismatches=mismatch_report,
                    publish_status="NOT_PUBLISHED",
                    source_lag_type=None,
                    failures=failures,
                    tickers=ticker_results,
                )

            # STEP 8 — Source Lag: all-ticker source latest date consistency.
            distinct_src_latest = {
                t.source_max_date for t in ticker_results if t.source_max_date
            }
            if len(distinct_src_latest) > 1:
                return UpdateResult(
                    status=STATUS_FAILED,
                    run_timestamp=run_ts,
                    market_target_date=target_market_date,
                    ticker_count=len(self.tickers),
                    fetch_success_count=fetch_ok,
                    fetch_failed_count=0,
                    previous_latest_date=prev_latest,
                    source_latest_date=None,
                    published_latest_date=None,
                    gap_days=None,
                    rows_added=0,
                    overlap_mismatches=mismatch_report,
                    publish_status="NOT_PUBLISHED",
                    source_lag_type="PARTIAL",
                    failures={
                        "_batch": (
                            "partial source lag: ticker latest dates inconsistent "
                            f"{sorted(distinct_src_latest)}"
                        )
                    },
                    tickers=ticker_results,
                )

            src_latest = next(iter(distinct_src_latest), None)
            gap_days = None
            if src_latest:
                gap_days = (
                    pd.Timestamp(target_market_date) - pd.Timestamp(src_latest)
                ).days

            rows_added = sum(t.new_rows for t in ticker_results)
            if rows_added == 0:
                return UpdateResult(
                    status=STATUS_NO_NEW_DATA,
                    run_timestamp=run_ts,
                    market_target_date=target_market_date,
                    ticker_count=len(self.tickers),
                    fetch_success_count=fetch_ok,
                    fetch_failed_count=0,
                    previous_latest_date=prev_latest,
                    source_latest_date=src_latest,
                    published_latest_date=prev_latest,
                    gap_days=gap_days,
                    rows_added=0,
                    overlap_mismatches=mismatch_report,
                    publish_status="SKIPPED_NO_NEW_DATA",
                    source_lag_type=(
                        "UNIFORM" if src_latest and src_latest < target_market_date else None
                    ),
                    failures={},
                    tickers=ticker_results,
                )

            # STEP 9 — Atomic Publish
            published_latest = self._publish(candidates)
            source_lag = bool(src_latest and src_latest < target_market_date)
            return UpdateResult(
                status=STATUS_SOURCE_LAG if source_lag else STATUS_UPDATED,
                run_timestamp=run_ts,
                market_target_date=target_market_date,
                ticker_count=len(self.tickers),
                fetch_success_count=fetch_ok,
                fetch_failed_count=0,
                previous_latest_date=prev_latest,
                source_latest_date=src_latest,
                published_latest_date=published_latest,
                gap_days=gap_days,
                rows_added=rows_added,
                overlap_mismatches=mismatch_report,
                publish_status="PUBLISHED",
                source_lag_type="UNIFORM" if source_lag else None,
                failures={},
                tickers=ticker_results,
            )
        finally:
            if staging_owned and staging_root.exists():
                shutil.rmtree(staging_root, ignore_errors=True)

    # ------------------------------------------------------------------
    # per-ticker pipeline
    # ------------------------------------------------------------------
    def _process_ticker(
        self,
        ticker: str,
        staging_root: Path,
        candidates: Dict[str, Path],
        target_market_date: str,
    ) -> TickerResult:
        tr = TickerResult(ticker=ticker)
        prod_path = self.investor_dir / f"{ticker}_investor.csv"
        try:
            # 1. Existing data inspection
            if not prod_path.exists():
                raise ValueError("missing ticker: production investor CSV not found")
            existing = pd.read_csv(prod_path, parse_dates=["date"])
            _validate_frame(existing, self.today, f"{ticker} production")
            tr.previous_max_date = existing["date"].max().strftime("%Y-%m-%d")

            # 2. Incremental source fetch (overlap 포함), market target date까지만
            start = (
                existing["date"].max().date() - timedelta(days=self.overlap_days)
            ).strftime("%Y-%m-%d")
            raw = self.source.fetch(ticker, start, target_market_date)
            staged = _normalize_schema(raw, ticker, f"{ticker} source")

            # market target date 이후 source row는 비정상 처리한다.
            target_ts = pd.Timestamp(target_market_date)
            if (staged["date"] > target_ts).any():
                raise ValueError("source returned rows beyond market target date")

            # 3. Staging — production에 바로 쓰지 않는다.
            staged_path = staging_root / f"{ticker}.staged.csv"
            staged.to_csv(staged_path, index=False)

            if staged.empty:
                # source가 완전히 비어 있는데 기존 데이터가 아직 market target에
                # 도달하지 못했다면 unexpected empty로 실패 처리한다. 이미
                # market target까지 도달해 있다면 정상적인 '신규 없음'이다.
                if existing["date"].max() < target_ts:
                    raise ValueError("unexpected empty source response")
                tr.ok = True
                tr.source_max_date = tr.previous_max_date
                candidates[ticker] = prod_path
                return tr

            tr.source_max_date = staged["date"].max().strftime("%Y-%m-%d")

            # 4/5. Historical immutability gate — overlap 비교 (existing wins)
            overlap = staged[staged["date"] <= existing["date"].max()]
            tr.overlap_mismatches = self._compare_overlap(ticker, existing, overlap)

            # 6. Staged candidate 생성 — existing rows + date > existing max
            new_rows = staged[staged["date"] > existing["date"].max()].copy()
            if new_rows.empty:
                candidate = existing.copy()
            else:
                candidate = pd.concat([existing, new_rows], ignore_index=True)
            candidate = candidate.sort_values("date").reset_index(drop=True)
            candidate = candidate[REQUIRED_COLUMNS]

            _validate_frame(candidate, self.today, f"{ticker} candidate")
            self._assert_existing_preserved(ticker, existing, candidate)

            tr.new_rows = len(candidate) - len(existing)
            tr.ok = True

            cand_path = staging_root / f"{ticker}.candidate.csv"
            candidate.to_csv(cand_path, index=False)
            candidates[ticker] = cand_path
            return tr
        except Exception as e:  # noqa: BLE001 — 실패를 수집해 batch gate로 보낸다
            tr.error = str(e)
            tr.ok = False
            return tr

    def _compare_overlap(
        self, ticker: str, existing: pd.DataFrame, overlap: pd.DataFrame
    ) -> List[str]:
        """source overlap과 production 기존 날짜를 비교한다.

        mismatch가 있어도 기존 production 값을 유지한다 (existing wins).
        """
        mismatches: List[str] = []
        if overlap.empty:
            return mismatches
        merged = existing.merge(
            overlap, on="date", how="inner", suffixes=("_prod", "_src")
        )
        for col in NUMERIC_COLUMNS:
            diff = merged[merged[f"{col}_prod"] != merged[f"{col}_src"]]
            for d in diff["date"]:
                entry = f"{d.strftime('%Y-%m-%d')}:{col}"
                if entry not in mismatches:
                    mismatches.append(entry)
        return mismatches

    @staticmethod
    def _assert_existing_preserved(
        ticker: str, existing: pd.DataFrame, candidate: pd.DataFrame
    ) -> None:
        head = candidate.iloc[: len(existing)].reset_index(drop=True)
        base = existing.reset_index(drop=True)
        if not head["date"].equals(base["date"]):
            raise ValueError(f"{ticker}: historical mutation detected (dates)")
        for col in NUMERIC_COLUMNS:
            if not head[col].equals(base[col]):
                raise ValueError(
                    f"{ticker}: historical mutation detected ({col})"
                )

    # ------------------------------------------------------------------
    # publish
    # ------------------------------------------------------------------
    def _publish(self, candidates: Dict[str, Path]) -> str:
        """모든 ticker candidate를 atomic하게 publish한다.

        1. production dir 내 temp file 생성 + flush/close
        2. pre-publish backup
        3. os.replace (per-file atomic)
        4. 실패 시 backup에서 rollback
        """
        self.investor_dir.mkdir(parents=True, exist_ok=True)
        backup_dir = Path(tempfile.mkdtemp(prefix="safe_investor_backup_"))
        temp_paths: Dict[str, Path] = {}
        published: List[str] = []
        try:
            # phase 1: temp file 작성 (flush/close 후)
            for ticker, cand_path in candidates.items():
                fd, tmp = tempfile.mkstemp(
                    prefix=f".{ticker}.", suffix=".tmp", dir=self.investor_dir
                )
                with os.fdopen(fd, "wb") as out, open(cand_path, "rb") as src:
                    shutil.copyfileobj(src, out)
                    out.flush()
                    os.fsync(out.fileno())
                temp_paths[ticker] = Path(tmp)

            # phase 2: backup + atomic replace
            latest = None
            for ticker, tmp_path in temp_paths.items():
                prod_path = self.investor_dir / f"{ticker}_investor.csv"
                shutil.copy2(prod_path, backup_dir / f"{ticker}_investor.csv")
                os.replace(tmp_path, prod_path)
                published.append(ticker)

            for ticker in published:
                df = pd.read_csv(
                    self.investor_dir / f"{ticker}_investor.csv", parse_dates=["date"]
                )
                mx = df["date"].max().strftime("%Y-%m-%d")
                latest = max(latest, mx) if latest else mx
            return latest or ""
        except Exception:
            # rollback: 아직 replace되지 않은 ticker는 원본 그대로이고,
            # replace된 ticker는 backup으로 복원한다.
            for ticker in published:
                backup = backup_dir / f"{ticker}_investor.csv"
                if backup.exists():
                    shutil.copy2(backup, self.investor_dir / f"{ticker}_investor.csv")
            for tmp_path in temp_paths.values():
                if tmp_path.exists():
                    tmp_path.unlink()
            raise
        finally:
            shutil.rmtree(backup_dir, ignore_errors=True)


def main(argv: Optional[List[str]] = None) -> int:
    root = Path(__file__).parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from src.config import TICKERS

    parser = argparse.ArgumentParser(description="Safe Investor Updater")
    parser.add_argument("--investor-dir", default="data/investor")
    parser.add_argument("--raw-dir", default="data/raw")
    parser.add_argument("--staging-dir", default=None)
    parser.add_argument("--json", action="store_true", help="machine-readable result")
    args = parser.parse_args(argv)

    updater = SafeInvestorUpdater(
        tickers=TICKERS,
        investor_dir=Path(args.investor_dir),
        raw_dir=Path(args.raw_dir),
        source=NaverInvestorSource(),
        staging_dir=Path(args.staging_dir) if args.staging_dir else None,
    )
    result = updater.run()

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"status: {result.status}")
        print(f"market target date: {result.market_target_date}")
        print(f"tickers: {result.ticker_count} "
              f"(fetch ok {result.fetch_success_count} / "
              f"failed {result.fetch_failed_count})")
        print(f"previous latest: {result.previous_latest_date}")
        print(f"source latest:   {result.source_latest_date}")
        print(f"published latest:{result.published_latest_date}")
        print(f"gap days: {result.gap_days}")
        print(f"rows added: {result.rows_added}")
        print(f"publish: {result.publish_status}")
        if result.source_lag_type:
            print(f"source lag: {result.source_lag_type}")
        if result.failures:
            print("failures:")
            for t, err in result.failures.items():
                print(f"  {t}: {err}")

    if result.status in (STATUS_UPDATED, STATUS_NO_NEW_DATA, STATUS_SOURCE_LAG):
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
