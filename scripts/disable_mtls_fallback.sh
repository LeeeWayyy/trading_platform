#!/usr/bin/env bash
# Emergency mTLS Fallback Disablement Script
#
# Component 6 of P2T3 Phase 3 (OAuth2/OIDC Authentication).
#
# PURPOSE:
# Idempotently toggle ENABLE_MTLS_FALLBACK in .env file during emergencies.
# Use when mTLS fallback mode needs to be disabled due to:
# - Security incident (compromised client certificate)
# - Excessive authentication failures
# - Auth0 IdP recovery (normal OAuth2 restored)
#
# USAGE:
#   ./scripts/disable_mtls_fallback.sh [--dry-run] [--enable]
#
# OPTIONS:
#   --dry-run   Show what would be changed without modifying .env
#   --enable    Re-enable mTLS fallback (use after incident resolution)
#
# SAFETY:
# - Idempotent (safe to run multiple times)
# - Creates backup before modification (.env.backup.<timestamp>)
# - Audit logging to syslog + stdout
# - Dry-run mode for testing
#
# REFERENCES:
# - docs/TASKS/P2T3-Phase3_Component6-7_Plan.md
# - docs/RUNBOOKS/auth0-idp-outage.md
#
# EXIT CODES:
#   0 - Success (fallback disabled/enabled)
#   1 - .env file not found
#   2 - Invalid arguments
#   3 - Backup creation failed
#   4 - .env modification failed

set -euo pipefail

# --- Configuration ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$PROJECT_ROOT/.env"
BACKUP_DIR="$PROJECT_ROOT/.env.backups"

# --- Colors for output ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# --- Parse arguments ---
DRY_RUN=false
ENABLE_MODE=false

for arg in "$@"; do
    case $arg in
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --enable)
            ENABLE_MODE=true
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [--dry-run] [--enable]"
            echo ""
            echo "Options:"
            echo "  --dry-run   Show what would be changed without modifying .env"
            echo "  --enable    Re-enable mTLS fallback (default: disable)"
            echo "  -h, --help  Show this help message"
            echo ""
            echo "Examples:"
            echo "  $0                  # Disable mTLS fallback"
            echo "  $0 --enable         # Re-enable mTLS fallback"
            echo "  $0 --dry-run        # Preview changes without applying"
            exit 0
            ;;
        *)
            echo -e "${RED}ERROR: Unknown argument: $arg${NC}" >&2
            echo "Use --help for usage information" >&2
            exit 2
            ;;
    esac
done

# --- Helper Functions ---
log_info() {
    echo -e "${BLUE}[INFO]${NC} $*"
    # Log to syslog if available
    if command -v logger &>/dev/null; then
        logger -t "mtls_fallback_toggle" "[INFO] $*"
    fi
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $*"
    if command -v logger &>/dev/null; then
        logger -t "mtls_fallback_toggle" "[SUCCESS] $*"
    fi
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $*" >&2
    if command -v logger &>/dev/null; then
        logger -t "mtls_fallback_toggle" "[WARN] $*"
    fi
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $*" >&2
    if command -v logger &>/dev/null; then
        logger -t "mtls_fallback_toggle" "[ERROR] $*"
    fi
}

audit_log() {
    local action=$1
    local timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    local user="${SUDO_USER:-$USER}"
    local hostname=$(hostname)

    local message="AUDIT: mTLS Fallback Toggle | Action: $action | User: $user | Host: $hostname | Timestamp: $timestamp"

    echo -e "${BLUE}[AUDIT]${NC} $message"

    # Log to syslog with high priority
    if command -v logger &>/dev/null; then
        logger -p user.warning -t "mtls_fallback_audit" "$message"
    fi

    # Append to audit log file
    local audit_file="$PROJECT_ROOT/logs/mtls_fallback_audit.log"
    if [ -d "$(dirname "$audit_file")" ]; then
        echo "$message" >> "$audit_file"
    fi
}

create_backup() {
    local src_file=$1

    # Create backup directory if it doesn't exist
    mkdir -p "$BACKUP_DIR"

    local timestamp=$(date -u +"%Y%m%d_%H%M%S")
    local backup_file="$BACKUP_DIR/.env.backup.$timestamp"

    if [ "$DRY_RUN" = true ]; then
        log_info "DRY-RUN: Would create backup at $backup_file"
        return 0
    fi

    if cp "$src_file" "$backup_file"; then
        log_success "Backup created: $backup_file"

        # Keep only last 10 backups
        local backup_count=$(ls -1 "$BACKUP_DIR/.env.backup."* 2>/dev/null | wc -l)
        if [ "$backup_count" -gt 10 ]; then
            log_info "Cleaning old backups (keeping last 10)..."
            ls -1t "$BACKUP_DIR/.env.backup."* | tail -n +11 | xargs rm -f
        fi

        return 0
    else
        log_error "Failed to create backup"
        return 3
    fi
}

toggle_fallback() {
    local enable=$1
    local env_file=$2

    local target_value
    local action_desc

    if [ "$enable" = true ]; then
        target_value="true"
        action_desc="ENABLE"
    else
        target_value="false"
        action_desc="DISABLE"
    fi

    # Check current state
    local current_value
    if grep -q "^ENABLE_MTLS_FALLBACK=" "$env_file"; then
        current_value=$(grep "^ENABLE_MTLS_FALLBACK=" "$env_file" | cut -d'=' -f2)
    else
        current_value="not set"
    fi

    log_info "Current ENABLE_MTLS_FALLBACK: $current_value"
    log_info "Target ENABLE_MTLS_FALLBACK: $target_value"

    # Check if already in desired state (idempotency)
    if [ "$current_value" = "$target_value" ]; then
        log_success "mTLS fallback already ${action_desc}D (no change needed)"
        audit_log "NO_CHANGE_${action_desc} (already $target_value)"
        return 0
    fi

    # Create backup before modification
    create_backup "$env_file" || return $?

    # Toggle the value
    if [ "$DRY_RUN" = true ]; then
        log_info "DRY-RUN: Would set ENABLE_MTLS_FALLBACK=$target_value in $env_file"
        audit_log "DRY_RUN_${action_desc}"
        return 0
    fi

    # Perform the modification
    if grep -q "^ENABLE_MTLS_FALLBACK=" "$env_file"; then
        # Update existing line
        if sed -i.tmp "s/^ENABLE_MTLS_FALLBACK=.*/ENABLE_MTLS_FALLBACK=$target_value/" "$env_file"; then
            rm -f "${env_file}.tmp"
            log_success "Updated ENABLE_MTLS_FALLBACK=$target_value in $env_file"
        else
            log_error "sed command failed"
            return 4
        fi
    else
        # Add new line (should not happen if .env.example is correct)
        echo "ENABLE_MTLS_FALLBACK=$target_value" >> "$env_file"
        log_success "Added ENABLE_MTLS_FALLBACK=$target_value to $env_file"
    fi

    # Verify the change
    local new_value=$(grep "^ENABLE_MTLS_FALLBACK=" "$env_file" | cut -d'=' -f2)
    if [ "$new_value" = "$target_value" ]; then
        log_success "mTLS fallback ${action_desc}D successfully"
        audit_log "${action_desc} (changed from $current_value to $target_value)"
    else
        log_error "Verification failed: expected $target_value, got $new_value"
        return 4
    fi

    # Display next steps
    echo ""
    echo -e "${YELLOW}NEXT STEPS:${NC}"
    if [ "$enable" = false ]; then
        echo "  1. Restart web_console service to apply changes"
        echo "  2. Verify normal OAuth2/OIDC authentication works"
        echo "  3. Monitor logs for authentication errors"
        echo "  4. See: docs/RUNBOOKS/auth0-idp-outage.md"
    else
        echo "  1. Verify Auth0 IdP is still unavailable (or test scenario)"
        echo "  2. Restart web_console service to apply changes"
        echo "  3. Test admin certificate authentication"
        echo "  4. Monitor IdP health checks for recovery"
        echo "  5. See: docs/RUNBOOKS/auth0-idp-outage.md"
    fi
    echo ""
}

# --- Main Execution ---
main() {
    local action_desc="DISABLE"
    if [ "$ENABLE_MODE" = true ]; then
        action_desc="ENABLE"
    fi

    echo -e "${BLUE}═══════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  Emergency mTLS Fallback Toggle Script${NC}"
    echo -e "${BLUE}  Action: ${action_desc} mTLS Fallback${NC}"
    if [ "$DRY_RUN" = true ]; then
        echo -e "${YELLOW}  Mode: DRY-RUN (no changes will be made)${NC}"
    fi
    echo -e "${BLUE}═══════════════════════════════════════════════════════${NC}"
    echo ""

    # Check .env file exists
    if [ ! -f "$ENV_FILE" ]; then
        log_error ".env file not found: $ENV_FILE"
        log_info "Create .env from .env.example first: cp .env.example .env"
        return 1
    fi

    log_info "Using .env file: $ENV_FILE"

    # Toggle fallback setting
    toggle_fallback "$ENABLE_MODE" "$ENV_FILE"

    local exit_code=$?

    echo ""
    if [ $exit_code -eq 0 ]; then
        if [ "$DRY_RUN" = false ]; then
            log_success "Operation completed successfully"
        else
            log_info "DRY-RUN completed (no changes made)"
        fi
    else
        log_error "Operation failed with exit code $exit_code"
    fi

    return $exit_code
}

# Run main function
main
exit $?
