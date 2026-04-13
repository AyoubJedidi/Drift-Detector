"""
Tests for Phase 3 - Step 1: differ.py

Run with: pytest tests/test_differ.py -v
"""

import pytest
from drift_detect.phase3.differ import (
    diff_resource,
    diff_missing_from_cluster,
    diff_missing_from_git,
    DriftItem,
    DriftResult,
    _clean_path,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_deployment(name="nginx", namespace="default", replicas=3, image="nginx:1.21", extra_spec=None):
    obj = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": name, "namespace": namespace},
        "spec": {
            "replicas": replicas,
            "template": {
                "spec": {
                    "containers": [{"name": "nginx", "image": image}]
                }
            }
        }
    }
    if extra_spec:
        obj["spec"].update(extra_spec)
    return obj


# ---------------------------------------------------------------------------
# _clean_path() tests
# ---------------------------------------------------------------------------

class TestCleanPath:
    def test_simple_nested_path(self):
        assert _clean_path("root['spec']['replicas']") == "spec.replicas"

    def test_deeply_nested_path(self):
        result = _clean_path("root['spec']['template']['spec']['containers'][0]['image']")
        assert "spec" in result
        assert "image" in result

    def test_removes_root_prefix(self):
        result = _clean_path("root['kind']")
        assert not result.startswith("root")


# ---------------------------------------------------------------------------
# diff_resource() — in sync
# ---------------------------------------------------------------------------

class TestDiffResourceInSync:
    def test_identical_objects_are_in_sync(self):
        obj = make_deployment()
        result = diff_resource(obj, obj)
        assert result.status == "in_sync"
        assert result.drifts == []

    def test_in_sync_result_has_correct_metadata(self):
        obj = make_deployment(name="nginx", namespace="production")
        result = diff_resource(obj, obj)
        assert result.kind == "Deployment"
        assert result.name == "nginx"
        assert result.namespace == "production"


# ---------------------------------------------------------------------------
# diff_resource() — value changes
# ---------------------------------------------------------------------------

class TestDiffResourceChanged:
    def test_detects_replica_change(self):
        git  = make_deployment(replicas=3)
        live = make_deployment(replicas=1)
        result = diff_resource(git, live)
        assert result.status == "drifted"
        paths = [d.field_path for d in result.drifts]
        assert any("replicas" in p for p in paths)

    def test_detects_image_change(self):
        git  = make_deployment(image="nginx:1.21")
        live = make_deployment(image="nginx:1.25")
        result = diff_resource(git, live)
        assert result.status == "drifted"
        paths = [d.field_path for d in result.drifts]
        assert any("image" in p for p in paths)

    def test_drift_item_has_correct_values(self):
        git  = make_deployment(replicas=3)
        live = make_deployment(replicas=1)
        result = diff_resource(git, live)
        replica_drift = next(d for d in result.drifts if "replicas" in d.field_path)
        assert replica_drift.git_value == 3
        assert replica_drift.live_value == 1
        assert replica_drift.change_type == "changed"

    def test_detects_multiple_changes(self):
        git  = make_deployment(replicas=3, image="nginx:1.21")
        live = make_deployment(replicas=1, image="nginx:1.25")
        result = diff_resource(git, live)
        assert len(result.drifts) >= 2

    def test_detects_added_field(self):
        git  = make_deployment()
        live = make_deployment()
        live["spec"]["minReadySeconds"] = 30
        result = diff_resource(git, live)
        assert result.status == "drifted"
        paths = [d.field_path for d in result.drifts]
        assert any("minReadySeconds" in p for p in paths)

    def test_detects_removed_field(self):
        git  = make_deployment()
        git["spec"]["minReadySeconds"] = 30
        live = make_deployment()
        result = diff_resource(git, live)
        assert result.status == "drifted"
        paths = [d.field_path for d in result.drifts]
        assert any("minReadySeconds" in p for p in paths)


# ---------------------------------------------------------------------------
# diff_missing_from_cluster() and diff_missing_from_git()
# ---------------------------------------------------------------------------

class TestMissingResources:
    def test_missing_from_cluster_status(self):
        git = make_deployment(name="ghost")
        result = diff_missing_from_cluster(git)
        assert result.status == "missing_from_cluster"
        assert result.name == "ghost"
        assert result.drifts == []

    def test_missing_from_git_status(self):
        live = make_deployment(name="orphan")
        result = diff_missing_from_git(live)
        assert result.status == "missing_from_git"
        assert result.name == "orphan"
        assert result.drifts == []

    def test_missing_from_cluster_preserves_namespace(self):
        git = make_deployment(namespace="production")
        result = diff_missing_from_cluster(git)
        assert result.namespace == "production"