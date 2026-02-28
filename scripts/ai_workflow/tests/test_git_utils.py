"""
Tests for git_utils.py module.

Tests GitHub URL parsing, owner/repo detection, and gh API helpers.
"""

from unittest.mock import MagicMock, patch

import pytest
from ai_workflow.git_utils import (
    _parse_github_url,
    _validate_github_name,
    get_owner_repo,
    gh_api,
    gh_graphql,
)


class TestValidateGithubName:
    """Tests for _validate_github_name function."""

    def test_valid_alphanumeric(self):
        """Should accept alphanumeric names."""
        _validate_github_name("owner123", "owner")
        _validate_github_name("repo456", "repo")

    def test_valid_with_hyphen(self):
        """Should accept names with hyphens."""
        _validate_github_name("my-owner", "owner")
        _validate_github_name("my-repo", "repo")

    def test_valid_with_underscore(self):
        """Should accept names with underscores."""
        _validate_github_name("my_owner", "owner")
        _validate_github_name("my_repo", "repo")

    def test_valid_with_dot(self):
        """Should accept names with dots in middle."""
        _validate_github_name("my.repo", "repo")

    def test_rejects_empty(self):
        """Should reject empty names."""
        with pytest.raises(ValueError, match="Empty"):
            _validate_github_name("", "owner")

    def test_rejects_leading_dot(self):
        """Should reject names starting with dot."""
        with pytest.raises(ValueError, match="cannot start or end with dot"):
            _validate_github_name(".hidden", "repo")

    def test_rejects_trailing_dot(self):
        """Should reject names ending with dot."""
        with pytest.raises(ValueError, match="cannot start or end with dot"):
            _validate_github_name("repo.", "repo")

    def test_rejects_consecutive_dots(self):
        """Should reject names with consecutive dots."""
        with pytest.raises(ValueError, match="consecutive dots"):
            _validate_github_name("my..repo", "repo")

    def test_rejects_invalid_chars(self):
        """Should reject names with invalid characters."""
        with pytest.raises(ValueError, match="Invalid"):
            _validate_github_name("owner/slash", "owner")

        with pytest.raises(ValueError, match="Invalid"):
            _validate_github_name("repo@symbol", "repo")


class TestParseGithubUrl:
    """Tests for _parse_github_url function."""

    def test_parse_ssh_url(self):
        """Should parse SSH format URLs."""
        owner, repo = _parse_github_url("git@github.com:testowner/testrepo.git")
        assert owner == "testowner"
        assert repo == "testrepo"

    def test_parse_ssh_url_without_git_suffix(self):
        """Should parse SSH URLs without .git suffix."""
        owner, repo = _parse_github_url("git@github.com:owner/repo")
        assert owner == "owner"
        assert repo == "repo"

    def test_parse_https_url(self):
        """Should parse HTTPS format URLs."""
        owner, repo = _parse_github_url("https://github.com/testowner/testrepo.git")
        assert owner == "testowner"
        assert repo == "testrepo"

    def test_parse_https_url_without_git_suffix(self):
        """Should parse HTTPS URLs without .git suffix."""
        owner, repo = _parse_github_url("https://github.com/owner/repo")
        assert owner == "owner"
        assert repo == "repo"

    def test_rejects_invalid_url(self):
        """Should reject unrecognized URL formats."""
        with pytest.raises(ValueError, match="Cannot parse"):
            _parse_github_url("invalid-url")

        with pytest.raises(ValueError, match="Cannot parse"):
            _parse_github_url("https://gitlab.com/owner/repo")


class TestGetOwnerRepo:
    """Tests for get_owner_repo function."""

    def test_parses_remote_url(self):
        """Should parse owner/repo from git remote."""
        mock_result = MagicMock(
            returncode=0, stdout="git@github.com:testowner/testrepo.git\n", stderr=""
        )

        with patch("subprocess.run", return_value=mock_result):
            owner, repo = get_owner_repo()

        assert owner == "testowner"
        assert repo == "testrepo"

    def test_handles_https_remote(self):
        """Should handle HTTPS remote URLs."""
        mock_result = MagicMock(
            returncode=0, stdout="https://github.com/myorg/myrepo.git\n", stderr=""
        )

        with patch("subprocess.run", return_value=mock_result):
            owner, repo = get_owner_repo()

        assert owner == "myorg"
        assert repo == "myrepo"

    def test_handles_missing_remote(self):
        """Should raise ValueError when no remote."""
        mock_result = MagicMock(returncode=1, stdout="", stderr="fatal: No remote configured")

        with patch("subprocess.run", return_value=mock_result):
            with pytest.raises(ValueError, match="Cannot get git remote"):
                get_owner_repo()

    def test_with_custom_repo_path(self):
        """Should use custom repo path."""
        mock_result = MagicMock(returncode=0, stdout="git@github.com:owner/repo.git\n", stderr="")

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            get_owner_repo(repo_path="/custom/path")

        # Check that -C was used with the path
        call_args = mock_run.call_args[0][0]
        assert "-C" in call_args
        assert "/custom/path" in call_args


class TestGhApi:
    """Tests for gh_api function."""

    def test_calls_gh_cli(self):
        """Should call gh CLI with endpoint."""
        mock_result = MagicMock(returncode=0, stdout='{"data": "test"}', stderr="")

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = gh_api("repos/owner/repo/pulls")

        assert result.returncode == 0
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert "gh" in call_args
        assert "api" in call_args
        assert "repos/owner/repo/pulls" in call_args

    def test_substitutes_owner_repo(self):
        """Should substitute {owner}/{repo} placeholders."""
        mock_git_result = MagicMock(returncode=0, stdout="git@github.com:testowner/testrepo.git\n")
        mock_api_result = MagicMock(returncode=0, stdout='{"data": "test"}', stderr="")

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [mock_git_result, mock_api_result]
            result = gh_api("repos/{owner}/{repo}/pulls")

        assert result.returncode == 0
        # Check the API call has substituted values
        api_call_args = mock_run.call_args_list[-1][0][0]
        assert "testowner" in " ".join(api_call_args)
        assert "testrepo" in " ".join(api_call_args)

    def test_handles_substitution_failure(self):
        """Should return error when substitution fails."""
        mock_result = MagicMock(returncode=1, stdout="", stderr="No remote")

        with patch("subprocess.run", return_value=mock_result):
            result = gh_api("repos/{owner}/{repo}/pulls")

        assert result.returncode == 1

    def test_pagination_flag(self):
        """Should add --paginate flag when requested."""
        mock_result = MagicMock(returncode=0, stdout="[]", stderr="")

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            gh_api("repos/owner/repo/issues", paginate=True)

        call_args = mock_run.call_args[0][0]
        assert "--paginate" in call_args

    def test_jq_filter(self):
        """Should add jq filter when provided."""
        mock_result = MagicMock(returncode=0, stdout="test", stderr="")

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            gh_api("repos/owner/repo/pulls", jq=".[] | .number")

        call_args = mock_run.call_args[0][0]
        assert "--jq" in call_args
        assert ".[] | .number" in call_args


class TestGhGraphql:
    """Tests for gh_graphql function."""

    def test_executes_graphql_query(self):
        """Should execute GraphQL query."""
        mock_git_result = MagicMock(returncode=0, stdout="git@github.com:owner/repo.git\n")
        mock_graphql_result = MagicMock(returncode=0, stdout='{"data": {}}', stderr="")

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [mock_git_result, mock_graphql_result]
            result = gh_graphql("query { viewer { login } }")

        assert result.returncode == 0
        graphql_call_args = mock_run.call_args_list[-1][0][0]
        assert "graphql" in graphql_call_args

    def test_passes_owner_repo_variables(self):
        """Should pass owner/repo as variables."""
        mock_git_result = MagicMock(returncode=0, stdout="git@github.com:testowner/testrepo.git\n")
        mock_graphql_result = MagicMock(returncode=0, stdout="{}", stderr="")

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [mock_git_result, mock_graphql_result]
            gh_graphql("query { repository(owner: $owner, name: $repo) { name } }")

        graphql_call = mock_run.call_args_list[-1]
        call_args = graphql_call[0][0]
        assert "owner=testowner" in " ".join(call_args)
        assert "repo=testrepo" in " ".join(call_args)

    def test_handles_get_owner_repo_failure(self):
        """Should return error when owner/repo detection fails."""
        mock_result = MagicMock(returncode=1, stdout="", stderr="No remote")

        with patch("subprocess.run", return_value=mock_result):
            result = gh_graphql("query { viewer { login } }")

        assert result.returncode == 1
