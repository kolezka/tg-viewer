"""Second-round media resolution + catalog-folding regressions.

Measured against the same reference backup as test_media_catalog.py, after the
field-`f` extraction fix landed. Remaining gaps, each quantified before fixing:

  * 231 document previews have no full document in the backup (Telegram purged
    it). 133 of those are still referenced by a surviving message, but
    resolve_media_files only ever built a `telegram-cloud-document-{dc}-{fid}`
    candidate, so none of them could link.
  * Photo suffixes `p` and `u` occur on disk but were missing from the
    candidate list.
  * A cloud photo is cached at several sizes under one file_id, and every size
    was its own gallery item. The largest is the one worth showing.
"""
from __future__ import annotations

from tool.postbox_parser import build_media_catalog, resolve_media_files


# ── resolution ──────────────────────────────────────────────────────────────
def test_orphan_document_preview_resolves_when_full_file_is_gone():
    """The preview is the only surviving copy — link the message to it."""
    fid = 570116660405469205
    index = {f"telegram-cloud-document-size-4-{fid}-m"}

    resolved = resolve_media_files([{"file_id": fid, "dc_id": 4}], index)

    assert [r["filename"] for r in resolved] == [f"telegram-cloud-document-size-4-{fid}-m"]


def test_full_document_wins_over_its_preview():
    """When both exist, the message must point at the full file, not the stub."""
    fid = 5413756493642093641
    index = {
        f"telegram-cloud-document-2-{fid}",
        f"telegram-cloud-document-size-2-{fid}-m",
    }

    resolved = resolve_media_files([{"file_id": fid, "dc_id": 2}], index)

    assert [r["filename"] for r in resolved] == [f"telegram-cloud-document-2-{fid}"]


def test_orphan_preview_resolves_without_a_known_dc():
    """dc_id is absent on field-`f` refs, so the DC sweep must reach previews."""
    fid = 5701166604054692
    index = {f"telegram-cloud-document-size-4-{fid}-m"}

    resolved = resolve_media_files([{"file_id": fid, "dc_id": 0}], index)

    assert len(resolved) == 1


def test_photo_suffixes_p_and_u_resolve():
    for suffix in ("p", "u"):
        fid = 5951647211726769921
        index = {f"telegram-cloud-photo-size-4-{fid}-{suffix}"}

        resolved = resolve_media_files([{"file_id": fid, "dc_id": 4}], index)

        assert len(resolved) == 1, f"suffix {suffix!r} did not resolve"


# ── catalog folding ─────────────────────────────────────────────────────────
def _write(media_dir, name, size):
    (media_dir / name).write_bytes(b"\x00" * size)


def test_photo_sizes_collapse_to_the_largest_variant(tmp_path):
    """One gallery item per photo, showing the best copy we have."""
    media = tmp_path / "media"
    media.mkdir()
    fid = 5962787772773288034
    base = f"telegram-cloud-photo-size-4-{fid}"
    _write(media, f"{base}-s", 900)
    _write(media, f"{base}-m", 8000)
    _write(media, f"{base}-y", 120000)

    catalog = build_media_catalog(media, messages=[])

    assert [e["filename"] for e in catalog] == [f"{base}-y"], (
        "only the largest variant belongs in the gallery"
    )
    assert catalog[0]["thumbnail"] == f"{base}-s"
    assert catalog[0]["size"] == 120000


def test_single_size_photo_is_untouched(tmp_path):
    """No sibling to fold — the entry keeps its own identity and no thumbnail."""
    media = tmp_path / "media"
    media.mkdir()
    name = "telegram-cloud-photo-size-4-111222333444555666-y"
    _write(media, name, 4096)

    catalog = build_media_catalog(media, messages=[])

    assert [e["filename"] for e in catalog] == [name]
    assert catalog[0]["thumbnail"] is None


def test_distinct_photos_are_not_folded_together(tmp_path):
    """Folding keys on file_id — two different photos must both survive."""
    media = tmp_path / "media"
    media.mkdir()
    _write(media, "telegram-cloud-photo-size-4-1000000000000000001-y", 5000)
    _write(media, "telegram-cloud-photo-size-4-1000000000000000002-y", 6000)

    catalog = build_media_catalog(media, messages=[])

    assert len(catalog) == 2


def test_folded_photo_inherits_linkage_of_any_variant(tmp_path):
    """A message resolving to the `-m` copy must still caption the kept `-y`."""
    media = tmp_path / "media"
    media.mkdir()
    fid = 5962787772773288034
    base = f"telegram-cloud-photo-size-4-{fid}"
    _write(media, f"{base}-m", 8000)
    _write(media, f"{base}-y", 120000)
    messages = [{
        "peer_id": 42,
        "peer_name": "Alice",
        "timestamp": 1700000000,
        "date": "2023-11-14T22:13:20+00:00",
        "media": [{"filename": f"{base}-m", "width": 1280, "height": 720}],
    }]

    catalog = build_media_catalog(media, messages=messages)

    assert len(catalog) == 1
    assert catalog[0]["filename"] == f"{base}-y"
    assert catalog[0]["linked_message"]["peer_name"] == "Alice"
