from __future__ import annotations

from dataclasses import dataclass
from typing import Any


STATUS_EMPTY = "EMPTY"
STATUS_AVAILABLE = "AVAILABLE"
STATUS_STALE = "STALE"
STATUS_MISSING = "MISSING"
STATUS_UNAVAILABLE = "UNAVAILABLE"

DATA_OPERATIONAL = "operational"
DATA_HISTORICAL_VALIDATION = "historical_validation"
DATA_METADATA = "metadata"

MODE_SHADOW = "SHADOW"
DEFAULT_BASELINE_COMMIT = "38e56c5"


@dataclass(frozen=True)
class Metric:
    value: Any = None
    sample_size: int | None = None
    status: str = STATUS_UNAVAILABLE
    source: str | None = None
    as_of: str | None = None
    data_kind: str | None = None
    warnings: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "value": self.value,
            "sample_size": self.sample_size,
            "status": self.status,
            "source": self.source,
            "as_of": self.as_of,
            "data_kind": self.data_kind,
        }
        if self.warnings:
            payload["warnings"] = self.warnings
        return payload


def unavailable_metric(source: str | None, data_kind: str, warning: str) -> dict[str, Any]:
    return Metric(
        status=STATUS_UNAVAILABLE,
        source=source,
        data_kind=data_kind,
        warnings=[warning],
    ).to_dict()


def available_metric(
    value: Any,
    source: str,
    data_kind: str,
    sample_size: int | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    return Metric(
        value=value,
        sample_size=sample_size,
        status=STATUS_AVAILABLE,
        source=source,
        as_of=as_of,
        data_kind=data_kind,
    ).to_dict()
