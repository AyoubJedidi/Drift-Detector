"""
Phase 4 - Part 3: CLI

The user-facing command line interface.
Built with Click — defines all flags and connects them to scanner + printer.

Usage:
  drift-detect scan ./my-manifests
  drift-detect scan https://github.com/org/repo --branch main
  drift-detect scan ./my-manifests --namespace production --fail-on critical
  drift-detect scan ./my-manifests --output json --quiet
  drift-detect scan ./my-manifests --snapshot-dir ~/.drift-detect/snaps
"""

import sys
from pathlib import Path
from typing import Optional

import click

from drift_detect.phase4.scanner import scan, has_drift_above
from drift_detect.phase4.printer import print_results


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------

@click.group()
def cli():
    """drift-detect — GitOps drift detector for Kubernetes."""
    pass


# ---------------------------------------------------------------------------
# scan command
# ---------------------------------------------------------------------------

@cli.command(name="scan")
@click.argument("source")
@click.option("--branch",       default=None,     help="Git branch to checkout (remote repos only).")
@click.option("--tag",          default=None,     help="Git tag to checkout (remote repos only).")
@click.option("--source-type",  default=None,     type=click.Choice(["helm", "kustomize", "raw"]), help="Force source type instead of auto-detecting.")
@click.option("--values",       multiple=True,    type=click.Path(exists=True), help="Helm values file(s). Can be specified multiple times.")
@click.option("--namespace",    default=None,     help="Only scan resources in this namespace.")
@click.option("--kind",         default=None,     help="Only scan resources of this kind (e.g. Deployment).")
@click.option("--fail-on",      default="critical", type=click.Choice(["critical", "warning", "info"]), help="Exit code 1 if drift at or above this severity is found.")
@click.option("--output",       default="human",  type=click.Choice(["human", "json"]), help="Output format.")
@click.option("--quiet",        is_flag=True,     help="Print only the summary line.")
@click.option("--no-color",     is_flag=True,     help="Disable colored output.")
@click.option("--driftignore",  default=None,     type=click.Path(), help="Path to .driftignore file (default: .driftignore in current dir).")
@click.option("--snapshot-dir", default=None,     type=click.Path(file_okay=False), help="Directory to write JSON snapshots to. Enables delta tracking against the previous scan of this source.")
@click.option("--snapshot-retain", default=30, type=click.IntRange(min=0), show_default=True, help="Keep this many most-recent snapshots per source. Older ones are deleted.")
def scan_cmd(
    source,
    branch,
    tag,
    source_type,
    values,
    namespace,
    kind,
    fail_on,
    output,
    quiet,
    no_color,
    driftignore,
    snapshot_dir,
    snapshot_retain,
):
    """
    Scan a Git source against a live Kubernetes cluster and report drift.

    SOURCE can be a local path or a remote Git URL:

    \b
    drift-detect scan ./my-manifests
    drift-detect scan https://github.com/org/repo --branch main
    """
    try:
        if not quiet and output != "json":
            click.echo(f"Scanning {source} ...")

        helm_values = [Path(v) for v in values] if values else None

        scan_result = scan(
            source=source,
            branch=branch,
            tag=tag,
            source_type_override=source_type,
            helm_values=helm_values,
            namespace_filter=namespace,
            kind_filter=kind,
            driftignore_path=Path(driftignore) if driftignore else None,
            snapshot_dir=Path(snapshot_dir) if snapshot_dir else None,
            snapshot_retain=snapshot_retain,
        )

        print_results(
            scan_result,
            output_format=output,
            quiet=quiet,
            no_color=no_color,
        )

        # Exit code logic
        if has_drift_above(scan_result, threshold=fail_on):
            sys.exit(1)

    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(2)

    except RuntimeError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(2)

    except Exception as e:
        click.echo(f"Unexpected error: {e}", err=True)
        sys.exit(2)