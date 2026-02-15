"""Feature catalog and statistics helpers for Alpha158 feature browsing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

import pandas as pd

_ROLLING_WINDOWS: tuple[int, ...] = (5, 10, 20, 30, 60)

# category, count, description, formula template, inputs, lookback windows
_FEATURE_SPECS: tuple[tuple[str, int, str, str, list[str], tuple[int, ...] | None], ...] = (
    (
        "KBAR",
        5,
        "Price bar ratios",
        "Candlestick price-bar ratio variant {index}",
        ["open", "high", "low", "close"],
        None,
    ),
    (
        "KLEN",
        1,
        "Bar length ratio",
        "(high - low) / open",
        ["high", "low", "open"],
        None,
    ),
    (
        "KMID",
        1,
        "Midpoint ratio",
        "(close - open) / ((high + low) / 2)",
        ["open", "high", "low", "close"],
        None,
    ),
    (
        "KUP",
        2,
        "Upper shadow ratios",
        "Upper shadow ratio variant {index}",
        ["open", "high", "close"],
        None,
    ),
    (
        "KLOW",
        2,
        "Lower shadow ratios",
        "Lower shadow ratio variant {index}",
        ["open", "low", "close"],
        None,
    ),
    (
        "KSFT",
        2,
        "Shadow difference ratios",
        "Shadow shift ratio variant {index}",
        ["open", "high", "low", "close"],
        None,
    ),
    ("ROC", 5, "Rate of change", "close / delay(close, {window}) - 1", ["close"], _ROLLING_WINDOWS),
    (
        "MA",
        5,
        "Moving average ratio",
        "close / mean(close, {window})",
        ["close"],
        _ROLLING_WINDOWS,
    ),
    (
        "STD",
        5,
        "Rolling std deviation",
        "std(close, {window})",
        ["close"],
        _ROLLING_WINDOWS,
    ),
    (
        "BETA",
        5,
        "Rolling beta",
        "beta(close, {window})",
        ["close"],
        _ROLLING_WINDOWS,
    ),
    (
        "RSQR",
        5,
        "Rolling R-squared",
        "rsquare(close, {window})",
        ["close"],
        _ROLLING_WINDOWS,
    ),
    (
        "RESI",
        5,
        "Rolling residual",
        "residual(close, {window})",
        ["close"],
        _ROLLING_WINDOWS,
    ),
    (
        "MAX",
        5,
        "Rolling max ratio",
        "high / max(high, {window})",
        ["high"],
        _ROLLING_WINDOWS,
    ),
    (
        "MIN",
        5,
        "Rolling min ratio",
        "low / min(low, {window})",
        ["low"],
        _ROLLING_WINDOWS,
    ),
    (
        "QTLU",
        5,
        "Upper quantile ratio",
        "close / quantile(close, {window}, 0.8)",
        ["close"],
        _ROLLING_WINDOWS,
    ),
    (
        "QTLD",
        5,
        "Lower quantile ratio",
        "close / quantile(close, {window}, 0.2)",
        ["close"],
        _ROLLING_WINDOWS,
    ),
    (
        "RANK",
        5,
        "Rolling rank",
        "rank(close, {window})",
        ["close"],
        _ROLLING_WINDOWS,
    ),
    (
        "RSV",
        5,
        "Relative strength value",
        "(close - min(low, {window})) / (max(high, {window}) - min(low, {window}))",
        ["close", "high", "low"],
        _ROLLING_WINDOWS,
    ),
    (
        "IMAX",
        5,
        "Argmax position",
        "argmax(high, {window}) / {window}",
        ["high"],
        _ROLLING_WINDOWS,
    ),
    (
        "IMIN",
        5,
        "Argmin position",
        "argmin(low, {window}) / {window}",
        ["low"],
        _ROLLING_WINDOWS,
    ),
    (
        "IMXD",
        5,
        "Argmax minus Argmin",
        "(argmax(high, {window}) - argmin(low, {window})) / {window}",
        ["high", "low"],
        _ROLLING_WINDOWS,
    ),
    (
        "CORR",
        5,
        "Correlation with volume",
        "corr(close, volume, {window})",
        ["close", "volume"],
        _ROLLING_WINDOWS,
    ),
    (
        "CORD",
        5,
        "Change correlation with volume",
        "corr(delta(close, 1), delta(volume, 1), {window})",
        ["close", "volume"],
        _ROLLING_WINDOWS,
    ),
    (
        "CNTP",
        5,
        "Count positive return days",
        "count(delta(close, 1) > 0, {window}) / {window}",
        ["close"],
        _ROLLING_WINDOWS,
    ),
    (
        "CNTN",
        5,
        "Count negative return days",
        "count(delta(close, 1) < 0, {window}) / {window}",
        ["close"],
        _ROLLING_WINDOWS,
    ),
    (
        "CNTD",
        5,
        "Count net (pos minus neg)",
        "(count(delta(close, 1) > 0, {window}) - count(delta(close, 1) < 0, {window})) / {window}",
        ["close"],
        _ROLLING_WINDOWS,
    ),
    (
        "SUMP",
        5,
        "Sum positive returns",
        "sum(max(delta(close, 1), 0), {window})",
        ["close"],
        _ROLLING_WINDOWS,
    ),
    (
        "SUMN",
        5,
        "Sum negative returns",
        "sum(abs(min(delta(close, 1), 0)), {window})",
        ["close"],
        _ROLLING_WINDOWS,
    ),
    (
        "SUMD",
        5,
        "Sum net returns",
        "sum(delta(close, 1), {window})",
        ["close"],
        _ROLLING_WINDOWS,
    ),
    (
        "VMA",
        5,
        "Volume moving average",
        "volume / mean(volume, {window})",
        ["volume"],
        _ROLLING_WINDOWS,
    ),
    (
        "VSTD",
        5,
        "Volume rolling std",
        "std(volume, {window})",
        ["volume"],
        _ROLLING_WINDOWS,
    ),
    (
        "WVMA",
        5,
        "Weighted volume MA",
        "mean(abs(delta(close, 1)) * volume, {window})",
        ["close", "volume"],
        _ROLLING_WINDOWS,
    ),
    (
        "VSUMP",
        5,
        "Volume-weighted sum pos",
        "sum(max(delta(close, 1), 0) * volume, {window})",
        ["close", "volume"],
        _ROLLING_WINDOWS,
    ),
    (
        "VSUMN",
        5,
        "Volume-weighted sum neg",
        "sum(abs(min(delta(close, 1), 0)) * volume, {window})",
        ["close", "volume"],
        _ROLLING_WINDOWS,
    ),
    (
        "VSUMD",
        5,
        "Volume-weighted sum net",
        "sum(delta(close, 1) * volume, {window})",
        ["close", "volume"],
        _ROLLING_WINDOWS,
    ),
)


@dataclass(frozen=True)
class FeatureMetadata:
    name: str
    category: str
    description: str
    formula: str
    input_columns: list[str]
    lookback_window: int | None
    data_type: str


@dataclass
class FeatureStatistics:
    name: str
    count: int
    mean: float | None
    std: float | None
    min_val: float | None
    q25: float | None
    median: float | None
    q75: float | None
    max_val: float | None
    null_pct: float
    computed_at: datetime


def _to_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    if pd.isna(value):
        return None
    return float(value)


@lru_cache(maxsize=1)
def _catalog_tuple() -> tuple[FeatureMetadata, ...]:
    catalog: list[FeatureMetadata] = []
    for category, count, description, formula_tmpl, inputs, windows in _FEATURE_SPECS:
        for idx in range(count):
            lookback = None if windows is None else windows[idx]
            formula = formula_tmpl.format(index=idx, window=lookback)
            description_with_window = description
            if lookback is not None:
                description_with_window = f"{description} ({lookback}d)"
            catalog.append(
                FeatureMetadata(
                    name=f"{category}{idx}",
                    category=category,
                    description=description_with_window,
                    formula=formula,
                    input_columns=list(inputs),
                    lookback_window=lookback,
                    data_type="float64",
                )
            )

    if len(catalog) != 158:
        raise RuntimeError(f"Feature catalog size mismatch: expected 158, got {len(catalog)}")

    return tuple(catalog)


def get_feature_catalog() -> list[FeatureMetadata]:
    """Return static metadata for all Alpha158 features (no I/O)."""
    return [
        FeatureMetadata(
            name=item.name,
            category=item.category,
            description=item.description,
            formula=item.formula,
            input_columns=list(item.input_columns),
            lookback_window=item.lookback_window,
            data_type=item.data_type,
        )
        for item in _catalog_tuple()
    ]


def validate_catalog_against_runtime(runtime_columns: list[str]) -> tuple[list[str], list[str]]:
    """Return (missing_from_catalog, extra_in_catalog) by column-name comparison."""
    catalog_names = {feature.name for feature in _catalog_tuple()}
    runtime_names = {str(name) for name in runtime_columns}
    missing_from_catalog = sorted(runtime_names - catalog_names)
    extra_in_catalog = sorted(catalog_names - runtime_names)
    return missing_from_catalog, extra_in_catalog


def compute_feature_statistics(
    features_df: pd.DataFrame,
    feature_names: list[str] | None = None,
) -> list[FeatureStatistics]:
    """Compute descriptive statistics for selected feature columns."""
    if feature_names is None:
        selected = [str(col) for col in features_df.columns]
    else:
        selected = [name for name in feature_names if name in features_df.columns]

    if not selected:
        return []

    selected_df = features_df[selected]
    numeric_df = selected_df.apply(pd.to_numeric, errors="coerce")

    describe_df: pd.DataFrame
    if len(numeric_df.columns) > 0 and len(numeric_df.index) > 0:
        describe_df = numeric_df.describe(percentiles=[0.25, 0.5, 0.75])
    else:
        describe_df = pd.DataFrame()

    computed_at = datetime.now(UTC)
    results: list[FeatureStatistics] = []
    total_rows = len(numeric_df.index)

    for column in selected:
        series = numeric_df[column] if column in numeric_df.columns else pd.Series(dtype="float64")
        count = int(series.count())
        null_pct = 0.0 if total_rows == 0 else float(series.isnull().mean() * 100)

        if describe_df.empty or column not in describe_df.columns:
            mean = std = min_val = q25 = median = q75 = max_val = None
        else:
            mean = _to_optional_float(describe_df.at["mean", column])
            std = _to_optional_float(describe_df.at["std", column])
            min_val = _to_optional_float(describe_df.at["min", column])
            q25 = _to_optional_float(describe_df.at["25%", column])
            median = _to_optional_float(describe_df.at["50%", column])
            q75 = _to_optional_float(describe_df.at["75%", column])
            max_val = _to_optional_float(describe_df.at["max", column])

        results.append(
            FeatureStatistics(
                name=column,
                count=count,
                mean=mean,
                std=std,
                min_val=min_val,
                q25=q25,
                median=median,
                q75=q75,
                max_val=max_val,
                null_pct=null_pct,
                computed_at=computed_at,
            )
        )

    return results


def get_sample_values(
    features_df: pd.DataFrame,
    feature_name: str,
    n_samples: int = 10,
) -> list[dict[str, Any]]:
    """Return sample date/symbol/value rows from the most recent available date."""
    if n_samples <= 0 or features_df.empty or feature_name not in features_df.columns:
        return []

    subset = features_df[[feature_name]].reset_index()
    if subset.empty or len(subset.columns) < 3:
        return []

    date_col = subset.columns[0]
    symbol_col = subset.columns[1]
    subset[date_col] = pd.to_datetime(subset[date_col], errors="coerce")
    subset = subset.dropna(subset=[date_col])
    if subset.empty:
        return []

    most_recent = subset[date_col].max()
    sampled = subset.loc[subset[date_col] == most_recent].head(n_samples)

    rows: list[dict[str, Any]] = []
    for _, row in sampled.iterrows():
        value = row[feature_name]
        rows.append(
            {
                "date": pd.Timestamp(row[date_col]).date().isoformat(),
                "symbol": str(row[symbol_col]),
                "value": None if pd.isna(value) else float(value),
            }
        )
    return rows


__all__ = [
    "FeatureMetadata",
    "FeatureStatistics",
    "compute_feature_statistics",
    "get_feature_catalog",
    "get_sample_values",
    "validate_catalog_against_runtime",
]
