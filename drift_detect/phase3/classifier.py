"""
Phase 3 - Step 2: Severity Classifier

Takes a list of DriftItems from differ.py and assigns each one a severity:
  - critical: changes that directly affect runtime behavior or security
  - warning:  changes that affect reliability or performance
  - info:     cosmetic or non-functional changes

Classification is based on the field path of each drift item.
Users can override the default mapping via a config file (future - Phase 4).
"""

from typing import List
from drift_detect.phase3.differ import DriftItem


# ---------------------------------------------------------------------------
# Severity constants
# ---------------------------------------------------------------------------

CRITICAL = "critical"
WARNING  = "warning"
INFO     = "info"


# ---------------------------------------------------------------------------
# Default severity mapping
#
# Each entry is a (substring, severity) tuple.
# The classifier checks if the substring appears anywhere in the field path.
# Order matters — first match wins.
# ---------------------------------------------------------------------------

DEFAULT_SEVERITY_RULES = [
    # --- CRITICAL ---
    # Container image — wrong image = wrong code running
    ("containers",          CRITICAL),   # catches image, env, volumeMounts etc inside containers
    ("initContainers",      CRITICAL),
    ("image",               CRITICAL),

    # Replicas — scaling directly affects availability
    ("replicas",            CRITICAL),

    # Environment variables — may contain secrets or config
    ("env",                 CRITICAL),

    # Secret and configmap references
    ("secretRef",           CRITICAL),
    ("configMapRef",        CRITICAL),
    ("secretKeyRef",        CRITICAL),
    ("configMapKeyRef",     CRITICAL),

    # Security context — privilege escalation risk
    ("securityContext",     CRITICAL),

    # Service account — identity and RBAC
    ("serviceAccountName",  CRITICAL),

    # Volume mounts — filesystem access
    ("volumeMounts",        CRITICAL),
    ("volumes",             CRITICAL),

    # --- WARNING ---
    # Resource limits — affects scheduling and OOM behavior
    ("resources",           WARNING),
    ("limits",              WARNING),
    ("requests",            WARNING),

    # Probes — affects health checking
    ("livenessProbe",       WARNING),
    ("readinessProbe",      WARNING),
    ("startupProbe",        WARNING),

    # Service ports — affects connectivity
    ("ports",               WARNING),

    # Scheduling
    ("nodeSelector",        WARNING),
    ("tolerations",         WARNING),
    ("affinity",            WARNING),

    # --- INFO ---
    # Labels and annotations — metadata only
    ("labels",              INFO),
    ("annotations",         INFO),
]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def classify(drift_items: List[DriftItem], custom_rules: list = None) -> List[DriftItem]:
    """
    Assign severity to each DriftItem based on its field path.

    Args:
        drift_items:  List of DriftItems from differ.py
        custom_rules: Optional list of (substring, severity) tuples
                      that take priority over defaults.

    Returns:
        The same list with severity field set on each item.
        Items are sorted by severity (critical first).
    """
    rules = list(custom_rules or []) + DEFAULT_SEVERITY_RULES

    for item in drift_items:
        item.severity = _classify_one(item.field_path, rules)

    # Sort: critical → warning → info
    return sorted(drift_items, key=lambda d: _severity_order(d.severity))


def classify_result(result, custom_rules: list = None):
    """
    Classify all DriftItems in a DriftResult in place.

    Args:
        result:       DriftResult from differ.py
        custom_rules: Optional custom severity rules

    Returns:
        The same DriftResult with severity set on all drifts.
    """
    result.drifts = classify(result.drifts, custom_rules=custom_rules)
    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _classify_one(field_path: str, rules: list) -> str:
    """Return the severity for a single field path — first match wins."""
    for substring, severity in rules:
        if substring in field_path:
            return severity
    # Default fallback — unknown fields are info
    return INFO


def _severity_order(severity: str) -> int:
    """Return sort order for severity — lower = shown first."""
    return {CRITICAL: 0, WARNING: 1, INFO: 2}.get(severity, 3)


def severity_counts(drift_items: List[DriftItem]) -> dict:
    """
    Count drift items by severity.

    Returns:
        dict with keys "critical", "warning", "info"
    """
    counts = {CRITICAL: 0, WARNING: 0, INFO: 0}
    for item in drift_items:
        if item.severity in counts:
            counts[item.severity] += 1
    return counts