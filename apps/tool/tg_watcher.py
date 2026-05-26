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


def _log(msg: str) -> None:
    print(f"[watcher {datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def _handle_sigterm(signum, frame) -> None:  # pragma: no cover — signal handler
    global _running
    _log(f"received signal {signum}; stopping observer")
    _running = False


# Filename suffixes/prefixes we don't bother capturing — pure transients.
_SKIP_PATTERNS = (
    ".DS_Store",
    "-shm",
    "-wal",
    "-journal",
    ".lock",
)


def _should_capture(path: Path) -> bool:
    """Filter out files that aren't worth deduping into the vault."""
    name = path.name
    if name.startswith("."):
        return False
    if name.endswith("_partial.meta"):
        return False
    for sfx in _SKIP_PATTERNS:
        if name.endswith(sfx):
            return False
    return True


def _sha256_of(path: Path, chunk: int = 1 << 20) -> str | None:
    """Stream the file content into sha256. Returns None on read errors."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while True:
                data = f.read(chunk)
                if not data:
                    break
                h.update(data)
    except (OSError, PermissionError):
        return None
    return h.hexdigest()


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
        # Track sha→already-stored so we don't rehash on every event.
        self._known: set[str] = {p.name for p in self.objects_dir.iterdir() if p.is_file()}
        _log(f"vault opened: {vault_dir}  ({len(self._known)} objects already present)")

    def capture(self, path: Path, event_type: str) -> None:
        """Hash + store the file; append a single-line JSON record to index."""
        try:
            size = path.stat().st_size
            mtime = path.stat().st_mtime
        except OSError:
            return
        if size == 0:
            return  # rsync's "vanishing source" or partial-file race

        sha = _sha256_of(path)
        if sha is None:
            return

        target = self.objects_dir / sha
        novel = sha not in self._known
        if novel:
            # Atomic copy via tmp file so a partially-written object never
            # ends up in the vault index.
            tmp = target.with_suffix(".tmp")
            try:
                with open(path, "rb") as src, open(tmp, "wb") as dst:
                    while True:
                        buf = src.read(1 << 20)
                        if not buf:
                            break
                        dst.write(buf)
                os.replace(tmp, target)
                self._known.add(sha)
            except OSError as exc:
                _log(f"copy failed for {path}: {exc}")
                try:
                    tmp.unlink()
                except OSError:
                    pass
                return

        record = {
            "captured_at": time.time(),
            "event": event_type,
            "path": str(path),
            "sha256": sha,
            "size": size,
            "mtime": mtime,
            "novel": novel,
        }
        with open(self.index_file, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
        if novel:
            _log(f"captured {path.name} ({size}B, sha {sha[:12]})")


class _Handler(FileSystemEventHandler):
    def __init__(self, vault: VaultWriter) -> None:
        super().__init__()
        self.vault = vault

    def on_created(self, event) -> None:
        if event.is_directory:
            return
        p = Path(event.src_path)
        if _should_capture(p):
            # Tiny pause so a freshly-created file has a chance to be fully
            # written before we hash it. The watchdog FSEvents backend often
            # fires for both create-and-write coalesced, but not always.
            time.sleep(0.05)
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
