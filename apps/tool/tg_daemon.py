#!/usr/bin/env python3
"""tg_daemon.py — periodic backup-decrypt-parse loop with retention pruning.

Designed to run forever under launchd. Each cycle:

  1. Sleeps `interval_seconds` (default 5 min) since the previous cycle.
  2. Runs `apps/tool/tg-backup.sh` with `--link-dest <previous_backup>` so
     unchanged files in `postbox/media/` are hardlinked rather than copied —
     a fresh snapshot costs only the delta-bytes (typically megabytes, not
     the full 2.5 GB).
  3. Runs decryptor + parser. The parser already emits ghost diff
     against the previous snapshot (Step 3d added in commit f110f88), so
     just-deleted messages are captured every cycle.
  4. Prunes old snapshots per the retention policy: keep every snapshot
     younger than 4 h; one per hour from 4 h..24 h; one per day older
     than 24 h up to 30 days; older are deleted.

Stdout is line-buffered with a "[daemon HH:MM:SS]" prefix so launchd's log
remains greppable. SIGTERM exits cleanly between cycles.
"""
from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable


_running = True


def _log(msg: str) -> None:
    print(f"[daemon {datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def _handle_sigterm(signum, frame) -> None:  # pragma: no cover — signal handler
    global _running
    _log(f"received signal {signum}; will exit after current cycle")
    _running = False


def _find_latest_backup(root: Path) -> Path | None:
    """Return the most recent `tg_<timestamp>/` snapshot under `root`.

    The daemon writes flat snapshots (root/tg_TS/), not the nested
    tg_TS/tg_TS/ pattern that `./tg-viewer backup` produces — we pass a
    stable `root` and let tg-backup.sh create one tg_TS/ inside.
    """
    candidates: list[tuple[float, Path]] = []
    for snap in root.glob("tg_*"):
        if snap.is_dir():
            candidates.append((snap.stat().st_mtime, snap))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def _list_snapshots(root: Path) -> list[Path]:
    """All `tg_*/` snapshots under root, sorted newest-first."""
    items: list[tuple[float, Path]] = []
    for snap in root.glob("tg_*"):
        if snap.is_dir():
            items.append((snap.stat().st_mtime, snap))
    items.sort(reverse=True)
    return [p for _, p in items]


def _bucket(age_seconds: float, now_seconds: float) -> tuple[str, int]:
    """Map an age into a (granularity, bucket-id) pair for retention.

    Buckets:
      * `recent`  : age <= 4h           — keep every snapshot (no bucketing).
      * `hourly`  : 4h < age <= 24h     — one per UTC hour wins.
      * `daily`   : 24h < age <= 30d    — one per UTC day wins.
      * `expired` : age > 30d           — discard.

    Returns a stable bucket key so we can dedupe newest-in-bucket.
    """
    if age_seconds <= 4 * 3600:
        return ("recent", 0)  # never collide → never discarded by bucketing
    if age_seconds <= 24 * 3600:
        return ("hourly", int(now_seconds - age_seconds) // 3600)
    if age_seconds <= 30 * 86400:
        return ("daily", int(now_seconds - age_seconds) // 86400)
    return ("expired", 0)


def plan_pruning(snapshots: Iterable[Path], now_seconds: float) -> list[Path]:
    """Return the snapshots that should be deleted per the retention policy.

    `snapshots` should be a list of inner `tg_*/tg_*/` directories. Newest
    snapshot in each bucket survives; the others (and anything in `expired`)
    go to the deletion list. `recent` bucket keeps everything.
    """
    snaps = sorted(snapshots, key=lambda p: p.stat().st_mtime, reverse=True)
    survivors: set[Path] = set()
    keep_by_bucket: dict[tuple[str, int], Path] = {}
    delete: list[Path] = []

    for s in snaps:
        age = now_seconds - s.stat().st_mtime
        granularity, bucket_id = _bucket(age, now_seconds)
        if granularity == "recent":
            survivors.add(s)
            continue
        if granularity == "expired":
            delete.append(s)
            continue
        key = (granularity, bucket_id)
        existing = keep_by_bucket.get(key)
        if existing is None:
            keep_by_bucket[key] = s
        else:
            # `existing` is newer (snaps is desc-sorted); discard this older one.
            delete.append(s)

    survivors.update(keep_by_bucket.values())
    return [s for s in snaps if s not in survivors]


def _delete_snapshot(snapshot: Path) -> None:
    """Delete one tg_TS/ snapshot.

    Hardlinks (from --link-dest) mean the underlying inode is reference-
    counted across snapshots — removing one snapshot only frees the bytes
    if it was the last reference. APFS handles the bookkeeping.
    """
    shutil.rmtree(snapshot, ignore_errors=True)


def _run_backup_with_link_dest(
    backup_script: Path, dest_root: Path, link_dest: Path | None
) -> Path | None:
    """Invoke tg-backup.sh, optionally with --link-dest for hardlink dedup."""
    cmd = [str(backup_script), str(dest_root), "--batch"]
    if link_dest is not None:
        cmd += ["--link-dest", str(link_dest)]
    # tg-backup.sh prints the new backup path on its own log line; we
    # rediscover it from the filesystem rather than parsing stdout.
    before = {p.name for p in dest_root.glob("tg_*") if p.is_dir()}
    proc = subprocess.run(cmd, check=False)
    if proc.returncode not in (0, 23, 24):  # 23/24 are rsync-partial codes, fine
        _log(f"backup failed (rc={proc.returncode})")
        return None
    after = {p.name for p in dest_root.glob("tg_*") if p.is_dir()}
    new_dirs = after - before
    if not new_dirs:
        _log("backup didn't create a new directory — odd, skipping cycle")
        return None
    return dest_root / sorted(new_dirs)[-1]


def _run_decrypt_and_parse(repo_root: Path, backup_dir: Path) -> bool:
    """Pipe the new snapshot through decryptor + parser. Returns success."""
    apps_dir = repo_root / "apps"
    for module in ("tool.tg_appstore_decrypt", "tool.postbox_parser"):
        proc = subprocess.run(
            [sys.executable, "-m", module, str(backup_dir)],
            cwd=str(apps_dir),
            check=False,
        )
        if proc.returncode != 0:
            _log(f"{module} failed (rc={proc.returncode})")
            return False
    return True


def daemon_loop(
    repo_root: Path,
    dest_root: Path,
    interval_seconds: int = 300,
    max_cycles: int | None = None,
) -> None:
    """Periodic backup → decrypt → parse → prune cycle.

    Set `max_cycles` for tests; None means run until SIGTERM.
    """
    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)

    backup_script = repo_root / "apps" / "tool" / "tg-backup.sh"
    dest_root.mkdir(parents=True, exist_ok=True)
    _log(
        f"starting daemon: repo={repo_root}, dest={dest_root}, "
        f"interval={interval_seconds}s"
    )

    cycle = 0
    while _running:
        cycle += 1
        if max_cycles is not None and cycle > max_cycles:
            break

        prev = _find_latest_backup(dest_root)
        _log(f"cycle #{cycle}: prev={prev.parent.name if prev else 'none'}")
        new_backup = _run_backup_with_link_dest(backup_script, dest_root, prev)
        if new_backup is not None:
            _run_decrypt_and_parse(repo_root, new_backup)

            # Pruning
            to_prune = plan_pruning(_list_snapshots(dest_root), time.time())
            if to_prune:
                _log(f"pruning {len(to_prune)} stale snapshot(s)")
                for snap in to_prune:
                    _delete_snapshot(snap)

        if not _running:
            break

        # Sleep in 1-second slices so SIGTERM exits quickly.
        for _ in range(interval_seconds):
            if not _running:
                break
            time.sleep(1)

    _log("clean exit")


def main() -> None:  # pragma: no cover — entrypoint
    parser = argparse.ArgumentParser(description="tg-viewer periodic backup daemon")
    parser.add_argument("--dest", required=True, help="Snapshot root directory")
    parser.add_argument("--repo", default=".", help="Path to tg-viewer repo root")
    parser.add_argument("--interval", type=int, default=300, help="Seconds between cycles")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    args = parser.parse_args()

    daemon_loop(
        repo_root=Path(args.repo).resolve(),
        dest_root=Path(args.dest).resolve(),
        interval_seconds=args.interval,
        max_cycles=1 if args.once else None,
    )


if __name__ == "__main__":
    main()
