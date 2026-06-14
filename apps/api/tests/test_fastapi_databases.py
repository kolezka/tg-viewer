def test_databases_list(fastapi_client):
    r = fastapi_client.get("/api/databases")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["name"] == "account-1000000001"
    assert data[0]["decrypted"] is True
    assert data[0]["message_count"] == 3
    assert "t2 (peers)" in data[0]["tables"]


def test_database_detail_404(fastapi_client):
    r = fastapi_client.get("/api/database/account-doesnotexist")
    assert r.status_code == 404


def test_database_detail_payload(fastapi_client):
    r = fastapi_client.get("/api/database/account-1000000001")
    assert r.status_code == 200
    data = r.json()
    assert data["decrypted"] is True
    assert len(data["messages"]) == 3
    assert len(data["peers"]) == 2
    # Heavy collections are now stripped from this unpaginated endpoint; they
    # are served via their own paginated routes (/api/logs, /api/storage, etc.).
    for heavy in ("messages_fts", "storage_catalog", "log_events", "ghosts_history", "forensic_index"):
        assert heavy not in data
