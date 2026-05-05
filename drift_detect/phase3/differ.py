"""
Phase 3 - Step 1: Diff Engine

Takes two normalized Kubernetes resource dicts (git vs live) and finds
exactly what changed between them using deepdiff.

Output is a list of DriftItem objects — one per changed LEAF field, with
one important exception: dicts added or removed as list items are treated
as atomic units (see _emit_added docstring for rationale).

Each DriftItem has the field path, old value, new value, and severity
placeholder (severity is filled in by Step 2 - classifier.py).

FIX HISTORY:
  - Original version emitted one DriftItem per DeepDiff entry, which meant
    heavily-defaulted nested structures (e.g. spec.template.spec) produced
    a single coarse-grained "changed" item instead of per-field items.
    That broke severity classification because rules like ".image -> critical"
    never matched against "spec.template.spec".
    Fix: _emit_change / _emit_added / _emit_removed now recursively walk
    dict and list values to produce leaf-level DriftItems.

  - Second iteration over-decomposed list items. Adding one env var
    `{name: DEBUG, value: true}` produced TWO critical drifts (one for
    `.name`, one for `.value`) when it's semantically ONE change.
    Fix: dicts at list-item path positions (path ends with `]`) are
    treated atomically — one DriftItem for the whole item.

SEMANTIC RULE:
  - Dicts inside lists  → atomic (e.g. adding an env var, container, port)
  - Scalars inside lists → individual (e.g. each arg in a command list)
  - Field changes within an existing list item → per-field (so a user
    tweaking `env[0].value` gets one precise drift, not a whole-item dump)
"""

from dataclasses import dataclass, field
from typing import List, Any
from deepdiff import DeepDiff


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class DriftItem:
    """
    Represents a single difference between Git and live state.

    field_path:  Dotted path e.g. "spec.template.spec.containers[0].image"
    git_value:   What Git declares (None if added in cluster)
    live_value:  What the cluster has (None if missing from cluster)
    change_type: "changed" | "added" | "removed"
    severity:    Filled in by classifier.py — "critical" | "warning" | "info"
    """
    field_path:  str
    git_value:   Any
    live_value:  Any
    change_type: str
    severity:    str = "info"


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
        ignore_order=True,          # list ordering changes are not drift
        report_repetition=False,
        verbose_level=2,
        # NOTE: cutoff_intersection_for_pairs defaults to 0.7. When structures
        # are less than 70% similar, DeepDiff reports at the parent level.
        # We compensate for that below with recursive decomposition in _emit_*.
    )

    if not diff:
        return DriftResult(kind=kind, name=name, namespace=namespace, status="in_sync")

    drift_items = _parse_diff(diff)

    # If post-processing decomposed everything to no-op, treat as in_sync
    if not drift_items:
        return DriftResult(kind=kind, name=name, namespace=namespace, status="in_sync")

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

    DeepDiff may group nested changes at a parent path (e.g. when cutoff
    heuristics decide structures are too dissimilar). We decompose those
    blobs by walking the values ourselves so severity rules can match
    granular field paths like "containers[0].image".
    """
    items: List[DriftItem] = []

    # Field value changed — may need decomposition if both sides are structured
    for path, change in diff.get("values_changed", {}).items():
        items.extend(_emit_change(
            _clean_path(path),
            change["old_value"],
            change["new_value"],
        ))

    # Type changed (e.g. string → int) — always a leaf
    for path, change in diff.get("type_changes", {}).items():
        items.append(DriftItem(
            field_path=_clean_path(path),
            git_value=change["old_value"],
            live_value=change["new_value"],
            change_type="changed",
        ))

    # Field added in live (not in git)
    for path, value in diff.get("dictionary_item_added", {}).items():
        items.extend(_emit_added(_clean_path(path), value))

    # Field removed from live (in git but not live)
    for path, value in diff.get("dictionary_item_removed", {}).items():
        items.extend(_emit_removed(_clean_path(path), value))

    # Item added to a list
    for path, value in diff.get("iterable_item_added", {}).items():
        items.extend(_emit_added(_clean_path(path), value))

    # Item removed from a list
    for path, value in diff.get("iterable_item_removed", {}).items():
        items.extend(_emit_removed(_clean_path(path), value))

    return items


# ---------------------------------------------------------------------------
# Leaf-level decomposition
# ---------------------------------------------------------------------------

def _is_list_item_path(path: str) -> bool:
    """
    True if the path points to an element inside a list — i.e. it ends with
    a bracket-indexed segment like `...env[0]` or `...containers[2]`.
    """
    return path.endswith("]")


def _emit_change(path: str, old_value: Any, new_value: Any) -> List[DriftItem]:
    """
    Emit DriftItems for a value change.

    If both sides are dicts or lists, recurse to produce one item per leaf
    difference. Otherwise emit a single leaf-level "changed" item.
    """
    if isinstance(old_value, dict) and isinstance(new_value, dict):
        return _recursive_dict_diff(path, old_value, new_value)
    if isinstance(old_value, list) and isinstance(new_value, list):
        return _recursive_list_diff(path, old_value, new_value)
    return [DriftItem(
        field_path=path,
        git_value=old_value,
        live_value=new_value,
        change_type="changed",
    )]


def _emit_added(path: str, value: Any) -> List[DriftItem]:
    """
    Walk an added value, emit DriftItems.

    Atomicity rule — dicts at a list-item boundary are NOT decomposed:
      - Adding an env var `{name: DEBUG, value: true}` → 1 DriftItem
      - Adding a container `{name: sidecar, image: busybox, ...}` → 1 DriftItem
      - Adding a Service port `{port: 443, targetPort: 443}` → 1 DriftItem

    Rationale: these represent one logical change to a user. Decomposing
    them inflates drift counts and scrambles severity classification
    (e.g. adding one env var would otherwise produce TWO critical drifts
    for `.name` and `.value`).

    Scalars in lists remain individually meaningful:
      - Adding args `[--foo, --bar]` → 2 DriftItems (one per arg)
    """
    # Dict at a list-item boundary → atomic
    if isinstance(value, dict) and _is_list_item_path(path):
        return [DriftItem(
            field_path=path,
            git_value=None,
            live_value=value,
            change_type="added",
        )]

    # Dict at a dict-key boundary → decompose to leaves
    if isinstance(value, dict):
        items = []
        for k, v in value.items():
            items.extend(_emit_added(f"{path}.{k}", v))
        return items

    # List → recurse into each element (children may be atomic dicts or scalars)
    if isinstance(value, list):
        items = []
        for i, v in enumerate(value):
            items.extend(_emit_added(f"{path}[{i}]", v))
        return items

    # Scalar leaf
    return [DriftItem(
        field_path=path,
        git_value=None,
        live_value=value,
        change_type="added",
    )]


def _emit_removed(path: str, value: Any) -> List[DriftItem]:
    """
    Walk a removed value, emit DriftItems.

    Same atomicity rule as _emit_added: dicts at list-item boundaries
    are atomic, dicts at dict-key boundaries decompose.
    """
    # Dict at a list-item boundary → atomic
    if isinstance(value, dict) and _is_list_item_path(path):
        return [DriftItem(
            field_path=path,
            git_value=value,
            live_value=None,
            change_type="removed",
        )]

    if isinstance(value, dict):
        items = []
        for k, v in value.items():
            items.extend(_emit_removed(f"{path}.{k}", v))
        return items

    if isinstance(value, list):
        items = []
        for i, v in enumerate(value):
            items.extend(_emit_removed(f"{path}[{i}]", v))
        return items

    return [DriftItem(
        field_path=path,
        git_value=value,
        live_value=None,
        change_type="removed",
    )]


def _recursive_dict_diff(base_path: str, git_d: dict, live_d: dict) -> List[DriftItem]:
    """Compare two dicts key-by-key, emit leaf-level items only."""
    items: List[DriftItem] = []
    all_keys = set(git_d.keys()) | set(live_d.keys())
    for key in sorted(all_keys):
        child_path = f"{base_path}.{key}" if base_path else key
        if key in git_d and key in live_d:
            g, l = git_d[key], live_d[key]
            if g == l:
                continue
            items.extend(_emit_change(child_path, g, l))
        elif key in live_d:
            items.extend(_emit_added(child_path, live_d[key]))
        else:
            items.extend(_emit_removed(child_path, git_d[key]))
    return items


def _recursive_list_diff(base_path: str, git_l: list, live_l: list) -> List[DriftItem]:
    """
    Compare two lists positionally.

    Lists reaching this function have already been flagged as different by
    DeepDiff. Positional comparison here extracts leaf-level granularity for
    the UI. For Kubernetes container lists this usually works because
    DeepDiff sorts similar items before handing them to us.

    A more robust implementation would match dict items by a key field
    (containers by .name, ports by .port, etc.). Left as a future improvement.
    """
    items: List[DriftItem] = []
    max_len = max(len(git_l), len(live_l))
    for i in range(max_len):
        child_path = f"{base_path}[{i}]"
        if i < len(git_l) and i < len(live_l):
            g, l = git_l[i], live_l[i]
            if g == l:
                continue
            items.extend(_emit_change(child_path, g, l))
        elif i < len(live_l):
            items.extend(_emit_added(child_path, live_l[i]))
        else:
            items.extend(_emit_removed(child_path, git_l[i]))
    return items


# ---------------------------------------------------------------------------
# Path cleanup
# ---------------------------------------------------------------------------

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