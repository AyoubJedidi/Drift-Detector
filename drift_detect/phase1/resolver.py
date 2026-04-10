"""
Phase 1 - Step 1: Repository Resolver

Accepts either:
  - A local filesystem path  (e.g. ./my-manifests or /home/user/repo)
  - A remote Git URL         (e.g. https://github.com/org/repo)

Returns a local directory path in both cases.
For remote repos, clones into a temp dir and checks out the requested branch/tag.
"""

import shutil
import tempfile
from pathlib import Path

import git  # GitPython


# URL prefixes that identify a remote repository
_REMOTE_PREFIXES = ("https://", "http://", "git@", "ssh://")


def is_remote(source: str) -> bool:
    """Return True if the source looks like a remote Git URL."""
    return source.startswith(_REMOTE_PREFIXES)


def resolve_repo(
    source: str,
    branch: str | None = None,
    tag: str | None = None,
) -> Path:
    """
    Resolve a source (local path or remote URL) to a local directory.

    Args:
        source: Local path or remote Git URL provided by the user.
        branch: Optional branch name to checkout (remote repos only).
        tag:    Optional tag name to checkout (remote repos only).

    Returns:
        Path to a local directory containing the manifests.

    Raises:
        ValueError:   Bad input (both branch and tag given, local path doesn't exist, etc.)
        RuntimeError: Git operation failed (clone error, bad branch/tag, etc.)
    """
    if branch and tag:
        raise ValueError("Specify --branch or --tag, not both.")

    if is_remote(source):
        return _clone_remote(source, branch=branch, tag=tag)
    else:
        return _resolve_local(source, branch=branch, tag=tag)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_local(source: str, branch: str | None, tag: str | None) -> Path:
    """Validate and return a local path. branch/tag are not allowed for plain dirs."""
    path = Path(source).expanduser().resolve()

    if not path.exists():
        raise ValueError(
            f"Local path does not exist: {path}\n"
            "Tip: provide a valid directory path, or a remote URL starting with https://"
        )

    if not path.is_dir():
        raise ValueError(f"Path is not a directory: {path}")

    # branch/tag only make sense for Git repos, warn but don't hard-fail
    if branch or tag:
        # If there's a .git folder, check it out; otherwise raise
        git_dir = path / ".git"
        if git_dir.exists():
            ref = branch or tag
            try:
                repo = git.Repo(path)
                repo.git.checkout(ref)
            except git.GitCommandError as exc:
                raise RuntimeError(
                    f"Failed to checkout '{ref}' in local repo {path}: {exc}"
                ) from exc
        else:
            raise ValueError(
                f"--branch/--tag specified but '{path}' is not a Git repository."
            )

    return path


def _clone_remote(url: str, branch: str | None, tag: str | None) -> Path:
    """Clone a remote repo into a temp directory and return its path."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="drift-detect-"))

    ref = branch or tag  # which ref to clone; None means default branch

    try:
        print(f"Cloning {url} …")
        clone_kwargs: dict = {"to_path": str(tmp_dir), "depth": 1}

        if ref:
            clone_kwargs["branch"] = ref

        git.Repo.clone_from(url, **clone_kwargs)
        print(f"Cloned to temporary directory: {tmp_dir}")
        return tmp_dir

    except Exception as exc:

        # Clean up temp dir on failure so we don't litter
        shutil.rmtree(tmp_dir, ignore_errors=True)
        ref_hint = f" (ref: {ref})" if ref else ""
        raise RuntimeError(
            f"Failed to clone {url}{ref_hint}:\n{exc}"
        ) from exc