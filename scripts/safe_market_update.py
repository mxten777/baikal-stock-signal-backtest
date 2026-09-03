"""
Safe Market Updater — Daily Operation 전용 안전 시세 갱신 layer.

기존 scripts/download_all_tickers.py 를 수정하지 않고,
daily operation 전용 safety/orchestration layer 로 동작한다.

핵심 원칙:
    DOWNLOAD TO STAGING
    VALIDATE EVERYTHING
    EXISTING HISTORY WINS
    APPEND NEW DATES ONLY
    ALL TICKERS PASS BEFORE PUBLISH

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
from typing import Callable, Dict, List, Optional, Protocol

import pandas as pd

REQUIRED_COLUMNS = ["date", "open", "high", "low", "close", "volume"]
PRICE_COLUMNS = ["open", "high", "low", "close", "volume"]

DEFAULT_TICKERS: Dict[str, str] = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "005380": "현대차",
    "000270": "기아",
    "035420": "NAVER",
    "035720": "카카오",
    "207940": "삼성바이오로직스",
    "068270": "셀트리온",
    "012450": "한화에어로스페이스",
    "034020": "두산에너빌리티",
    "080220": "제주반도체",
    "105560": "KB금융",
    "055550": "신한지주",
    "006400": "삼성SDI",
    "051910": "LG화학",
    "373220": "LG에너지솔루션",
    "028260": "삼성물산",
    "096770": "SK이노베이션",
    "009540": "HD한국조선해양",
    "042660": "한화오션",
}

STATUS_UPDATED = "UPDATED"
STATUS_NO_NEW_DATA = "NO_NEW_DATA"
STATUS_FAILED = "FAILED"

# overlap 재조회 일수: 기존 max date 이전 며칠부터 source를 다시 받아
# 기존 날짜와 source 날짜가 일치하는지 검증한다.
OVERLAP_DAYS = 10


class MarketDataSource(Protocol):
    """시세 source interface. production 구현은 FinanceDataReader."""

    def fetch(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        """date, open, high, low, close, volume 컬럼 DataFrame 반환.

        Raises on failure. 빈 DataFrame 반환은 '신규 데이터 없음' 후보가 된다.
        """
        ...


class FinanceDataReaderSource:
    """Production source — FinanceDataReader 기반."""

    def fetch(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        import FinanceDataReader as fdr

        df = fdr.DataReader(ticker, start, end)
        if df is None:
            raise ValueError("source returned None")
        df = df.reset_index()
        df = df.rename(columns={
            "Date": "date", "Open": "open", "High": "high",
            "Low": "low", "Close": "close", "Volume": "volume",
        })
        return df


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
    ticker_count: int
    fetch_success_count: int
    fetch_failed_count: int
    previous_latest_date: Optional[str]
    source_latest_date: Optional[str]
    published_latest_date: Optional[str]
    rows_added: int
    overlap_mismatches: Dict[str, List[str]]
    publish_status: str  # "PUBLISHED" | "SKIPPED_NO_NEW_DATA" | "NOT_PUBLISHED"
    failures: Dict[str, str]
    tickers: List[TickerResult]

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "run_timestamp": self.run_timestamp,
            "ticker_count": self.ticker_count,
            "fetch_success_count": self.fetch_success_count,
            "fetch_failed_count": self.fetch_failed_count,
            "previous_latest_date": self.previous_latest_date,
            "source_latest_date": self.source_latest_date,
            "published_latest_date": self.published_latest_date,
            "rows_added": self.rows_added,
            "overlap_mismatches": self.overlap_mismatches,
            "publish_status": self.publish_status,
            "failures": self.failures,
        }


def _normalize_schema(df: pd.DataFrame) -> pd.DataFrame:
    """source DataFrame을 production schema로 정규화한다.

    Raises ValueError on schema failure.
    """
    if df is None:
        raise ValueError("source returned None")
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"schema failure: missing columns {missing}")
    out = df[REQUIRED_COLUMNS].copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    if out["date"].isna().any():
        raise ValueError("schema failure: invalid/null date in source")
    for col in PRICE_COLUMNS:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    if out[PRICE_COLUMNS].isna().any().any():
        raise ValueError("schema failure: non-numeric price/volume in source")
    return out


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


class SafeMarketUpdater:
    """Daily operation 전용 safe market updater."""

    def __init__(
        self,
        tickers: Dict[str, str],
        raw_dir: Path,
        source: MarketDataSource,
        staging_dir: Optional[Path] = None,
        today: Optional[date] = None,
        overlap_days: int = OVERLAP_DAYS,
    ):
        self.tickers = dict(tickers)
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
        ticker_results: List[TickerResult] = []
        failures: Dict[str, str] = {}
        mismatch_report: Dict[str, List[str]] = {}

        # staging 영역 생성 — production glob과 분리된다.
        if self.staging_dir:
            staging_root = self.staging_dir
            if staging_root.exists():
                shutil.rmtree(staging_root)
            staging_root.mkdir(parents=True, exist_ok=True)
            self._staging_owned = False
        else:
            staging_root = Path(tempfile.mkdtemp(prefix="safe_market_staging_"))
            self._staging_owned = True

        candidates: Dict[str, Path] = {}
        try:
            for ticker in self.tickers:
                tr = self._process_ticker(ticker, staging_root, candidates)
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
            src_latest = max(
                (t.source_max_date for t in ticker_results if t.source_max_date),
                default=None,
            )

            # STEP 7 — Batch Coverage Gate: 하나라도 실패하면 publish 0.
            if failures:
                return UpdateResult(
                    status=STATUS_FAILED,
                    run_timestamp=run_ts,
                    ticker_count=len(self.tickers),
                    fetch_success_count=fetch_ok,
                    fetch_failed_count=len(failures),
                    previous_latest_date=prev_latest,
                    source_latest_date=src_latest,
                    published_latest_date=None,
                    rows_added=0,
                    overlap_mismatches=mismatch_report,
                    publish_status="NOT_PUBLISHED",
                    failures=failures,
                    tickers=ticker_results,
                )

            rows_added = sum(t.new_rows for t in ticker_results)
            if rows_added == 0:
                return UpdateResult(
                    status=STATUS_NO_NEW_DATA,
                    run_timestamp=run_ts,
                    ticker_count=len(self.tickers),
                    fetch_success_count=fetch_ok,
                    fetch_failed_count=0,
                    previous_latest_date=prev_latest,
                    source_latest_date=src_latest,
                    published_latest_date=prev_latest,
                    rows_added=0,
                    overlap_mismatches=mismatch_report,
                    publish_status="SKIPPED_NO_NEW_DATA",
                    failures={},
                    tickers=ticker_results,
                )

            # STEP 8 — Atomic Publish
            published_latest = self._publish(candidates)
            return UpdateResult(
                status=STATUS_UPDATED,
                run_timestamp=run_ts,
                ticker_count=len(self.tickers),
                fetch_success_count=fetch_ok,
                fetch_failed_count=0,
                previous_latest_date=prev_latest,
                source_latest_date=src_latest,
                published_latest_date=published_latest,
                rows_added=rows_added,
                overlap_mismatches=mismatch_report,
                publish_status="PUBLISHED",
                failures={},
                tickers=ticker_results,
            )
        finally:
            if self._staging_owned and staging_root.exists():
                shutil.rmtree(staging_root, ignore_errors=True)

    # ------------------------------------------------------------------
    # per-ticker pipeline
    # ------------------------------------------------------------------
    def _process_ticker(
        self, ticker: str, staging_root: Path, candidates: Dict[str, Path]
    ) -> TickerResult:
        tr = TickerResult(ticker=ticker)
        prod_path = self.raw_dir / f"{ticker}.csv"
        try:
            # 1. Existing data inspection
            if not prod_path.exists():
                raise ValueError("missing ticker: production CSV not found")
            existing = pd.read_csv(prod_path, parse_dates=["date"])
            _validate_frame(existing, self.today, f"{ticker} production")
            tr.previous_max_date = existing["date"].max().strftime("%Y-%m-%d")

            # 2. Incremental source fetch (overlap 포함)
            start = (
                existing["date"].max().date() - timedelta(days=self.overlap_days)
            ).strftime("%Y-%m-%d")
            end = self.today.strftime("%Y-%m-%d")
            raw = self.source.fetch(ticker, start, end)
            staged = _normalize_schema(raw)

            # 3. Staging — production에 바로 쓰지 않는다.
            staged_path = staging_root / f"{ticker}.staged.csv"
            staged.to_csv(staged_path, index=False)

            if staged.empty:
                # source가 비어 있어도 기존 max date 이후 데이터가 정상적으로
                # 없는 경우(휴장 등)와 구분하기 어려우므로, 기존 데이터가
                # today까지 최신이 아니면 unexpected empty로 실패 처리한다.
                if existing["date"].max().date() < self.today:
                    raise ValueError("unexpected empty source response")
                tr.ok = True
                tr.source_max_date = tr.previous_max_date
                candidates[ticker] = prod_path
                return tr

            tr.source_max_date = staged["date"].max().strftime("%Y-%m-%d")

            # 4/5. Historical immutability gate — overlap 비교
            overlap = staged[staged["date"] <= existing["date"].max()]
            mismatch_dates = self._compare_overlap(ticker, existing, overlap)
            tr.overlap_mismatches = mismatch_dates

            # 6. Staged candidate 생성 — existing rows + date > existing max
            new_rows = staged[staged["date"] > existing["date"].max()].copy()
            if new_rows.empty:
                candidate = existing.copy()
            else:
                candidate = pd.concat([existing, new_rows], ignore_index=True)
            candidate = candidate.sort_values("date").reset_index(drop=True)
            candidate = candidate[REQUIRED_COLUMNS]

            _validate_frame(candidate, self.today, f"{ticker} candidate")

            # 기존 rows 전부 존재 + 값 동일 검증 (historical mutation FAIL)
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
        for col in PRICE_COLUMNS:
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
        for col in PRICE_COLUMNS:
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
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        backup_dir = Path(tempfile.mkdtemp(prefix="safe_market_backup_"))
        temp_paths: Dict[str, Path] = {}
        published: List[str] = []
        try:
            # phase 1: temp file 작성 (flush/close 후)
            for ticker, cand_path in candidates.items():
                fd, tmp = tempfile.mkstemp(
                    prefix=f".{ticker}.", suffix=".tmp", dir=self.raw_dir
                )
                with os.fdopen(fd, "wb") as out, open(cand_path, "rb") as src:
                    shutil.copyfileobj(src, out)
                    out.flush()
                    os.fsync(out.fileno())
                temp_paths[ticker] = Path(tmp)

            # phase 2: backup + atomic replace
            latest = None
            for ticker, tmp_path in temp_paths.items():
                prod_path = self.raw_dir / f"{ticker}.csv"
                shutil.copy2(prod_path, backup_dir / f"{ticker}.csv")
                os.replace(tmp_path, prod_path)
                published.append(ticker)

            for ticker in published:
                df = pd.read_csv(self.raw_dir / f"{ticker}.csv", parse_dates=["date"])
                mx = df["date"].max().strftime("%Y-%m-%d")
                latest = max(latest, mx) if latest else mx
            return latest or ""
        except Exception:
            # rollback: 아직 replace되지 않은 ticker는 원본 그대로이고,
            # replace된 ticker는 backup으로 복원한다.
            for ticker in published:
                backup = backup_dir / f"{ticker}.csv"
                if backup.exists():
                    shutil.copy2(backup, self.raw_dir / f"{ticker}.csv")
            for tmp_path in temp_paths.values():
                if tmp_path.exists():
                    tmp_path.unlink()
            raise
        finally:
            shutil.rmtree(backup_dir, ignore_errors=True)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Safe Market Updater")
    parser.add_argument("--raw-dir", default="data/raw")
    parser.add_argument("--staging-dir", default=None)
    parser.add_argument("--json", action="store_true", help="machine-readable result")
    args = parser.parse_args(argv)

    updater = SafeMarketUpdater(
        tickers=DEFAULT_TICKERS,
        raw_dir=Path(args.raw_dir),
        source=FinanceDataReaderSource(),
        staging_dir=Path(args.staging_dir) if args.staging_dir else None,
    )
    result = updater.run()

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"status: {result.status}")
        print(f"tickers: {result.ticker_count} "
              f"(fetch ok {result.fetch_success_count} / "
              f"failed {result.fetch_failed_count})")
        print(f"previous latest: {result.previous_latest_date}")
        print(f"source latest:   {result.source_latest_date}")
        print(f"published latest:{result.published_latest_date}")
        print(f"rows added: {result.rows_added}")
        print(f"publish: {result.publish_status}")
        if result.failures:
            print("failures:")
            for t, err in result.failures.items():
                print(f"  {t}: {err}")

    if result.status in (STATUS_UPDATED, STATUS_NO_NEW_DATA):
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
