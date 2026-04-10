"""
Phase 2 - Step 1: Live State Fetcher

Given a list of Kubernetes resources from Phase 1 (each with kind, name, namespace),
fetches the current live state from the cluster via the Kubernetes API.

Requires:
  - A reachable cluster
  - A valid kubeconfig at ~/.kube/config (same one kubectl uses)
  - pip install kubernetes
"""

from dataclasses import dataclass, field
from typing import List, Optional

from kubernetes import client, config
from kubernetes.client.rest import ApiException


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ResourceRef:
    """
    A lightweight reference to a Kubernetes resource.
    Built from Phase 1 output — each rendered manifest has these fields.
    """
    kind:      str
    name:      str
    namespace: str
    api_version: str  # e.g. "apps/v1", "v1", "networking.k8s.io/v1"


@dataclass
class FetchResult:
    """
    Result of fetching a single resource from the cluster.

    status is one of:
      - "found"               → live object retrieved successfully
      - "missing_from_cluster"→ resource exists in Git but not in cluster
      - "unknown_kind"        → we don't know how to fetch this kind (CRD etc.)
      - "error"               → API call failed unexpectedly
    """
    ref:        ResourceRef
    status:     str
    live_object: Optional[dict] = field(default=None)
    error:      Optional[str]   = field(default=None)


# ---------------------------------------------------------------------------
# API kind → (api_class, fetch_method) mapping
# ---------------------------------------------------------------------------

# Maps lowercase kind name to a tuple of:
#   (api_class_name, method_name)
# method_name is the namespaced read method on that API class.
_KIND_MAP = {
    # Core v1
    "pod":                    ("CoreV1Api",       "read_namespaced_pod"),
    "service":                ("CoreV1Api",       "read_namespaced_service"),
    "configmap":              ("CoreV1Api",       "read_namespaced_config_map"),
    "secret":                 ("CoreV1Api",       "read_namespaced_secret"),
    "serviceaccount":         ("CoreV1Api",       "read_namespaced_service_account"),
    "persistentvolumeclaim":  ("CoreV1Api",       "read_namespaced_persistent_volume_claim"),

    # Apps v1
    "deployment":             ("AppsV1Api",       "read_namespaced_deployment"),
    "statefulset":            ("AppsV1Api",       "read_namespaced_stateful_set"),
    "daemonset":              ("AppsV1Api",       "read_namespaced_daemon_set"),
    "replicaset":             ("AppsV1Api",       "read_namespaced_replica_set"),

    # Networking v1
    "ingress":                ("NetworkingV1Api", "read_namespaced_ingress"),
    "networkpolicy":          ("NetworkingV1Api", "read_namespaced_network_policy"),

    # RBAC v1
    "role":                   ("RbacAuthorizationV1Api", "read_namespaced_role"),
    "rolebinding":            ("RbacAuthorizationV1Api", "read_namespaced_role_binding"),

    # Batch v1
    "job":                    ("BatchV1Api",      "read_namespaced_job"),
    "cronjob":                ("BatchV1Api",      "read_namespaced_cron_job"),

    # Autoscaling v1
    "horizontalpodautoscaler": ("AutoscalingV1Api", "read_namespaced_horizontal_pod_autoscaler"),
}


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def load_cluster_config() -> None:
    """
    Load kubeconfig from ~/.kube/config.
    Raises RuntimeError with a clear message if it fails.
    """
    try:
        config.load_kube_config()
    except Exception as e:
        raise RuntimeError(
            f"Could not load kubeconfig: {e}\n"
            "Is kubectl configured? Does ~/.kube/config exist?\n"
            "Tip: run `kubectl get nodes` to verify your cluster is reachable."
        )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def fetch_live_resources(refs: List[ResourceRef]) -> List[FetchResult]:
    """
    Fetch live state for a list of resource references.

    Args:
        refs: List of ResourceRef built from Phase 1 output.

    Returns:
        List of FetchResult — one per input ref.
    """
    load_cluster_config()
    api_client = client.ApiClient()

    results = []
    for ref in refs:
        result = _fetch_one(ref, api_client)
        results.append(result)

    return results


def build_refs_from_manifests(manifests: List[dict]) -> List[ResourceRef]:
    """
    Convert Phase 1 output (list of dicts) into ResourceRef objects.

    Skips manifests missing required fields with a warning.
    """
    refs = []
    for manifest in manifests:
        kind      = manifest.get("kind", "")
        api_ver   = manifest.get("apiVersion", "")
        metadata  = manifest.get("metadata", {})
        name      = metadata.get("name", "")
        namespace = metadata.get("namespace", "default")

        if not kind or not name:
            print(f"Warning: skipping manifest missing kind or name: {manifest}")
            continue

        refs.append(ResourceRef(
            kind=kind,
            name=name,
            namespace=namespace,
            api_version=api_ver,
        ))

    return refs


# ---------------------------------------------------------------------------
# Internal fetch logic
# ---------------------------------------------------------------------------

def _fetch_one(ref: ResourceRef, api_client: client.ApiClient) -> FetchResult:
    """Fetch a single resource from the cluster."""
    kind_key = ref.kind.lower()

    if kind_key not in _KIND_MAP:
        print(f"Warning: unknown kind '{ref.kind}' — skipping (CRD or unsupported resource).")
        return FetchResult(ref=ref, status="unknown_kind")

    api_class_name, method_name = _KIND_MAP[kind_key]

    try:
        # Dynamically get the right API class (e.g. client.AppsV1Api)
        api_class    = getattr(client, api_class_name)
        api_instance = api_class(api_client)
        method       = getattr(api_instance, method_name)

        # All namespaced read methods take (name, namespace)
        k8s_object = method(name=ref.name, namespace=ref.namespace)

        # Convert the SDK object to a plain dict
        live_dict = api_client.sanitize_for_serialization(k8s_object)

        return FetchResult(ref=ref, status="found", live_object=live_dict)

    except ApiException as e:
        if e.status == 404:
            return FetchResult(ref=ref, status="missing_from_cluster")
        # Any other API error
        return FetchResult(
            ref=ref,
            status="error",
            error=f"API error {e.status}: {e.reason}",
        )

    except Exception as e:
        return FetchResult(ref=ref, status="error", error=str(e))