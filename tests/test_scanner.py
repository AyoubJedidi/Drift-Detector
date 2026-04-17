"""
Tests for Phase 4 - Part 1: scanner.py

All external calls (cluster, git) are mocked.
Run with: pytest tests/test_scanner.py -v
"""

from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from drift_detect.phase4.scanner import scan, has_drift_above, summary_counts
from drift_detect.phase3.differ import DriftResult, DriftItem


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_manifest(kind="Deployment", name="nginx", namespace="default"):
    return {
        "apiVersion": "apps/v1",
        "kind": kind,
        "metadata": {"name": name, "namespace": namespace},
        "spec": {"replicas": 3},
    }


def make_drift_result(status="in_sync", name="nginx", drifts=None):
    return DriftResult(
        kind="Deployment",
        name=name,
        namespace="default",
        status=status,
        drifts=drifts or [],
    )


def make_drift_item(severity="critical"):
    return DriftItem(
        field_path="spec.replicas",
        git_value=3,
        live_value=1,
        change_type="changed",
        severity=severity,
    )


# ---------------------------------------------------------------------------
# scan() tests — full pipeline mocked
# ---------------------------------------------------------------------------

class TestScan:
    def _mock_pipeline(
        self,
        mock_resolve,
        mock_detect,
        mock_render,
        mock_refs,
        mock_fetch,
        mock_diff,
        manifests=None,
        fetch_status="found",
        live_object=None,
    ):
        mock_resolve.return_value = Path("/tmp/fake-repo")
        mock_detect.return_value = "raw"
        mock_render.return_value = manifests or [make_manifest()]
        mock_refs.return_value = [MagicMock(
            kind="Deployment", name="nginx",
            namespace="default", api_version="apps/v1"
        )]

        fetch_result = MagicMock()
        fetch_result.status = fetch_status
        fetch_result.live_object = live_object or make_manifest()
        fetch_result.ref = MagicMock(kind="Deployment", name="nginx", namespace="default")
        mock_fetch.return_value = [fetch_result]

    @patch("drift_detect.phase4.scanner.classify_result")
    @patch("drift_detect.phase4.scanner.diff_resource")
    @patch("drift_detect.phase4.scanner.normalize_pair")
    @patch("drift_detect.phase4.scanner.fetch_live_resources")
    @patch("drift_detect.phase4.scanner.build_refs_from_manifests")
    @patch("drift_detect.phase4.scanner.render")
    @patch("drift_detect.phase4.scanner.resolve_source_type")
    @patch("drift_detect.phase4.scanner.resolve_repo")
    def test_scan_returns_list_of_drift_results(
        self, mock_resolve, mock_detect, mock_render,
        mock_refs, mock_fetch, mock_norm, mock_diff, mock_classify
    ):
        self._mock_pipeline(mock_resolve, mock_detect, mock_render, mock_refs, mock_fetch, mock_diff)
        mock_norm.return_value = (make_manifest(), make_manifest())
        mock_diff.return_value = make_drift_result()

        results = scan("./fake")
        assert isinstance(results, list)
        assert len(results) == 1

    @patch("drift_detect.phase4.scanner.classify_result")
    @patch("drift_detect.phase4.scanner.diff_resource")
    @patch("drift_detect.phase4.scanner.normalize_pair")
    @patch("drift_detect.phase4.scanner.fetch_live_resources")
    @patch("drift_detect.phase4.scanner.build_refs_from_manifests")
    @patch("drift_detect.phase4.scanner.render")
    @patch("drift_detect.phase4.scanner.resolve_source_type")
    @patch("drift_detect.phase4.scanner.resolve_repo")
    def test_scan_empty_manifests_returns_empty(
        self, mock_resolve, mock_detect, mock_render,
        mock_refs, mock_fetch, mock_norm, mock_diff, mock_classify
    ):
        mock_resolve.return_value = Path("/tmp/fake")
        mock_detect.return_value = "raw"
        mock_render.return_value = []

        results = scan("./fake")
        assert results == []

    @patch("drift_detect.phase4.scanner.classify_result")
    @patch("drift_detect.phase4.scanner.diff_missing_from_cluster")
    @patch("drift_detect.phase4.scanner.fetch_live_resources")
    @patch("drift_detect.phase4.scanner.build_refs_from_manifests")
    @patch("drift_detect.phase4.scanner.render")
    @patch("drift_detect.phase4.scanner.resolve_source_type")
    @patch("drift_detect.phase4.scanner.resolve_repo")
    def test_missing_from_cluster_handled(
        self, mock_resolve, mock_detect, mock_render,
        mock_refs, mock_fetch, mock_missing, mock_classify
    ):
        mock_resolve.return_value = Path("/tmp/fake")
        mock_detect.return_value = "raw"
        mock_render.return_value = [make_manifest()]
        mock_refs.return_value = [MagicMock(kind="Deployment", name="nginx", namespace="default")]

        fetch_result = MagicMock()
        fetch_result.status = "missing_from_cluster"
        fetch_result.ref = MagicMock(kind="Deployment", name="nginx", namespace="default")
        mock_fetch.return_value = [fetch_result]
        mock_missing.return_value = make_drift_result(status="missing_from_cluster")

        results = scan("./fake")
        assert results[0].status == "missing_from_cluster"

    @patch("drift_detect.phase4.scanner.classify_result")
    @patch("drift_detect.phase4.scanner.diff_resource")
    @patch("drift_detect.phase4.scanner.normalize_pair")
    @patch("drift_detect.phase4.scanner.fetch_live_resources")
    @patch("drift_detect.phase4.scanner.build_refs_from_manifests")
    @patch("drift_detect.phase4.scanner.render")
    @patch("drift_detect.phase4.scanner.resolve_source_type")
    @patch("drift_detect.phase4.scanner.resolve_repo")
    def test_namespace_filter_applied(
        self, mock_resolve, mock_detect, mock_render,
        mock_refs, mock_fetch, mock_norm, mock_diff, mock_classify
    ):
        mock_resolve.return_value = Path("/tmp/fake")
        mock_detect.return_value = "raw"
        mock_render.return_value = [
            make_manifest(namespace="default"),
            make_manifest(name="other", namespace="production"),
        ]
        mock_refs.return_value = []
        mock_fetch.return_value = []

        results = scan("./fake", namespace_filter="production")
        # Only the production resource passed to refs
        called_with = mock_refs.call_args[0][0]
        assert all(m["metadata"]["namespace"] == "production" for m in called_with)


# ---------------------------------------------------------------------------
# has_drift_above() tests
# ---------------------------------------------------------------------------

class TestHasDriftAbove:
    def test_critical_drift_triggers_critical_threshold(self):
        results = [make_drift_result(status="drifted", drifts=[make_drift_item("critical")])]
        assert has_drift_above(results, "critical") is True

    def test_warning_drift_does_not_trigger_critical_threshold(self):
        results = [make_drift_result(status="drifted", drifts=[make_drift_item("warning")])]
        assert has_drift_above(results, "critical") is False

    def test_warning_drift_triggers_warning_threshold(self):
        results = [make_drift_result(status="drifted", drifts=[make_drift_item("warning")])]
        assert has_drift_above(results, "warning") is True

    def test_in_sync_never_triggers(self):
        results = [make_drift_result(status="in_sync")]
        assert has_drift_above(results, "info") is False

    def test_empty_results_returns_false(self):
        assert has_drift_above([], "critical") is False


# ---------------------------------------------------------------------------
# summary_counts() tests
# ---------------------------------------------------------------------------

class TestSummaryCounts:
    def test_counts_in_sync(self):
        results = [make_drift_result(status="in_sync")]
        counts = summary_counts(results)
        assert counts["in_sync"] == 1
        assert counts["total"] == 1

    def test_counts_critical_drifts(self):
        results = [make_drift_result(
            status="drifted",
            drifts=[make_drift_item("critical"), make_drift_item("critical")]
        )]
        counts = summary_counts(results)
        assert counts["critical"] == 2
        assert counts["drifted"] == 1

    def test_counts_missing_from_cluster(self):
        results = [make_drift_result(status="missing_from_cluster")]
        counts = summary_counts(results)
        assert counts["missing_from_cluster"] == 1

    def test_empty_results(self):
        counts = summary_counts([])
        assert counts["total"] == 0
        assert counts["critical"] == 0