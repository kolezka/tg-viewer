"""Unit tests for tool/tg_watcher.py — filter rules + vault dedup.

We don't spin up the real FSEvents observer here (flaky in CI and slow);
we exercise the pure-logic surface — `_should_capture` and the
`VaultWriter` content-addressed dedupe.
"""
from __future__ import annotations

import json
from pathlib import Path

from tool.tg_watcher import VaultWriter, _should_capture


def test_should_capture_accepts_real_telegram_names():
    assert _should_capture(Path("secret-file-5811993738297220291-4"))
    assert _should_capture(Path("local-file--1155884980421330152"))
    assert _should_capture(Path("telegram-cloud-photo-size-2-555-y"))
    assert _should_capture(Path("telegram-cloud-document-1-1258816259754035"))
    assert _should_capture(Path("telegram-peer-photo-size-4-X-0-0-0"))
    assert _should_capture(Path("telegram-stickerpackthumbnail-4--1354900319-0-0"))
    assert _should_capture(Path("telegram-local-file--5590119168954476542"))
    assert _should_capture(Path("tg_image_1518471281.jpeg"))


def test_should_capture_rejects_noise_and_transients():
    # SQLite live state and dotfiles: never interesting
    assert not _should_capture(Path(".DS_Store"))
    assert not _should_capture(Path("db_sqlite-shm"))
    assert not _should_capture(Path("db_sqlite-wal"))
    assert not _should_capture(Path("db_sqlite-journal"))
    assert not _should_capture(Path("foo.lock"))
    # Partial metadata blob — sister to the real file but no content
    assert not _should_capture(Path("secret-file-X-4_partial.meta"))
    # Status counters Telegram rewrites every few seconds — would flood vault
    assert not _should_capture(Path("network-stats"))
    assert not _should_capture(Path("crashhandler"))
    assert not _should_capture(Path("notificationsKey"))
    # Other random sandbox files
    assert not _should_capture(Path("accounts-shared-data"))
    assert not _should_capture(Path("some-other-thing"))


def test_vault_writer_deduplicates_by_content(tmp_path: Path):
    vault = tmp_path / "vault"
    writer = VaultWriter(vault)

    # Use allow-listed prefixes so _should_capture admits these — VaultWriter
    # is called directly here but real Handler paths go through that filter.
    f1 = tmp_path / "secret-file-1-4"
    f2 = tmp_path / "secret-file-2-4"        # different name, same content
    f3 = tmp_path / "secret-file-3-4"        # different content
    f1.write_bytes(b"hello world")
    f2.write_bytes(b"hello world")
    f3.write_bytes(b"different")

    writer.capture(f1, "created")
    writer.capture(f2, "created")
    writer.capture(f3, "created")

    objects = list((vault / "objects").iterdir())
    assert len(objects) == 2

    lines = (vault / "index.jsonl").read_text().splitlines()
    records = [json.loads(line) for line in lines]
    assert len(records) == 3
    # The first occurrence of "hello world" is novel=True, the second is False.
    by_path = {Path(r["path"]).name: r for r in records}
    assert by_path["secret-file-1-4"]["novel"] is True
    assert by_path["secret-file-2-4"]["novel"] is False
    assert by_path["secret-file-1-4"]["sha256"] == by_path["secret-file-2-4"]["sha256"]
    assert by_path["secret-file-1-4"]["sha256"] != by_path["secret-file-3-4"]["sha256"]


def test_vault_writer_skips_zero_byte_files(tmp_path: Path):
    """Zero-byte captures are usually rsync-vanishing-source or mid-rename
    races; skipping avoids polluting the index with no-value records."""
    vault = tmp_path / "vault"
    writer = VaultWriter(vault)
    empty = tmp_path / "secret-file-empty-4"
    empty.write_bytes(b"")
    writer.capture(empty, "created")
    assert not (vault / "objects").exists() or not list((vault / "objects").iterdir())
    assert not (vault / "index.jsonl").exists() or (vault / "index.jsonl").read_text() == ""


def test_vault_writer_remembers_known_objects_across_instances(tmp_path: Path):
    """A fresh VaultWriter pointed at an existing vault should not re-hash
    objects already on disk — `_known` is seeded from the objects/ dir."""
    vault = tmp_path / "vault"
    writer1 = VaultWriter(vault)
    f = tmp_path / "secret-file-alpha-4"
    f.write_bytes(b"persisted")
    writer1.capture(f, "created")
    first_count = len(list((vault / "objects").iterdir()))

    # Drop writer1, open a new one — it sees the existing object and treats
    # the next capture of the same content as non-novel.
    writer2 = VaultWriter(vault)
    f2 = tmp_path / "secret-file-beta-4"
    f2.write_bytes(b"persisted")
    writer2.capture(f2, "created")
    second_count = len(list((vault / "objects").iterdir()))
    assert second_count == first_count

    lines = (vault / "index.jsonl").read_text().splitlines()
    assert json.loads(lines[-1])["novel"] is False
