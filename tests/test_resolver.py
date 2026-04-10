"""
Tests for Phase 1 - resolver.py

Run with: pytest tests/test_resolver.py -v
"""

import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from drift_detect.phase1.resolver import is_remote, resolve_repo


# ---------------------------------------------------------------------------
# is_remote() tests
# ---------------------------------------------------------------------------

class TestIsRemote:
    def test_https_url_is_remote(self):
        assert is_remote("https://github.com/org/repo") is True

    def test_http_url_is_remote(self):
        assert is_remote("http://github.com/org/repo") is True

    def test_git_at_url_is_remote(self):
        assert is_remote("git@github.com:org/repo.git") is True

    def test_ssh_url_is_remote(self):
        assert is_remote("ssh://git@github.com/org/repo") is True

    def test_local_relative_path_is_not_remote(self):
        assert is_remote("./my-manifests") is False

    def test_local_absolute_path_is_not_remote(self):
        assert is_remote("/home/user/repo") is False

    def test_bare_name_is_not_remote(self):
        assert is_remote("my-repo") is False


# ---------------------------------------------------------------------------
# resolve_repo() - local path tests
# ---------------------------------------------------------------------------

class TestResolveLocal:
    def test_valid_local_dir_is_returned(self, tmp_path):
        result = resolve_repo(str(tmp_path))
        assert result == tmp_path.resolve()

    def test_nonexistent_path_raises_value_error(self):
        with pytest.raises(ValueError, match="does not exist"):
            resolve_repo("/this/path/does/not/exist/ever")

    def test_file_path_raises_value_error(self, tmp_path):
        f = tmp_path / "file.yaml"
        f.write_text("hello")
        with pytest.raises(ValueError, match="not a directory"):
            resolve_repo(str(f))

    def test_branch_on_non_git_dir_raises_value_error(self, tmp_path):
        with pytest.raises(ValueError, match="not a Git repository"):
            resolve_repo(str(tmp_path), branch="main")

    def test_branch_and_tag_together_raises_value_error(self, tmp_path):
        with pytest.raises(ValueError, match="not both"):
            resolve_repo(str(tmp_path), branch="main", tag="v1.0")


# ---------------------------------------------------------------------------
# resolve_repo() - remote URL tests (mocked — no real network calls)
# ---------------------------------------------------------------------------

class TestResolveRemote:
    @patch("drift_detect.phase1.resolver.git.Repo.clone_from")
    def test_remote_url_clones_and_returns_path(self, mock_clone):
        mock_clone.return_value = MagicMock()
        result = resolve_repo("https://github.com/org/repo")
        assert mock_clone.called
        # Result should be a Path inside the system's temp directory
        assert str(result).startswith(tempfile.gettempdir())

    @patch("drift_detect.phase1.resolver.git.Repo.clone_from")
    def test_remote_with_branch_passes_branch_to_clone(self, mock_clone):
        mock_clone.return_value = MagicMock()
        resolve_repo("https://github.com/org/repo", branch="develop")
        _, kwargs = mock_clone.call_args
        assert kwargs.get("branch") == "develop"

    @patch("drift_detect.phase1.resolver.git.Repo.clone_from")
    def test_remote_with_tag_passes_tag_to_clone(self, mock_clone):
        mock_clone.return_value = MagicMock()
        resolve_repo("https://github.com/org/repo", tag="v1.2.3")
        _, kwargs = mock_clone.call_args
        assert kwargs.get("branch") == "v1.2.3"

    @patch("drift_detect.phase1.resolver.git.Repo.clone_from", side_effect=Exception("network error"))
    def test_clone_failure_raises_runtime_error(self, mock_clone):
        with pytest.raises(RuntimeError, match="Failed to clone"):
            resolve_repo("https://github.com/org/repo")