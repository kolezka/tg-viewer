"""Unit tests for tool/forensics.py — filename → file_id + the 3-way join."""
from __future__ import annotations

import pytest

from tool.forensics import build_forensic_index, extract_file_id_from_filename


@pytest.mark.parametrize("name, expected", [
    # secret-chat E2E media
    ("secret-file-5811993738297220291-4", 5811993738297220291),
    ("secret-file-5811993738297220291-4.jpg", 5811993738297220291),
    # local-file (outgoing photos before upload); negative id → double dash
    ("local-file-2406285482497847259", 2406285482497847259),
    ("local-file--1155884980421330152", -1155884980421330152),
    ("local-file--1155884980421330152_partial.meta", -1155884980421330152),
    # telegram-cloud variants
    ("telegram-cloud-photo-size-2-5879730080295536510-y", 5879730080295536510),
    ("telegram-cloud-document-1-1258816259754035", 1258816259754035),
    ("telegram-cloud-document-size-2-5212978356080884071-m", 5212978356080884071),
    # peer-photo + stickerpack thumb
    ("telegram-peer-photo-size-4-6043928604170719494-0-0-0", 6043928604170719494),
    ("telegram-stickerpackthumbnail-4--1354900319-0-0", -1354900319),
    # telegram-local-file
    ("telegram-local-file-6349152574912250062", 6349152574912250062),
    ("telegram-local-file--5590119168954476542", -5590119168954476542),
])
def test_extract_file_id_recognises_known_prefixes(name, expected):
    assert extract_file_id_from_filename(name) == expected


@pytest.mark.parametrize("name", [
    "",
    "unknown-prefix-12345",
    "cache",                   # the storage subdir name leaks in if you forget to skip dirs
    "accounts-shared-data",
])
def test_extract_file_id_returns_none_for_unknown(name):
    assert extract_file_id_from_filename(name) is None


def test_build_forensic_index_joins_three_sources():
    storage = [
        {"filename": "secret-file-100-4", "size_bytes": 73900, "on_disk": False, "source": "storage"},
        {"filename": "telegram-cloud-document-2-200", "size_bytes": 1024, "on_disk": True, "source": "storage"},
    ]
    logs = [
        {
            "event": "encrypted_message",
            "source_file": "log-A.txt",
            "source_line": 10,
            "data": {
                "chatId": 999, "date": 1779742923,
                "file": {"id": 100, "dcId": 4, "accessHash": -1, "size": 73904, "keyFingerprint": -2},
            },
        },
    ]
    msgs = [
        {"peer_id": 14656299518, "peer_name": "alice", "timestamp": 1779742923, "date": "2026-...",
         "outgoing": False, "media": [{"file_id": 100, "filename": "secret-file-100-4"}]},
        {"peer_id": 1, "peer_name": "bob", "timestamp": 1779742000, "date": "2026-...",
         "outgoing": True, "media": [{"file_id": 200}]},
    ]

    idx = build_forensic_index(storage, logs, msgs, account_id="account-1")
    by_id = {e["file_id"]: e for e in idx}

    # Triple-source row — the tombstone with peer + log metadata
    secret = by_id[100]
    assert set(secret["sources"]) == {"storage", "log", "message"}
    assert secret["tombstone"] is True
    assert secret["on_disk"] is False
    assert secret["size_bytes"] == 73900
    assert secret["log_event"]["dcId"] == 4
    assert secret["message"]["peer_name"] == "alice"
    assert secret["filenames"] == ["secret-file-100-4"]

    # Single-source: only storage knows it (existing doc still on disk)
    cloud = by_id[200]  # the message references id=200 too; storage references id=200; no log
    assert "storage" in cloud["sources"]
    assert "message" in cloud["sources"]
    assert cloud["on_disk"] is True
    assert cloud["tombstone"] is False


def test_build_forensic_index_log_only_entry():
    # File seen in logs but never persisted (we deleted before storage caught up)
    logs = [
        {"event": "encrypted_message",
         "data": {"chatId": 1, "date": 1000,
                  "file": {"id": 999, "dcId": 4, "accessHash": 0, "size": 50, "keyFingerprint": 0}}}
    ]
    idx = build_forensic_index([], logs, [])
    assert len(idx) == 1
    e = idx[0]
    assert e["file_id"] == 999
    assert e["sources"] == ["log"]
    assert e["on_disk"] is False
    assert e["tombstone"] is False  # we can't tell — storage never saw it
    assert e["size_bytes"] == 50    # inherited from the log


def test_build_forensic_index_orders_tombstones_first():
    # Two tombstones (one with newer log) + one alive entry
    storage = [
        {"filename": "secret-file-1-4", "size_bytes": 100, "on_disk": False, "source": "storage"},
        {"filename": "secret-file-2-4", "size_bytes": 200, "on_disk": False, "source": "storage"},
        {"filename": "secret-file-3-4", "size_bytes": 300, "on_disk": True,  "source": "storage"},
    ]
    logs = [
        {"event": "encrypted_message", "data": {"chatId": 9, "date": 2000, "file": {"id": 1}}},
        {"event": "encrypted_message", "data": {"chatId": 9, "date": 1000, "file": {"id": 2}}},
    ]
    idx = build_forensic_index(storage, logs, [])
    # Tombstones come first, ordered by newest activity desc.
    assert [e["file_id"] for e in idx] == [1, 2, 3]


def test_build_forensic_index_handles_empty_inputs():
    assert build_forensic_index([], [], []) == []
    assert build_forensic_index([], [], None) == []  # type: ignore[arg-type]
