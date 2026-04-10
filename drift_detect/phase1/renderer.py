"""
Phase 1 - Step 3: Renderer

Given a local directory and a source type, produces a flat list of
Kubernetes resource dicts — regardless of whether the source is:
  - raw YAML files
  - a Helm chart (rendered via `helm template`)
  - a Kustomize overlay (rendered via `kustomize build`)

Output is always: List[dict], each dict is one Kubernetes resource.
"""

import subprocess
from pathlib import Path
from typing import List, Optional

import yaml

from drift_detect.phase1.detector import HELM, KUSTOMIZE, RAW


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def render(
    directory: Path,
    source_type: str,
    helm_values: Optional[List[Path]] = None,
) -> List[dict]:
    """
    Render a directory into a list of Kubernetes resource dicts.

    Args:
        directory:   Resolved local path from resolver.py
        source_type: One of "helm", "kustomize", "raw" from detector.py
        helm_values: Optional list of values files for Helm (--values)

    Returns:
        List of dicts, each representing one Kubernetes resource.

    Raises:
        RuntimeError: If helm/kustomize subprocess fails.
        ValueError:   If source_type is unrecognized.
    """
    if source_type == HELM:
        raw_yaml = _render_helm(directory, helm_values or [])
    elif source_type == KUSTOMIZE:
        raw_yaml = _render_kustomize(directory)
    elif source_type == RAW:
        raw_yaml = _render_raw(directory)
    else:
        raise ValueError(f"Unknown source type: {source_type}")

    return _parse_yaml_string(raw_yaml)


# ---------------------------------------------------------------------------
# Renderers per source type
# ---------------------------------------------------------------------------

def _render_helm(directory: Path, values_files: List[Path]) -> str:
    """Run `helm template` and return the raw YAML string output."""
    _check_binary("helm")

    cmd = ["helm", "template", "drift-release", str(directory)]
    for vf in values_files:
        cmd += ["--values", str(vf)]

    return _run_subprocess(cmd, cwd=directory, tool="helm")


def _render_kustomize(directory: Path) -> str:
    """Run `kustomize build` and return the raw YAML string output."""
    _check_binary("kustomize")

    cmd = ["kustomize", "build", str(directory)]
    return _run_subprocess(cmd, cwd=directory, tool="kustomize")


def _render_raw(directory: Path) -> str:
    """
    Recursively find all YAML files and concatenate their contents.
    Skips files that are not valid YAML or don't look like k8s resources.
    """
    yaml_files = sorted(
        p for p in directory.rglob("*")
        if p.suffix in (".yaml", ".yml") and p.is_file()
    )

    if not yaml_files:
        return ""

    chunks = []
    for f in yaml_files:
        try:
            content = f.read_text(encoding="utf-8")
            chunks.append(content)
        except OSError as e:
            # Skip unreadable files, don't crash the whole scan
            print(f"Warning: could not read {f}: {e}")

    # Join with document separator so multi-file YAML parses cleanly
    return "\n---\n".join(chunks)


# ---------------------------------------------------------------------------
# YAML parsing
# ---------------------------------------------------------------------------

def _parse_yaml_string(raw: str) -> List[dict]:
    """
    Parse a (potentially multi-document) YAML string into a list of dicts.

    Skips documents that are:
      - empty / None
      - not a dict (not a valid k8s resource)
      - missing 'kind' or 'apiVersion' (not a k8s resource)

    Malformed documents are warned about and skipped — never crash.
    """
    if not raw.strip():
        return []

    results = []
    for i, doc in enumerate(yaml.safe_load_all(raw)):
        if doc is None:
            continue
        if not isinstance(doc, dict):
            print(f"Warning: document {i} is not a YAML mapping, skipping.")
            continue
        if "kind" not in doc or "apiVersion" not in doc:
            name = doc.get("metadata", {}).get("name", f"document {i}")
            print(f"Warning: '{name}' is missing 'kind' or 'apiVersion', skipping.")
            continue
        results.append(doc)

    return results


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------

def _run_subprocess(cmd: List[str], cwd: Path, tool: str) -> str:
    """Run a command, return stdout. Raise RuntimeError on failure."""
    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=60,
        )
    except FileNotFoundError:
        raise RuntimeError(
            f"'{tool}' binary not found. Please install it and ensure it's in your PATH."
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"'{tool}' timed out after 60 seconds.")

    if result.returncode != 0:
        raise RuntimeError(
            f"'{tool}' failed with exit code {result.returncode}:\n{result.stderr.strip()}"
        )

    return result.stdout


def _check_binary(name: str) -> None:
    """Raise RuntimeError early if a required binary is missing."""
    import shutil
    if shutil.which(name) is None:
        raise RuntimeError(
            f"Required binary '{name}' not found in PATH. "
            f"Please install it before using --source-type {name}."
        )