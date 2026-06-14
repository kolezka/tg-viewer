#!/usr/bin/env python3
"""storage_db_parser.py — Parse Telegram's plaintext media catalog sidecars.

Telegram on macOS keeps two plaintext SQLite databases that catalog every media
file it has ever known about — including ones already purged from disk:

  postbox/media/storage/db/db_sqlite        (names = plain filenames)
  postbox/media/cache-storage/db/db_sqlite  (names = absolute filesystem paths)

The interesting table in both is `t15`:
  key   = 16-byte opaque content hash (we don't decode it)
  value = packed blob:
            byte 0           : 0x00 marker
            bytes 1..2       : uint16 LE — name length N
            bytes 3..3+N     : UTF-8 name
            byte 3+N         : status flag (0x00 incomplete, 0x01 downloaded,
                               0x06/0x07 = peer/document size variants — we
                               don't interpret it, just skip)
            bytes 3+N+1..+5  : uint32 LE — file size in bytes
            trailing 3 bytes : padding (often zeros, sometimes carries an
                               internal counter — ignored)
            Total payload is name_len + 12 bytes after the 3-byte header.

Verified against ~2000 live entries: a naive `uint64 LE @ 3+N` decode
multiplies the real size by 256 and adds the status flag in the low
byte. The format is u32, not u64.

Cross-referencing the catalog against the on-disk `postbox/media/` inventory
surfaces *tombstones* — entries Telegram still remembers but whose bytes are
gone. That's the forensic signal this module exists to produce.
"""
from __future__ import annotations

import sqlite3
import struct
from pathlib import Path
from typing import Any


def decode_t15_value(blob: bytes) -> tuple[str, int] | None:
    """Decode a single t15 `value` blob into (name, size_bytes).

    Returns None for malformed input rather than raising — we'd rather skip a
    corrupt row than abort the entire catalog scan.
    """
    if not blob or len(blob) < 12:
        return None
    if blob[0] != 0x00:
        return None

    try:
        name_len = struct.unpack("<H", blob[1:3])[0]
    except struct.error:
        return None

    name_end = 3 + name_len
    # 1 status flag + 4 size + 3 padding = 8 trailer bytes
    if name_len == 0 or name_end + 8 > len(blob):
        return None

    try:
        name = blob[3:name_end].decode("utf-8")
    except UnicodeDecodeError:
        return None

    # Skip the 1-byte status flag at [name_end], read u32 LE size from the
    # next 4 bytes. The remaining ~3 bytes are padding / internal counter.
    try:
        size = struct.unpack("<I", blob[name_end + 1:name_end + 5])[0]
    except struct.error:
        return None

    return name, size


def read_storage_catalog(db_path: Path, source: str) -> list[dict[str, Any]]:
    """Read `t15` rows from a plaintext sidecar SQLite at `db_path`.

    `source` is the tag stamped on each returned entry — typically
    "storage" or "cache-storage". Robust to missing tables / corrupt rows.
    """
    if not db_path.is_file():
        return []

    entries: list[dict[str, Any]] = []
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        print(f"    storage_db_parser: cannot open {db_path}: {exc}")
        return []

    try:
        try:
            rows = conn.execute("SELECT value FROM t15").fetchall()
        except sqlite3.Error as exc:
            print(f"    storage_db_parser: no t15 in {db_path}: {exc}")
            return []

        for (value,) in rows:
            if not isinstance(value, (bytes, bytearray, memoryview)):
                continue
            decoded = decode_t15_value(bytes(value))
            if not decoded:
                continue
            name, size = decoded
            entry: dict[str, Any] = {
                "filename": name,
                "size_bytes": size,
                "source": source,
                "absolute_path": name if name.startswith("/") else None,
            }
            entries.append(entry)
    finally:
        conn.close()

    return entries


def build_storage_catalog(
    account_dir: Path, media_dir: Path
) -> list[dict[str, Any]]:
    """Read both sidecar catalogs, dedupe on filename, mark on_disk flag.

    Dedup rule: when the same filename shows up in both catalogs, prefer the
    `storage` entry — its `filename` is the bare name that matches the on-disk
    layout, while `cache-storage` carries the absolute path. We merge by basename
    so an absolute-path entry and a bare-name entry for the same file collapse
    into one row that records both pieces of info.

    `on_disk` is determined by listing `media_dir` once; entries that don't
    appear there are tombstones.
    """
    storage_db = account_dir / "postbox" / "media" / "storage" / "db" / "db_sqlite"
    cache_db = account_dir / "postbox" / "media" / "cache-storage" / "db" / "db_sqlite"

    storage_entries = read_storage_catalog(storage_db, "storage")
    cache_entries = read_storage_catalog(cache_db, "cache-storage")

    on_disk_names: set[str] = set()
    if media_dir.is_dir():
        for f in media_dir.iterdir():
            if f.is_file():
                on_disk_names.add(f.name)

    merged: dict[str, dict[str, Any]] = {}

    # storage first so it wins ties; basename keying handles the abs-path overlap.
    for entry in storage_entries:
        key = _basename(entry["filename"])
        merged[key] = entry

    for entry in cache_entries:
        key = _basename(entry["filename"])
        if key in merged:
            # Storage entry already present — just attach the absolute path
            # if we found one in cache-storage and don't have one yet.
            if entry.get("absolute_path") and not merged[key].get("absolute_path"):
                merged[key]["absolute_path"] = entry["absolute_path"]
            continue
        merged[key] = entry

    catalog: list[dict[str, Any]] = []
    for basename, entry in merged.items():
        entry["on_disk"] = basename in on_disk_names
        catalog.append(entry)

    catalog.sort(key=lambda e: (e.get("on_disk", True), e["filename"]))
    return catalog


def _basename(name: str) -> str:
    """Return the last path segment, or `name` if it has no slash."""
    if "/" in name:
        return name.rsplit("/", 1)[1]
    return name
