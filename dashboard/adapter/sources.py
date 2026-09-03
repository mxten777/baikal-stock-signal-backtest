from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


GIT_BASELINE_SOURCE = "git:baseline"

ALLOWED_SOURCE_FILES = frozenset(
    {
        "output/shadow_signal_records.csv",
        "output/v02_step9_final_comparison.csv",
        "output/v02_step9_final_risk_review.csv",
        "output/v02_step8_filtered_opportunity_cost.csv",
        "output/v02_step6_filter_opportunity_cost.csv",
        "output/v02_step3_foreign_score_performance.csv",
        "output/v02_step7_filter_by_market.csv",
        "output/v02_step7_filter_by_stock.csv",
        "output/v02_step7_filter_by_horizon.csv",
    }
)


@dataclass(frozen=True)
class SourceAllowlist:
    repo_root: Path
    allowed_files: frozenset[str] = ALLOWED_SOURCE_FILES
    allowed_metadata: frozenset[str] = frozenset({GIT_BASELINE_SOURCE})

    def resolve(self, relative_path: str) -> Path:
        normalized = Path(relative_path).as_posix()
        if normalized not in self.allowed_files:
            raise PermissionError(f"source is not allowlisted: {relative_path}")
        candidate = (self.repo_root / normalized).resolve()
        root = self.repo_root.resolve()
        if root != candidate and root not in candidate.parents:
            raise PermissionError(f"source escapes repository root: {relative_path}")
        return candidate

    def require_metadata(self, source: str) -> str:
        if source not in self.allowed_metadata:
            raise PermissionError(f"metadata source is not allowlisted: {source}")
        return source
