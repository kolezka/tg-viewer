"""Unit tests for tool/storage_db_parser.py.

Covers decode_t15_value (hand-crafted bytes) and read_storage_catalog /
build_storage_catalog (against a tiny in-process SQLite). The fixture
data deliberately mirrors the real-world tombstone case described in the
task: an entry that exists in t15 but has no corresponding file on disk.
"""
from __future__ import annotations

import sqlite3
import struct
from pathlib import Path

import pytest

from tool.storage_db_parser import (
    build_storage_catalog,
    decode_t15_value,
    read_storage_catalog,
)


def _pack_t15_value(
    name: str, size: int, *, flag: int = 0x01, trailing_pad: int = 0
) -> bytes:
    """Build a t15 value blob in the real on-disk layout.

    Real layout: 0x00 marker | name_len_LE_u16 | name | flag_byte |
    size_LE_u32 | 3-byte padding. The flag varies by file type
    (0x00 incomplete, 0x01 downloaded, 0x06/0x07 documents/photos);
    tests default to 0x01.
    """
    name_bytes = name.encode("utf-8")
    out = b"\x00" + struct.pack("<H", len(name_bytes)) + name_bytes
    out += bytes([flag]) + struct.pack("<I", size) + b"\x00\x00\x00"
    if trailing_pad:
        out += b"\x00" * trailing_pad
    return out


def test_decode_t15_value_basic_filename():
    name = "secret-file-5811993738297220291-4"
    blob = _pack_t15_value(name, 73900)
    assert decode_t15_value(blob) == (name, 73900)


def test_decode_t15_value_absolute_path():
    name = "/Users/me/Library/Group Containers/X/media/file-1"
    blob = _pack_t15_value(name, 1024)
    decoded = decode_t15_value(blob)
    assert decoded == (name, 1024)


def test_decode_t15_value_handles_trailing_padding():
    blob = _pack_t15_value("some-file", 42, trailing_pad=6)
    assert decode_t15_value(blob) == ("some-file", 42)


def test_decode_t15_value_rejects_empty():
    assert decode_t15_value(b"") is None
    assert decode_t15_value(b"\x00\x00") is None


def test_decode_t15_value_rejects_wrong_marker():
    blob = b"\x01\x00\x03abc" + struct.pack("<Q", 7)
    assert decode_t15_value(blob) is None


def test_decode_t15_value_rejects_zero_name_length():
    blob = b"\x00\x00\x00" + struct.pack("<Q", 7)
    assert decode_t15_value(blob) is None


def test_decode_t15_value_rejects_truncated_size():
    name = "abc"
    blob = b"\x00" + struct.pack("<H", len(name)) + name.encode() + b"\x00\x00"
    assert decode_t15_value(blob) is None


def test_decode_t15_value_rejects_invalid_utf8():
    # Length says 3 but bytes are an invalid UTF-8 lead sequence
    blob = b"\x00\x00\x03\xff\xfe\xfd" + struct.pack("<Q", 1)
    assert decode_t15_value(blob) is None


def _make_t15_db(path: Path, entries: list[tuple[bytes, bytes]]) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t15 (key BLOB PRIMARY KEY, value BLOB)")
    conn.executemany("INSERT INTO t15 (key, value) VALUES (?, ?)", entries)
    conn.commit()
    conn.close()


def test_read_storage_catalog_yields_decoded_entries(tmp_path: Path):
    db = tmp_path / "db_sqlite"
    rows = [
        (b"k" * 16, _pack_t15_value("file-alpha", 100)),
        (b"j" * 16, _pack_t15_value("/abs/path/file-beta", 200)),
        (b"i" * 16, b"\x00" + b"\xff"),  # corrupt -> dropped
    ]
    _make_t15_db(db, rows)

    entries = read_storage_catalog(db, source="storage")
    assert len(entries) == 2

    by_name = {e["filename"]: e for e in entries}
    assert by_name["file-alpha"]["size_bytes"] == 100
    assert by_name["file-alpha"]["source"] == "storage"
    assert by_name["file-alpha"]["absolute_path"] is None

    assert by_name["/abs/path/file-beta"]["absolute_path"] == "/abs/path/file-beta"


def test_read_storage_catalog_missing_file_returns_empty(tmp_path: Path):
    assert read_storage_catalog(tmp_path / "nope", "storage") == []


def test_read_storage_catalog_missing_t15_table_returns_empty(tmp_path: Path):
    db = tmp_path / "db_sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t99 (key INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    assert read_storage_catalog(db, "storage") == []


def test_build_storage_catalog_marks_tombstones(tmp_path: Path):
    # Lay out an account dir matching the live tree shape.
    account = tmp_path / "account-1"
    storage_db = account / "postbox" / "media" / "storage" / "db" / "db_sqlite"
    cache_db = account / "postbox" / "media" / "cache-storage" / "db" / "db_sqlite"
    media_dir = account / "postbox" / "media"
    storage_db.parent.mkdir(parents=True)
    cache_db.parent.mkdir(parents=True)

    # storage catalog: three secret-files, two of which we'll create on disk.
    _make_t15_db(storage_db, [
        (b"a" * 16, _pack_t15_value("secret-file-111-4", 1000)),
        (b"b" * 16, _pack_t15_value("secret-file-222-4", 2000)),
        (b"c" * 16, _pack_t15_value("secret-file-333-4", 73900)),  # tombstone
    ])
    # cache-storage: absolute-path entries; "secret-file-111-4" overlaps with
    # storage (dedup expected) plus one unique abs-path entry.
    abs_overlap = str(media_dir / "secret-file-111-4")
    abs_unique = str(media_dir / "telegram-cloud-photo-size-2-foo-y")
    _make_t15_db(cache_db, [
        (b"d" * 16, _pack_t15_value(abs_overlap, 1000)),
        (b"e" * 16, _pack_t15_value(abs_unique, 5000)),
    ])

    # Two files on disk; "secret-file-333-4" intentionally absent (tombstone).
    (media_dir / "secret-file-111-4").write_bytes(b"x" * 10)
    (media_dir / "secret-file-222-4").write_bytes(b"y" * 10)

    catalog = build_storage_catalog(account, media_dir)

    by_name = {e["filename"]: e for e in catalog}
    # Dedup: storage entry wins, but it inherits the cache absolute_path.
    assert "secret-file-111-4" in by_name
    assert by_name["secret-file-111-4"]["source"] == "storage"
    assert by_name["secret-file-111-4"]["absolute_path"] == abs_overlap
    assert by_name["secret-file-111-4"]["on_disk"] is True

    # Pure-storage entries.
    assert by_name["secret-file-222-4"]["on_disk"] is True
    assert by_name["secret-file-333-4"]["on_disk"] is False  # the tombstone
    assert by_name["secret-file-333-4"]["size_bytes"] == 73900

    # Cache-only entry survives untouched.
    assert by_name[abs_unique]["source"] == "cache-storage"
    assert by_name[abs_unique]["absolute_path"] == abs_unique
    # Basename matches an on-disk file? No — we never wrote that file.
    assert by_name[abs_unique]["on_disk"] is False

    tombstones = [e for e in catalog if not e["on_disk"]]
    assert len(tombstones) == 2  # secret-file-333-4 and the unique cache entry
    assert len(catalog) == 4  # 3 unique storage + 1 cache, minus the dedup


def test_build_storage_catalog_no_dbs_returns_empty(tmp_path: Path):
    account = tmp_path / "account-empty"
    account.mkdir()
    media_dir = account / "postbox" / "media"
    media_dir.mkdir(parents=True)
    assert build_storage_catalog(account, media_dir) == []
