"""Run the scheduled DART card analysis, then refresh its static HTML snapshot."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CARD_ANALYSIS_COMMAND = (
    sys.executable, "-B", "-m", "scripts.dart_card_runner",
    "--execute", "--max-items", "15",
)
HTML_GENERATION_COMMAND = (
    sys.executable, "-B", "-m", "scripts.dart_cards_html",
)


def run_pipeline(
    run_command: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> dict:
    analysis = run_command(CARD_ANALYSIS_COMMAND, cwd=ROOT, check=False)
    if analysis.returncode != 0:
        deferred = analysis.returncode == 2
        return {
            "verdict": "DEFERRED" if deferred else "FAIL",
            "exit_code": analysis.returncode,
            "stage": "CARD_ANALYSIS",
            "card_analysis_exit_code": analysis.returncode,
            "html_generation_exit_code": None,
        }

    generated = run_command(HTML_GENERATION_COMMAND, cwd=ROOT, check=False)
    return {
        "verdict": "PASS" if generated.returncode == 0 else "FAIL",
        "exit_code": generated.returncode,
        "stage": "COMPLETE" if generated.returncode == 0 else "HTML_GENERATION",
        "card_analysis_exit_code": analysis.returncode,
        "html_generation_exit_code": generated.returncode,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        report = run_pipeline()
    except OSError as exc:
        report = {
            "verdict": "FAIL", "exit_code": 1,
            "stage": "PROCESS_START", "error": type(exc).__name__,
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
