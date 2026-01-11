#!/usr/bin/env python3
"""
Model Registry CLI for model lifecycle management.

Commands:
    model register <type> <path> --version <semver>
    model promote <type> <version>
    model rollback <type>
    model list <type> --status [staged|production|archived]
    model validate <type> <version>
    model restore --from-backup <date>
    model backup create
    model backup list
    model gc --dry-run

Example:
    $ python scripts/model_cli.py register risk_model artifacts/risk_v1.pkl --version v1.0.0
    $ python scripts/model_cli.py promote risk_model v1.0.0
    $ python scripts/model_cli.py list risk_model --status production
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from libs.models import (  # noqa: E402
    ModelMetadata,
    ModelRegistry,
    ModelStatus,
    ModelType,
    PromotionGateError,
    capture_environment,
    compute_config_hash,
    generate_model_id,
)
from libs.models.backup import RegistryBackupManager, RegistryGC  # noqa: E402


def get_registry(
    registry_dir: str = "data/models",
    skip_lineage_check: bool = False,
) -> ModelRegistry:
    """Get or create registry instance.

    Args:
        registry_dir: Path to registry directory.
        skip_lineage_check: If True, allow registration without lineage validation.
            If False (default), try to initialize DatasetVersionManager for validation.

    Returns:
        ModelRegistry instance.

    Raises:
        SystemExit: If skip_lineage_check=False and DatasetVersionManager cannot be initialized.
    """
    import sys

    if skip_lineage_check:
        print(
            "WARNING: --skip-lineage-check specified. "
            "Dataset/snapshot lineage validation will be SKIPPED. "
            "This is NOT recommended for production use.",
            file=sys.stderr,
        )
        return ModelRegistry(registry_dir=Path(registry_dir))

    # Try to initialize DatasetVersionManager for lineage validation
    try:
        from libs.data_quality.manifest import ManifestManager
        from libs.data_quality.versioning import DatasetVersionManager

        manifest_mgr = ManifestManager()
        version_mgr = DatasetVersionManager(manifest_mgr)
        print("DatasetVersionManager initialized for lineage validation.", file=sys.stderr)
        return ModelRegistry(registry_dir=Path(registry_dir), version_manager=version_mgr)
    except ImportError as e:
        print(
            f"ERROR: Failed to import required modules for lineage validation: {e}\n"
            "Use --skip-lineage-check ONLY for testing (NOT recommended for production).",
            file=sys.stderr,
        )
        sys.exit(1)
    except OSError as e:
        print(
            f"ERROR: File I/O error initializing DatasetVersionManager: {e}\n"
            "Use --skip-lineage-check ONLY for testing (NOT recommended for production).",
            file=sys.stderr,
        )
        sys.exit(2)
    except (ValueError, RuntimeError) as e:
        print(
            f"ERROR: Failed to initialize DatasetVersionManager: {e}\n"
            "Use --skip-lineage-check ONLY for testing (NOT recommended for production).",
            file=sys.stderr,
        )
        sys.exit(3)


def get_registry_readonly(registry_dir: str = "data/models") -> ModelRegistry:
    """Get registry instance for read-only operations (list, validate, etc.).

    Read-only operations don't need lineage validation since they don't modify data.

    Args:
        registry_dir: Path to registry directory.

    Returns:
        ModelRegistry instance.
    """
    return ModelRegistry(registry_dir=Path(registry_dir))


def cmd_register(args: argparse.Namespace) -> int:
    """Register a new model version."""
    registry = get_registry(args.registry_dir, skip_lineage_check=args.skip_lineage_check)

    print(f"Registering {args.type}/{args.version} from {args.path}...")

    # Load model from path (for now, just validate path exists)
    model_path = Path(args.path)
    if not model_path.exists():
        print(f"Error: Model path does not exist: {model_path}")
        return 1

    # For pickle files, load the model
    import pickle

    with open(model_path, "rb") as f:
        model = pickle.load(f)  # noqa: S301

    # Create metadata
    env = capture_environment(created_by=args.created_by or "model_cli")
    try:
        dataset_versions = json.loads(args.dataset_versions) if args.dataset_versions else {}
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON for --dataset-versions: {e}")
        return 1

    try:
        parameters = json.loads(args.parameters) if args.parameters else {}
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON for --parameters: {e}")
        return 1

    try:
        metrics = json.loads(args.metrics) if args.metrics else {}
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON for --metrics: {e}")
        return 1

    try:
        config = json.loads(args.config) if args.config else {}
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON for --config: {e}")
        return 1

    if not isinstance(config, dict):
        print("Error: --config must be a JSON object")
        return 1

    config_hash = compute_config_hash(config)

    # Ensure required fields for artifact type
    model_type = ModelType(args.type)

    metadata = ModelMetadata(
        model_id=generate_model_id(),
        model_type=model_type,
        version=args.version,
        created_at=datetime.now(UTC),
        dataset_version_ids=dataset_versions,
        snapshot_id=args.snapshot_id or "manual",
        factor_list=args.factors.split(",") if args.factors else [],
        parameters=parameters,
        checksum_sha256="",  # Will be computed
        metrics=metrics,
        env=env,
        config=config,
        config_hash=config_hash,
        feature_formulas=None,
        experiment_id=None,
        run_id=None,
        dataset_uri=None,
        qlib_version=None,
    )

    try:
        model_id = registry.register_model(
            model=model,
            metadata=metadata,
            changed_by=args.created_by or "model_cli",
        )
        print(f"Successfully registered: {model_id}")
        return 0
    except FileNotFoundError as e:
        print(f"Error: Model file not found: {e}")
        return 2
    except OSError as e:
        print(f"Error: File I/O error: {e}")
        return 3
    except (ValueError, KeyError) as e:
        print(f"Error: Invalid model metadata: {e}")
        return 4


def cmd_promote(args: argparse.Namespace) -> int:
    """Promote model to production."""
    registry = get_registry_readonly(args.registry_dir)

    print(f"Promoting {args.type}/{args.version}...")

    try:
        result = registry.promote_model(
            model_type=args.type,
            version=args.version,
            changed_by=args.user or "model_cli",
            skip_gates=args.skip_gates,
        )
        print(f"Promotion successful: {result.message}")
        if result.from_version:
            print(f"  Previous version: {result.from_version}")
        print(f"  New version: {result.to_version}")
        return 0
    except PromotionGateError as e:
        print(f"Promotion failed: {e}")
        return 1
    except FileNotFoundError as e:
        print(f"Error: Model not found: {e}")
        return 2
    except OSError as e:
        print(f"Error: File I/O error: {e}")
        return 3
    except (ValueError, KeyError) as e:
        print(f"Error: Invalid model configuration: {e}")
        return 4


def cmd_rollback(args: argparse.Namespace) -> int:
    """Rollback to previous production version."""
    registry = get_registry_readonly(args.registry_dir)

    print(f"Rolling back {args.type}...")

    try:
        result = registry.rollback_model(
            model_type=args.type,
            changed_by=args.user or "model_cli",
        )
        print(f"Rollback successful: {result.message}")
        return 0
    except FileNotFoundError as e:
        print(f"Error: Model or backup not found: {e}")
        return 2
    except OSError as e:
        print(f"Error: File I/O error: {e}")
        return 3
    except (ValueError, KeyError, RuntimeError) as e:
        print(f"Error: Rollback failed: {e}")
        return 4


def cmd_list(args: argparse.Namespace) -> int:
    """List models."""
    registry = get_registry_readonly(args.registry_dir)

    status_filter = ModelStatus(args.status) if args.status else None
    models = registry.list_models(model_type=args.type, status=status_filter)

    if args.json:
        # JSON output
        output = []
        for m in models:
            output.append(
                {
                    "model_id": m.model_id,
                    "model_type": m.model_type.value,
                    "version": m.version,
                    "created_at": m.created_at.isoformat(),
                    "checksum": m.checksum_sha256[:16],
                }
            )
        print(json.dumps(output, indent=2))
    else:
        # Table output
        if not models:
            print("No models found.")
            return 0

        print(f"\n{'Model Type':<15} {'Version':<10} {'Created':<20} {'Checksum':<18}")
        print("-" * 65)
        for m in models:
            print(
                f"{m.model_type.value:<15} "
                f"{m.version:<10} "
                f"{m.created_at.strftime('%Y-%m-%d %H:%M'):<20} "
                f"{m.checksum_sha256[:16]:<18}"
            )

    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    """Validate model artifact."""
    registry = get_registry_readonly(args.registry_dir)

    print(f"Validating {args.type}/{args.version}...")

    result = registry.validate_model(args.type, args.version)

    print(f"  Valid: {result.valid}")
    print(f"  Checksum verified: {result.checksum_verified}")
    print(f"  Load successful: {result.load_successful}")

    if result.errors:
        print("  Errors:")
        for error in result.errors:
            print(f"    - {error}")

    return 0 if result.valid else 1


def cmd_backup_create(args: argparse.Namespace) -> int:
    """Create registry backup."""
    manager = RegistryBackupManager(Path(args.registry_dir))

    print("Creating backup...")
    manifest = manager.create_backup()

    print(f"Backup created: {manifest.backup_id}")
    print(f"  Path: {manifest.backup_path}")
    print(f"  Size: {manifest.size_bytes:,} bytes")
    print(f"  Checksum: {manifest.checksum[:16] if manifest.checksum else 'N/A'}")

    return 0


def cmd_backup_list(args: argparse.Namespace) -> int:
    """List available backups."""
    manager = RegistryBackupManager(Path(args.registry_dir))

    if not manager.backups_dir.exists():
        print("No backups found.")
        return 0

    print(f"\n{'Backup ID':<40} {'Created':<20} {'Size':<15}")
    print("-" * 75)

    for backup_path in sorted(manager.backups_dir.iterdir(), reverse=True):
        if not backup_path.is_dir():
            continue
        manifest_path = backup_path / "backup_manifest.json"
        if manifest_path.exists():
            with open(manifest_path) as f:
                manifest = json.load(f)
            print(
                f"{manifest['backup_id']:<40} "
                f"{manifest['created_at'][:19]:<20} "
                f"{manifest['size_bytes']:>12,}"
            )

    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    """Restore from backup."""
    manager = RegistryBackupManager(Path(args.registry_dir))

    backup_date = None
    if args.from_backup:
        backup_date = date.fromisoformat(args.from_backup)

    print(f"Restoring from backup{f' ({backup_date})' if backup_date else ''}...")

    result = manager.restore_from_backup(backup_date=backup_date)

    if result.success:
        print(f"Restore successful: {result.message}")
        print(f"  Models restored: {result.models_restored}")
    else:
        print(f"Restore failed: {result.message}")
        return 1

    return 0


def cmd_gc(args: argparse.Namespace) -> int:
    """Run garbage collection."""
    registry = get_registry_readonly(args.registry_dir)
    gc = RegistryGC(registry)

    print(f"Running GC (dry_run={args.dry_run})...")

    report = gc.run_gc(dry_run=args.dry_run)

    print(f"  Expired staged: {len(report.expired_staged)}")
    print(f"  Expired archived: {len(report.expired_archived)}")
    print(f"  Bytes freed: {report.bytes_freed:,}")

    if report.expired_staged:
        print("\nExpired staged models:")
        for model_id in report.expired_staged:
            print(f"  - {model_id}")

    if report.expired_archived:
        print("\nExpired archived models:")
        for model_id in report.expired_archived:
            print(f"  - {model_id}")

    return 0


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Model Registry CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--registry-dir",
        default="data/models",
        help="Registry directory (default: data/models)",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command")

    # Register command
    register_parser = subparsers.add_parser("register", help="Register a new model")
    register_parser.add_argument("type", choices=[t.value for t in ModelType], help="Model type")
    register_parser.add_argument("path", help="Path to model artifact")
    register_parser.add_argument("--version", required=True, help="Semantic version (vX.Y.Z)")
    register_parser.add_argument("--created-by", help="Creator identifier")
    register_parser.add_argument("--snapshot-id", help="Snapshot ID")
    register_parser.add_argument("--dataset-versions", help="JSON dict of dataset versions")
    register_parser.add_argument("--factors", help="Comma-separated factor list")
    register_parser.add_argument("--parameters", help="JSON parameters dict")
    register_parser.add_argument("--metrics", help="JSON metrics dict")
    register_parser.add_argument("--config", help="JSON config dict")
    register_parser.add_argument(
        "--skip-lineage-check",
        action="store_true",
        help="Skip dataset/snapshot lineage validation (NOT recommended for production)",
    )

    # Promote command
    promote_parser = subparsers.add_parser("promote", help="Promote model to production")
    promote_parser.add_argument("type", choices=[t.value for t in ModelType], help="Model type")
    promote_parser.add_argument("version", help="Version to promote")
    promote_parser.add_argument("--user", help="User making the change")
    promote_parser.add_argument("--skip-gates", action="store_true", help="Skip promotion gates")

    # Rollback command
    rollback_parser = subparsers.add_parser("rollback", help="Rollback to previous version")
    rollback_parser.add_argument("type", choices=[t.value for t in ModelType], help="Model type")
    rollback_parser.add_argument("--user", help="User making the change")

    # List command
    list_parser = subparsers.add_parser("list", help="List models")
    list_parser.add_argument("type", nargs="?", help="Model type (optional)")
    list_parser.add_argument(
        "--status",
        choices=[s.value for s in ModelStatus],
        help="Filter by status",
    )
    list_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # Validate command
    validate_parser = subparsers.add_parser("validate", help="Validate model")
    validate_parser.add_argument("type", choices=[t.value for t in ModelType], help="Model type")
    validate_parser.add_argument("version", help="Version to validate")

    # Backup commands
    backup_parser = subparsers.add_parser("backup", help="Backup operations")
    backup_subparsers = backup_parser.add_subparsers(dest="backup_cmd", help="Backup command")
    backup_subparsers.add_parser("create", help="Create backup")
    backup_subparsers.add_parser("list", help="List backups")

    # Restore command
    restore_parser = subparsers.add_parser("restore", help="Restore from backup")
    restore_parser.add_argument("--from-backup", help="Backup date (YYYY-MM-DD)")

    # GC command
    gc_parser = subparsers.add_parser("gc", help="Garbage collection")
    gc_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Don't actually delete (default)",
    )
    gc_parser.add_argument("--execute", action="store_true", help="Actually delete")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    # Dispatch to command handler
    if args.command == "register":
        return cmd_register(args)
    elif args.command == "promote":
        return cmd_promote(args)
    elif args.command == "rollback":
        return cmd_rollback(args)
    elif args.command == "list":
        return cmd_list(args)
    elif args.command == "validate":
        return cmd_validate(args)
    elif args.command == "backup":
        if args.backup_cmd == "create":
            return cmd_backup_create(args)
        elif args.backup_cmd == "list":
            return cmd_backup_list(args)
        else:
            backup_parser.print_help()
            return 1
    elif args.command == "restore":
        return cmd_restore(args)
    elif args.command == "gc":
        if args.execute:
            args.dry_run = False
        return cmd_gc(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
