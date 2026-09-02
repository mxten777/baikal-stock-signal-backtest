from __future__ import annotations

import pandas as pd

from scripts.step4_foreign_score_mismatch import build_crosstab, score_bucket


def test_score_bucket_uses_existing_foreign_score_ranges() -> None:
    assert [score_bucket(score) for score in (10, 40, 50, 75, 100)] == ["LOW", "LOW", "MID", "HIGH", "HIGH"]


def test_crosstab_keeps_all_existing_class_and_score_buckets() -> None:
    frame = pd.DataFrame({
        "foreign_class": ["POSITIVE", "NEUTRAL", "NEGATIVE"],
        "foreign_score_bucket": ["HIGH", "MID", "LOW"],
    })
    report = build_crosstab(frame).set_index("Foreign Class")
    assert report.loc["POSITIVE", "HIGH"] == 1
    assert report.loc["NEUTRAL", "MID"] == 1
    assert report.loc["NEGATIVE", "LOW"] == 1