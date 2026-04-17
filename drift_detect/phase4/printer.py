"""
Phase 4 - Part 2: Terminal Printer

Takes a List[DriftResult] from scanner.py and formats it for the terminal.
Supports two output modes:
  - human:  colored, grouped by namespace, readable
  - json:   machine-readable, for CI pipelines

No logic here — pure formatting only.
"""

import json
import sys
from typing import List

from drift_detect.phase3.differ import DriftResult, DriftItem
from drift_detect.phase4.scanner import summary_counts


# ---------------------------------------------------------------------------
# ANSI color codes
# ---------------------------------------------------------------------------

RESET  = "\033[0m"
BOLD   = "\033[1m"
RED    = "\033[91m"    # critical
YELLOW = "\033[93m"    # warning
BLUE   = "\033[94m"    # info
GREEN  = "\033[92m"    # in sync
DIM    = "\033[2m"     # subtle text


# ---------------------------------------------------------------------------
# Main entry points
# ---------------------------------------------------------------------------

def print_results(
    results: List[DriftResult],
    output_format: str = "human",
    quiet: bool = False,
    no_color: bool = False,
) -> None:
    """
    Print drift results to stdout.

    Args:
        results:       List of DriftResult from scanner.py
        output_format: "human" or "json"
        quiet:         If True, print only the summary line
        no_color:      If True, disable ANSI colors
    """
    if output_format == "json":
        _print_json(results)
        return

    _print_human(results, quiet=quiet, no_color=no_color)


# ---------------------------------------------------------------------------
# Human-readable output
# ---------------------------------------------------------------------------

def _print_human(results: List[DriftResult], quiet: bool, no_color: bool) -> None:
    """Print colored, grouped terminal output."""

    def c(color, text):
        """Apply color if colors are enabled."""
        if no_color:
            return text
        return f"{color}{text}{RESET}"

    if not quiet:
        # Group results by namespace
        namespaces = {}
        for result in results:
            ns = result.namespace or "default"
            namespaces.setdefault(ns, []).append(result)

        for namespace, ns_results in sorted(namespaces.items()):
            print(c(BOLD, f"\nNamespace: {namespace}"))
            print(c(DIM, "─" * 50))

            for result in ns_results:
                _print_single_result(result, c)

    # Always print summary
    _print_summary(results, c if not no_color else lambda _, t: t)


def _print_single_result(result: DriftResult, c) -> None:
    """Print one resource's drift status."""

    resource_label = f"{result.kind}/{result.name}"

    if result.status == "in_sync":
        print(f"  {c(GREEN, '✔')} {resource_label}")
        return

    if result.status == "missing_from_cluster":
        print(f"  {c(RED, '✖')} {resource_label} {c(RED, '— missing from cluster')}")
        return

    if result.status == "missing_from_git":
        print(f"  {c(YELLOW, '?')} {resource_label} {c(YELLOW, '— running but not in Git')}")
        return

    if result.status == "drifted":
        # Group drift items by severity
        critical = [d for d in result.drifts if d.severity == "critical"]
        warning  = [d for d in result.drifts if d.severity == "warning"]
        info     = [d for d in result.drifts if d.severity == "info"]

        print(f"  {c(RED, '✖')} {c(BOLD, resource_label)}")

        for drift in critical:
            _print_drift_item(drift, c(RED, "CRITICAL"), c)

        for drift in warning:
            _print_drift_item(drift, c(YELLOW, "WARNING"), c)

        for drift in info:
            _print_drift_item(drift, c(BLUE, "INFO"), c)


def _print_drift_item(drift: DriftItem, severity_label: str, c) -> None:
    """Print a single field-level drift."""
    if drift.change_type == "changed":
        print(
            f"      [{severity_label}] {drift.field_path}\n"
            f"        {c(DIM, 'git:')}  {drift.git_value}\n"
            f"        {c(DIM, 'live:')} {drift.live_value}"
        )
    elif drift.change_type == "added":
        print(
            f"      [{severity_label}] {drift.field_path}\n"
            f"        {c(DIM, 'added in cluster:')} {drift.live_value}"
        )
    elif drift.change_type == "removed":
        print(
            f"      [{severity_label}] {drift.field_path}\n"
            f"        {c(DIM, 'removed from cluster:')} {drift.git_value}"
        )


def _print_summary(results: List[DriftResult], c) -> None:
    """Print the summary line at the bottom."""
    counts = summary_counts(results)

    print(c(DIM, "\n" + "─" * 50))

    # Severity counts
    critical_str = c(RED,    f"{counts['critical']} critical")
    warning_str  = c(YELLOW, f"{counts['warning']} warning")
    info_str     = c(BLUE,   f"{counts['info']} info")
    print(f"  {critical_str}  {warning_str}  {info_str}")

    # Resource counts
    total   = counts["total"]
    drifted = counts["drifted"] + counts["missing_from_cluster"] + counts["missing_from_git"]
    in_sync = counts["in_sync"]

    if drifted > 0:
        print(f"  {c(RED, f'{drifted} drifted')}  {c(GREEN, f'{in_sync} in sync')}  ({total} total)")
    else:
        print(f"  {c(GREEN, f'All {total} resources in sync')}")


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------

def _print_json(results: List[DriftResult]) -> None:
    """Print results as a JSON array to stdout."""
    output = []
    for result in results:
        output.append({
            "kind":      result.kind,
            "name":      result.name,
            "namespace": result.namespace,
            "status":    result.status,
            "drifts": [
                {
                    "field_path":  d.field_path,
                    "git_value":   str(d.git_value),
                    "live_value":  str(d.live_value),
                    "change_type": d.change_type,
                    "severity":    d.severity,
                }
                for d in result.drifts
            ],
        })
    print(json.dumps(output, indent=2))