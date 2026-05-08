from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from drift_detect.phase1.resolver   import resolve_repo
from drift_detect.phase1.detector   import resolve_source_type
from drift_detect.phase1.renderer   import render
from drift_detect.phase2.fetcher    import build_refs_from_manifests, fetch_live_resources
from drift_detect.phase2.normalizer import normalize_pair
from drift_detect.phase3.differ     import (
    diff_resource,
    diff_missing_from_cluster,
    DriftResult,
)
from drift_detect.phase3.classifier import classify_result
from drift_detect.phase3.driftignore import load_driftignore, apply_ignore_rules
from drift_detect.phase6.snapshot   import (
    Snapshot,
    Delta,
    build_snapshot,
    save_snapshot,
    load_previous_snapshot,
    prune_snapshots,
    compute_delta,
)


# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------

@dataclass
class ScanResult:
    """
    Full output of a scan run.

    results:          per-resource drift results from Phase 3
    delta:            difference vs the previous snapshot, or None if
                      snapshots weren't enabled for this run
    snapshot_path:    path to the snapshot file written for this run, or
                      None if --snapshot-dir wasn't passed
    """
    results:        List[DriftResult]      = field(default_factory=list)
    delta:          Optional[Delta]        = None
    snapshot_path:  Optional[Path]         = None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def scan(
    source:                str,
    branch:                Optional[str] = None,
    tag:                   Optional[str] = None,
    source_type_override:  Optional[str] = None,
    helm_values:           Optional[List[Path]] = None,
    namespace_filter:      Optional[str] = None,
    kind_filter:           Optional[str] = None,
    custom_severity_rules: list = None,
    driftignore_path:      Optional[Path] = None,
    snapshot_dir:          Optional[Path] = None,
    snapshot_retain:       int = 30,
) -> ScanResult:
    """
    Run the full drift detection pipeline.

    Args:
        source:                Local path or remote Git URL
        branch:                Optional branch to checkout (remote repos)
        tag:                   Optional tag to checkout (remote repos)
        source_type_override:  Force "helm", "kustomize", or "raw"
        helm_values:           Optional values files for Helm rendering
        namespace_filter:      Only scan resources in this namespace
        kind_filter:           Only scan resources of this kind
        custom_severity_rules: Override default severity classification
        driftignore_path:      Path to .driftignore file
        snapshot_dir:          If provided, save scan + compute delta vs
                               most recent prior snapshot for this source
        snapshot_retain:       Keep this many most-recent snapshots per source

    Returns:
        ScanResult with results, optional delta, optional snapshot path.
    """

    # ------------------------------------------------------------------
    # Phase 1 — Git side
    # ------------------------------------------------------------------
    directory   = resolve_repo(source, branch=branch, tag=tag)
    source_type = resolve_source_type(directory, override=source_type_override)
    manifests   = render(directory, source_type, helm_values=helm_values)

    if not manifests:
        print("No Kubernetes manifests found in source.")
        return ScanResult()

    # ------------------------------------------------------------------
    # Apply filters before hitting the cluster
    # ------------------------------------------------------------------
    if namespace_filter:
        manifests = [
            m for m in manifests
            if m.get("metadata", {}).get("namespace", "default") == namespace_filter
        ]

    if kind_filter:
        manifests = [
            m for m in manifests
            if m.get("kind", "").lower() == kind_filter.lower()
        ]

    if not manifests:
        print("No resources matched the given filters.")
        return ScanResult()

    # ------------------------------------------------------------------
    # Phase 2 — Cluster side
    # ------------------------------------------------------------------
    refs          = build_refs_from_manifests(manifests)
    fetch_results = fetch_live_resources(refs)

    git_lookup = {
        (
            m.get("kind", ""),
            m.get("metadata", {}).get("name", ""),
            m.get("metadata", {}).get("namespace", "default"),
        ): m
        for m in manifests
    }

    # ------------------------------------------------------------------
    # Phase 3 — Diff + classify
    # ------------------------------------------------------------------
    results: List[DriftResult] = []

    for fetch_result in fetch_results:
        ref = fetch_result.ref
        key = (ref.kind, ref.name, ref.namespace)
        git_object = git_lookup.get(key, {})

        if fetch_result.status == "missing_from_cluster":
            result = diff_missing_from_cluster(git_object)

        elif fetch_result.status in ("unknown_kind", "error"):
            continue

        else:
            norm_git, norm_live = normalize_pair(
                git_object,
                fetch_result.live_object,
            )
            result = diff_resource(norm_git, norm_live)

        classify_result(result, custom_rules=custom_severity_rules)
        results.append(result)

    # Apply .driftignore rules
    ignore_rules = load_driftignore(driftignore_path)
    results = apply_ignore_rules(results, ignore_rules)

    # ------------------------------------------------------------------
    # Phase 6 — Snapshot + delta (only if --snapshot-dir was passed)
    # ------------------------------------------------------------------
    delta:          Optional[Delta] = None
    snapshot_path:  Optional[Path]  = None

    if snapshot_dir is not None:
        previous = load_previous_snapshot(source, snapshot_dir)
        current  = build_snapshot(results, source=source, namespace=namespace_filter)
        delta    = compute_delta(current, previous)

        snapshot_path = save_snapshot(current, snapshot_dir)
        prune_snapshots(source, snapshot_dir, retain=snapshot_retain)

    return ScanResult(
        results=results,
        delta=delta,
        snapshot_path=snapshot_path,
    )


# ---------------------------------------------------------------------------
# Convenience helpers for consumers (CLI, serve mode)
# ---------------------------------------------------------------------------

def has_drift_above(scan_result: ScanResult, threshold: str) -> bool:
    """
    Return True if any drift item is at or above the given severity threshold.

    Used for exit code logic:
      --fail-on critical  → only exit 1 if critical drift exists
      --fail-on warning   → exit 1 if critical or warning drift exists
      --fail-on info      → exit 1 if any drift exists
    """
    order = {"critical": 0, "warning": 1, "info": 2}
    threshold_level = order.get(threshold.lower(), 0)

    for result in scan_result.results:
        if result.status not in ("drifted",):
            continue
        for drift in result.drifts:
            if order.get(drift.severity, 99) <= threshold_level:
                return True

    return False


def summary_counts(scan_result: ScanResult) -> dict:
    """
    Return overall counts across all results.
    """
    counts = {
        "total": len(scan_result.results),
        "drifted": 0,
        "missing_from_cluster": 0,
        "missing_from_git": 0,
        "in_sync": 0,
        "critical": 0,
        "warning": 0,
        "info": 0,
    }

    for result in scan_result.results:
        if result.status == "in_sync":
            counts["in_sync"] += 1
        elif result.status == "drifted":
            counts["drifted"] += 1
            for drift in result.drifts:
                counts[drift.severity] = counts.get(drift.severity, 0) + 1
        elif result.status == "missing_from_cluster":
            counts["missing_from_cluster"] += 1
        elif result.status == "missing_from_git":
            counts["missing_from_git"] += 1

    return counts