"""
Phase 2 - Step 2: Normalizer

Strips server-generated fields from Kubernetes objects before comparison.
Applied to BOTH the Git-rendered object and the live cluster object.

Without this, every resource would appear to have drifted because Kubernetes
adds dozens of fields that were never in the original manifest.

Fields stripped:
  - metadata.resourceVersion
  - metadata.uid
  - metadata.creationTimestamp
  - metadata.generation
  - metadata.managedFields
  - metadata.annotations[kubectl.kubernetes.io/last-applied-configuration]
  - status (entirely — not declarative)
"""

import copy
from typing import Optional


# ---------------------------------------------------------------------------
# Fields to remove from metadata
# ---------------------------------------------------------------------------

_METADATA_FIELDS_TO_STRIP = {
    "resourceVersion",
    "uid",
    "creationTimestamp",
    "generation",
    "managedFields",
    "selfLink",          # deprecated but still appears in older clusters
    "generateName",      # server-assigned name prefix
}

# Annotations added by Kubernetes tooling — not declared by the user
_ANNOTATIONS_TO_STRIP = {
    "kubectl.kubernetes.io/last-applied-configuration",
    "deployment.kubernetes.io/revision",
    "deprecated.daemonset.template.generation",
}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def normalize(resource: dict) -> dict:
    """
    Return a clean copy of a Kubernetes resource dict with all
    server-generated noise removed.

    Does NOT modify the original — always works on a deep copy.

    Args:
        resource: A Kubernetes resource dict (from Git render or live fetch)

    Returns:
        A new dict with server-generated fields stripped out.
    """
    if not resource:
        return {}

    cleaned = copy.deepcopy(resource)

    _strip_metadata(cleaned)
    _strip_status(cleaned)

    return cleaned


def normalize_pair(git_object: dict, live_object: dict) -> tuple:
    """
    Normalize both sides of a comparison at once.

    Args:
        git_object:  Resource dict from Phase 1 (rendered from Git)
        live_object: Resource dict from Phase 2 Step 1 (fetched from cluster)

    Returns:
        Tuple of (normalized_git, normalized_live)
    """
    return normalize(git_object), normalize(live_object)


# ---------------------------------------------------------------------------
# Internal stripping logic
# ---------------------------------------------------------------------------

def _strip_metadata(resource: dict) -> None:
    """Remove server-generated fields from metadata in place."""
    metadata = resource.get("metadata")
    if not isinstance(metadata, dict):
        return

    # Remove known noisy top-level metadata fields
    for field in _METADATA_FIELDS_TO_STRIP:
        metadata.pop(field, None)

    # Clean up annotations
    _strip_annotations(metadata)

    # Remove metadata if it became empty after stripping
    # (keeps the dict clean for comparison)
    if not metadata:
        resource.pop("metadata", None)


def _strip_annotations(metadata: dict) -> None:
    """Remove known tooling annotations from metadata.annotations."""
    annotations = metadata.get("annotations")
    if not isinstance(annotations, dict):
        return

    for key in _ANNOTATIONS_TO_STRIP:
        annotations.pop(key, None)

    # Also strip any annotation that looks like an internal k8s annotation
    # e.g. "control-plane.alpha.kubernetes.io/leader"
    keys_to_remove = [
        k for k in annotations
        if _is_internal_annotation(k)
    ]
    for k in keys_to_remove:
        annotations.pop(k, None)

    # Remove annotations dict if empty
    if not annotations:
        metadata.pop("annotations", None)


def _strip_status(resource: dict) -> None:
    """Remove the status block entirely — it's never declared in Git."""
    resource.pop("status", None)


def _is_internal_annotation(key: str) -> bool:
    """
    Return True if an annotation key is Kubernetes-internal and should be stripped.

    Strips annotations from these known internal domains:
      - *.kubernetes.io/*
      - *.k8s.io/*
    """
    internal_suffixes = (
        "kubernetes.io/",
        "k8s.io/",
    )
    return any(suffix in key for suffix in internal_suffixes)