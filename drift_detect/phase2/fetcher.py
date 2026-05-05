""""
Phase 2 - Step 1: Live State Fetcher

Given a list of Kubernetes resources from Phase 1 (each with kind, name,
namespace), fetches the current live state from the cluster via the
Kubernetes API.

Kubeconfig discovery order (when no explicit path is passed):
  1. Explicit --kubeconfig path (passed to load_cluster_config)
  2. $KUBECONFIG environment variable
  3. ~/.kube/config
  4. In-cluster service account (when running inside a pod — for CronJob mode)

Requires:
  - A reachable cluster
  - A valid kubeconfig (same one kubectl uses) OR in-cluster SA
  - pip install kubernetes
"""

from dataclasses import dataclass, field
from typing import List, Optional

from kubernetes import client, config
from kubernetes.client.rest import ApiException
from kubernetes.config.config_exception import ConfigException


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ResourceRef:
    """
    A lightweight reference to a Kubernetes resource.
    Built from Phase 1 output — each rendered manifest has these fields.
    """
    kind:        str
    name:        str
    namespace:   str
    api_version: str  # e.g. "apps/v1", "v1", "networking.k8s.io/v1"


@dataclass
class FetchResult:
    """
    Result of fetching a single resource from the cluster.

    status is one of:
      - "found"                → live object retrieved successfully
      - "missing_from_cluster" → resource exists in Git but not in cluster
      - "unknown_kind"         → we don't know how to fetch this kind (CRD etc.)
      - "error"                → API call failed unexpectedly
    """
    ref:         ResourceRef
    status:      str
    live_object: Optional[dict] = field(default=None)
    error:       Optional[str]  = field(default=None)


# ---------------------------------------------------------------------------
# API kind → (api_class, fetch_method) mapping
# ---------------------------------------------------------------------------

# Maps lowercase kind name to (api_class_name, method_name)
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
# Connection management
# ---------------------------------------------------------------------------

# Module-level flag so we don't re-load kubeconfig on every fetch call.
_config_loaded = False


def load_cluster_config(
    kubeconfig: Optional[str] = None,
    context:    Optional[str] = None,
) -> None:
    """
    Load kubeconfig for the Kubernetes Python client.

    Call this ONCE at CLI startup, before any fetch calls. Repeat calls are
    no-ops unless force=True (not currently supported — add if needed).

    Args:
        kubeconfig: Explicit path to a kubeconfig file. If None, falls back
                    to $KUBECONFIG, then ~/.kube/config, then in-cluster SA.
        context:    Kubeconfig context name. If None, uses current-context.

    Raises:
        RuntimeError with a human-readable message on any auth or discovery
        failure. CLI layer should catch and surface as a ClickException.
    """
    global _config_loaded
    if _config_loaded:
        return

    # Explicit path wins
    if kubeconfig:
        try:
            config.load_kube_config(config_file=kubeconfig, context=context)
            _config_loaded = True
            return
        except FileNotFoundError:
            raise RuntimeError(
                f"Kubeconfig not found at: {kubeconfig}\n"
                "Check the path or omit --kubeconfig to use the default."
            )
        except ConfigException as e:
            raise RuntimeError(
                f"Kubeconfig at {kubeconfig} is invalid: {e}\n"
                "Check the file format or verify --context is correct."
            )

    # Standard discovery chain: $KUBECONFIG → ~/.kube/config
    try:
        config.load_kube_config(context=context)
        _config_loaded = True
        return
    except (ConfigException, FileNotFoundError):
        # Fall through to in-cluster
        pass

    # In-cluster fallback (for CronJob / in-pod usage)
    try:
        config.load_incluster_config()
        _config_loaded = True
        return
    except ConfigException as e:
        raise RuntimeError(
            "Could not load kubeconfig.\n"
            f"Tried: --kubeconfig, $KUBECONFIG, ~/.kube/config, in-cluster SA.\n"
            f"Last error: {e}\n"
            "Tip: run `kubectl get nodes` to verify a cluster is reachable,\n"
            "or pass --kubeconfig <path> explicitly."
        )


def verify_cluster_reachable() -> None:
    """
    Probe the cluster before scanning. Fails fast if unreachable — better
    UX than failing mid-scan with a confusing API error.

    Raises RuntimeError on connectivity failure.
    """
    try:
        v1 = client.CoreV1Api()
        v1.get_api_resources(_request_timeout=5)
    except Exception as e:
        raise RuntimeError(
            f"Cannot reach cluster: {e}\n"
            "Check:\n"
            "  - kubectl config current-context\n"
            "  - network connectivity to the API server\n"
            "  - kubeconfig credentials are not expired"
        )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def fetch_live_resources(
    refs: List[ResourceRef],
    kubeconfig: Optional[str] = None,
    context:    Optional[str] = None,
) -> List[FetchResult]:
    """
    Fetch live state for a list of resource references.

    Args:
        refs:       List of ResourceRef built from Phase 1 output.
        kubeconfig: Optional explicit kubeconfig path (forwarded to
                    load_cluster_config if config not yet loaded).
        context:    Optional kubeconfig context name.

    Returns:
        List of FetchResult — one per input ref.
    """
    # Load config on first call (idempotent after that).
    # If CLI already called load_cluster_config at startup, this is a no-op.
    load_cluster_config(kubeconfig=kubeconfig, context=context)

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
        if e.status == 403:
            return FetchResult(
                ref=ref,
                status="error",
                error=f"Forbidden: no permission to read {ref.kind}/{ref.name} "
                      f"in namespace {ref.namespace}. Check RBAC.",
            )
        if e.status == 401:
            return FetchResult(
                ref=ref,
                status="error",
                error="Unauthorized: credentials expired or invalid. "
                      "Refresh your kubeconfig token.",
            )
        return FetchResult(
            ref=ref,
            status="error",
            error=f"API error {e.status}: {e.reason}",
        )

    except Exception as e:
        return FetchResult(ref=ref, status="error", error=str(e))