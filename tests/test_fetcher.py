"""
Tests for Phase 2 - Step 1: fetcher.py

All cluster calls are mocked — no real cluster needed.
Run with: pytest tests/test_fetcher.py -v
"""

from unittest.mock import MagicMock, patch, PropertyMock
import pytest

from drift_detect.phase2.fetcher import (
    ResourceRef,
    FetchResult,
    fetch_live_resources,
    build_refs_from_manifests,
    _fetch_one,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_ref(kind="Deployment", name="nginx", namespace="default", api_version="apps/v1"):
    return ResourceRef(kind=kind, name=name, namespace=namespace, api_version=api_version)


def make_manifest(kind="Deployment", name="nginx", namespace="default", api_version="apps/v1"):
    return {
        "apiVersion": api_version,
        "kind": kind,
        "metadata": {"name": name, "namespace": namespace},
        "spec": {"replicas": 3},
    }


# ---------------------------------------------------------------------------
# build_refs_from_manifests() tests
# ---------------------------------------------------------------------------

class TestBuildRefs:
    def test_builds_ref_from_valid_manifest(self):
        manifests = [make_manifest()]
        refs = build_refs_from_manifests(manifests)
        assert len(refs) == 1
        assert refs[0].kind == "Deployment"
        assert refs[0].name == "nginx"
        assert refs[0].namespace == "default"

    def test_defaults_namespace_to_default(self):
        manifest = make_manifest()
        del manifest["metadata"]["namespace"]
        refs = build_refs_from_manifests([manifest])
        assert refs[0].namespace == "default"

    def test_skips_manifest_missing_name(self):
        manifest = make_manifest()
        del manifest["metadata"]["name"]
        refs = build_refs_from_manifests([manifest])
        assert refs == []

    def test_skips_manifest_missing_kind(self):
        manifest = make_manifest()
        del manifest["kind"]
        refs = build_refs_from_manifests([manifest])
        assert refs == []

    def test_builds_multiple_refs(self):
        manifests = [
            make_manifest(kind="Deployment", name="nginx"),
            make_manifest(kind="Service",    name="nginx-svc", api_version="v1"),
        ]
        refs = build_refs_from_manifests(manifests)
        assert len(refs) == 2


# ---------------------------------------------------------------------------
# _fetch_one() tests — mocked API calls
# ---------------------------------------------------------------------------

class TestFetchOne:
    def _make_api_client(self, return_value=None):
        """Build a mock ApiClient that returns a fake k8s object."""
        mock_api_client = MagicMock()
        mock_api_client.sanitize_for_serialization.return_value = return_value or {
            "kind": "Deployment",
            "metadata": {"name": "nginx"},
        }
        return mock_api_client

    @patch("drift_detect.phase2.fetcher.client")
    def test_found_resource_returns_found_status(self, mock_client):
        mock_api_instance = MagicMock()
        mock_client.AppsV1Api.return_value = mock_api_instance
        mock_api_instance.read_namespaced_deployment.return_value = MagicMock()

        mock_api_client = self._make_api_client()
        result = _fetch_one(make_ref(), mock_api_client)

        assert result.status == "found"
        assert result.live_object is not None

    @patch("drift_detect.phase2.fetcher.client")
    def test_missing_resource_returns_missing_status(self, mock_client):
        from kubernetes.client.rest import ApiException
        mock_api_instance = MagicMock()
        mock_client.AppsV1Api.return_value = mock_api_instance

        exc = ApiException(status=404, reason="Not Found")
        mock_api_instance.read_namespaced_deployment.side_effect = exc

        mock_api_client = self._make_api_client()
        result = _fetch_one(make_ref(), mock_api_client)

        assert result.status == "missing_from_cluster"
        assert result.live_object is None

    @patch("drift_detect.phase2.fetcher.client")
    def test_api_error_returns_error_status(self, mock_client):
        from kubernetes.client.rest import ApiException
        mock_api_instance = MagicMock()
        mock_client.AppsV1Api.return_value = mock_api_instance

        exc = ApiException(status=403, reason="Forbidden")
        mock_api_instance.read_namespaced_deployment.side_effect = exc

        mock_api_client = self._make_api_client()
        result = _fetch_one(make_ref(), mock_api_client)

        assert result.status == "error"
        assert "403" in result.error

    def test_unknown_kind_returns_unknown_kind_status(self):
        ref = make_ref(kind="MyCustomResource")
        mock_api_client = self._make_api_client()
        result = _fetch_one(ref, mock_api_client)
        assert result.status == "unknown_kind"

    @patch("drift_detect.phase2.fetcher.client")
    def test_service_uses_core_v1_api(self, mock_client):
        mock_api_instance = MagicMock()
        mock_client.CoreV1Api.return_value = mock_api_instance
        mock_api_instance.read_namespaced_service.return_value = MagicMock()

        mock_api_client = self._make_api_client()
        ref = make_ref(kind="Service", api_version="v1")
        _fetch_one(ref, mock_api_client)

        assert mock_client.CoreV1Api.called
        assert mock_api_instance.read_namespaced_service.called


# ---------------------------------------------------------------------------
# fetch_live_resources() tests
# ---------------------------------------------------------------------------

class TestFetchLiveResources:
    @patch("drift_detect.phase2.fetcher.load_cluster_config")
    @patch("drift_detect.phase2.fetcher.client")
    def test_returns_one_result_per_ref(self, mock_client, mock_load):
        mock_api_instance = MagicMock()
        mock_client.AppsV1Api.return_value = mock_api_instance
        mock_client.ApiClient.return_value = MagicMock()
        mock_api_instance.read_namespaced_deployment.return_value = MagicMock()

        refs = [make_ref(), make_ref(name="other")]
        results = fetch_live_resources(refs)
        assert len(results) == 2

    @patch("drift_detect.phase2.fetcher.load_cluster_config",
           side_effect=RuntimeError("no kubeconfig"))
    def test_load_config_failure_raises(self, mock_load):
        with pytest.raises(RuntimeError, match="no kubeconfig"):
            fetch_live_resources([make_ref()])