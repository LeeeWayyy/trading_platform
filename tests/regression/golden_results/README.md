# Golden Results

Purpose: canonical backtest baselines to catch regressions in the P4T4-T5.6 harness.

Naming convention: `{alpha_name}_{start_year}_{end_year}.json` (metrics) with matching `{alpha_name}_{start_year}_{end_year}_config.json` describing inputs.

Regeneration triggers:
- Major alpha logic change
- Dataset schema change
- Quarterly refresh (optional)

Storage limit: keep total golden artifacts under 10MB.

Staleness policy: CI **fails** when `manifest.json` is older than 90 days.

Review process: regeneration must be in a PR with reviewer approval; do not bypass review gates.

Checksum format: `sha256:<hexdigest>`.

Decimal MB definition: 1MB = 1,000,000 bytes (not MiB).

How to regenerate: run `python scripts/generate_golden_results.py --use-placeholders` (ensuring repo venv is active) to refresh files and update the manifest. The `--use-placeholders` flag is required to prevent accidental overwrites.
