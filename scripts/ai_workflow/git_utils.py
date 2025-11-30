"""
Git and GitHub utilities with owner/repo auto-detection.

Addresses review feedback:
- C1: Missing owner/repo resolution
- C3: gh_api error handling documented
- C4: Owner/repo format validation added
"""

import subprocess
import re
from typing import Optional, Tuple


# Valid GitHub owner/repo pattern (alphanumeric, hyphens, underscores, dots)
GITHUB_NAME_PATTERN = re.compile(r'^[\w.-]+$')


def _validate_github_name(name: str, field: str) -> None:
    """
    Validate GitHub owner or repo name format.

    Raises:
        ValueError if name contains invalid characters
    """
    if not name:
        raise ValueError(f"Empty {field} name")
    if not GITHUB_NAME_PATTERN.match(name):
        raise ValueError(
            f"Invalid {field} format: '{name}'. "
            f"Must contain only alphanumeric, hyphens, underscores, or dots."
        )
    if name.startswith('.') or name.endswith('.'):
        raise ValueError(f"Invalid {field}: cannot start or end with dot")
    if '..' in name:
        raise ValueError(f"Invalid {field}: cannot contain consecutive dots")


def get_owner_repo(repo_path: str = None) -> Tuple[str, str]:
    """
    Auto-detect owner and repo from git remote.

    Parses formats:
    - git@github.com:owner/repo.git
    - https://github.com/owner/repo.git
    - https://github.com/owner/repo

    Args:
        repo_path: Optional path to git repo (defaults to current directory).
                   Addresses Claude review C5: Removed lru_cache to support
                   multi-repo scenarios. Each call re-detects based on path.

    Returns:
        (owner, repo) tuple

    Raises:
        ValueError if cannot determine owner/repo or format is invalid
    """
    cmd = ["git", "remote", "get-url", "origin"]
    if repo_path:
        cmd = ["git", "-C", repo_path] + cmd[1:]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise ValueError(f"Cannot get git remote: {result.stderr}")

    url = result.stdout.strip()
    owner, repo = _parse_github_url(url)

    # Validate extracted values (C4 fix)
    _validate_github_name(owner, "owner")
    _validate_github_name(repo, "repo")

    return owner, repo


def _parse_github_url(url: str) -> Tuple[str, str]:
    """
    Parse owner/repo from GitHub URL.

    Supports:
    - git@github.com:owner/repo.git
    - https://github.com/owner/repo.git
    - https://github.com/owner/repo

    Returns:
        (owner, repo) tuple

    Raises:
        ValueError if URL format not recognized
    """
    # SSH format: git@github.com:owner/repo.git
    ssh_match = re.match(r'git@github\.com:([^/]+)/(.+?)(?:\.git)?$', url)
    if ssh_match:
        return ssh_match.group(1), ssh_match.group(2)

    # HTTPS format: https://github.com/owner/repo.git or .../repo
    https_match = re.match(r'https://github\.com/([^/]+)/(.+?)(?:\.git)?$', url)
    if https_match:
        return https_match.group(1), https_match.group(2)

    raise ValueError(f"Cannot parse GitHub URL: {url}")


def gh_api(
    endpoint: str,
    paginate: bool = False,
    jq: str = None,
    repo_path: str = None
) -> subprocess.CompletedProcess:
    """
    Call GitHub API via gh CLI with owner/repo auto-substitution.

    IMPORTANT: Callers MUST check result.returncode != 0 for errors.
    This function does NOT raise exceptions on API errors.

    Args:
        endpoint: API endpoint with optional {owner}/{repo} placeholders
        paginate: Enable pagination for large responses
        jq: Optional jq filter for response
        repo_path: Optional repo path for multi-repo support

    Returns:
        CompletedProcess with stdout/stderr
    """
    # Auto-substitute owner/repo if placeholders present
    # Addresses C3: Always validate owner/repo before substitution
    if "{owner}" in endpoint or "{repo}" in endpoint:
        try:
            owner, repo = get_owner_repo(repo_path)
            # Double-check validation even after get_owner_repo (defense in depth)
            _validate_github_name(owner, "owner")
            _validate_github_name(repo, "repo")
            endpoint = endpoint.replace("{owner}", owner).replace("{repo}", repo)
        except ValueError as e:
            # Return failed result instead of raising
            return subprocess.CompletedProcess(
                args=["gh", "api"], returncode=1,
                stdout="", stderr=str(e)
            )

    # Build command with optional cwd for multi-repo support
    cmd = ["gh", "api", endpoint]
    if paginate:
        cmd.append("--paginate")
    if jq:
        cmd.extend(["--jq", jq])

    return subprocess.run(cmd, capture_output=True, text=True, timeout=120)


# NOTE: gh_api_or_raise removed per Gemini LOW review - was dead code (never used).
# If needed in future, wrap gh_api() with exception raising at call site.


def gh_graphql(query: str) -> subprocess.CompletedProcess:
    """
    Execute GraphQL query with owner/repo substitution.

    IMPORTANT: Callers MUST check result.returncode != 0 for errors.

    Args:
        query: GraphQL query with optional $owner/$repo variables

    Returns:
        CompletedProcess with stdout/stderr
    """
    try:
        owner, repo = get_owner_repo()
    except ValueError as e:
        return subprocess.CompletedProcess(
            args=["gh", "api", "graphql"],
            returncode=1, stdout="", stderr=str(e)
        )

    cmd = [
        "gh", "api", "graphql",
        "-f", f"owner={owner}",
        "-f", f"repo={repo}",
        "-f", f"query={query}"
    ]

    return subprocess.run(cmd, capture_output=True, text=True, timeout=60)
