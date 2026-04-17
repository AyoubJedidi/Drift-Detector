"""
Tests for Phase 4 - Part 2: printer.py

Run with: pytest tests/test_printer.py -v
"""

import json
import pytest
from io import StringIO
from unittest.mock import patch

from drift_detect.phase3.differ import DriftResult, DriftItem
from drift_detect.phase4.printer import print_results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_drift_item(field_path="spec.replicas", severity="critical",
                    git_value=3, live_value=1, change_type="changed"):
    return DriftItem(
        field_path=field_path,
        git_value=git_value,
        live_value=live_value,
        change_type=change_type,
        severity=severity,
    )


def make_result(status="in_sync", name="nginx", namespace="default", drifts=None):
    return DriftResult(
        kind="Deployment",
        name=name,
        namespace=namespace,
        status=status,
        drifts=drifts or [],
    )


def capture_output(results, **kwargs):
    """Helper to capture printed output as a string."""
    with patch("sys.stdout", new_callable=StringIO) as mock_out:
        print_results(results, **kwargs)
        return mock_out.getvalue()


# ---------------------------------------------------------------------------
# Human output tests
# ---------------------------------------------------------------------------

class TestHumanOutput:
    def test_in_sync_resource_shows_checkmark(self):
        output = capture_output([make_result(status="in_sync")], no_color=True)
        assert "✔" in output
        assert "nginx" in output

    def test_drifted_resource_shows_cross(self):
        output = capture_output(
            [make_result(status="drifted", drifts=[make_drift_item()])],
            no_color=True
        )
        assert "✖" in output
        assert "nginx" in output

    def test_missing_from_cluster_shows_message(self):
        output = capture_output(
            [make_result(status="missing_from_cluster")],
            no_color=True
        )
        assert "missing from cluster" in output

    def test_missing_from_git_shows_message(self):
        output = capture_output(
            [make_result(status="missing_from_git")],
            no_color=True
        )
        assert "not in Git" in output

    def test_drift_shows_field_path(self):
        output = capture_output(
            [make_result(status="drifted", drifts=[make_drift_item("spec.replicas")])],
            no_color=True
        )
        assert "spec.replicas" in output

    def test_drift_shows_git_and_live_values(self):
        output = capture_output(
            [make_result(status="drifted", drifts=[make_drift_item(git_value=3, live_value=1)])],
            no_color=True
        )
        assert "3" in output
        assert "1" in output

    def test_severity_label_shown(self):
        output = capture_output(
            [make_result(status="drifted", drifts=[make_drift_item(severity="critical")])],
            no_color=True
        )
        assert "CRITICAL" in output

    def test_warning_severity_shown(self):
        output = capture_output(
            [make_result(status="drifted", drifts=[make_drift_item(severity="warning")])],
            no_color=True
        )
        assert "WARNING" in output

    def test_namespace_shown(self):
        output = capture_output(
            [make_result(namespace="production")],
            no_color=True
        )
        assert "production" in output

    def test_summary_line_shown(self):
        output = capture_output(
            [make_result(status="in_sync")],
            no_color=True
        )
        assert "in sync" in output

    def test_added_change_type_shown(self):
        item = make_drift_item(change_type="added", git_value=None, live_value="new-value")
        output = capture_output(
            [make_result(status="drifted", drifts=[item])],
            no_color=True
        )
        assert "added in cluster" in output

    def test_removed_change_type_shown(self):
        item = make_drift_item(change_type="removed", git_value="old-value", live_value=None)
        output = capture_output(
            [make_result(status="drifted", drifts=[item])],
            no_color=True
        )
        assert "removed from cluster" in output


# ---------------------------------------------------------------------------
# Quiet mode
# ---------------------------------------------------------------------------

class TestQuietMode:
    def test_quiet_skips_resource_details(self):
        output = capture_output(
            [make_result(status="drifted", drifts=[make_drift_item()])],
            quiet=True,
            no_color=True
        )
        assert "spec.replicas" not in output

    def test_quiet_still_shows_summary(self):
        output = capture_output(
            [make_result(status="drifted", drifts=[make_drift_item()])],
            quiet=True,
            no_color=True
        )
        assert "critical" in output


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------

class TestJsonOutput:
    def test_json_output_is_valid_json(self):
        output = capture_output(
            [make_result(status="drifted", drifts=[make_drift_item()])],
            output_format="json"
        )
        parsed = json.loads(output)
        assert isinstance(parsed, list)

    def test_json_contains_resource_fields(self):
        output = capture_output(
            [make_result(status="drifted", drifts=[make_drift_item()])],
            output_format="json"
        )
        parsed = json.loads(output)
        assert parsed[0]["kind"] == "Deployment"
        assert parsed[0]["name"] == "nginx"
        assert parsed[0]["status"] == "drifted"

    def test_json_contains_drift_items(self):
        output = capture_output(
            [make_result(status="drifted", drifts=[make_drift_item("spec.replicas")])],
            output_format="json"
        )
        parsed = json.loads(output)
        assert len(parsed[0]["drifts"]) == 1
        assert parsed[0]["drifts"][0]["field_path"] == "spec.replicas"

    def test_json_empty_results(self):
        output = capture_output([], output_format="json")
        assert json.loads(output) == []