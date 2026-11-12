"""Single-attempt Expanded 573 scheduler/manual entry point."""

from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback
from zoneinfo import ZoneInfo

import FinanceDataReader

ROOT = Path(__file__).resolve().parent
CODE = Path(r"C:\baikal777\baikal-stock-signal-expanded-release-33d36eb41742")
sys.path.insert(0, str(CODE))

from scripts.expanded_daily_orchestrator import run_expanded_daily_orchestration
from scripts.korean_market_calendar import is_trading_day, load_holidays
from src.expanded_shadow_data import FinanceDataReaderMarketSource, NaverInvestorFlowSource, RetryPolicy
from src.expanded_shadow_ops import ExpandedRunLock, ExpandedShadowPaths
from src.expanded_shadow_universe import load_expanded_universe
from src.expanded_snapshot_preparation import PREPARATION_READY, prepare_expanded_snapshots, resolve_expanded_source_date
from src.expanded_trading_halts import OFFICIAL_HALT_EVIDENCE, assess_trading_status


class SafetyStop(RuntimeError):
    pass


def guard(event: str, args: tuple) -> None:
    def check(value) -> None:
        if isinstance(value, int):
            raise SafetyStop("Unexpected descriptor mutation")
        path = Path(os.fsdecode(value))
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.resolve().is_relative_to(ROOT):
            raise SafetyStop("Write outside Expanded 573 root: " + str(path))
        if path.is_relative_to(ROOT / "output" / "expanded_shadow" / "partial"):
            raise SafetyStop("PARTIAL publication is disabled; manual input recovery required")
        for parent in (path, *path.parents):
            if parent.exists() and parent.lstat().st_file_attributes & 1024:
                raise SafetyStop("Redirected output path")

    if event == "open":
        path, mode, flags = args
        writing = isinstance(mode, str) and any(c in mode for c in "wax+")
        writing = writing or isinstance(flags, int) and flags & (
            os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
        )
        if writing and not isinstance(path, int):
            check(path)
    elif event in {"os.mkdir", "os.remove", "os.rmdir", "os.chmod", "os.utime", "os.truncate"}:
        check(args[0])
    elif event == "os.rename":
        check(args[0])
        check(args[1])
    elif event.startswith("ctypes.") or event in {"os.system", "subprocess.Popen", "os.link", "os.symlink"}:
        raise SafetyStop("Unexpected native/process/link operation: " + event)


def completed(paths: ExpandedShadowPaths, target: str) -> bool:
    latest = paths.latest_manifest_path(target)
    if not latest.exists():
        return False
    pointer = json.loads(latest.read_bytes())
    manifest_path = paths.run_manifest_path(target, pointer["run_id"])
    manifest = json.loads(manifest_path.read_bytes())
    if manifest["basDd"] != target or manifest["status"] not in {"SUCCESS", "SUCCESS_WITH_TICKER_FAILURES"}:
        raise SafetyStop("Existing publication is not a completed run")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-date", help="Manual recovery only; ISO trading date")
    parser.add_argument("--check", action="store_true", help="Read-only configuration/date/duplicate check")
    args = parser.parse_args(argv)
    paths = ExpandedShadowPaths(ROOT)
    policy = json.loads((ROOT / "operating-policy.json").read_bytes())
    universe = load_expanded_universe(paths.controlled_universe_path, basDd=None)
    if (policy["data_root"] != str(ROOT) or universe.row_count != 573
            or any(t.ticker == "018000" for t in universe.tickers)
            or hashlib.sha256(paths.controlled_universe_path.read_bytes()).hexdigest() != policy["universe_sha256"]):
        raise SafetyStop("Expanded 573 policy/membership mismatch")
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    target = args.source_date or now.date().isoformat()
    day = datetime.strptime(target, "%Y-%m-%d").date()
    holidays, warning = load_holidays(ROOT)
    if warning:
        raise SafetyStop(warning)
    if not is_trading_day(day, holidays):
        if args.source_date:
            raise SafetyStop("Manual target is not a trading day")
        print(json.dumps({"status": "NON_TRADING_DAY", "source_date": target}))
        return 0
    if target < policy["effective_from"]:
        raise SafetyStop("Target precedes the 573 operating boundary")
    if completed(paths, target):
        print(json.dumps({"status": "ALREADY_COMPLETED", "source_date": target, "analysis_calls": 0}))
        return 0
    resolution = resolve_expanded_source_date(repo_root=ROOT, explicit_source_date=target, now=now)
    if args.check:
        print(json.dumps({"status": "CONFIGURATION_OK", "resolution": asdict(resolution), "analysis_calls": 0}))
        return 0
    os.chdir(ROOT)
    temp = ROOT / "temp"
    temp.mkdir(exist_ok=True)
    for key in ("TEMP", "TMP", "TMPDIR"):
        os.environ[key] = str(temp)
    tempfile.tempdir = str(temp)
    # Defense in depth only; this is not an OS sandbox.
    sys.addaudithook(guard)
    lock = ExpandedRunLock(paths)
    lock.acquire("expanded-573-" + now.strftime("%Y%m%d%H%M%S"), target)
    try:
        # Carry forward only the reviewed official originals/list; any filing
        # change is rejected by the existing verifier, not inferred as ACTIVE.
        base = OFFICIAL_HALT_EVIDENCE[("032580", "2026-11-10")]
        OFFICIAL_HALT_EVIDENCE[("032580", target)] = replace(
            base, source_date=target, filing_ids=("20261112000218", *base.filing_ids)
        )
        assessment = assess_trading_status(target, tuple(t.ticker for t in universe.tickers))
        if assessment.unknown:
            raise SafetyStop("Trading status UNKNOWN: " + json.dumps(asdict(assessment)))
        preparation = prepare_expanded_snapshots(
            repo_root=ROOT, source_date=target, market_source=FinanceDataReaderMarketSource(),
            investor_source=NaverInvestorFlowSource(), retry_policy=RetryPolicy(max_attempts=1),
            trading_status=assessment,
        )
        print(json.dumps({"stage": "INPUT", **asdict(preparation)}), flush=True)
        if preparation.preparation_status != PREPARATION_READY:
            raise SafetyStop("Inputs are not READY; no analysis/publication attempted")
        result = run_expanded_daily_orchestration(
            repo_root=ROOT, source_date=target, source_commit=policy["source_commit"],
            trading_status=assessment, use_lock=False, benchmark_provider="legacy",
        )
        print(json.dumps(result.to_dict(), ensure_ascii=True), flush=True)
        if result.final_status not in {"SUCCESS", "SUCCESS_WITH_WARNING"} or not completed(paths, target):
            raise SafetyStop("Run did not finish successfully; inspect existing artifacts before manual recovery")
        return 0
    finally:
        lock.release()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"status": "SAFETY_STOP", "type": type(exc).__name__, "reason": str(exc)}),
              file=sys.stderr, flush=True)
        traceback.print_exc()
        raise SystemExit(1)
