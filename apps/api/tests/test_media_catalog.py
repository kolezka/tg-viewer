"""Media linkage + catalog-quality regressions.

Derived empirically from a real backup (977k t7 rows, 14.3k media files) where
the gallery showed 3 KB thumbnails with no chat attribution and no file size:

  * `extract_media_refs` scanned the wrong Postbox field. The cloud file_id
    lives under field `f` (`01 66 01`), not `i` (`01 69 01`); the old marker
    matched random byte alignments and produced ids that never hit disk, so
    only 1.2% of catalog entries carried a `linked_message`.
  * `build_media_catalog` listed `telegram-cloud-document-size-*` previews as
    standalone items alongside the full document they preview, so the gallery
    was dominated by low-resolution stubs.
  * `telegram-peer-photo-size-*` are peer avatars, not chat media; they need
    their own media_type so the gallery can filter them out.
  * The catalog wrote `size_bytes` while the API model reads `size`, so every
    item rendered its size as "—".
"""
from __future__ import annotations

import struct

from tool.postbox_parser import (
    build_media_catalog,
    extract_media_refs,
    resolve_media_files,
)


# ── blob helpers ────────────────────────────────────────────────────────────
def _cloud_file_ref(file_id: int) -> bytes:
    """A media ref as Telegram actually serializes it.

    Byte-for-byte the dominant real-world shape (376,594 occurrences in the
    reference backup): an `s` container holding field `f` with an int64 LE
    file_id.
    """
    return b"\x01\x73\x0b\x01\x66\x01" + struct.pack("<q", file_id)


# ── file_id extraction ──────────────────────────────────────────────────────
def test_extract_media_refs_reads_field_f():
    """The `01 66 01` (field `f`) form is the one Telegram writes."""
    fid = 5470177992950946662
    refs = extract_media_refs(b"Value\x00\x12\x00\x00\x00" + _cloud_file_ref(fid))

    assert fid in [r["file_id"] for r in refs]


def test_extract_media_refs_resolves_field_f_to_disk():
    """End-to-end: a field-`f` ref must resolve against a real filename."""
    fid = 5470177992950946662
    index = {f"telegram-cloud-document-2-{fid}"}

    resolved = resolve_media_files(extract_media_refs(_cloud_file_ref(fid)), index)

    assert [r["filename"] for r in resolved] == [f"telegram-cloud-document-2-{fid}"]


def test_extract_media_refs_ignores_zero_padded_alignments():
    """Guard the class of bug, not the symptom.

    The old scan reported ids like 0x12_00000000 — a small value sitting in the
    high half of an int64 read at a wrong offset. Any id whose low 32 bits are
    all zero is a byte-alignment artifact, never a Telegram file_id.
    """
    real = 5470177992950946662
    blob = (
        _cloud_file_ref(77309411328)  # 0x12_00000000 — artifact shape
        + _cloud_file_ref(real)
    )

    ids = [r["file_id"] for r in extract_media_refs(blob)]

    assert real in ids, "the fix must not throw away genuine ids"
    assert 77309411328 not in ids, "alignment artifact leaked into refs"


# ── catalog quality ─────────────────────────────────────────────────────────
def _write(media_dir, name, size=1024):
    (media_dir / name).write_bytes(b"\x00" * size)


def test_document_preview_is_folded_into_its_full_file(tmp_path):
    """`-size-` previews are thumbnails of a document, not separate media."""
    media = tmp_path / "media"
    media.mkdir()
    fid = 5413756493642093641
    _write(media, f"telegram-cloud-document-2-{fid}", 22061)
    _write(media, f"telegram-cloud-document-size-2-{fid}-m", 1554)

    catalog = build_media_catalog(media, messages=[])
    by_name = {e["filename"]: e for e in catalog}

    assert f"telegram-cloud-document-size-2-{fid}-m" not in by_name, (
        "thumbnail must not be listed as its own gallery item"
    )
    full = by_name[f"telegram-cloud-document-2-{fid}"]
    assert full["thumbnail"] == f"telegram-cloud-document-size-2-{fid}-m"


def test_orphan_document_preview_is_kept(tmp_path):
    """A preview whose full file was never downloaded is all we have — keep it."""
    media = tmp_path / "media"
    media.mkdir()
    _write(media, "telegram-cloud-document-size-4-570116660405469205-m", 1554)

    names = [e["filename"] for e in build_media_catalog(media, messages=[])]

    assert names == ["telegram-cloud-document-size-4-570116660405469205-m"]


def test_peer_photo_is_typed_as_avatar(tmp_path):
    """Avatars are not chat media; they get their own filterable type."""
    media = tmp_path / "media"
    media.mkdir()
    _write(media, "telegram-peer-photo-size-4-1234567890123456789")

    entry = build_media_catalog(media, messages=[])[0]

    assert entry["media_type"] == "avatar"


def test_catalog_entry_exposes_size_under_the_key_the_api_reads(tmp_path):
    """Parser and MediaItem must agree on the field name, or the UI shows "—"."""
    media = tmp_path / "media"
    media.mkdir()
    _write(media, "telegram-cloud-document-2-5413756493642093641", 22061)

    entry = build_media_catalog(media, messages=[])[0]

    assert entry["size"] == 22061


def test_media_item_model_keeps_the_catalog_size(tmp_path):
    """Contract test across the parser→API boundary."""
    from api.models import MediaItem

    media = tmp_path / "media"
    media.mkdir()
    _write(media, "telegram-cloud-document-2-5413756493642093641", 22061)

    entry = build_media_catalog(media, messages=[])[0]

    assert MediaItem(**entry).size == 22061
