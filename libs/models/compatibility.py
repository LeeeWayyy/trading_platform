"""
Version compatibility checking for model loading.

This module provides:
- VersionCompatibilityChecker: Check model vs current dataset versions
- Strict mode control via STRICT_VERSION_MODE
- ANY version drift policy (not semantic)

Key design decisions:
- STRICT_VERSION_MODE=true (production): ANY drift blocks
- STRICT_VERSION_MODE=false (development): ANY drift warns, allows load
- Missing dataset ALWAYS blocks (regardless of mode)
- This is stricter than semantic versioning - any mismatch triggers policy
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class CompatibilityResult:
    """Result of version compatibility check.

    Attributes:
        compatible: Whether load should proceed.
        level: Compatibility level - "exact", "drift", or "missing".
        warnings: List of warning messages for logging.
    """

    compatible: bool
    level: str  # "exact" | "drift" | "missing"
    warnings: list[str] = field(default_factory=list)


class MissingDatasetError(Exception):
    """Raised when required dataset is missing from current environment."""

    def __init__(self, dataset: str, model_version: str) -> None:
        self.dataset = dataset
        self.model_version = model_version
        super().__init__(
            f"Dataset '{dataset}' (version {model_version}) required by model "
            f"is not available in current environment"
        )


class VersionCompatibilityChecker:
    """Check model compatibility with current dataset versions.

    Version Drift Policy (per spec ~2289-2294):
    - STRICT_VERSION_MODE=true (production default): ANY drift -> BLOCK
    - STRICT_VERSION_MODE=false (development): ANY drift -> WARN, allow load
    - MISSING dataset -> BLOCK always (regardless of mode)

    Note: This is stricter than semantic versioning - any version mismatch
    triggers the policy, not just major/minor changes.

    Example:
        checker = VersionCompatibilityChecker()

        # Model trained on specific versions
        model_versions = {"crsp": "v1.2.3", "compustat": "v1.0.1"}

        # Current environment versions
        current_versions = {"crsp": "v1.2.4", "compustat": "v1.0.1"}

        result = checker.check_compatibility(model_versions, current_versions)

        if not result.compatible:
            raise IncompatibleVersionError(result.warnings)
        elif result.warnings:
            logger.warning("Version drift detected", warnings=result.warnings)
    """

    ENV_VAR = "STRICT_VERSION_MODE"

    def __init__(self, strict_mode: bool | None = None) -> None:
        """Initialize checker.

        Args:
            strict_mode: Override strict mode. If None, reads from environment.
                        Defaults to True (production behavior).
        """
        if strict_mode is not None:
            self._strict_mode = strict_mode
        else:
            # Read from environment, default to strict
            env_value = os.environ.get(self.ENV_VAR, "true").lower()
            self._strict_mode = env_value not in ("false", "0", "no")

    @property
    def strict_mode(self) -> bool:
        """Current strict mode setting."""
        return self._strict_mode

    def check_compatibility(
        self,
        model_versions: dict[str, str],
        current_versions: dict[str, str],
        strict_mode: bool | None = None,
    ) -> CompatibilityResult:
        """Check if model dataset versions are compatible with current environment.

        Policy:
        - ANY version difference is drift (not semantic-based)
        - strict_mode=True: drift blocks load
        - strict_mode=False: drift warns but allows load
        - Missing dataset ALWAYS blocks

        Args:
            model_versions: Dataset versions the model was trained on.
            current_versions: Current dataset versions in environment.
            strict_mode: Override instance strict mode for this call.

        Returns:
            CompatibilityResult with compatibility status and warnings.
        """
        use_strict = strict_mode if strict_mode is not None else self._strict_mode
        warnings: list[str] = []
        has_drift = False

        for dataset, model_ver in model_versions.items():
            current_ver = current_versions.get(dataset)

            if current_ver is None:
                # MISSING dataset always blocks
                warning = (
                    f"Dataset '{dataset}' (version {model_ver}) required by model "
                    f"is not available in current environment"
                )
                logger.error(
                    "Missing dataset blocks model load",
                    extra={"dataset": dataset, "model_version": model_ver},
                )
                return CompatibilityResult(
                    compatible=False,
                    level="missing",
                    warnings=[warning],
                )

            if model_ver != current_ver:
                # ANY version difference is drift (not semantic)
                has_drift = True
                warning = (
                    f"Dataset '{dataset}': model trained on {model_ver}, "
                    f"current environment has {current_ver}"
                )
                warnings.append(warning)
                logger.warning(
                    "Dataset version drift detected",
                    extra={
                        "dataset": dataset,
                        "model_version": model_ver,
                        "current_version": current_ver,
                    },
                )

        if has_drift:
            # Drift handling depends on strict mode
            compatible = not use_strict

            if use_strict:
                logger.error(
                    "Version drift blocks model load in strict mode",
                    extra={"drift_warnings": warnings},
                )
            else:
                logger.warning(
                    "Version drift allowed in non-strict mode",
                    extra={"drift_warnings": warnings},
                )

            return CompatibilityResult(
                compatible=compatible,
                level="drift",
                warnings=warnings,
            )

        # Exact match
        logger.debug(
            "Model versions exactly match current environment",
            extra={"model_versions": model_versions},
        )
        return CompatibilityResult(
            compatible=True,
            level="exact",
            warnings=[],
        )

    def check_compatibility_or_raise(
        self,
        model_versions: dict[str, str],
        current_versions: dict[str, str],
        strict_mode: bool | None = None,
    ) -> None:
        """Check compatibility, raising on incompatibility.

        Args:
            model_versions: Dataset versions the model was trained on.
            current_versions: Current dataset versions in environment.
            strict_mode: Override instance strict mode for this call.

        Raises:
            MissingDatasetError: If required dataset is missing.
            VersionDriftError: If drift detected in strict mode.
        """
        result = self.check_compatibility(
            model_versions, current_versions, strict_mode
        )

        if not result.compatible:
            if result.level == "missing":
                # Extract dataset and version info from warning message
                # Warning format: "Dataset 'X' (version Y) required by model..."
                dataset = "unknown"
                version = "unknown"
                if result.warnings:
                    warning = result.warnings[0]
                    # Parse: "Dataset 'crsp' (version v1.2.3) required..."
                    import re
                    match = re.search(r"Dataset '([^']+)' \(version ([^)]+)\)", warning)
                    if match:
                        dataset = match.group(1)
                        version = match.group(2)
                raise MissingDatasetError(dataset, version)
            else:
                raise VersionDriftError(result.warnings)


class VersionDriftError(Exception):
    """Raised when version drift blocks model load in strict mode."""

    def __init__(self, warnings: list[str]) -> None:
        self.warnings = warnings
        message = "Version drift detected in strict mode:\n" + "\n".join(
            f"  - {w}" for w in warnings
        )
        super().__init__(message)


# Re-export for convenience
__all__ = [
    "CompatibilityResult",
    "MissingDatasetError",
    "VersionCompatibilityChecker",
    "VersionDriftError",
]
