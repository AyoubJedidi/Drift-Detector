import json
from typing import List

from drift_detect.phase3.differ      import DriftResult, DriftItem
from drift_detect.phase4.scanner     import ScanResult, summary_counts
from drift_detect.phase6.snapshot    import Delta, SnapshotEntry


# ---------------------------------------------------------------------------
# ANSI color codes
# ---------------------------------------------------------------------------

RESET  = "\033[0m"
BOLD   = "\033[1m"
RED    = "\033[91m"
YELLOW = "\033[93m"
BLUE   = "\033[94m"
GREEN  = "\033[92m"
DIM    = "\033[2m"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def print_results(
    scan_result:   ScanResult,
    output_format: str = "human",
    quiet:         bool = False,
    no_color:      bool = False,
) -> None:
    """
    Print scan results to stdout.

    Args:
        scan_result:   ScanResult from scanner.scan()
        output_format: "human" or "json"
        quiet:         If True, print only the summary line
        no_color:      If True, disable ANSI colors
    """
    if output_format == "json":
        _print_json(scan_result)
        return

    _print_human(scan_result, quiet=quiet, no_color=no_color)


# ---------------------------------------------------------------------------
# Human-readable output
# ---------------------------------------------------------------------------

def _print_human(scan_result: ScanResult, quiet: bool, no_color: bool) -> None:
    """Print colored, grouped terminal output."""

    def c(color, text):
        return text if no_color else f"{color}{text}{RESET}"

    results = scan_result.results

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
    _print_summary(scan_result, c)

    # Delta section (only if snapshots were enabled and there's something to show)
    if scan_result.delta is not None:
        _print_delta(scan_result.delta, c)


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


def _print_summary(scan_result: ScanResult, c) -> None:
    """Print the summary line at the bottom."""
    counts = summary_counts(scan_result)

    print(c(DIM, "\n" + "─" * 50))

    critical_str = c(RED,    f"{counts['critical']} critical")
    warning_str  = c(YELLOW, f"{counts['warning']} warning")
    info_str     = c(BLUE,   f"{counts['info']} info")
    print(f"  {critical_str}  {warning_str}  {info_str}")

    total   = counts["total"]
    drifted = counts["drifted"] + counts["missing_from_cluster"] + counts["missing_from_git"]
    in_sync = counts["in_sync"]

    if drifted > 0:
        print(f"  {c(RED, f'{drifted} drifted')}  {c(GREEN, f'{in_sync} in sync')}  ({total} total)")
    else:
        print(f"  {c(GREEN, f'All {total} resources in sync')}")


def _print_delta(delta: Delta, c) -> None:
    """Print the delta section comparing this scan to the previous snapshot."""
    print(c(DIM, "\nΔ Since last scan"))
    print(c(DIM, "─" * 50))

    if delta.is_initial():
        print(c(DIM, "  (initial scan — no prior snapshot to compare against)"))
        return

    if delta.is_empty():
        print(c(DIM, f"  No change since {delta.previous_timestamp}"))
        return

    print(c(DIM, f"  Compared to scan at {delta.previous_timestamp}"))

    if delta.new:
        print(f"  {c(RED, f'+ {len(delta.new)} new')}:")
        for entry in delta.new:
            _print_delta_entry(entry, "+", RED, c)

    if delta.resolved:
        print(f"  {c(GREEN, f'- {len(delta.resolved)} resolved')}:")
        for entry in delta.resolved:
            _print_delta_entry(entry, "-", GREEN, c)


def _print_delta_entry(entry: SnapshotEntry, prefix: str, color: str, c) -> None:
    """One line per delta entry."""
    label = f"{entry.kind}/{entry.name} @ {entry.field_path}"
    print(f"      {c(color, prefix)} {label}  {c(DIM, f'({entry.severity})')}")


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------

def _print_json(scan_result: ScanResult) -> None:
    """
    Print results as JSON to stdout.

    Schema:
        {
            "summary": { critical, warning, info, total, drifted, in_sync, ... },
            "resources": [
                {
                    "kind": "...", "name": "...", "namespace": "...",
                    "status": "...", "drifts": [...]
                }
            ],
            "delta": {  // present only if --snapshot-dir was passed
                "previous_scan_at": "...",
                "new":      [...],
                "resolved": [...]
            },
            "snapshot_path": "..."  // present only if a snapshot was written
        }

    Note: git_value and live_value are emitted as native types (dict, list,
    str, int, etc.) — NOT stringified — so consumers can jq into them.
    """
    payload = {
        "summary": summary_counts(scan_result),
        "resources": [
            {
                "kind":      r.kind,
                "name":      r.name,
                "namespace": r.namespace,
                "status":    r.status,
                "drifts": [
                    {
                        "field_path":  d.field_path,
                        "git_value":   d.git_value,    # native type, not str()
                        "live_value":  d.live_value,   # native type, not str()
                        "change_type": d.change_type,
                        "severity":    d.severity,
                    }
                    for d in r.drifts
                ],
            }
            for r in scan_result.results
        ],
    }

    if scan_result.delta is not None:
        payload["delta"] = scan_result.delta.to_dict()

    if scan_result.snapshot_path is not None:
        payload["snapshot_path"] = str(scan_result.snapshot_path)

    print(json.dumps(payload, indent=2, default=_json_fallback))


def _json_fallback(obj):
    """Last-resort serializer for objects json doesn't know how to handle."""
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    return str(obj)