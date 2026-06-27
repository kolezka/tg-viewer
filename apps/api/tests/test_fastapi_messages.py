def test_messages_default(fastapi_client):
    r = fastapi_client.get("/api/messages?per_page=10")
    assert r.status_code == 200
    data = r.json()
    # 3 t7 + 2 fts, but one fts row dedupes against t7 ('msg to bob'),
    # so total is 4, not 5.
    assert data["total"] == 4
    assert data["messages"][0]["timestamp"] == 1700000020  # newest first


def test_messages_filter_by_peer(fastapi_client):
    r = fastapi_client.get("/api/messages?peer_id=111")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 2


def test_messages_filter_by_multiple_peers(fastapi_client):
    r = fastapi_client.get("/api/messages?peer_id=111,222")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 4


def test_messages_search(fastapi_client):
    r = fastapi_client.get("/api/messages?search=alice")
    assert r.status_code == 200
    data = r.json()
    # 'hello from alice' and 'reply to alice' match
    assert data["total"] == 2


def test_messages_search_ignores_structural_fields(fastapi_client):
    # '111' is peer 111's id/phone but appears in no message TEXT — a text
    # search must not match on serialized dict structure.
    r = fastapi_client.get("/api/messages?search=111")
    assert r.status_code == 200
    assert r.json()["total"] == 0


def test_messages_search_matches_text_and_peer_name(fastapi_client):
    # 'msg to bob' (t7) + 'deleted message to bob' (fts) — the dup 'msg to bob'
    # fts row is deduped, so 2 distinct hits.
    r = fastapi_client.get("/api/messages?search=bob")
    assert r.status_code == 200
    assert r.json()["total"] == 2


def test_messages_does_not_mutate_loaded_data(fastapi_client):
    """Regression: old Flask code added _database/_account in place. Must not leak."""
    fastapi_client.get("/api/messages")
    state = fastapi_client.app.state.app_state
    raw_msgs = state.databases["account-1000000001"]["messages"]
    for m in raw_msgs:
        assert "_database" not in m
        assert "_account" not in m
