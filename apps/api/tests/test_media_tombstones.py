"""Dangling-symlink tombstones in the media catalog.

Telegram gives a cached file a second, extension-bearing name by symlinking
`<name>.jpg -> /abs/path/to/<name>` inside its Group Container. When Telegram
later purges the bytes, the symlink is left behind pointing at nothing.

Both `build_media_index` and `build_media_catalog` filter on `Path.is_file()`,
which is False for a broken symlink, so all 241 of them in the reference backup
were invisible — absent from the media catalog, the storage catalog, and the
tombstone list alike. For a tool built to recover deleted content that is a
blind spot, and the dangling name is the only place the original extension
(hence the true media type) survives.

Only links whose target is *not* otherwise present are evidence; the rest are
duplicate views of a file already catalogued and must not be re-added.
"""
from __future__ import annotations

import os

from tool.postbox_parser import build_media_catalog, build_media_index


def _real(media_dir, name, size=1024):
    (media_dir / name).write_bytes(b"\x00" * size)


def _dangling(media_dir, name, target):
    """A symlink to an absolute path that does not exist."""
    os.symlink(f"/nonexistent/live/container/{target}", media_dir / name)


def test_dangling_symlink_is_catalogued_as_deleted(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    _dangling(media, "telegram-cloud-document-4-5780730778723817295.mp3",
              "telegram-cloud-document-4-5780730778723817295")

    catalog = build_media_catalog(media, messages=[])

    assert len(catalog) == 1
    assert catalog[0]["media_type"] == "deleted"


def test_tombstone_recovers_the_media_type_from_the_extension(tmp_path):
    """The extension is the whole point — surviving files don't carry one."""
    media = tmp_path / "media"
    media.mkdir()
    for name, expected in [
        ("a-y.jpg", "image/jpeg"),
        ("b.mp3", "audio/mpeg"),
        ("c.mp4", "video/mp4"),
    ]:
        _dangling(media, name, name.rsplit(".", 1)[0])

    by_name = {e["filename"]: e for e in build_media_catalog(media, messages=[])}

    assert by_name["a-y.jpg"]["mime_type"] == "image/jpeg"
    assert by_name["b.mp3"]["mime_type"] == "audio/mpeg"
    assert by_name["c.mp4"]["mime_type"] == "video/mp4"


def test_tombstone_has_no_size(tmp_path):
    """There are no bytes; reporting 0 would read as an empty file."""
    media = tmp_path / "media"
    media.mkdir()
    _dangling(media, "gone.jpg", "gone")

    entry = build_media_catalog(media, messages=[])[0]

    assert entry["size"] is None
    assert entry["deleted_target"] == "gone"


def test_dangling_link_to_a_file_we_already_have_is_not_duplicated(tmp_path):
    """109 of 241 links in the reference backup are duplicate views."""
    media = tmp_path / "media"
    media.mkdir()
    _real(media, "telegram-cloud-photo-size-4-999-y", 5000)
    _dangling(media, "telegram-cloud-photo-size-4-999-y.jpg",
              "telegram-cloud-photo-size-4-999-y")

    names = [e["filename"] for e in build_media_catalog(media, messages=[])]

    assert names == ["telegram-cloud-photo-size-4-999-y"]


def test_working_symlink_is_still_catalogued_normally(tmp_path):
    """Secret-chat media legitimately arrives as a resolvable symlink."""
    media = tmp_path / "media"
    media.mkdir()
    (tmp_path / "elsewhere").write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
    os.symlink(tmp_path / "elsewhere", media / "secret-file-123-4")

    catalog = build_media_catalog(media, messages=[])

    assert [e["filename"] for e in catalog] == ["secret-file-123-4"]
    assert catalog[0]["media_type"] != "deleted"


def test_media_index_still_excludes_tombstones(tmp_path):
    """Resolution must not point a message at bytes that do not exist."""
    media = tmp_path / "media"
    media.mkdir()
    _dangling(media, "gone.jpg", "gone")

    assert build_media_index(media) == set()


def test_tombstone_survives_the_model_boundary(tmp_path):
    from api.models import MediaItem

    media = tmp_path / "media"
    media.mkdir()
    _dangling(media, "gone.mp4", "gone")

    item = MediaItem(**build_media_catalog(media, messages=[])[0])

    assert item.media_type == "deleted"
    assert item.deleted_target == "gone"
    assert item.size is None
