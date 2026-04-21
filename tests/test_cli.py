"""
Tests for Phase 4 - Part 3: cli.py

Uses Click's test runner — no real cluster or git needed.
Run with: pytest tests/test_cli.py -v
"""

import json
from unittest.mock import patch, MagicMock
import pytest
from click.testing import CliRunner

from drift_detect.cli import cli
from drift_detect.phase3.differ import DriftResult, DriftItem


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_result(status="in_sync", name="nginx", drifts=None):
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


runner = CliRunner()


# ---------------------------------------------------------------------------
# Basic invocation
# ---------------------------------------------------------------------------

class TestScanCommand:
    @patch("drift_detect.cli.scan")
    def test_scan_with_local_path(self, mock_scan, tmp_path):
        mock_scan.return_value = [make_result()]
        result = runner.invoke(cli, ["scan", str(tmp_path)])
        assert result.exit_code == 0

    @patch("drift_detect.cli.scan")
    def test_scan_with_remote_url(self, mock_scan):
        mock_scan.return_value = [make_result()]
        result = runner.invoke(cli, ["scan", "https://github.com/org/repo"])
        assert result.exit_code == 0

    @patch("drift_detect.cli.scan")
    def test_scan_shows_scanning_message(self, mock_scan, tmp_path):
        mock_scan.return_value = []
        result = runner.invoke(cli, ["scan", str(tmp_path)])
        assert "Scanning" in result.output

    @patch("drift_detect.cli.scan")
    def test_quiet_flag_suppresses_scanning_message(self, mock_scan, tmp_path):
        mock_scan.return_value = []
        result = runner.invoke(cli, ["scan", str(tmp_path), "--quiet"])
        assert "Scanning" not in result.output


# ---------------------------------------------------------------------------
# Flags passed correctly to scan()
# ---------------------------------------------------------------------------

class TestFlagsPassedToScan:
    @patch("drift_detect.cli.scan")
    def test_branch_flag_passed(self, mock_scan, tmp_path):
        mock_scan.return_value = []
        runner.invoke(cli, ["scan", str(tmp_path), "--branch", "main"])
        _, kwargs = mock_scan.call_args
        assert kwargs["branch"] == "main"

    @patch("drift_detect.cli.scan")
    def test_tag_flag_passed(self, mock_scan, tmp_path):
        mock_scan.return_value = []
        runner.invoke(cli, ["scan", str(tmp_path), "--tag", "v1.0"])
        _, kwargs = mock_scan.call_args
        assert kwargs["tag"] == "v1.0"

    @patch("drift_detect.cli.scan")
    def test_namespace_flag_passed(self, mock_scan, tmp_path):
        mock_scan.return_value = []
        runner.invoke(cli, ["scan", str(tmp_path), "--namespace", "production"])
        _, kwargs = mock_scan.call_args
        assert kwargs["namespace_filter"] == "production"

    @patch("drift_detect.cli.scan")
    def test_kind_flag_passed(self, mock_scan, tmp_path):
        mock_scan.return_value = []
        runner.invoke(cli, ["scan", str(tmp_path), "--kind", "Deployment"])
        _, kwargs = mock_scan.call_args
        assert kwargs["kind_filter"] == "Deployment"

    @patch("drift_detect.cli.scan")
    def test_source_type_flag_passed(self, mock_scan, tmp_path):
        mock_scan.return_value = []
        runner.invoke(cli, ["scan", str(tmp_path), "--source-type", "raw"])
        _, kwargs = mock_scan.call_args
        assert kwargs["source_type_override"] == "raw"


# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------

class TestExitCodes:
    @patch("drift_detect.cli.scan")
    def test_no_drift_exits_zero(self, mock_scan, tmp_path):
        mock_scan.return_value = [make_result(status="in_sync")]
        result = runner.invoke(cli, ["scan", str(tmp_path)])
        assert result.exit_code == 0

    @patch("drift_detect.cli.scan")
    def test_critical_drift_exits_one_with_fail_on_critical(self, mock_scan, tmp_path):
        mock_scan.return_value = [
            make_result(status="drifted", drifts=[make_drift_item("critical")])
        ]
        result = runner.invoke(cli, ["scan", str(tmp_path), "--fail-on", "critical"])
        assert result.exit_code == 1

    @patch("drift_detect.cli.scan")
    def test_warning_drift_exits_zero_with_fail_on_critical(self, mock_scan, tmp_path):
        mock_scan.return_value = [
            make_result(status="drifted", drifts=[make_drift_item("warning")])
        ]
        result = runner.invoke(cli, ["scan", str(tmp_path), "--fail-on", "critical"])
        assert result.exit_code == 0

    @patch("drift_detect.cli.scan")
    def test_value_error_exits_two(self, mock_scan, tmp_path):
        mock_scan.side_effect = ValueError("bad input")
        result = runner.invoke(cli, ["scan", str(tmp_path)])
        assert result.exit_code == 2

    @patch("drift_detect.cli.scan")
    def test_runtime_error_exits_two(self, mock_scan, tmp_path):
        mock_scan.side_effect = RuntimeError("cluster unreachable")
        result = runner.invoke(cli, ["scan", str(tmp_path)])
        assert result.exit_code == 2


# ---------------------------------------------------------------------------
# Output format
# ---------------------------------------------------------------------------

class TestOutputFormat:
    @patch("drift_detect.cli.scan")
    def test_json_output_is_valid(self, mock_scan, tmp_path):
        mock_scan.return_value = [
            make_result(status="drifted", drifts=[make_drift_item()])
        ]
        result = runner.invoke(cli, ["scan", str(tmp_path), "--output", "json"])
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)

    @patch("drift_detect.cli.scan")
    def test_help_flag_works(self, mock_scan):
        result = runner.invoke(cli, ["scan", "--help"])
        assert result.exit_code == 0
        assert "SOURCE" in result.output