from __future__ import annotations

import csv
import json
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.expanded_daily_orchestrator import run_expanded_daily_orchestration
from scripts.expanded_operational_run import run_expanded_operational_run
from src.expanded_shadow_daily import probe_expanded_data_readiness, run_expanded_shadow_daily
from src.expanded_shadow_ops import ExpandedManifestError, ExpandedShadowPaths, validate_manifest
from src.expanded_snapshot_preparation import SourceDateResolution, prepare_expanded_snapshots
from src.expanded_trading_halts import TradingHaltStatusUnknown, trading_halt_exclusions
from tests.test_expanded_shadow_ops import _manifest
from tests.test_expanded_shadow_pipeline import _investor_frame, _market_frame, _valid_rows


HALT_DATE = "2026-10-15"
CONTINUED_HALT_DATE = "2026-10-16"
HALTED_TICKER = "018000"
ACTIVE_TICKER = "005930"


class FakeHaltProvider:
    def __init__(self, source_date: str, state: str = "halt", *, unknown: bool = False):
        self.source_date = source_date
        self.state = state
        self.unknown = unknown

    def fetch_disclosure(self):
        return {
            "itemCode": HALTED_TICKER,
            "disclosure": {
                "itemCode": HALTED_TICKER,
                "disclosureId": 153834975,
                "contents": (
                    "<p>주권매매거래정지</p><td>정지일시</td>"
                    "<td>2026-10-15</td><td>신주권 변경상장일 전일까지</td>"
                ),
            },
        }

    def fetch_status(self, ticker: str):
        if self.unknown:
            raise TradingHaltStatusUnknown("provider unavailable")
        return {
            "itemCode": ticker,
            "localTradedAt": f"{self.source_date}T20:20:23+09:00",
            "tradableStatusUpdatedAt": f"{self.source_date}T08:01:54.524+09:00",
            "tradableStatus": self.state,
            "tradeStopType": {"code": "2" if self.state == "halt" else "0"},
        }


class SessionSource:
    def __init__(self, builder, source_date: str, *, failed_ticker: str | None = None):
        self.builder = builder
        self.source_date = source_date
        self.failed_ticker = failed_ticker
        self.calls: list[str] = []

    def fetch(self, ticker: str, _start: str, _end: str):
        self.calls.append(ticker)
        if self.source_date in {HALT_DATE, CONTINUED_HALT_DATE} and ticker == HALTED_TICKER:
            raise AssertionError("a verified halted ticker must not be fetched")
        if ticker == self.failed_ticker:
            raise TimeoutError("unconfirmed missing source")
        return self.builder(ticker, source_date=self.source_date)


def _write_universe(root: Path) -> tuple[Path, list[str]]:
    rows = _valid_rows()
    row = next(item for item in rows if item["market"] == "KOSDAQ")
    row.update(ticker=HALTED_TICKER, name="Unison")
    path = ExpandedShadowPaths(root).controlled_universe_path
    path.parent.mkdir(parents=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return path, [item["ticker"] for item in rows]


def _stale_halt_snapshots(root: Path, source_date: str = HALT_DATE) -> dict[Path, bytes]:
    paths = ExpandedShadowPaths(root)
    market = paths.market_dir(source_date) / f"{HALTED_TICKER}.csv"
    investor = paths.investor_dir(source_date) / f"{HALTED_TICKER}_investor.csv"
    market.parent.mkdir(parents=True, exist_ok=True)
    investor.parent.mkdir(parents=True, exist_ok=True)
    _market_frame(HALTED_TICKER, source_date="2026-10-14").drop(columns="ticker").to_csv(market, index=False)
    frame = _investor_frame(HALTED_TICKER, source_date=HALT_DATE)
    frame.loc[frame.index[-1], ["foreign_net_buy", "institution_net_buy"]] = 0
    frame.to_csv(investor, index=False)
    return {path: path.read_bytes() for path in (market, investor)}


def test_exclusion_is_exact_session_and_ticker_with_disclosure_evidence():
    exclusions = trading_halt_exclusions(
        HALT_DATE, (HALTED_TICKER, ACTIVE_TICKER), FakeHaltProvider(HALT_DATE),
    )
    assert len(exclusions) == 1
    assert exclusions[0]["ticker"] == HALTED_TICKER
    assert exclusions[0]["source_date"] == HALT_DATE
    assert exclusions[0]["reason"] == "TRADING_HALT_SHARE_CONSOLIDATION"
    assert exclusions[0]["evidence_id"] == "20261012_102662"
    assert exclusions[0]["evidence_url"].endswith("/disclosure/153834975")
    continued = trading_halt_exclusions(
        "2026-10-16", (HALTED_TICKER,), FakeHaltProvider("2026-10-16"),
    )
    assert continued[0]["source_date"] == "2026-10-16"
    assert continued[0]["status_provider"] == "Naver"
    assert continued[0]["status_as_of"].startswith("2026-10-16T")
    assert trading_halt_exclusions(
        "2026-10-14", (HALTED_TICKER,), FakeHaltProvider("2026-10-14"),
    ) == []
    assert trading_halt_exclusions(
        "2026-10-19", (HALTED_TICKER,), FakeHaltProvider("2026-10-19"),
    )[0]["source_date"] == "2026-10-19"
    assert trading_halt_exclusions(
        "2026-10-20", (HALTED_TICKER,), FakeHaltProvider("2026-10-20", "tradable"),
    ) == []
    assert trading_halt_exclusions(
        HALT_DATE, (ACTIVE_TICKER,), FakeHaltProvider(HALT_DATE),
    ) == []
    exclusions[0]["ticker"] = ACTIVE_TICKER
    assert trading_halt_exclusions(
        HALT_DATE, (HALTED_TICKER,), FakeHaltProvider(HALT_DATE),
    )[0]["ticker"] == HALTED_TICKER


@pytest.mark.parametrize("existing_halt_files", [False, True])
def test_preparation_and_gate_skip_only_verified_halt(tmp_path: Path, existing_halt_files: bool):
    preserved = _stale_halt_snapshots(tmp_path) if existing_halt_files else {}
    market = SessionSource(_market_frame, HALT_DATE)
    investor = SessionSource(_investor_frame, HALT_DATE)
    tickers = (HALTED_TICKER, ACTIVE_TICKER)
    result = prepare_expanded_snapshots(
        repo_root=tmp_path, source_date=HALT_DATE,
        market_source=market, investor_source=investor, tickers=tickers,
        trading_halt_provider=FakeHaltProvider(HALT_DATE),
    )
    assert result.preparation_status == "READY"
    assert result.universe_count == 2
    assert result.ready_count == result.market_ready == result.investor_ready == 1
    assert result.market_failures == result.investor_failures == ()
    assert market.calls == investor.calls == [ACTIVE_TICKER]
    gate = probe_expanded_data_readiness(
        ExpandedShadowPaths(tmp_path), HALT_DATE, tickers, FakeHaltProvider(HALT_DATE),
    )
    assert gate.ready
    assert gate.universe_count == 2
    assert gate.failed_tickers == ()
    assert gate.trading_halt_exclusions == result.to_dict()["trading_halt_exclusions"]
    assert {path: path.read_bytes() for path in preserved} == preserved
    if not existing_halt_files:
        paths = ExpandedShadowPaths(tmp_path)
        assert not (paths.market_dir(HALT_DATE) / f"{HALTED_TICKER}.csv").exists()
        assert not (paths.investor_dir(HALT_DATE) / f"{HALTED_TICKER}_investor.csv").exists()


def test_unconfirmed_missing_ticker_still_blocks(tmp_path: Path):
    result = prepare_expanded_snapshots(
        repo_root=tmp_path, source_date=HALT_DATE,
        market_source=SessionSource(_market_frame, HALT_DATE, failed_ticker=ACTIVE_TICKER),
        investor_source=SessionSource(_investor_frame, HALT_DATE),
        tickers=(HALTED_TICKER, ACTIVE_TICKER),
        trading_halt_provider=FakeHaltProvider(HALT_DATE),
    )
    assert result.preparation_status == "SNAPSHOT_PREPARATION_FAILED"
    assert result.market_failures[0]["ticker"] == ACTIVE_TICKER
    gate = probe_expanded_data_readiness(
        ExpandedShadowPaths(tmp_path), HALT_DATE, (HALTED_TICKER, ACTIVE_TICKER),
        FakeHaltProvider(HALT_DATE),
    )
    assert not gate.ready
    assert {item["ticker"] for item in gate.failed_tickers} == {ACTIVE_TICKER}


def test_active_status_returns_unison_to_analysis(tmp_path: Path):
    source_date = "2026-10-20"
    market = SessionSource(_market_frame, source_date)
    investor = SessionSource(_investor_frame, source_date)
    result = prepare_expanded_snapshots(
        repo_root=tmp_path, source_date=source_date,
        market_source=market,
        investor_source=investor,
        tickers=(HALTED_TICKER,),
        trading_halt_provider=FakeHaltProvider(source_date, "tradable"),
    )
    assert result.preparation_status == "READY"
    assert result.trading_halt_exclusions == []
    assert market.calls == investor.calls == [HALTED_TICKER]


def test_unverified_status_blocks_even_when_snapshots_are_aligned(tmp_path: Path):
    paths = ExpandedShadowPaths(tmp_path)
    source_date = "2026-10-19"
    for ticker in (HALTED_TICKER, ACTIVE_TICKER):
        market_path = paths.market_dir(source_date) / f"{ticker}.csv"
        investor_path = paths.investor_dir(source_date) / f"{ticker}_investor.csv"
        market_path.parent.mkdir(parents=True, exist_ok=True)
        investor_path.parent.mkdir(parents=True, exist_ok=True)
        _market_frame(ticker, source_date=source_date).to_csv(market_path, index=False)
        _investor_frame(ticker, source_date=source_date).to_csv(investor_path, index=False)

    gate = probe_expanded_data_readiness(
        paths, source_date, (HALTED_TICKER, ACTIVE_TICKER),
        FakeHaltProvider(source_date, unknown=True),
    )
    assert not gate.ready
    assert gate.trading_halt_exclusions == []
    assert gate.failed_tickers[0]["error_code"] == "TRADING_STATUS_UNVERIFIED"


def test_daily_run_records_unverified_status_and_does_not_analyze(tmp_path: Path):
    _write_universe(tmp_path)
    pipeline_calls = []
    result = run_expanded_shadow_daily(
        repo_root=tmp_path,
        source_date="2026-10-19",
        pipeline_runner=lambda **kwargs: pipeline_calls.append(kwargs),
        trading_halt_provider=FakeHaltProvider("2026-10-19", unknown=True),
    )

    paths = ExpandedShadowPaths(tmp_path)
    event = json.loads(paths.registry_path.read_text(encoding="utf-8").splitlines()[-1])
    assert result.status == "DATA_NOT_READY"
    assert result.readiness is not None
    assert result.readiness.failed_tickers[0]["error_code"] == "TRADING_STATUS_UNVERIFIED"
    assert "provider unavailable" in result.message
    assert "provider unavailable" in event["error_message"]
    assert pipeline_calls == []


def test_data_not_ready_gate_is_recorded_separately_from_analysis(tmp_path: Path):
    _write_universe(tmp_path)
    result = run_expanded_shadow_daily(
        repo_root=tmp_path,
        source_date="2026-10-19",
        run_id="readiness-block",
        trading_halt_provider=FakeHaltProvider("2026-10-19"),
    )
    assert result.status == "DATA_NOT_READY"
    paths = ExpandedShadowPaths(tmp_path)
    event = json.loads(paths.registry_path.read_text(encoding="utf-8").splitlines()[-1])
    assert event["event_type"] == "READINESS_GATE_BLOCKED"
    assert event["status"] == "DATA_NOT_READY"
    assert event["attempted_ticker_count"] == 0
    assert event["error_code"] == "DATA_NOT_READY"


def test_manifest_requires_exact_evidence_for_reduced_attempt_count():
    manifest = _manifest(
        basDd=HALT_DATE, attempted_ticker_count=573, ready_count=573,
        trading_halt_exclusions=trading_halt_exclusions(
            HALT_DATE, (HALTED_TICKER,), FakeHaltProvider(HALT_DATE),
        ),
    )
    validate_manifest(manifest)
    for invalid in (
        replace(manifest, trading_halt_exclusions=[]),
        replace(manifest, basDd="2026-10-19"),
        replace(manifest, attempted_ticker_count=572),
        replace(manifest, trading_halt_exclusions=manifest.trading_halt_exclusions * 2),
        replace(manifest, trading_halt_exclusions=[{**manifest.trading_halt_exclusions[0], "ticker": ACTIVE_TICKER}]),
        replace(manifest, trading_halt_exclusions=[{**manifest.trading_halt_exclusions[0], "evidence_url": ""}]),
    ):
        with pytest.raises(ExpandedManifestError):
            validate_manifest(invalid)


@pytest.mark.parametrize(
    "source_date, expected_attempted",
    [
        ("2026-10-14", 574),
        (HALT_DATE, 573),
        (CONTINUED_HALT_DATE, 573),
        ("2026-10-19", 573),
    ],
)
def test_master_574_fake_sources_real_signals_and_publication(
    tmp_path: Path, source_date: str, expected_attempted: int,
):
    universe_path, tickers = _write_universe(tmp_path)
    universe_before = universe_path.read_bytes()
    preserved = _stale_halt_snapshots(tmp_path, source_date) if expected_attempted == 573 else {}
    market = SessionSource(_market_frame, source_date)
    investor = SessionSource(_investor_frame, source_date)
    performance_calls: list[str] = []

    def performance_runner(**kwargs):
        performance_calls.append(kwargs["source_date"])
        return {"registered": 0, "updated": 0}

    def orchestrator(**kwargs):
        return run_expanded_daily_orchestration(**kwargs, performance_runner=performance_runner)

    kwargs = dict(
        repo_root=tmp_path, explicit_source_date=source_date,
        market_source=market, investor_source=investor, orchestrator=orchestrator,
        resolver=lambda **_kwargs: SourceDateResolution(source_date, "EXPLICIT", source_date),
        trading_halt_provider=FakeHaltProvider(source_date),
    )
    result = run_expanded_operational_run(**kwargs)
    assert result.final_status == "SUCCESS"
    assert result.snapshot_preparation is not None
    assert result.snapshot_preparation["universe_count"] == 574
    assert result.snapshot_preparation["ready_count"] == expected_attempted
    assert len(market.calls) == len(investor.calls) == expected_attempted
    expected_tickers = set(tickers) - ({HALTED_TICKER} if expected_attempted == 573 else set())
    assert set(market.calls) == set(investor.calls) == expected_tickers
    assert performance_calls == [source_date]
    paths = ExpandedShadowPaths(tmp_path)
    manifest = json.loads(paths.resolve_manifest_path(source_date).read_text(encoding="utf-8"))
    exclusions = trading_halt_exclusions(
        source_date, tickers, FakeHaltProvider(source_date),
    )
    assert manifest["canonical_universe_count"] == 574
    assert manifest["attempted_ticker_count"] == manifest["ready_count"] == expected_attempted
    assert manifest["quarantine_count"] == 0
    assert sum(manifest["status_counts"].values()) == 574
    assert manifest["status_counts"]["READY"] == expected_attempted
    if expected_attempted == 573:
        assert manifest["status_counts"]["TRADING_HALT_EXCLUDED"] == 1
        if paths.signal_ledger_path.exists():
            with paths.signal_ledger_path.open(encoding="utf-8", newline="") as handle:
                assert HALTED_TICKER not in {row["stock_code"] for row in csv.DictReader(handle)}
    assert set(manifest["retry_counts"]) == expected_tickers
    assert manifest["trading_halt_exclusions"] == exclusions
    assert result.orchestration is not None
    assert result.orchestration["daily_run"]["trading_halt_exclusions"] == exclusions
    gate = result.orchestration["daily_run"]["data_gate"]
    assert gate["universe_count"] == 574
    assert gate["market_ready_count"] == gate["investor_ready_count"] == expected_attempted
    assert gate["trading_halt_exclusions"] == exclusions
    registry = paths.registry_path.read_bytes()
    assert json.loads(registry)["trading_halt_exclusions"] == exclusions
    assert result.snapshot_preparation["trading_halt_exclusions"] == exclusions
    assert universe_path.read_bytes() == universe_before
    assert {path: path.read_bytes() for path in preserved} == preserved

    protected = {path: path.read_bytes() for path in paths.output_root.rglob("*") if path.is_file()}
    market.calls.clear()
    investor.calls.clear()
    repeated = run_expanded_operational_run(**kwargs)
    assert repeated.final_status == "ALREADY_COMPLETED"
    assert market.calls == investor.calls == []
    assert paths.registry_path.read_bytes() == registry
    assert {path: path.read_bytes() for path in protected} == protected
    assert universe_path.read_bytes() == universe_before
