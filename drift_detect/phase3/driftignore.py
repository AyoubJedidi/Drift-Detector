"""
Phase 3 - DriftIgnore

Parses a .driftignore file and filters DriftItems and DriftResults
based on user-defined ignore rules.

.driftignore format (YAML):

  ignore_fields:
    - metadata.annotations.argocd.argoproj.io/*
    - spec.template.metadata.annotations

  ignore_resources:
    - kind: Job
      name: db-migration
      namespace: default

  ignore_labels:
    - operator.io/*

  ignore_annotations:
    - argocd.argoproj.io/*

Wildcard * matches any suffix in a path segment.
"""

import fnmatch
from pathlib import Path
from typing import List, Optional

import yaml

from drift_detect.phase3.differ import DriftItem, DriftResult


# ---------------------------------------------------------------------------
# Data structure
# ---------------------------------------------------------------------------

class DriftIgnoreRules:
    """Parsed rules from a .driftignore file."""

    def __init__(
        self,
        ignore_fields:      List[str] = None,
        ignore_resources:   List[dict] = None,
        ignore_labels:      List[str] = None,
        ignore_annotations: List[str] = None,
    ):
        self.ignore_fields      = ignore_fields      or []
        self.ignore_resources   = ignore_resources   or []
        self.ignore_labels      = ignore_labels      or []
        self.ignore_annotations = ignore_annotations or []

    def is_empty(self) -> bool:
        return not any([
            self.ignore_fields,
            self.ignore_resources,
            self.ignore_labels,
            self.ignore_annotations,
        ])


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def load_driftignore(path: Optional[Path] = None) -> DriftIgnoreRules:
    """
    Load and parse a .driftignore file.

    Args:
        path: Path to the .driftignore file.
              If None, looks for .driftignore in the current directory.
              If file doesn't exist, returns empty rules (no filtering).

    Returns:
        DriftIgnoreRules with parsed rules.
    """
    if path is None:
        path = Path(".driftignore")

    if not path.exists():
        return DriftIgnoreRules()

    try:
        content = path.read_text(encoding="utf-8")
        data = yaml.safe_load(content)
    except Exception as e:
        print(f"Warning: could not parse .driftignore: {e}")
        return DriftIgnoreRules()

    if not isinstance(data, dict):
        print("Warning: .driftignore must be a YAML mapping. Ignoring.")
        return DriftIgnoreRules()

    return DriftIgnoreRules(
        ignore_fields=data.get("ignore_fields", []),
        ignore_resources=data.get("ignore_resources", []),
        ignore_labels=data.get("ignore_labels", []),
        ignore_annotations=data.get("ignore_annotations", []),
    )


# ---------------------------------------------------------------------------
# Filter entry point
# ---------------------------------------------------------------------------

def apply_ignore_rules(
    results: List[DriftResult],
    rules:   DriftIgnoreRules,
) -> List[DriftResult]:
    """
    Filter drift results based on ignore rules.

    - Resources matching ignore_resources are removed entirely.
    - DriftItems matching ignore_fields/labels/annotations are removed.
    - Resources that had drift but all items were ignored become "in_sync".

    Args:
        results: List of DriftResult from differ + classifier
        rules:   Parsed DriftIgnoreRules

    Returns:
        Filtered list of DriftResult.
    """
    if rules.is_empty():
        return results

    filtered = []
    for result in results:
        # Check if entire resource is ignored
        if _is_resource_ignored(result, rules):
            continue

        # Filter individual drift items
        if result.status == "drifted":
            result.drifts = [
                item for item in result.drifts
                if not _is_field_ignored(item, rules)
            ]
            # If all drifts were ignored, mark as in_sync
            if not result.drifts:
                result.status = "in_sync"

        filtered.append(result)

    return filtered


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_resource_ignored(result: DriftResult, rules: DriftIgnoreRules) -> bool:
    """Return True if the entire resource matches an ignore_resources rule."""
    for rule in rules.ignore_resources:
        kind      = rule.get("kind", "*")
        name      = rule.get("name", "*")
        namespace = rule.get("namespace", "*")

        kind_match      = kind      == "*" or _wildcard_match(result.kind,      kind)
        name_match      = name      == "*" or _wildcard_match(result.name,      name)
        namespace_match = namespace == "*" or _wildcard_match(result.namespace, namespace)

        if kind_match and name_match and namespace_match:
            return True

    return False


def _is_field_ignored(item: DriftItem, rules: DriftIgnoreRules) -> bool:
    """Return True if a drift item's field path matches any ignore rule."""
    # Check ignore_fields against full path
    for pattern in rules.ignore_fields:
        if _wildcard_match(item.field_path, pattern):
            return True

    # Check ignore_annotations — extract the annotation key after "annotations."
    if "annotations" in item.field_path:
        annotation_key = _extract_key_after(item.field_path, "annotations.")
        if annotation_key:
            for pattern in rules.ignore_annotations:
                if _wildcard_match(annotation_key, pattern):
                    return True

    # Check ignore_labels — extract the label key after "labels."
    if "labels" in item.field_path:
        label_key = _extract_key_after(item.field_path, "labels.")
        if label_key:
            for pattern in rules.ignore_labels:
                if _wildcard_match(label_key, pattern):
                    return True

    return False


def _extract_key_after(field_path: str, marker: str) -> Optional[str]:
    """
    Extract the key portion after a marker in a field path.

    e.g. "metadata.annotations.argocd.argoproj.io/sync-wave"
         with marker "annotations."
         returns "argocd.argoproj.io/sync-wave"
    """
    idx = field_path.find(marker)
    if idx == -1:
        return None
    return field_path[idx + len(marker):]


def _wildcard_match(value: str, pattern: str) -> bool:
    """
    Match a value against a pattern with wildcard support.

    Uses fnmatch which supports:
      * → matches anything
      ? → matches any single character

    Examples:
      argocd.argoproj.io/* matches argocd.argoproj.io/sync-wave
      metadata.annotations.* matches metadata.annotations.anything
    """
    return fnmatch.fnmatch(value, pattern)