"""
Phase 1 - Step 2: Source Type Detector

Given a local directory, detects what kind of Kubernetes manifest source it contains:
  - helm       → Chart.yaml found (Helm chart)
  - kustomize  → kustomization.yaml / kustomization.yml found
  - raw        → plain YAML files, no special structure

The user can always override auto-detection with --source-type.
"""

from pathlib import Path


# Valid source types
HELM = "helm"
KUSTOMIZE = "kustomize"
RAW = "raw"

VALID_SOURCE_TYPES = (HELM, KUSTOMIZE, RAW)


def detect_source_type(directory: Path) -> str:
    """
    Auto-detect the manifest source type in a directory.

    Walks the directory tree looking for marker files:
      - Chart.yaml        → Helm
      - kustomization.yaml or kustomization.yml → Kustomize
      - anything else     → raw YAML

    Args:
        directory: Path to a local directory (already resolved by resolver.py)

    Returns:
        One of: "helm", "kustomize", "raw"

    Raises:
        ValueError: If the directory does not exist or is not a directory.
    """
    if not directory.exists():
        raise ValueError(f"Directory does not exist: {directory}")
    if not directory.is_dir():
        raise ValueError(f"Not a directory: {directory}")

    # Walk the full tree — Chart.yaml / kustomization.yaml can be in subdirs
    for path in directory.rglob("*"):
        if path.name in ("Chart.yaml", "Chart.yml"):
            return HELM
        if path.name in ("kustomization.yaml", "kustomization.yml"):
            return KUSTOMIZE

    return RAW


def validate_source_type(source_type: str) -> str:
    """
    Validate a user-supplied --source-type value.

    Args:
        source_type: Raw string from the CLI flag.

    Returns:
        Lowercased, validated source type string.

    Raises:
        ValueError: If the value is not one of the valid types.
    """
    normalized = source_type.strip().lower()
    if normalized not in VALID_SOURCE_TYPES:
        raise ValueError(
            f"Invalid source type '{source_type}'. "
            f"Must be one of: {', '.join(VALID_SOURCE_TYPES)}"
        )
    return normalized


def resolve_source_type(directory: Path, override: "str | None" = None) -> str:
    """
    Main entry point — returns source type, respecting user override.

    Args:
        directory: Resolved local directory from resolver.py
        override:  Optional --source-type value from the user

    Returns:
        One of: "helm", "kustomize", "raw"
    """
    if override:
        return validate_source_type(override)
    return detect_source_type(directory)