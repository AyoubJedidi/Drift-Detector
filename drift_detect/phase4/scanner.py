"""
Phase 4 - Part 1: Scanner

The main pipeline orchestrator. Calls every phase in order and returns
a list of DriftResult objects ready for display.

This is the single entry point for the entire tool — the CLI calls this,
the serve mode calls this, the CI mode calls this.

Nobody else needs to know about resolvers, renderers, fetchers, or normalizers.
"""

from pathlib import Path
from typing import List, Optional

from drift_detect.phase1.resolver   import resolve_repo
from drift_detect.phase1.detector   import resolve_source_type
from drift_detect.phase1.renderer   import render
from drift_detect.phase2.fetcher    import build_refs_from_manifests, fetch_live_resources, FetchResult
from drift_detect.phase2.normalizer import normalize_pair
from drift_detect.phase3.differ     import (
    diff_resource,
    diff_missing_from_cluster,
    diff_missing_from_git,
    DriftResult,
)
from drift_detect.phase3.classifier import classify_result
from drift_detect.phase3.driftignore import load_driftignore, apply_ignore_rules


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def scan(
    source:               str,
    branch:               Optional[str] = None,
    tag:                  Optional[str] = None,
    source_type_override: Optional[str] = None,
    helm_values:          Optional[List[Path]] = None,
    namespace_filter:     Optional[str] = None,
    kind_filter:          Optional[str] = None,
    custom_severity_rules: list = None,
    driftignore_path:     Optional[Path] = None,
) -> List[DriftResult]:
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

    Returns:
        List of DriftResult — one per resource found in Git.
    """

    # ------------------------------------------------------------------
    # Phase 1 — Git side
    # ------------------------------------------------------------------
    directory   = resolve_repo(source, branch=branch, tag=tag)
    source_type = resolve_source_type(directory, override=source_type_override)
    manifests   = render(directory, source_type, helm_values=helm_values)

    if not manifests:
        print("No Kubernetes manifests found in source.")
        return []

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
        print(f"No resources matched the given filters.")
        return []

    # ------------------------------------------------------------------
    # Phase 2 — Cluster side
    # ------------------------------------------------------------------
    refs           = build_refs_from_manifests(manifests)
    fetch_results  = fetch_live_resources(refs)

    # Build a lookup: (kind, name, namespace) → git manifest
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
            # Skip — already warned during fetch
            continue

        else:
            # Resource found — normalize both sides then diff
            norm_git, norm_live = normalize_pair(
                git_object,
                fetch_result.live_object,
            )
            result = diff_resource(norm_git, norm_live)

        # Classify severity of each drift item
        classify_result(result, custom_rules=custom_severity_rules)
        results.append(result)

    # Apply .driftignore rules
    ignore_rules = load_driftignore(driftignore_path)
    results = apply_ignore_rules(results, ignore_rules)

    return results


# ---------------------------------------------------------------------------
# Convenience helpers for consumers (CLI, serve mode)
# ---------------------------------------------------------------------------

def has_drift_above(results: List[DriftResult], threshold: str) -> bool:
    """
    Return True if any result has drift at or above the given severity threshold.

    Used for exit code logic:
      --fail-on critical  → only exit 1 if critical drift exists
      --fail-on warning   → exit 1 if critical or warning drift exists
      --fail-on info      → exit 1 if any drift exists
    """
    order = {"critical": 0, "warning": 1, "info": 2}
    threshold_level = order.get(threshold.lower(), 0)

    for result in results:
        if result.status not in ("drifted",):
            continue
        for drift in result.drifts:
            if order.get(drift.severity, 99) <= threshold_level:
                return True

    return False


def summary_counts(results: List[DriftResult]) -> dict:
    """
    Return overall counts across all results.

    Returns:
        {
            "total":    total resources scanned,
            "drifted":  resources with drift,
            "missing_from_cluster": resources in git but not cluster,
            "missing_from_git":     resources in cluster but not git,
            "in_sync":  resources with no drift,
            "critical": total critical drift items,
            "warning":  total warning drift items,
            "info":     total info drift items,
        }
    """
    counts = {
        "total": len(results),
        "drifted": 0,
        "missing_from_cluster": 0,
        "missing_from_git": 0,
        "in_sync": 0,
        "critical": 0,
        "warning": 0,
        "info": 0,
    }

    for result in results:
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