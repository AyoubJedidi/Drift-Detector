"""
Tests for Phase 1 - Step 2: detector.py

Run with: pytest tests/test_detector.py -v
"""

from pathlib import Path
import pytest

from drift_detect.phase1.detector import (
    detect_source_type,
    validate_source_type,
    resolve_source_type,
    HELM, KUSTOMIZE, RAW,
)


# ---------------------------------------------------------------------------
# Helpers — build fake directory structures in tmp_path
# ---------------------------------------------------------------------------

def make_helm_dir(base: Path) -> Path:
    """Create a minimal Helm chart structure."""
    (base / "Chart.yaml").write_text("apiVersion: v2\nname: my-chart")
    (base / "templates").mkdir()
    (base / "templates" / "deployment.yaml").write_text("kind: Deployment")
    return base


def make_kustomize_dir(base: Path) -> Path:
    """Create a minimal Kustomize structure."""
    (base / "kustomization.yaml").write_text("resources:\n  - deployment.yaml")
    (base / "deployment.yaml").write_text("kind: Deployment")
    return base


def make_kustomize_dir_yml(base: Path) -> Path:
    """Kustomize with .yml extension instead of .yaml."""
    (base / "kustomization.yml").write_text("resources:\n  - deployment.yaml")
    return base


def make_raw_dir(base: Path) -> Path:
    """Plain YAML files, no special marker."""
    (base / "deployment.yaml").write_text("kind: Deployment")
    (base / "service.yaml").write_text("kind: Service")
    return base


def make_helm_dir_yml(base: Path) -> Path:
    """Helm chart with Chart.yml instead of Chart.yaml."""
    (base / "Chart.yml").write_text("apiVersion: v2\nname: my-chart")
    return base


def make_nested_helm_dir(base: Path) -> Path:
    """Chart.yaml buried in a subdirectory."""
    sub = base / "charts" / "my-chart"
    sub.mkdir(parents=True)
    (sub / "Chart.yaml").write_text("apiVersion: v2\nname: my-chart")
    return base



    """Chart.yaml buried in a subdirectory."""
    sub = base / "charts" / "my-chart"
    sub.mkdir(parents=True)
    (sub / "Chart.yaml").write_text("apiVersion: v2\nname: my-chart")
    return base


# ---------------------------------------------------------------------------
# detect_source_type() tests
# ---------------------------------------------------------------------------

class TestDetectSourceType:
    def test_helm_detected_by_chart_yaml(self, tmp_path):
        make_helm_dir(tmp_path)
        assert detect_source_type(tmp_path) == HELM

    def test_helm_detected_by_chart_yml(self, tmp_path):
        make_helm_dir_yml(tmp_path)
        assert detect_source_type(tmp_path) == HELM

    def test_helm_detected_in_subdirectory(self, tmp_path):
        make_nested_helm_dir(tmp_path)
        assert detect_source_type(tmp_path) == HELM

    def test_kustomize_detected_by_kustomization_yaml(self, tmp_path):
        make_kustomize_dir(tmp_path)
        assert detect_source_type(tmp_path) == KUSTOMIZE

    def test_kustomize_detected_by_kustomization_yml(self, tmp_path):
        make_kustomize_dir_yml(tmp_path)
        assert detect_source_type(tmp_path) == KUSTOMIZE

    def test_raw_yaml_when_no_marker_found(self, tmp_path):
        make_raw_dir(tmp_path)
        assert detect_source_type(tmp_path) == RAW

    def test_empty_directory_returns_raw(self, tmp_path):
        assert detect_source_type(tmp_path) == RAW

    def test_nonexistent_directory_raises(self, tmp_path):
        with pytest.raises(ValueError, match="does not exist"):
            detect_source_type(tmp_path / "ghost")

    def test_file_instead_of_dir_raises(self, tmp_path):
        f = tmp_path / "file.yaml"
        f.write_text("kind: Deployment")
        with pytest.raises(ValueError, match="Not a directory"):
            detect_source_type(f)


# ---------------------------------------------------------------------------
# validate_source_type() tests
# ---------------------------------------------------------------------------

class TestValidateSourceType:
    def test_helm_is_valid(self):
        assert validate_source_type("helm") == HELM

    def test_kustomize_is_valid(self):
        assert validate_source_type("kustomize") == KUSTOMIZE

    def test_raw_is_valid(self):
        assert validate_source_type("raw") == RAW

    def test_uppercase_is_accepted(self):
        assert validate_source_type("HELM") == HELM

    def test_mixed_case_is_accepted(self):
        assert validate_source_type("Kustomize") == KUSTOMIZE

    def test_invalid_type_raises(self):
        with pytest.raises(ValueError, match="Invalid source type"):
            validate_source_type("flux")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="Invalid source type"):
            validate_source_type("")


# ---------------------------------------------------------------------------
# resolve_source_type() tests — the main entry point
# ---------------------------------------------------------------------------

class TestResolveSourceType:
    def test_auto_detects_helm(self, tmp_path):
        make_helm_dir(tmp_path)
        assert resolve_source_type(tmp_path) == HELM

    def test_auto_detects_raw(self, tmp_path):
        make_raw_dir(tmp_path)
        assert resolve_source_type(tmp_path) == RAW

    def test_override_takes_priority_over_auto(self, tmp_path):
        # Directory looks like Helm, but user says raw
        make_helm_dir(tmp_path)
        assert resolve_source_type(tmp_path, override="raw") == RAW

    def test_override_none_falls_back_to_auto(self, tmp_path):
        make_kustomize_dir(tmp_path)
        assert resolve_source_type(tmp_path, override=None) == KUSTOMIZE