def test_media_catalog(fastapi_client):
    r = fastapi_client.get("/api/media")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    assert data["counts"]["all"] == 1
    assert data["counts"]["photo"] == 1
    assert data["media"][0]["filename"] == "test.jpg"
    assert data["media"][0]["account"] == "account-1000000001"


def test_media_catalog_type_filter(fastapi_client):
    r = fastapi_client.get("/api/media?type=video")
    assert r.status_code == 200
    assert r.json()["total"] == 0


def test_media_file_serves_jpeg(fastapi_client):
    r = fastapi_client.get("/api/media/account-1000000001/test.jpg")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/jpeg")
    assert r.content == b"\xff\xd8\xff"


def test_media_file_rejects_bad_account(fastapi_client):
    r = fastapi_client.get("/api/media/notanaccount/test.jpg")
    assert r.status_code == 400


def test_media_file_rejects_traversal(fastapi_client):
    r = fastapi_client.get("/api/media/account-1000000001/..%2Fevil")
    assert r.status_code in (400, 404)


def test_media_file_404_when_missing(fastapi_client):
    r = fastapi_client.get("/api/media/account-1000000001/missing.jpg")
    assert r.status_code == 404


def _add_avatar(fastapi_client):
    """Inject an avatar entry into the loaded catalog for this test only."""
    state = fastapi_client.app.state.app_state
    catalog = state.databases["account-1000000001"]["media_catalog"]
    catalog.append({
        "filename": "telegram-peer-photo-size-4-1234567890123456789",
        "mime_type": "image/jpeg",
        "media_type": "avatar",
        "size": 10240,
    })
    return catalog


def test_avatars_are_excluded_from_the_unfiltered_gallery(fastapi_client):
    """Avatars outnumber real photos; they must not dilute the default view."""
    catalog = _add_avatar(fastapi_client)
    try:
        data = fastapi_client.get("/api/media").json()

        names = [m["filename"] for m in data["media"]]
        assert "telegram-peer-photo-size-4-1234567890123456789" not in names
        assert data["total"] == 1
        assert data["counts"]["all"] == 1, "'all' must match what the gallery shows"
        assert data["counts"]["avatar"] == 1, "avatars still counted under their own key"
    finally:
        catalog.pop()


def test_avatars_are_reachable_through_their_own_filter(fastapi_client):
    catalog = _add_avatar(fastapi_client)
    try:
        data = fastapi_client.get("/api/media?type=avatar").json()

        assert data["total"] == 1
        assert data["media"][0]["filename"] == "telegram-peer-photo-size-4-1234567890123456789"
        assert data["media"][0]["size"] == 10240
    finally:
        catalog.pop()


def _add_tombstone(fastapi_client):
    state = fastapi_client.app.state.app_state
    catalog = state.databases["account-1000000001"]["media_catalog"]
    catalog.append({
        "filename": "telegram-cloud-document-4-5780730778723817295.mp3",
        "mime_type": "audio/mpeg",
        "media_type": "deleted",
        "size": None,
        "deleted_target": "telegram-cloud-document-4-5780730778723817295",
    })
    return catalog


def test_tombstones_are_excluded_from_the_unfiltered_gallery(fastapi_client):
    """They have no bytes; by default the gallery would be a wall of stubs."""
    catalog = _add_tombstone(fastapi_client)
    try:
        data = fastapi_client.get("/api/media").json()

        assert data["total"] == 1
        assert data["counts"]["all"] == 1
        assert data["counts"]["deleted"] == 1
    finally:
        catalog.pop()


def test_tombstones_are_reachable_and_keep_their_evidence(fastapi_client):
    catalog = _add_tombstone(fastapi_client)
    try:
        item = fastapi_client.get("/api/media?type=deleted").json()["media"][0]

        assert item["size"] is None
        assert item["mime_type"] == "audio/mpeg", "extension is the only type record"
        assert item["deleted_target"] == "telegram-cloud-document-4-5780730778723817295"
    finally:
        catalog.pop()


def test_purged_media_reports_gone_not_a_security_error(fastapi_client, tmp_path):
    """A tombstone is an everyday case, not an attempted escape.

    A dangling symlink resolves to its absolute target in Telegram's live
    container, which is outside the backup root — so it used to trip the
    traversal guard and answer 403 "Path outside backup root". With 133 of
    them in a real backup that both misleads the caller and drowns any genuine
    traversal attempt in the logs.
    """
    import os

    state = fastapi_client.app.state.app_state
    media_dir = state.backup_dir / "account-1000000001" / "postbox" / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    link = media_dir / "purged.mp4"
    os.symlink("/nonexistent/live/container/purged", link)
    try:
        r = fastapi_client.get("/api/media/account-1000000001/purged.mp4")

        assert r.status_code == 410, f"expected 410 Gone, got {r.status_code}"
        assert "outside" not in r.json()["detail"].lower()
    finally:
        link.unlink()


def test_traversal_guard_still_answers_403(fastapi_client, tmp_path):
    """The escape hatch the 410 branch sits in front of must stay shut."""
    import os

    state = fastapi_client.app.state.app_state
    media_dir = state.backup_dir / "account-1000000001" / "postbox" / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"secret")
    link = media_dir / "escape.txt"
    os.symlink(outside, link)
    try:
        r = fastapi_client.get("/api/media/account-1000000001/escape.txt")

        assert r.status_code == 403
    finally:
        link.unlink()
