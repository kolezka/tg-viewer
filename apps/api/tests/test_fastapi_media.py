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
