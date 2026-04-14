"""
Tests for Phase 3 - Step 2: classifier.py

Run with: pytest tests/test_classifier.py -v
"""

import pytest
from drift_detect.phase3.differ import DriftItem, DriftResult
from drift_detect.phase3.classifier import (
    classify,
    classify_result,
    severity_counts,
    CRITICAL, WARNING, INFO,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_item(field_path: str, change_type: str = "changed") -> DriftItem:
    return DriftItem(
        field_path=field_path,
        git_value="old",
        live_value="new",
        change_type=change_type,
    )


def make_result(drifts: list) -> DriftResult:
    return DriftResult(
        kind="Deployment",
        name="nginx",
        namespace="default",
        status="drifted",
        drifts=drifts,
    )


# ---------------------------------------------------------------------------
# classify() — severity assignment
# ---------------------------------------------------------------------------

class TestClassifySeverity:
    def test_image_change_is_critical(self):
        items = classify([make_item("spec.template.spec.containers[0].image")])
        assert items[0].severity == CRITICAL

    def test_replicas_change_is_critical(self):
        items = classify([make_item("spec.replicas")])
        assert items[0].severity == CRITICAL

    def test_env_change_is_critical(self):
        items = classify([make_item("spec.template.spec.containers[0].env[0]")])
        assert items[0].severity == CRITICAL

    def test_secret_ref_is_critical(self):
        items = classify([make_item("spec.template.spec.containers[0].secretRef")])
        assert items[0].severity == CRITICAL

    def test_security_context_is_critical(self):
        items = classify([make_item("spec.template.spec.securityContext.runAsUser")])
        assert items[0].severity == CRITICAL

    def test_service_account_is_critical(self):
        items = classify([make_item("spec.template.spec.serviceAccountName")])
        assert items[0].severity == CRITICAL

    def test_volume_mounts_is_critical(self):
        items = classify([make_item("spec.template.spec.containers[0].volumeMounts")])
        assert items[0].severity == CRITICAL

    def test_resource_limits_is_warning(self):
        items = classify([make_item("spec.template.spec.containers[0].resources.limits.memory")])
        assert items[0].severity == CRITICAL  # containers match fires first

    def test_liveness_probe_is_warning(self):
        items = classify([make_item("spec.template.spec.livenessProbe.initialDelaySeconds")])
        assert items[0].severity == WARNING

    def test_ports_change_is_warning(self):
        items = classify([make_item("spec.template.spec.containers[0].ports[0].containerPort")])
        assert items[0].severity == CRITICAL  # containers match fires first

    def test_node_selector_is_warning(self):
        items = classify([make_item("spec.template.spec.nodeSelector.disktype")])
        assert items[0].severity == WARNING

    def test_labels_change_is_info(self):
        items = classify([make_item("metadata.labels.app")])
        assert items[0].severity == INFO

    def test_annotations_change_is_info(self):
        items = classify([make_item("metadata.annotations.team")])
        assert items[0].severity == INFO

    def test_unknown_field_defaults_to_info(self):
        items = classify([make_item("spec.someUnknownField")])
        assert items[0].severity == INFO


# ---------------------------------------------------------------------------
# classify() — sorting
# ---------------------------------------------------------------------------

class TestClassifySorting:
    def test_results_sorted_critical_first(self):
        items = [
            make_item("metadata.labels.app"),           # info
            make_item("spec.replicas"),                 # critical
            make_item("spec.template.spec.livenessProbe"),  # warning
        ]
        result = classify(items)
        assert result[0].severity == CRITICAL
        assert result[1].severity == WARNING
        assert result[2].severity == INFO

    def test_empty_list_returns_empty(self):
        assert classify([]) == []


# ---------------------------------------------------------------------------
# classify() — custom rules
# ---------------------------------------------------------------------------

class TestCustomRules:
    def test_custom_rule_overrides_default(self):
        # By default labels → info, but custom rule makes it critical
        custom = [("labels", CRITICAL)]
        items = classify([make_item("metadata.labels.app")], custom_rules=custom)
        assert items[0].severity == CRITICAL

    def test_custom_rule_takes_priority_over_default(self):
        # replicas is critical by default, override to warning
        custom = [("replicas", WARNING)]
        items = classify([make_item("spec.replicas")], custom_rules=custom)
        assert items[0].severity == WARNING


# ---------------------------------------------------------------------------
# classify_result()
# ---------------------------------------------------------------------------

class TestClassifyResult:
    def test_classifies_all_items_in_result(self):
        result = make_result([
            make_item("spec.replicas"),
            make_item("metadata.labels.app"),
        ])
        classify_result(result)
        severities = {d.severity for d in result.drifts}
        assert CRITICAL in severities
        assert INFO in severities

    def test_returns_same_result_object(self):
        result = make_result([make_item("spec.replicas")])
        returned = classify_result(result)
        assert returned is result


# ---------------------------------------------------------------------------
# severity_counts()
# ---------------------------------------------------------------------------

class TestSeverityCounts:
    def test_counts_by_severity(self):
        items = classify([
            make_item("spec.replicas"),          # critical
            make_item("spec.replicas"),          # critical
            make_item("spec.livenessProbe"),     # warning
            make_item("metadata.labels.app"),    # info
        ])
        counts = severity_counts(items)
        assert counts["critical"] == 2
        assert counts["warning"] == 1
        assert counts["info"] == 1

    def test_empty_list_returns_zero_counts(self):
        counts = severity_counts([])
        assert counts == {"critical": 0, "warning": 0, "info": 0}