"""
Phase 6 - Step 1: Snapshot Storage and Delta Computation

Saves drift scan results to disk as JSON snapshots, loads the most recent
snapshot for a given source, and computes the delta between two scans.

A snapshot is a flat list of drift entries plus metadata. The shape is
intentionally minimal — just enough to identify each drift uniquely so
delta comparison reduces to set operations.

Storage layout:
    <snapshot_dir>/<timestamp>_<source_hash>.json

The source_hash keeps snapshots from different scan sources separated, so
scanning ./prod-manifests and ./dev-manifests doesn't interleave.

Drift identity for delta comparison:
    (kind, namespace, name, field_path, change_type)
"""

import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple, Set

from drift_detect.phase3.differ import DriftResult, DriftItem


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DriftKey:
    """Identity tuple used to compare drifts across snapshots."""
    kind:        str
    namespace:   str
    name:        str
    field_path:  str
    change_type: str

    def __str__(self) -> str:
        return f"{self.kind}/{self.namespace}/{self.name} @ {self.field_path} ({self.change_type})"


@dataclass
class SnapshotEntry:
    """One drift entry as stored in a snapshot file."""
    kind:        str
    namespace:   str
    name:        str
    field_path:  str
    change_type: str
    severity:    str
    git_value:   object   # native type, may be dict/list/str/int/None
    live_value:  object

    def key(self) -> DriftKey:
        return DriftKey(
            kind=self.kind,
            namespace=self.namespace,
            name=self.name,
            field_path=self.field_path,
            change_type=self.change_type,
        )

    @staticmethod
    def from_drift(result: DriftResult, drift: DriftItem) -> "SnapshotEntry":
        return SnapshotEntry(
            kind=result.kind,
            namespace=result.namespace,
            name=result.name,
            field_path=drift.field_path,
            change_type=drift.change_type,
            severity=drift.severity,
            git_value=drift.git_value,
            live_value=drift.live_value,
        )

    @staticmethod
    def from_dict(d: dict) -> "SnapshotEntry":
        return SnapshotEntry(
            kind=d["kind"],
            namespace=d["namespace"],
            name=d["name"],
            field_path=d["field_path"],
            change_type=d["change_type"],
            severity=d["severity"],
            git_value=d.get("git_value"),
            live_value=d.get("live_value"),
        )


@dataclass
class Snapshot:
    """A complete snapshot — metadata plus the flat list of drift entries."""
    timestamp:       str         # ISO 8601, UTC
    source:          str         # the --source argument as the user passed it
    source_hash:     str         # short hash of source for filename grouping
    namespace:       Optional[str]
    drifts:          List[SnapshotEntry] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "timestamp":   self.timestamp,
            "source":      self.source,
            "source_hash": self.source_hash,
            "namespace":   self.namespace,
            "drifts":      [asdict(d) for d in self.drifts],
        }

    @staticmethod
    def from_dict(d: dict) -> "Snapshot":
        return Snapshot(
            timestamp=d["timestamp"],
            source=d["source"],
            source_hash=d["source_hash"],
            namespace=d.get("namespace"),
            drifts=[SnapshotEntry.from_dict(x) for x in d.get("drifts", [])],
        )


@dataclass
class Delta:
    """Difference between two scans of the same source."""
    new:                 List[SnapshotEntry] = field(default_factory=list)
    resolved:            List[SnapshotEntry] = field(default_factory=list)
    previous_timestamp:  Optional[str]       = None

    def is_empty(self) -> bool:
        return not self.new and not self.resolved

    def is_initial(self) -> bool:
        """True if there was no prior snapshot to compare against."""
        return self.previous_timestamp is None

    def to_dict(self) -> dict:
        return {
            "previous_scan_at": self.previous_timestamp,
            "new":      [asdict(e) for e in self.new],
            "resolved": [asdict(e) for e in self.resolved],
        }


# ---------------------------------------------------------------------------
# Build a snapshot from scan results
# ---------------------------------------------------------------------------

def build_snapshot(
    results:   List[DriftResult],
    source:    str,
    namespace: Optional[str] = None,
) -> Snapshot:
    """Convert in-memory scan results to a Snapshot for storage."""
    entries: List[SnapshotEntry] = []

    for result in results:
        if result.status == "drifted":
            for drift in result.drifts:
                entries.append(SnapshotEntry.from_drift(result, drift))
        elif result.status == "missing_from_cluster":
            # Represent as a synthetic drift entry so deltas can track it
            entries.append(SnapshotEntry(
                kind=result.kind,
                namespace=result.namespace,
                name=result.name,
                field_path="<resource>",
                change_type="missing_from_cluster",
                severity="critical",
                git_value=None,
                live_value=None,
            ))
        elif result.status == "missing_from_git":
            entries.append(SnapshotEntry(
                kind=result.kind,
                namespace=result.namespace,
                name=result.name,
                field_path="<resource>",
                change_type="missing_from_git",
                severity="warning",
                git_value=None,
                live_value=None,
            ))
        # in_sync resources contribute nothing — no entries

    return Snapshot(
        timestamp=_now_iso(),
        source=source,
        source_hash=_hash_source(source),
        namespace=namespace,
        drifts=entries,
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def save_snapshot(snapshot: Snapshot, snapshot_dir: Path) -> Path:
    try:
        snapshot_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise RuntimeError(
            f"Cannot create snapshot directory {snapshot_dir}: {e}\n"
            f"Check permissions or pass a different --snapshot-dir."
        ) from e

    filename = f"{_filename_safe(snapshot.timestamp)}_{snapshot.source_hash}.json"
    path = snapshot_dir / filename
    try:
        path.write_text(json.dumps(snapshot.to_dict(), indent=2), encoding="utf-8")
    except OSError as e:
        raise RuntimeError(
            f"Cannot write snapshot to {path}: {e}\n"
            f"Check that --snapshot-dir is writable."
        ) from e
    return path


def load_previous_snapshot(
    source:       str,
    snapshot_dir: Path,
) -> Optional[Snapshot]:
    """
    Return the most recent snapshot for the given source, or None if none exist.

    Snapshots from other sources are ignored (filtered by source_hash in filename).
    Corrupted snapshot files are skipped with a warning, not an error — a
    broken snapshot shouldn't block a working scan.
    """
    if not snapshot_dir.exists():
        return None

    target_hash = _hash_source(source)
    candidates = sorted(
        snapshot_dir.glob(f"*_{target_hash}.json"),
        reverse=True,  # newest first by filename (timestamps sort lexically)
    )

for path in candidates:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Snapshot.from_dict(data)
    except (json.JSONDecodeError, KeyError) as e:
        print(f"Warning: skipping corrupt snapshot {path.name}: {e}")
        continue
    except OSError as e:
        print(f"Warning: cannot read snapshot {path.name}: {e}")
        continue

return None


def prune_snapshots(
    source:        str,
    snapshot_dir:  Path,
    retain:        int,
) -> int:
    """
    Keep the `retain` most recent snapshots for `source`; delete older ones.

    Returns the number of files deleted.
    """
    if not snapshot_dir.exists() or retain < 0:
        return 0

    target_hash = _hash_source(source)
    files = sorted(
        snapshot_dir.glob(f"*_{target_hash}.json"),
        reverse=True,
    )

    to_delete = files[retain:]
    deleted = 0
    for path in to_delete:
        try:
            path.unlink()
            deleted += 1
        except OSError as e:
            print(f"Warning: could not delete {path.name}: {e}")
    return deleted


# ---------------------------------------------------------------------------
# Delta computation
# ---------------------------------------------------------------------------

def compute_delta(current: Snapshot, previous: Optional[Snapshot]) -> Delta:
    """
    Diff two snapshots — what's new in current, what's resolved since previous.

    If `previous` is None (first scan ever for this source), returns a Delta
    with previous_timestamp=None and empty new/resolved lists. Callers can
    detect this with `delta.is_initial()`.
    """
    if previous is None:
        return Delta(new=[], resolved=[], previous_timestamp=None)

    current_keys: Set[DriftKey] = {e.key() for e in current.drifts}
    previous_keys: Set[DriftKey] = {e.key() for e in previous.drifts}

    new_keys      = current_keys - previous_keys
    resolved_keys = previous_keys - current_keys

    new_entries      = [e for e in current.drifts  if e.key() in new_keys]
    resolved_entries = [e for e in previous.drifts if e.key() in resolved_keys]

    return Delta(
        new=new_entries,
        resolved=resolved_entries,
        previous_timestamp=previous.timestamp,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """Current time as ISO 8601 UTC, e.g. '2026-05-08T14:23:01Z'."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _filename_safe(timestamp: str) -> str:
    """Make a timestamp safe for use as a filename (replace : with -)."""
    return timestamp.replace(":", "-")


def _hash_source(source: str) -> str:
    """Short hash of the source string for filename grouping."""
    h = hashlib.sha256(source.encode("utf-8")).hexdigest()
    return h[:8]