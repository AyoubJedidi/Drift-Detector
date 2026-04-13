"""
Phase 3 - Step 1: Diff Engine

Takes two normalized Kubernetes resource dicts (git vs live) and finds
exactly what changed between them using deepdiff.

Output is a list of DriftItem objects — one per changed field.
Each DriftItem has the field path, old value, new value, and severity placeholder
(severity is filled in by Step 2 - classifier.py).
"""

from dataclasses import dataclass, field
from typing import List, Optional, Any
from deepdiff import DeepDiff


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class DriftItem:
    """
    Represents a single field-level difference between Git and live state.

    field_path:  Human-readable path e.g. "spec.replicas"
    git_value:   What Git declares
    live_value:  What the cluster has
    change_type: "changed" | "added" | "removed"
    severity:    Filled in by classifier.py — "critical" | "warning" | "info"
    """
    field_path:  str
    git_value:   Any
    live_value:  Any
    change_type: str
    severity:    str = "info"  # default, overridden by classifier


@dataclass
class DriftResult:
    """
    Full drift result for a single Kubernetes resource.

    status:
      - "in_sync"              → git and live are identical
      - "drifted"              → differences found
      - "missing_from_cluster" → exists in Git, not in cluster
      - "missing_from_git"     → exists in cluster, not in Git
    """
    kind:       str
    name:       str
    namespace:  str
    status:     str
    drifts:     List[DriftItem] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def diff_resource(
    git_object:  dict,
    live_object: dict,
) -> DriftResult:
    """
    Compare a normalized Git object against a normalized live object.

    Args:
        git_object:  Normalized dict from Phase 1 + Phase 2 normalizer
        live_object: Normalized dict from Phase 2 fetcher + normalizer

    Returns:
        DriftResult with a list of DriftItems (empty if in sync)
    """
    kind      = git_object.get("kind", "Unknown")
    name      = git_object.get("metadata", {}).get("name", "unknown")
    namespace = git_object.get("metadata", {}).get("namespace", "default")

    diff = DeepDiff(
        git_object,
        live_object,
        ignore_order=True,          # list order changes are not drift
        report_repetition=False,
        verbose_level=2,
    )

    if not diff:
        return DriftResult(kind=kind, name=name, namespace=namespace, status="in_sync")

    drift_items = _parse_diff(diff)

    return DriftResult(
        kind=kind,
        name=name,
        namespace=namespace,
        status="drifted",
        drifts=drift_items,
    )


def diff_missing_from_cluster(git_object: dict) -> DriftResult:
    """Build a DriftResult for a resource present in Git but absent from cluster."""
    return DriftResult(
        kind=git_object.get("kind", "Unknown"),
        name=git_object.get("metadata", {}).get("name", "unknown"),
        namespace=git_object.get("metadata", {}).get("namespace", "default"),
        status="missing_from_cluster",
    )


def diff_missing_from_git(live_object: dict) -> DriftResult:
    """Build a DriftResult for a resource running in cluster but absent from Git."""
    return DriftResult(
        kind=live_object.get("kind", "Unknown"),
        name=live_object.get("metadata", {}).get("name", "unknown"),
        namespace=live_object.get("metadata", {}).get("namespace", "default"),
        status="missing_from_git",
    )


# ---------------------------------------------------------------------------
# DeepDiff output parser
# ---------------------------------------------------------------------------

def _parse_diff(diff: DeepDiff) -> List[DriftItem]:
    """
    Convert raw DeepDiff output into a flat list of DriftItems.

    DeepDiff groups changes into categories:
      - values_changed    → field exists in both, value differs
      - dictionary_item_added   → field exists in live but not in git
      - dictionary_item_removed → field exists in git but not in live
      - iterable_item_added     → item added to a list
      - iterable_item_removed   → item removed from a list
      - type_changes      → same field, different type
    """
    items = []

    # Field value changed
    for path, change in diff.get("values_changed", {}).items():
        items.append(DriftItem(
            field_path=_clean_path(path),
            git_value=change["old_value"],
            live_value=change["new_value"],
            change_type="changed",
        ))

    # Field added in live (not in git)
    for path, value in diff.get("dictionary_item_added", {}).items():
        items.append(DriftItem(
            field_path=_clean_path(path),
            git_value=None,
            live_value=value,
            change_type="added",
        ))

    # Field removed from live (in git but not live)
    for path, value in diff.get("dictionary_item_removed", {}).items():
        items.append(DriftItem(
            field_path=_clean_path(path),
            git_value=value,
            live_value=None,
            change_type="removed",
        ))

    # Item added to a list
    for path, value in diff.get("iterable_item_added", {}).items():
        items.append(DriftItem(
            field_path=_clean_path(path),
            git_value=None,
            live_value=value,
            change_type="added",
        ))

    # Item removed from a list
    for path, value in diff.get("iterable_item_removed", {}).items():
        items.append(DriftItem(
            field_path=_clean_path(path),
            git_value=value,
            live_value=None,
            change_type="removed",
        ))

    # Type changed (e.g. string → int)
    for path, change in diff.get("type_changes", {}).items():
        items.append(DriftItem(
            field_path=_clean_path(path),
            git_value=change["old_value"],
            live_value=change["new_value"],
            change_type="changed",
        ))

    return items


def _clean_path(raw_path: str) -> str:
    """
    Convert deepdiff path format to a readable dot-notation path.

    deepdiff uses:  root['spec']['replicas']
    We want:        spec.replicas

    deepdiff uses:  root['spec']['template']['containers'][0]['image']
    We want:        spec.template.containers[0].image
    """
    path = raw_path

    # Remove root prefix
    path = path.replace("root['", "").replace("root[", "")

    # Replace ']['  with .
    path = path.replace("']['", ".")

    # Replace remaining [' and '] for dict keys
    path = path.replace("['", ".").replace("']", "")

    # Clean up any leading dots
    path = path.lstrip(".")

    return path