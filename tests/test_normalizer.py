"""
Tests for Phase 2 - Step 2: normalizer.py

Run with: pytest tests/test_normalizer.py -v
"""

import pytest
from drift_detect.phase2.normalizer import normalize, normalize_pair, _is_internal_annotation


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_live_object(**overrides):
    """
    A realistic live Kubernetes object as returned by the API —
    full of server-generated noise.
    """
    obj = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": "nginx",
            "namespace": "default",
            "resourceVersion": "492847",
            "uid": "a1b2c3-d4e5-f6g7",
            "creationTimestamp": "2024-01-15T10:30:00Z",
            "generation": 5,
            "managedFields": [{"manager": "kubectl"}],
            "selfLink": "/apis/apps/v1/namespaces/default/deployments/nginx",
            "annotations": {
                "kubectl.kubernetes.io/last-applied-configuration": '{"kind":"Deployment"}',
                "deployment.kubernetes.io/revision": "3",
                "myapp.io/owner": "team-a",   # user annotation — should survive
            },
            "labels": {
                "app": "nginx",
            },
        },
        "spec": {
            "replicas": 3,
            "template": {
                "spec": {
                    "containers": [{"name": "nginx", "image": "nginx:1.21"}]
                }
            }
        },
        "status": {
            "availableReplicas": 3,
            "conditions": [],
        },
    }
    obj.update(overrides)
    return obj


def make_git_object():
    """A minimal Git-rendered object — clean, no server fields."""
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": "nginx",
            "namespace": "default",
            "labels": {"app": "nginx"},
            "annotations": {"myapp.io/owner": "team-a"},
        },
        "spec": {
            "replicas": 3,
            "template": {
                "spec": {
                    "containers": [{"name": "nginx", "image": "nginx:1.21"}]
                }
            }
        },
    }


# ---------------------------------------------------------------------------
# normalize() — metadata stripping
# ---------------------------------------------------------------------------

class TestNormalizeMetadata:
    def test_strips_resource_version(self):
        result = normalize(make_live_object())
        assert "resourceVersion" not in result["metadata"]

    def test_strips_uid(self):
        result = normalize(make_live_object())
        assert "uid" not in result["metadata"]

    def test_strips_creation_timestamp(self):
        result = normalize(make_live_object())
        assert "creationTimestamp" not in result["metadata"]

    def test_strips_generation(self):
        result = normalize(make_live_object())
        assert "generation" not in result["metadata"]

    def test_strips_managed_fields(self):
        result = normalize(make_live_object())
        assert "managedFields" not in result["metadata"]

    def test_strips_self_link(self):
        result = normalize(make_live_object())
        assert "selfLink" not in result["metadata"]

    def test_preserves_name(self):
        result = normalize(make_live_object())
        assert result["metadata"]["name"] == "nginx"

    def test_preserves_namespace(self):
        result = normalize(make_live_object())
        assert result["metadata"]["namespace"] == "default"

    def test_preserves_labels(self):
        result = normalize(make_live_object())
        assert result["metadata"]["labels"] == {"app": "nginx"}


# ---------------------------------------------------------------------------
# normalize() — annotation stripping
# ---------------------------------------------------------------------------

class TestNormalizeAnnotations:
    def test_strips_last_applied_configuration(self):
        result = normalize(make_live_object())
        annotations = result["metadata"].get("annotations", {})
        assert "kubectl.kubernetes.io/last-applied-configuration" not in annotations

    def test_strips_deployment_revision_annotation(self):
        result = normalize(make_live_object())
        annotations = result["metadata"].get("annotations", {})
        assert "deployment.kubernetes.io/revision" not in annotations

    def test_preserves_user_annotations(self):
        result = normalize(make_live_object())
        annotations = result["metadata"].get("annotations", {})
        assert annotations.get("myapp.io/owner") == "team-a"

    def test_empty_annotations_removed(self):
        obj = make_live_object()
        # Remove all user annotations so only internal ones remain
        obj["metadata"]["annotations"] = {
            "kubectl.kubernetes.io/last-applied-configuration": "{}",
        }
        result = normalize(obj)
        assert "annotations" not in result["metadata"]


# ---------------------------------------------------------------------------
# normalize() — status stripping
# ---------------------------------------------------------------------------

class TestNormalizeStatus:
    def test_strips_status_block(self):
        result = normalize(make_live_object())
        assert "status" not in result

    def test_no_status_block_is_fine(self):
        obj = make_live_object()
        del obj["status"]
        result = normalize(obj)
        assert "status" not in result


# ---------------------------------------------------------------------------
# normalize() — safety
# ---------------------------------------------------------------------------

class TestNormalizeSafety:
    def test_does_not_modify_original(self):
        original = make_live_object()
        original_copy = {**original}
        normalize(original)
        assert "resourceVersion" in original["metadata"]

    def test_empty_dict_returns_empty_dict(self):
        assert normalize({}) == {}

    def test_object_without_metadata_is_handled(self):
        obj = {"apiVersion": "v1", "kind": "ConfigMap"}
        result = normalize(obj)
        assert result["kind"] == "ConfigMap"


# ---------------------------------------------------------------------------
# normalize_pair()
# ---------------------------------------------------------------------------

class TestNormalizePair:
    def test_returns_tuple_of_two(self):
        git_obj  = make_git_object()
        live_obj = make_live_object()
        result = normalize_pair(git_obj, live_obj)
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_both_sides_are_normalized(self):
        git_obj  = make_git_object()
        live_obj = make_live_object()
        norm_git, norm_live = normalize_pair(git_obj, live_obj)
        assert "status" not in norm_live
        assert "resourceVersion" not in norm_live.get("metadata", {})

    def test_identical_objects_normalize_to_equal(self):
        """
        A Git object and a live object with only server noise differences
        should be equal after normalization.
        """
        git_obj  = make_git_object()
        live_obj = make_live_object()  # same content + server noise
        norm_git, norm_live = normalize_pair(git_obj, live_obj)
        assert norm_git == norm_live


# ---------------------------------------------------------------------------
# _is_internal_annotation()
# ---------------------------------------------------------------------------

class TestIsInternalAnnotation:
    def test_kubernetes_io_is_internal(self):
        assert _is_internal_annotation("kubectl.kubernetes.io/last-applied-configuration")

    def test_k8s_io_is_internal(self):
        assert _is_internal_annotation("app.k8s.io/version")

    def test_user_annotation_is_not_internal(self):
        assert not _is_internal_annotation("myapp.io/owner")

    def test_plain_key_is_not_internal(self):
        assert not _is_internal_annotation("team")