"""
Phase 2 - Step 2: Normalizer

Strips server-generated fields from Kubernetes objects before comparison.
Applied to BOTH the Git-rendered object and the live cluster object so
that only meaningful drift surfaces in the diff.

Without this, every resource would appear to have drifted because the
Kubernetes API adds dozens of defaulted fields that were never in the
original manifest (imagePullPolicy, dnsPolicy, strategy, etc).

What gets stripped:
  - metadata: resourceVersion, uid, creationTimestamp, generation,
    managedFields, selfLink, generateName
  - specific tooling annotations (kubectl last-applied, helm release,
    pv binding, etc) — via an explicit allowlist
  - status (entirely — never declarative)
  - API-defaulted fields in spec, by kind:
      Deployment:  progressDeadlineSeconds, revisionHistoryLimit, strategy
      StatefulSet: revisionHistoryLimit, podManagementPolicy, updateStrategy
      DaemonSet:   revisionHistoryLimit, updateStrategy
      PodSpec:     dnsPolicy, restartPolicy, schedulerName,
                   terminationGracePeriodSeconds, empty securityContext
      Container:   imagePullPolicy, terminationMessagePath,
                   terminationMessagePolicy, empty resources
      Service:     clusterIP, clusterIPs, ipFamilies, ipFamilyPolicy,
                   internalTrafficPolicy, sessionAffinity

Known trade-off: fields are stripped UNCONDITIONALLY from both sides. This
means a user who explicitly sets e.g. `imagePullPolicy: Always` in Git and
the cluster drifts to `Never` will NOT see it as drift. To detect drift on
a defaulted field, remove it from the strip lists below.
"""

import copy
from typing import Optional


# ---------------------------------------------------------------------------
# Metadata stripping config
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

# Explicit full-key annotations to strip.
# Prefer explicit entries over broad domain matches — many user-set
# annotations live under .kubernetes.io/ (e.g. ingress, AWS LB config)
# and must NOT be stripped.
_ANNOTATIONS_TO_STRIP = {
    "kubectl.kubernetes.io/last-applied-configuration",
    "deployment.kubernetes.io/revision",
    "deprecated.daemonset.template.generation",
    "pv.kubernetes.io/bind-completed",
    "pv.kubernetes.io/bound-by-controller",
    "volume.beta.kubernetes.io/storage-provisioner",
    "volume.kubernetes.io/storage-provisioner",
    "control-plane.alpha.kubernetes.io/leader",
}

# Prefixes (NOT substrings) of annotations to strip.
_ANNOTATION_PREFIXES_TO_STRIP = (
    "meta.helm.sh/",                   # Helm release tracking
    "autoscaling.alpha.kubernetes.io/",  # legacy HPA conditions/metrics
)


# ---------------------------------------------------------------------------
# API-defaulted spec fields, by kind
# ---------------------------------------------------------------------------

# Top-level spec.* paths defaulted by the API server per kind.
_SPEC_DEFAULTS_BY_KIND = {
    "Deployment": [
        "progressDeadlineSeconds",
        "revisionHistoryLimit",
        "strategy",
    ],
    "StatefulSet": [
        "podManagementPolicy",
        "revisionHistoryLimit",
        "updateStrategy",
    ],
    "DaemonSet": [
        "revisionHistoryLimit",
        "updateStrategy",
    ],
    "ReplicaSet": [
        "replicas",           # managed by Deployment owner
    ],
    "Job": [
        "backoffLimit",
        "completionMode",
        "completions",
        "parallelism",
        "suspend",
    ],
    "CronJob": [
        "concurrencyPolicy",
        "failedJobsHistoryLimit",
        "successfulJobsHistoryLimit",
        "suspend",
    ],
    "Service": [
        "clusterIP",
        "clusterIPs",
        "externalTrafficPolicy",
        "internalTrafficPolicy",
        "ipFamilies",
        "ipFamilyPolicy",
        "sessionAffinity",
        "allocateLoadBalancerNodePorts",
    ],
}

# Fields defaulted inside every PodSpec (spec.template.spec for workload
# kinds, spec for standalone Pods).
_POD_SPEC_DEFAULTS = [
    "dnsPolicy",
    "restartPolicy",
    "schedulerName",
    "terminationGracePeriodSeconds",
    "enableServiceLinks",
    "deprecatedServiceAccount",
]

# Fields defaulted inside every Container.
_CONTAINER_DEFAULTS = [
    "imagePullPolicy",
    "terminationMessagePath",
    "terminationMessagePolicy",
]

# Kinds that embed a PodSpec at spec.template.spec
_WORKLOAD_KINDS = {"Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job"}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def normalize(resource: dict) -> dict:
    """
    Return a clean copy of a Kubernetes resource dict with all
    server-generated noise removed.

    Never mutates the input — always works on a deep copy.

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
    _strip_api_defaults(cleaned)

    return cleaned


def normalize_pair(git_object: dict, live_object: dict) -> tuple:
    """
    Normalize both sides of a comparison at once.

    Returns:
        Tuple of (normalized_git, normalized_live)
    """
    return normalize(git_object), normalize(live_object)


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def _strip_metadata(resource: dict) -> None:
    metadata = resource.get("metadata")
    if not isinstance(metadata, dict):
        return

    for f in _METADATA_FIELDS_TO_STRIP:
        metadata.pop(f, None)

    _strip_annotations(metadata)

    if not metadata:
        resource.pop("metadata", None)


def _strip_annotations(metadata: dict) -> None:
    annotations = metadata.get("annotations")
    if not isinstance(annotations, dict):
        return

    # Explicit full keys
    for k in _ANNOTATIONS_TO_STRIP:
        annotations.pop(k, None)

    # Prefix-based stripping — use startswith, not substring match
    keys_to_remove = [
        k for k in list(annotations.keys())
        if any(k.startswith(prefix) for prefix in _ANNOTATION_PREFIXES_TO_STRIP)
    ]
    for k in keys_to_remove:
        annotations.pop(k, None)

    if not annotations:
        metadata.pop("annotations", None)


def _strip_status(resource: dict) -> None:
    resource.pop("status", None)


# ---------------------------------------------------------------------------
# API defaults (spec-level)
# ---------------------------------------------------------------------------

def _strip_api_defaults(resource: dict) -> None:
    """Remove API-server-defaulted fields from spec, by kind."""
    kind = resource.get("kind")
    if not kind:
        return

    spec = resource.get("spec")
    if not isinstance(spec, dict):
        return

    # Top-level spec.* fields
    for field_name in _SPEC_DEFAULTS_BY_KIND.get(kind, ()):
        spec.pop(field_name, None)

    # Workload kinds embed a PodSpec at spec.template.spec
    if kind in _WORKLOAD_KINDS:
        template = spec.get("template")
        if isinstance(template, dict):
            pod_spec = template.get("spec")
            if isinstance(pod_spec, dict):
                _strip_pod_spec_defaults(pod_spec)

            # The pod template often has its own empty metadata shell
            t_meta = template.get("metadata")
            if isinstance(t_meta, dict):
                t_meta.pop("creationTimestamp", None)
                if not t_meta:
                    template.pop("metadata", None)

    # Standalone Pods
    if kind == "Pod":
        _strip_pod_spec_defaults(spec)

    # Service port defaults — protocol defaults to TCP
    if kind == "Service":
        ports = spec.get("ports")
        if isinstance(ports, list):
            for port in ports:
                if isinstance(port, dict):
                    # API assigns nodePort for NodePort/LoadBalancer types
                    port.pop("nodePort", None)
                    # Protocol defaults to TCP — strip so it doesn't noise-up
                    # the diff. (Trade-off: user-set UDP→TCP drift invisible.)
                    if port.get("protocol") == "TCP":
                        port.pop("protocol", None)

    # CronJob wraps a JobTemplate which wraps a PodTemplate
    if kind == "CronJob":
        job_template = spec.get("jobTemplate")
        if isinstance(job_template, dict):
            job_spec = job_template.get("spec")
            if isinstance(job_spec, dict):
                for f in _SPEC_DEFAULTS_BY_KIND.get("Job", ()):
                    job_spec.pop(f, None)
                template = job_spec.get("template")
                if isinstance(template, dict):
                    pod_spec = template.get("spec")
                    if isinstance(pod_spec, dict):
                        _strip_pod_spec_defaults(pod_spec)


def _strip_pod_spec_defaults(pod_spec: dict) -> None:
    """Remove API-defaulted fields from a PodSpec."""
    for f in _POD_SPEC_DEFAULTS:
        pod_spec.pop(f, None)

    # Drop empty securityContext — API adds {} when user didn't specify one
    if pod_spec.get("securityContext") == {}:
        pod_spec.pop("securityContext", None)

    # Per-container defaults
    for containers_key in ("containers", "initContainers", "ephemeralContainers"):
        containers = pod_spec.get(containers_key)
        if isinstance(containers, list):
            for c in containers:
                if isinstance(c, dict):
                    _strip_container_defaults(c)


def _strip_container_defaults(container: dict) -> None:
    """Remove API-defaulted fields from a single container spec."""
    for f in _CONTAINER_DEFAULTS:
        container.pop(f, None)

    # Drop empty resources — API adds {} when user didn't specify requests/limits
    if container.get("resources") == {}:
        container.pop("resources", None)