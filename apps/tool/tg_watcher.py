#!/usr/bin/env python3
"""tg_watcher.py — FSEvents watcher that captures Telegram media at IN_CREATE.

Telegram occasionally writes a file and deletes it within seconds (the
"upload-then-purge" pattern that left us empty-handed for msg13 in
@czarnetlo). The periodic daemon runs every few minutes and misses such
files. The watcher closes that gap: it sits on FSEvents notifications and
copies new files into a deduplicating vault the moment they appear.

Vault layout

  vault/
    objects/<sha256-hex>             # one copy of each unique byte sequence
    index.jsonl                       # append-only log of (event, path,
                                      # sha, size, mtime, captured_at)

Reading is straightforward: scan index.jsonl, group by sha or path, then
serve files from objects/. The index is independent of the order of
events, so concurrent appends don't matter.

Why hash, not copy-by-name: Telegram keeps two copies of every secret
file on disk (`X` + `X_partial`), and renames happen in stages. Naming
the vault by content makes all duplicates collapse and survives renames.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
except ImportError:  # pragma: no cover — import guard
    print(
        "ERROR: watchdog not installed. Run: pip install watchdog\n"
        "       (or: ./tg-viewer setup)",
        file=sys.stderr,
    )
    sys.exit(2)


_running = True

# Rotate index.jsonl once it crosses this size so it never grows unbounded.
INDEX_MAX_BYTES = 50 * 1024 * 1024


def _log(msg: str) -> None:
    print(f"[watcher {datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def _handle_sigterm(signum, frame) -> None:  # pragma: no cover — signal handler
    global _running
    _log(f"received signal {signum}; stopping observer")
    _running = False


# Allow-list of filename prefixes that look like real media or other
# forensically-interesting content. The daemon snapshots the rest (DBs,
# metadata, logs) every cycle anyway; the watcher's job is to catch fast
# deletes of MEDIA specifically — the gap the daemon's 5-minute cadence
# can't close. An allow-list keeps the vault from being flooded by
# status-counter files (network-stats, crashhandler, etc.) that Telegram
# rewrites every few seconds without changing anything we care about.
_CAPTURE_PREFIXES = (
    "secret-file-",                  # incoming secret-chat E2E media
    "local-file-",                   # outgoing photos pre-upload
    "telegram-local-file-",          # alt outgoing local naming
    "telegram-cloud-photo-",         # cloud photos (all sizes)
    "telegram-cloud-document-",      # cloud documents + their thumb sizes
    "telegram-peer-photo-",          # profile pictures (good for renames)
    "telegram-stickerpackthumbnail-",  # sticker pack thumbs
    "tg_image_",                     # sandbox tmp staging area
)


def _should_capture(path: Path) -> bool:
    """Allow-list of forensically-interesting media filename prefixes.

    Skips _partial.meta transients explicitly so a noisy filename like
    `secret-file-X-4_partial.meta` doesn't squeak past the prefix check.
    """
    name = path.name
    if name.startswith(".") or name.endswith("_partial.meta"):
        return False
    return any(name.startswith(p) for p in _CAPTURE_PREFIXES)


def _stream_into(path: Path, dst, chunk: int = 1 << 20) -> str | None:
    """Open `path` once, stream its bytes into file object `dst` while hashing.

    Returns the sha256 hex digest, or None on read error / empty read. The
    file is read exactly once, so the bytes that land in `dst` are guaranteed
    to be the bytes that produced the digest — Telegram can't swap the file
    out from under us between hashing and copying.

    A freshly-created file may not be fully written yet when FSEvents fires,
    so a single short retry is allowed if the first attempt yields an empty
    read or raises OSError. The retry's pause stays in this helper, off the
    event-dispatch hot path.
    """
    for attempt in range(2):
        h = hashlib.sha256()
        got_any = False
        try:
            with open(path, "rb") as f:
                while True:
                    data = f.read(chunk)
                    if not data:
                        break
                    got_any = True
                    h.update(data)
                    dst.write(data)
            if got_any:
                return h.hexdigest()
        except (OSError, PermissionError):
            pass
        if attempt == 0:
            # Discard whatever partial bytes we wrote and retry once.
            try:
                dst.seek(0)
                dst.truncate(0)
            except OSError:
                return None
            time.sleep(0.05)
    return None


class VaultWriter:
    """Thread-safe-ish append-only writer for vault/objects + index.jsonl.

    No explicit locking: FSEvents callbacks fire on the observer's single
    thread, so this class is single-writer by construction. Multi-watcher
    deployments would need a lock around _append_index.
    """

    def __init__(self, vault_dir: Path) -> None:
        self.vault_dir = vault_dir
        self.objects_dir = vault_dir / "objects"
        self.index_file = vault_dir / "index.jsonl"
        self.objects_dir.mkdir(parents=True, exist_ok=True)
        # Reclaim orphaned tmp objects left by a crash mid-copy. They're never
        # promoted to a sha-named object, so they'd otherwise accumulate.
        for t in self.objects_dir.glob(".tmp-*"):
            t.unlink(missing_ok=True)
        # Track sha→already-stored so we don't rehash on every event.
        self._known: set[str] = {
            p.name
            for p in self.objects_dir.iterdir()
            if p.is_file() and not p.name.startswith(".tmp-")
        }
        self._tmp_counter = 0
        _log(f"vault opened: {vault_dir}  ({len(self._known)} objects already present)")

    def capture(self, path: Path, event_type: str) -> None:
        """Hash + store the file; append a single-line JSON record to index.

        The file is opened ONCE: its bytes are streamed into a tmp object
        file while sha256 is computed in the same pass. This closes the
        race where Telegram deletes or rewrites the file between a separate
        hash pass and a separate copy pass (which could store wrong bytes
        under a stale sha, or lose the file entirely). Only after a complete,
        atomic os.replace does the object appear in objects/, so no
        zero-length or partial object is ever left behind.
        """
        try:
            st = path.stat()
            size = st.st_size
            mtime = st.st_mtime
        except OSError:
            return
        if size == 0:
            return  # rsync's "vanishing source" or partial-file race

        # Stream into a tmp file while hashing. The tmp name is unique per
        # capture (pid+counter) so concurrent captures of the same content
        # don't clobber each other's in-flight tmp.
        self._tmp_counter += 1
        tmp = self.objects_dir / f".tmp-{os.getpid()}-{self._tmp_counter}"
        try:
            with open(tmp, "wb") as dst:
                sha = _stream_into(path, dst)
        except OSError as exc:
            _log(f"copy failed for {path}: {exc}")
            self._unlink_quiet(tmp)
            return

        if sha is None:
            # Read failed or file was empty/vanished — leave nothing behind.
            self._unlink_quiet(tmp)
            return

        novel = sha not in self._known
        if novel:
            target = self.objects_dir / sha
            try:
                os.replace(tmp, target)
                self._known.add(sha)
            except OSError as exc:
                _log(f"store failed for {path}: {exc}")
                self._unlink_quiet(tmp)
                return
        else:
            # Already have these bytes; discard the duplicate copy.
            self._unlink_quiet(tmp)

        record = {
            "captured_at": time.time(),
            "event": event_type,
            "path": str(path),
            "sha256": sha,
            "size": size,
            "mtime": mtime,
            "novel": novel,
        }
        self._append_index(record)
        if novel:
            _log(f"captured {path.name} ({size}B, sha {sha[:12]})")

    @staticmethod
    def _unlink_quiet(p: Path) -> None:
        try:
            p.unlink()
        except OSError:
            pass

    def _append_index(self, record: dict) -> None:
        """Append one JSON line, rotating index.jsonl if it grew too large."""
        self._rotate_index_if_needed()
        with open(self.index_file, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")

    def _rotate_index_if_needed(self) -> None:
        try:
            if self.index_file.stat().st_size < INDEX_MAX_BYTES:
                return
        except OSError:
            return  # no index yet, nothing to rotate
        rotated = self.index_file.with_name(self.index_file.name + ".1")
        try:
            os.replace(self.index_file, rotated)  # atomic; clobbers old .1
        except OSError as exc:
            _log(f"index rotation failed: {exc}")


class _Handler(FileSystemEventHandler):
    def __init__(self, vault: VaultWriter) -> None:
        super().__init__()
        self.vault = vault

    def on_created(self, event) -> None:
        if event.is_directory:
            return
        p = Path(event.src_path)
        if _should_capture(p):
            # No sleep here: blocking the observer callback thread stalls
            # every subsequent event. _stream_into() tolerates a not-yet-
            # fully-written file with a single short retry of its own.
            self.vault.capture(p, "created")

    def on_modified(self, event) -> None:
        if event.is_directory:
            return
        p = Path(event.src_path)
        if _should_capture(p):
            self.vault.capture(p, "modified")

    def on_moved(self, event) -> None:
        if event.is_directory:
            return
        # On macOS, rename-into-place fires on_moved with dest_path set.
        dest = Path(getattr(event, "dest_path", "") or event.src_path)
        if _should_capture(dest):
            self.vault.capture(dest, "moved")


def run_watcher(watch_dirs: list[Path], vault_dir: Path, max_seconds: int | None = None) -> None:
    """Block on the FSEvents observer until SIGTERM (or max_seconds for tests)."""
    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)

    missing = [d for d in watch_dirs if not d.is_dir()]
    if missing:
        _log(f"WARNING: these paths don't exist and will be ignored: {missing}")
    real_dirs = [d for d in watch_dirs if d.is_dir()]
    if not real_dirs:
        _log("no valid watch dirs; exiting")
        return

    vault = VaultWriter(vault_dir)
    handler = _Handler(vault)
    observer = Observer()
    for d in real_dirs:
        observer.schedule(handler, str(d), recursive=True)
        _log(f"watching {d}")
    observer.start()
    started = time.time()
    try:
        while _running:
            time.sleep(0.5)
            if max_seconds is not None and time.time() - started >= max_seconds:
                break
    finally:
        observer.stop()
        observer.join(timeout=2.0)
    _log("clean exit")


_DEFAULT_WATCH = [
    "~/Library/Group Containers/6N38VWS5BX.ru.keepcoder.Telegram/appstore",
    "~/Library/Containers/ru.keepcoder.Telegram/Data/tmp",
]


def main() -> None:  # pragma: no cover — entrypoint
    parser = argparse.ArgumentParser(description="tg-viewer FSEvents watcher")
    parser.add_argument(
        "--vault", required=True,
        help="Where the deduplicating vault (objects/ + index.jsonl) lives.",
    )
    parser.add_argument(
        "--watch", action="append", default=[],
        help="Watch this directory (repeatable). Defaults to Telegram's "
             "Group Container and sandbox tmp.",
    )
    args = parser.parse_args()

    watch_strs = args.watch or _DEFAULT_WATCH
    watch_dirs = [Path(p).expanduser().resolve() for p in watch_strs]
    vault_dir = Path(args.vault).expanduser().resolve()

    run_watcher(watch_dirs, vault_dir)


if __name__ == "__main__":
    main()
