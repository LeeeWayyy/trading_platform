"""Synthetic ID generation for orders missing client_order_id.

This module provides deterministic ID generation for orders that arrive without
a client_order_id, using fingerprinting to ensure row stability in AG Grid while
handling edge cases like same-batch collisions and orphaned suffix entries.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

# ID prefix constants - used for identifying synthetic/fallback IDs
SYNTHETIC_ID_PREFIX = "unknown_"
FALLBACK_ID_PREFIX = "__ng_fallback_"


def normalize_num(val: object) -> str:
    """Normalize numeric value for fingerprint hashing.

    Uses repr() for full precision to avoid fingerprint collisions
    (e.g., crypto with >6 decimal precision).
    """
    if isinstance(val, int | float):
        return repr(float(val))
    return str(val) if val is not None else ""


def compute_order_fingerprint(order: dict[str, Any]) -> tuple[str, str]:
    """Compute stable fingerprint for an order lacking client_order_id.

    Returns:
        Tuple of (fingerprint_string, base_hash) for synthetic ID generation.
    """
    # Use `or ""` to handle both missing keys AND explicit None values
    fingerprint_fields = [
        order.get("symbol") or "",
        order.get("side") or "",
        order.get("created_at") or "",
        order.get("account_id") or "",
        normalize_num(order.get("qty")),
        order.get("type") or "",
        normalize_num(order.get("limit_price")),
        order.get("time_in_force") or "",
    ]
    fingerprint = "|".join(fingerprint_fields)
    base_hash = hashlib.sha256(fingerprint.encode()).hexdigest()[:12]
    return fingerprint, base_hash


@dataclass
class SyntheticIdContext:
    """Context for synthetic ID generation across a batch of orders."""

    synthetic_id_map: dict[str, str] | None
    previous_order_ids: set[str] | None
    batch_generated_ids: set[str]


def resolve_synthetic_id(
    fingerprint: str,
    base_hash: str,
    ctx: SyntheticIdContext,
) -> str:
    """Resolve synthetic ID for an order, handling collisions and row stability.

    This function implements the synthetic ID assignment logic:
    1. If fingerprint exists in map, reuse that ID (with suffix preference for row stability)
    2. Handle same-batch collisions by finding unused suffix IDs
    3. Check for orphaned suffix entries when base is missing
    4. Generate new ID if no existing mapping

    Args:
        fingerprint: Order fingerprint string
        base_hash: SHA256 hash prefix for new IDs
        ctx: Shared context with ID maps and batch tracking

    Returns:
        Synthetic ID to use for this order
    """
    synthetic_id_map = ctx.synthetic_id_map
    previous_order_ids = ctx.previous_order_ids
    batch_generated_ids = ctx.batch_generated_ids

    if synthetic_id_map is not None and fingerprint in synthetic_id_map:
        # Fingerprint already mapped - check if base ID is still valid
        base_id = synthetic_id_map[fingerprint]
        synthetic_id = base_id

        # If base ID is NOT in previous snapshot but a suffix IS, use the suffix
        # This prevents row churn when base order fills and suffix remains
        if previous_order_ids is not None and base_id not in previous_order_ids:
            synthetic_id = _find_suffix_in_previous(
                fingerprint, synthetic_id_map, previous_order_ids, base_id
            )

        # Handle same-batch collision
        if synthetic_id in batch_generated_ids:
            synthetic_id = _resolve_batch_collision(
                fingerprint, synthetic_id, synthetic_id_map, batch_generated_ids
            )
    else:
        # Base fingerprint not in map - check for orphaned suffix entries
        synthetic_id = _find_orphan_suffix_or_create(
            fingerprint, base_hash, synthetic_id_map, batch_generated_ids
        )

    batch_generated_ids.add(synthetic_id)
    return synthetic_id


def _find_suffix_in_previous(
    fingerprint: str,
    synthetic_id_map: dict[str, str],
    previous_order_ids: set[str],
    default_id: str,
) -> str:
    """Find a suffix ID that was in the previous snapshot for row stability."""
    suffix = 1
    suffix_key = f"{fingerprint}|_suffix_{suffix}"
    while suffix_key in synthetic_id_map:
        suffix_id = synthetic_id_map[suffix_key]
        if suffix_id in previous_order_ids:
            return suffix_id
        suffix += 1
        suffix_key = f"{fingerprint}|_suffix_{suffix}"
    return default_id


def _resolve_batch_collision(
    fingerprint: str,
    current_id: str,
    synthetic_id_map: dict[str, str] | None,
    batch_generated_ids: set[str],
) -> str:
    """Resolve same-batch collision by finding or creating a suffix ID."""
    if synthetic_id_map is None:
        # No map to persist to - just append suffix
        suffix = 1
        new_id = f"{current_id}_{suffix}"
        while new_id in batch_generated_ids:
            suffix += 1
            new_id = f"{current_id}_{suffix}"
        return new_id

    suffix = 1
    suffix_key = f"{fingerprint}|_suffix_{suffix}"
    # First try to find an existing suffix key not yet used in this batch
    while suffix_key in synthetic_id_map:
        existing_suffix_id = synthetic_id_map[suffix_key]
        if existing_suffix_id not in batch_generated_ids:
            return existing_suffix_id
        suffix += 1
        suffix_key = f"{fingerprint}|_suffix_{suffix}"

    # No existing suffix found - generate new one
    new_id = f"{current_id}_{suffix}"
    synthetic_id_map[suffix_key] = new_id
    return new_id


def _find_orphan_suffix_or_create(
    fingerprint: str,
    base_hash: str,
    synthetic_id_map: dict[str, str] | None,
    batch_generated_ids: set[str],
) -> str:
    """Find orphaned suffix entry or create new synthetic ID."""
    # Check for orphaned suffix entries (when base order filled but suffix remains)
    if synthetic_id_map is not None:
        suffix = 1
        suffix_key = f"{fingerprint}|_suffix_{suffix}"
        while suffix_key in synthetic_id_map:
            orphan_id = synthetic_id_map[suffix_key]
            if orphan_id not in batch_generated_ids:
                return orphan_id
            suffix += 1
            suffix_key = f"{fingerprint}|_suffix_{suffix}"

    # Create new synthetic ID
    synthetic_id = f"{SYNTHETIC_ID_PREFIX}{base_hash}"

    # Check against both persistent map and current batch to avoid collisions
    existing_ids = batch_generated_ids.copy()
    if synthetic_id_map is not None:
        existing_ids.update(synthetic_id_map.values())

    suffix = 0
    while synthetic_id in existing_ids:
        suffix += 1
        synthetic_id = f"{SYNTHETIC_ID_PREFIX}{base_hash}_{suffix}"

    if synthetic_id_map is not None:
        synthetic_id_map[fingerprint] = synthetic_id

    return synthetic_id


__all__ = [
    "SYNTHETIC_ID_PREFIX",
    "FALLBACK_ID_PREFIX",
    "normalize_num",
    "compute_order_fingerprint",
    "SyntheticIdContext",
    "resolve_synthetic_id",
]
