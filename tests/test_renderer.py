"""
Tests for Phase 1 - Step 3: renderer.py

Run with: pytest tests/test_renderer.py -v
"""

from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from drift_detect.phase1.renderer import (
    render,
    _parse_yaml_string,
    _render_raw,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_DEPLOYMENT = """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: nginx
  namespace: default
spec:
  replicas: 3
"""

VALID_SERVICE = """
apiVersion: v1
kind: Service
metadata:
  name: nginx-svc
  namespace: default
"""

MULTI_DOC_YAML = f"{VALID_DEPLOYMENT}\n---\n{VALID_SERVICE}"

INVALID_YAML = "key: [unclosed bracket"

NOT_K8S_YAML = """
name: just-a-config
value: 123
"""


# ---------------------------------------------------------------------------
# _parse_yaml_string() tests — core parsing logic
# ---------------------------------------------------------------------------

class TestParseYamlString:
    def test_parses_single_resource(self):
        result = _parse_yaml_string(VALID_DEPLOYMENT)
        assert len(result) == 1
        assert result[0]["kind"] == "Deployment"

    def test_parses_multi_document_yaml(self):
        result = _parse_yaml_string(MULTI_DOC_YAML)
        assert len(result) == 2
        kinds = {r["kind"] for r in result}
        assert kinds == {"Deployment", "Service"}

    def test_skips_empty_documents(self):
        yaml_with_empty = f"{VALID_DEPLOYMENT}\n---\n\n---\n{VALID_SERVICE}"
        result = _parse_yaml_string(yaml_with_empty)
        assert len(result) == 2

    def test_skips_non_k8s_documents(self):
        mixed = f"{VALID_DEPLOYMENT}\n---\n{NOT_K8S_YAML}"
        result = _parse_yaml_string(mixed)
        assert len(result) == 1
        assert result[0]["kind"] == "Deployment"

    def test_empty_string_returns_empty_list(self):
        assert _parse_yaml_string("") == []

    def test_whitespace_only_returns_empty_list(self):
        assert _parse_yaml_string("   \n\n  ") == []


# ---------------------------------------------------------------------------
# _render_raw() tests — filesystem reading
# ---------------------------------------------------------------------------

class TestRenderRaw:
    def test_reads_single_yaml_file(self, tmp_path):
        (tmp_path / "deployment.yaml").write_text(VALID_DEPLOYMENT)
        result = _render_raw(tmp_path)
        assert "Deployment" in result

    def test_reads_multiple_yaml_files(self, tmp_path):
        (tmp_path / "deployment.yaml").write_text(VALID_DEPLOYMENT)
        (tmp_path / "service.yaml").write_text(VALID_SERVICE)
        result = _render_raw(tmp_path)
        assert "Deployment" in result
        assert "Service" in result

    def test_reads_yml_extension(self, tmp_path):
        (tmp_path / "deployment.yml").write_text(VALID_DEPLOYMENT)
        result = _render_raw(tmp_path)
        assert "Deployment" in result

    def test_empty_directory_returns_empty_string(self, tmp_path):
        result = _render_raw(tmp_path)
        assert result == ""

    def test_ignores_non_yaml_files(self, tmp_path):
        (tmp_path / "README.md").write_text("# readme")
        (tmp_path / "config.json").write_text("{}")
        result = _render_raw(tmp_path)
        assert result == ""

    def test_reads_nested_yaml_files(self, tmp_path):
        sub = tmp_path / "subdir"
        sub.mkdir()
        (sub / "deployment.yaml").write_text(VALID_DEPLOYMENT)
        result = _render_raw(tmp_path)
        assert "Deployment" in result


# ---------------------------------------------------------------------------
# render() integration tests — full pipeline per source type
# ---------------------------------------------------------------------------

class TestRender:
    def test_render_raw_returns_k8s_objects(self, tmp_path):
        (tmp_path / "deployment.yaml").write_text(VALID_DEPLOYMENT)
        result = render(tmp_path, "raw")
        assert len(result) == 1
        assert result[0]["kind"] == "Deployment"

    def test_render_raw_empty_dir_returns_empty_list(self, tmp_path):
        result = render(tmp_path, "raw")
        assert result == []

    def test_render_unknown_source_type_raises(self, tmp_path):
        with pytest.raises(ValueError, match="Unknown source type"):
            render(tmp_path, "flux")

    @patch("drift_detect.phase1.renderer._check_binary")
    @patch("drift_detect.phase1.renderer._run_subprocess")
    def test_render_helm_calls_helm_template(self, mock_run, mock_check, tmp_path):
        mock_run.return_value = VALID_DEPLOYMENT
        result = render(tmp_path, "helm")
        assert mock_run.called
        cmd = mock_run.call_args[0][0]
        assert "helm" in cmd
        assert "template" in cmd
        assert len(result) == 1

    @patch("drift_detect.phase1.renderer._check_binary")
    @patch("drift_detect.phase1.renderer._run_subprocess")
    def test_render_helm_passes_values_files(self, mock_run, mock_check, tmp_path):
        mock_run.return_value = VALID_DEPLOYMENT
        values_file = tmp_path / "values.yaml"
        values_file.write_text("replicas: 2")
        render(tmp_path, "helm", helm_values=[values_file])
        cmd = mock_run.call_args[0][0]
        assert "--values" in cmd
        assert str(values_file) in cmd

    @patch("drift_detect.phase1.renderer._check_binary")
    @patch("drift_detect.phase1.renderer._run_subprocess")
    def test_render_kustomize_calls_kustomize_build(self, mock_run, mock_check, tmp_path):
        mock_run.return_value = VALID_DEPLOYMENT
        result = render(tmp_path, "kustomize")
        assert mock_run.called
        cmd = mock_run.call_args[0][0]
        assert "kustomize" in cmd
        assert "build" in cmd
        assert len(result) == 1

    @patch("drift_detect.phase1.renderer._check_binary")
    @patch("drift_detect.phase1.renderer._run_subprocess")
    def test_render_helm_failure_raises_runtime_error(self, mock_run, mock_check, tmp_path):
        mock_run.side_effect = RuntimeError("helm failed")
        with pytest.raises(RuntimeError, match="helm failed"):
            render(tmp_path, "helm")