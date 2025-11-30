#!/usr/bin/env python3
"""
Verify that branch protection is correctly configured for master branch.

This script checks that:
1. The master branch has protection enabled
2. Status checks are required before merging
3. The "Run tests and check coverage" check is in the required list

This is a meta-check to ensure our Review-Hash validation cannot be bypassed.

Exit codes:
  0 - Branch protection correctly configured
  1 - Branch protection missing or misconfigured
  2 - Error accessing GitHub API

Usage:
  python scripts/verify_branch_protection.py

Requirements:
  - gh CLI installed and authenticated
  - Read access to repository settings

Author: Claude Code
Date: 2025-11-16
"""

import json
import os
import subprocess
import sys


def check_branch_protection() -> int:
    """
    Verify branch protection via GitHub API.

    Returns:
        0 if correctly configured, 1 if misconfigured, 2 if API error
    """
    # Required status check name (must match CI job name)
    REQUIRED_CHECK = "Run tests and check coverage"
    # Allow branch name to be configurable (Gemini LOW fix)
    BRANCH = os.environ.get("DEFAULT_BRANCH", "master")

    print("🔍 Checking branch protection for master branch...")
    print()

    # Query GitHub API for branch protection
    try:
        result = subprocess.run(
            ["gh", "api", f"repos/:owner/:repo/branches/{BRANCH}/protection"],
            capture_output=True,
            text=True,
            check=False,  # Don't raise on non-zero exit (404 is expected if not protected)
        )

        # Handle 404 - branch not protected
        if result.returncode != 0:
            if "Branch not protected" in result.stderr or "404" in result.stderr:
                print("❌ BRANCH PROTECTION NOT CONFIGURED")
                print()
                print(f"   The '{BRANCH}' branch is not protected!")
                print()
                print("   This means:")
                print("   - Review-Hash validation can be bypassed")
                print("   - Developers can use --no-verify without detection")
                print("   - The workflow enforcement system is advisory, not mandatory")
                print()
                print("   Action Required:")
                print("   1. Go to Settings → Branches → Branch protection rules")
                print("   2. Add rule for 'master'")
                print("   3. Enable 'Require status checks to pass before merging'")
                print(f"   4. Add '{REQUIRED_CHECK}' to required checks")
                print()
                print("   See docs/STANDARDS/BRANCH_PROTECTION.md for detailed instructions")
                return 1
            else:
                # Other API error
                print(f"❌ GitHub API Error: {result.stderr}")
                print()
                print("   Could not verify branch protection status.")
                print("   This may be a temporary API issue or authentication problem.")
                print()
                print("   Troubleshooting:")
                print("   - Ensure 'gh' CLI is installed: gh --version")
                print("   - Ensure authenticated: gh auth status")
                print("   - Check GitHub API status: https://www.githubstatus.com/")
                return 2

        # Parse protection settings
        try:
            protection = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            print(f"❌ Failed to parse GitHub API response: {e}")
            print(f"   Raw output: {result.stdout[:200]}")
            return 2

        # Check if status checks are required
        required_status_checks = protection.get("required_status_checks")
        if not required_status_checks:
            print("❌ REQUIRED STATUS CHECKS NOT ENABLED")
            print()
            print(f"   The '{BRANCH}' branch is protected, but status checks are not required.")
            print()
            print("   Action Required:")
            print(f"   1. Edit branch protection rule for '{BRANCH}'")
            print("   2. Enable 'Require status checks to pass before merging'")
            print(f"   3. Add '{REQUIRED_CHECK}' to the list")
            print()
            print("   See docs/STANDARDS/BRANCH_PROTECTION.md for instructions")
            return 1

        # Check if our critical check is in the required list
        # GitHub API uses "contexts" for status check names (deprecated)
        # and "checks" for newer check suite API
        required_checks = required_status_checks.get("contexts", [])

        # Also check "checks" field if contexts is empty (newer API format)
        if not required_checks:
            required_checks = [
                check["context"] for check in required_status_checks.get("checks", [])
            ]

        if REQUIRED_CHECK not in required_checks:
            print("❌ CRITICAL CHECK NOT REQUIRED")
            print()
            print(f"   The '{REQUIRED_CHECK}' status check is not in the required list.")
            print()
            print(f"   Current required checks ({len(required_checks)}):")
            for check in required_checks:
                print(f"     - {check}")
            print()
            print(f"   Missing: {REQUIRED_CHECK}")
            print()
            print("   This means Review-Hash validation can be bypassed!")
            print()
            print("   Action Required:")
            print(f"   1. Edit branch protection rule for '{BRANCH}'")
            print(f"   2. Search for and select '{REQUIRED_CHECK}'")
            print("   3. Save changes")
            print()
            print("   Note: The check must run at least once before it appears in the list.")
            print("   If this is a new repository, merge one PR first, then add the protection.")
            print()
            print("   See docs/STANDARDS/BRANCH_PROTECTION.md for instructions")
            return 1

        # Success - everything is configured correctly
        print("✅ BRANCH PROTECTION CORRECTLY CONFIGURED")
        print()
        print(f"   Branch: {BRANCH}")
        print(f"   Required checks: {len(required_checks)}")
        print(f"   Critical check present: ✅ {REQUIRED_CHECK}")
        print()
        print("   Branch protection settings:")
        print("     - Require status checks: ✅ Enabled")
        if required_status_checks.get("strict"):
            print("     - Require up-to-date branches: ✅ Enabled")
        else:
            print("     - Require up-to-date branches: ⚠️  Not enabled (recommended)")

        # Show if admins are included
        if protection.get("enforce_admins", {}).get("enabled"):
            print("     - Include administrators: ✅ Enabled")

        print()
        print("   Review-Hash validation is enforced. Bypass attempts will be blocked.")
        return 0

    except FileNotFoundError:
        print("❌ GitHub CLI not found")
        print()
        print("   The 'gh' command is not installed or not in PATH.")
        print()
        print("   Installation:")
        print("   - macOS: brew install gh")
        print("   - Linux: See https://github.com/cli/cli#installation")
        print()
        print("   After installing, authenticate with: gh auth login")
        return 2
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        print()
        print("   An unexpected error occurred while checking branch protection.")
        print("   This may indicate a bug in the verification script.")
        print()
        print(f"   Error details: {type(e).__name__}: {e}")
        return 2


def main() -> int:
    """Main entry point."""
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("Branch Protection Verification")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print()

    return check_branch_protection()


if __name__ == "__main__":
    sys.exit(main())
