"""Unit tests for feature metadata catalog and statistics helpers."""

from __future__ import annotations

from datetime import UTC

import pandas as pd

from libs.data.feature_metadata import (
    compute_feature_statistics,
    get_feature_catalog,
    get_sample_values,
    validate_catalog_against_runtime,
)


def _make_multiindex_df() -> pd.DataFrame:
    idx = pd.MultiIndex.from_tuples(
        [
            ("2026-01-01", "AAPL"),
            ("2026-01-01", "MSFT"),
            ("2026-01-02", "AAPL"),
            ("2026-01-02", "MSFT"),
        ],
        names=["datetime", "instrument"],
    )
    return pd.DataFrame(
        {
            "ROC0": [1.0, 2.0, 3.0, 4.0],
            "MA0": [10.0, 20.0, 30.0, 40.0],
            "NULL_COL": [None, None, None, None],
        },
        index=idx,
    )


def test_get_feature_catalog_returns_158_features() -> None:
    catalog = get_feature_catalog()
    assert len(catalog) == 158


def test_get_feature_catalog_contains_all_35_categories() -> None:
    expected_categories = {
        "KBAR",
        "KLEN",
        "KMID",
        "KUP",
        "KLOW",
        "KSFT",
        "ROC",
        "MA",
        "STD",
        "BETA",
        "RSQR",
        "RESI",
        "MAX",
        "MIN",
        "QTLU",
        "QTLD",
        "RANK",
        "RSV",
        "IMAX",
        "IMIN",
        "IMXD",
        "CORR",
        "CORD",
        "CNTP",
        "CNTN",
        "CNTD",
        "SUMP",
        "SUMN",
        "SUMD",
        "VMA",
        "VSTD",
        "WVMA",
        "VSUMP",
        "VSUMN",
        "VSUMD",
    }
    categories = {f.category for f in get_feature_catalog()}
    assert categories == expected_categories


def test_each_feature_has_required_non_empty_fields() -> None:
    for feature in get_feature_catalog():
        assert feature.name
        assert feature.description
        assert feature.formula
        assert feature.category
        assert feature.input_columns


def test_rolling_features_have_correct_lookback_windows() -> None:
    lookup = {feature.name: feature for feature in get_feature_catalog()}
    assert lookup["ROC0"].lookback_window == 5
    assert lookup["ROC4"].lookback_window == 60
    assert lookup["MA0"].lookback_window == 5
    assert lookup["MA4"].lookback_window == 60
    assert lookup["VSUMD4"].lookback_window == 60


def test_point_in_time_features_have_none_lookback_window() -> None:
    lookup = {feature.name: feature for feature in get_feature_catalog()}
    assert lookup["KBAR0"].lookback_window is None
    assert lookup["KLEN0"].lookback_window is None
    assert lookup["KMID0"].lookback_window is None
    assert lookup["KUP1"].lookback_window is None
    assert lookup["KSFT1"].lookback_window is None


def test_compute_feature_statistics_correct_for_synthetic_input() -> None:
    df = _make_multiindex_df()
    stats = compute_feature_statistics(df, ["ROC0"])
    assert len(stats) == 1
    stat = stats[0]
    assert stat.name == "ROC0"
    assert stat.count == 4
    assert stat.mean == 2.5
    assert stat.min_val == 1.0
    assert stat.max_val == 4.0
    assert stat.null_pct == 0.0
    assert stat.computed_at.tzinfo == UTC


def test_compute_feature_statistics_handles_empty_dataframe() -> None:
    empty = pd.DataFrame(columns=["ROC0", "MA0"])
    stats = compute_feature_statistics(empty)
    assert len(stats) == 2
    for stat in stats:
        assert stat.count == 0
        assert stat.mean is None
        assert stat.std is None
        assert stat.min_val is None
        assert stat.max_val is None
        assert stat.null_pct == 0.0


def test_compute_feature_statistics_handles_all_null_column() -> None:
    df = _make_multiindex_df()
    stats = compute_feature_statistics(df, ["NULL_COL"])
    assert len(stats) == 1
    stat = stats[0]
    assert stat.count == 0
    assert stat.mean is None
    assert stat.std is None
    assert stat.null_pct == 100.0


def test_compute_feature_statistics_subset_returns_only_requested_features() -> None:
    df = _make_multiindex_df()
    stats = compute_feature_statistics(df, ["MA0"])
    assert [s.name for s in stats] == ["MA0"]


def test_get_sample_values_returns_correct_sample_count_and_keys() -> None:
    df = _make_multiindex_df()
    samples = get_sample_values(df, "ROC0", n_samples=1)
    assert len(samples) == 1
    assert set(samples[0].keys()) == {"date", "symbol", "value"}


def test_get_sample_values_returns_empty_list_for_empty_dataframe() -> None:
    samples = get_sample_values(pd.DataFrame(), "ROC0")
    assert samples == []


def test_category_filtering_from_catalog() -> None:
    catalog = get_feature_catalog()
    roc_only = [f for f in catalog if f.category == "ROC"]
    assert len(roc_only) == 5
    assert all(f.name.startswith("ROC") for f in roc_only)


def test_feature_name_search_case_insensitive() -> None:
    catalog = get_feature_catalog()
    query = "kbar"
    matched = [f for f in catalog if query.lower() in f.name.lower()]
    assert len(matched) == 5
    assert all(f.name.startswith("KBAR") for f in matched)


def test_validate_catalog_against_runtime_matching_columns() -> None:
    runtime_columns = [f.name for f in get_feature_catalog()]
    missing, extra = validate_catalog_against_runtime(runtime_columns)
    assert missing == []
    assert extra == []


def test_validate_catalog_against_runtime_detects_missing_features() -> None:
    runtime_columns = [f.name for f in get_feature_catalog()]
    runtime_columns.append("CUSTOM_FEATURE")
    missing, extra = validate_catalog_against_runtime(runtime_columns)
    assert missing == ["CUSTOM_FEATURE"]
    assert extra == []


def test_validate_catalog_against_runtime_detects_extra_catalog_features() -> None:
    runtime_columns = [f.name for f in get_feature_catalog() if f.name != "KBAR0"]
    missing, extra = validate_catalog_against_runtime(runtime_columns)
    assert missing == []
    assert extra == ["KBAR0"]
