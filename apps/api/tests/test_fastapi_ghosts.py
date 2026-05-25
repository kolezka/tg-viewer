"""Endpoint tests for GET /api/ghosts.

State-level injection mirrors test_fastapi_logs.py — no on-disk fixture so
the shape changes don't require regenerating JSON files.
"""
from __future__ import annotations


def _seed(client):
    db = client.app.state.app_state.databases["account-1000000001"]
    db["ghosts_history"] = {
        "previous_snapshot": "/tmp/old-snapshot/parsed_data",
        "removed": [
            {"peer_id": 100, "peer_name": "alice", "timestamp": 200, "text": "deleted-one"},
            {"peer_id": 100, "peer_name": "alice", "timestamp": 100, "text": "deleted-two"},
        ],
        "added": [
            {"peer_id": 200, "peer_name": "bob", "timestamp": 300, "text": "new-msg"},
        ],
        "modified": [
            {
                "old": {"peer_id": 100, "peer_name": "alice", "timestamp": 50, "text": "before"},
                "new": {"peer_id": 100, "peer_name": "alice", "timestamp": 50, "text": "after"},
            },
        ],
    }


def test_ghosts_default_returns_removed(fastapi_client):
    _seed(fastapi_client)
    r = fastapi_client.get("/api/ghosts")
    assert r.status_code == 200
    data = r.json()
    assert data["kind"] == "removed"
    assert data["total"] == 2
    # Newest timestamp first
    assert [i["text"] for i in data["items"]] == ["deleted-one", "deleted-two"]
    assert data["previous_snapshot"] == "/tmp/old-snapshot/parsed_data"


def test_ghosts_kind_added(fastapi_client):
    _seed(fastapi_client)
    r = fastapi_client.get("/api/ghosts?kind=added")
    assert r.status_code == 200
    data = r.json()
    assert data["kind"] == "added"
    assert data["total"] == 1
    assert data["items"][0]["text"] == "new-msg"


def test_ghosts_kind_modified_flattens_to_new_side(fastapi_client):
    _seed(fastapi_client)
    r = fastapi_client.get("/api/ghosts?kind=modified")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    item = data["items"][0]
    assert item["text"] == "after"
    # `old` is preserved as a passthrough field via extra="allow"
    assert item["old"]["text"] == "before"


def test_ghosts_invalid_kind_falls_back_to_removed(fastapi_client):
    _seed(fastapi_client)
    r = fastapi_client.get("/api/ghosts?kind=bogus")
    data = r.json()
    assert data["kind"] == "removed"
    assert data["total"] == 2


def test_ghosts_search_matches_text(fastapi_client):
    _seed(fastapi_client)
    r = fastapi_client.get("/api/ghosts?search=one")
    data = r.json()
    assert data["total"] == 1
    assert data["items"][0]["text"] == "deleted-one"


def test_ghosts_pagination(fastapi_client):
    _seed(fastapi_client)
    r = fastapi_client.get("/api/ghosts?per_page=1&page=2")
    data = r.json()
    assert data["total"] == 2
    assert len(data["items"]) == 1
    assert data["items"][0]["text"] == "deleted-two"  # older one on page 2


def test_ghosts_account_filter(fastapi_client):
    _seed(fastapi_client)
    r = fastapi_client.get("/api/ghosts?account=nonexistent")
    data = r.json()
    assert data["total"] == 0
