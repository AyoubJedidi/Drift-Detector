"""
Tests for Phase 3 - driftignore.py

Run with: pytest tests/test_driftignore.py -v
"""

import pytest
from pathlib import Path
from drift_detect.phase3.differ import DriftItem, DriftResult
from drift_detect.phase3.driftignore import (
    load_driftignore,
    apply_ignore_rules,
    DriftIgnoreRules,
    _wildcard_match,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_item(field_path="spec.replicas", severity="critical"):
    return DriftItem(
        field_path=field_path,
        git_value="old",
        live_value="new",
        change_type="changed",
        severity=severity,
    )


def make_result(
    kind="Deployment", name="nginx", namespace="default",
    status="drifted", drifts=None
):
    return DriftResult(
        kind=kind, name=name, namespace=namespace,
        status=status, drifts=drifts or [],
    )


def write_driftignore(tmp_path: Path, content: str) -> Path:
    f = tmp_path / ".driftignore"
    f.write_text(content)
    return f


# ---------------------------------------------------------------------------
# _wildcard_match()
# ---------------------------------------------------------------------------

class TestWildcardMatch:
    def test_exact_match(self):
        assert _wildcard_match("spec.replicas", "spec.replicas")

    def test_wildcard_suffix(self):
        assert _wildcard_match("argocd.argoproj.io/sync-wave", "argocd.argoproj.io/*")

    def test_wildcard_prefix(self):
        assert _wildcard_match("metadata.annotations.team", "metadata.annotations.*")

    def test_no_match(self):
        assert not _wildcard_match("spec.replicas", "metadata.labels.*")

    def test_full_wildcard(self):
        assert _wildcard_match("anything.at.all", "*")


# ---------------------------------------------------------------------------
# load_driftignore()
# ---------------------------------------------------------------------------

class TestLoadDriftignore:
    def test_missing_file_returns_empty_rules(self, tmp_path):
        rules = load_driftignore(tmp_path / ".driftignore")
        assert rules.is_empty()

    def test_loads_ignore_fields(self, tmp_path):
        f = write_driftignore(tmp_path, """
ignore_fields:
  - metadata.annotations.argocd.argoproj.io/*
  - spec.template.metadata.annotations
""")
        rules = load_driftignore(f)
        assert len(rules.ignore_fields) == 2

    def test_loads_ignore_resources(self, tmp_path):
        f = write_driftignore(tmp_path, """
ignore_resources:
  - kind: Job
    name: db-migration
    namespace: default
""")
        rules = load_driftignore(f)
        assert len(rules.ignore_resources) == 1
        assert rules.ignore_resources[0]["kind"] == "Job"

    def test_loads_ignore_annotations(self, tmp_path):
        f = write_driftignore(tmp_path, """
ignore_annotations:
  - argocd.argoproj.io/*
""")
        rules = load_driftignore(f)
        assert "argocd.argoproj.io/*" in rules.ignore_annotations

    def test_loads_ignore_labels(self, tmp_path):
        f = write_driftignore(tmp_path, """
ignore_labels:
  - operator.io/*
""")
        rules = load_driftignore(f)
        assert "operator.io/*" in rules.ignore_labels

    def test_invalid_yaml_returns_empty(self, tmp_path):
        f = tmp_path / ".driftignore"
        f.write_text("key: [unclosed bracket")
        rules = load_driftignore(f)
        assert rules.is_empty()


# ---------------------------------------------------------------------------
# apply_ignore_rules() — resource level
# ---------------------------------------------------------------------------

class TestIgnoreResources:
    def test_ignores_exact_resource(self):
        rules = DriftIgnoreRules(ignore_resources=[
            {"kind": "Job", "name": "db-migration", "namespace": "default"}
        ])
        results = [make_result(kind="Job", name="db-migration")]
        filtered = apply_ignore_rules(results, rules)
        assert filtered == []

    def test_does_not_ignore_different_resource(self):
        rules = DriftIgnoreRules(ignore_resources=[
            {"kind": "Job", "name": "db-migration", "namespace": "default"}
        ])
        results = [make_result(kind="Deployment", name="nginx")]
        filtered = apply_ignore_rules(results, rules)
        assert len(filtered) == 1

    def test_ignores_resource_with_wildcard_name(self):
        rules = DriftIgnoreRules(ignore_resources=[
            {"kind": "Job", "name": "*", "namespace": "default"}
        ])
        results = [make_result(kind="Job", name="any-job")]
        filtered = apply_ignore_rules(results, rules)
        assert filtered == []

    def test_ignores_all_resources_of_kind(self):
        rules = DriftIgnoreRules(ignore_resources=[
            {"kind": "Job", "name": "*", "namespace": "*"}
        ])
        results = [
            make_result(kind="Job", name="job-1", namespace="default"),
            make_result(kind="Job", name="job-2", namespace="production"),
        ]
        filtered = apply_ignore_rules(results, rules)
        assert filtered == []


# ---------------------------------------------------------------------------
# apply_ignore_rules() — field level
# ---------------------------------------------------------------------------

class TestIgnoreFields:
    def test_ignores_exact_field(self):
        rules = DriftIgnoreRules(ignore_fields=["spec.replicas"])
        results = [make_result(drifts=[make_item("spec.replicas")])]
        filtered = apply_ignore_rules(results, rules)
        assert filtered[0].status == "in_sync"
        assert filtered[0].drifts == []

    def test_ignores_wildcard_field(self):
        rules = DriftIgnoreRules(ignore_fields=["metadata.annotations.*"])
        results = [make_result(drifts=[make_item("metadata.annotations.team")])]
        filtered = apply_ignore_rules(results, rules)
        assert filtered[0].status == "in_sync"

    def test_keeps_non_ignored_fields(self):
        rules = DriftIgnoreRules(ignore_fields=["metadata.annotations.*"])
        results = [make_result(drifts=[
            make_item("metadata.annotations.team"),
            make_item("spec.replicas"),
        ])]
        filtered = apply_ignore_rules(results, rules)
        assert filtered[0].status == "drifted"
        assert len(filtered[0].drifts) == 1
        assert filtered[0].drifts[0].field_path == "spec.replicas"

    def test_ignores_annotation_pattern(self):
        rules = DriftIgnoreRules(ignore_annotations=["argocd.argoproj.io/*"])
        results = [make_result(drifts=[
            make_item("metadata.annotations.argocd.argoproj.io/sync-wave")
        ])]
        filtered = apply_ignore_rules(results, rules)
        assert filtered[0].status == "in_sync"

    def test_ignores_label_pattern(self):
        rules = DriftIgnoreRules(ignore_labels=["operator.io/*"])
        results = [make_result(drifts=[
            make_item("metadata.labels.operator.io/managed")
        ])]
        filtered = apply_ignore_rules(results, rules)
        assert filtered[0].status == "in_sync"

    def test_empty_rules_changes_nothing(self):
        rules = DriftIgnoreRules()
        results = [make_result(drifts=[make_item("spec.replicas")])]
        filtered = apply_ignore_rules(results, rules)
        assert filtered[0].status == "drifted"
        assert len(filtered[0].drifts) == 1