"""Endpoint tests for GET /api/forensics — state-level fixture injection."""
from __future__ import annotations


def _seed(client):
    db = client.app.state.app_state.databases["account-1000000001"]
    db["forensic_index"] = [
        {
            "file_id": 100,
            "filenames": ["secret-file-100-4"],
            "size_bytes": 73900,
            "on_disk": False,
            "tombstone": True,
            "sources": ["log", "message", "storage"],
            "log_event": {"dcId": 4, "accessHash": -1, "size": 73904,
                          "keyFingerprint": -2, "chatId": 999, "date": 1779742923,
                          "source_file": "log-A.txt", "source_line": 10},
            "message": {"account": "account-1000000001", "peer_id": 14656299518,
                        "peer_name": "alice", "timestamp": 1779742923,
                        "date": "2026-05-25T20:00:00+00:00", "outgoing": False},
        },
        {
            "file_id": 200,
            "filenames": ["telegram-cloud-document-2-200"],
            "size_bytes": 1024,
            "on_disk": True,
            "tombstone": False,
            "sources": ["message", "storage"],
            "log_event": None,
            "message": {"account": "account-1000000001", "peer_id": 1,
                        "peer_name": "bob", "timestamp": 1779742000,
                        "date": "2026-05-25T19:50:00+00:00", "outgoing": True},
        },
        {
            "file_id": 999,
            "filenames": [],
            "size_bytes": 50,
            "on_disk": False,
            "tombstone": False,
            "sources": ["log"],
            "log_event": {"dcId": 4, "accessHash": 0, "size": 50,
                          "keyFingerprint": 0, "chatId": 1, "date": 1000,
                          "source_file": "log-B.txt", "source_line": 5},
            "message": None,
        },
    ]


def test_forensics_returns_all_with_counts(fastapi_client):
    _seed(fastapi_client)
    r = fastapi_client.get("/api/forensics")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 3
    assert data["counts"]["all"] == 3
    assert data["counts"]["tombstone"] == 1
    assert data["counts"]["with_message"] == 2
    assert data["counts"]["with_log"] == 2


def test_forensics_tombstone_only(fastapi_client):
    _seed(fastapi_client)
    r = fastapi_client.get("/api/forensics?tombstone_only=true")
    data = r.json()
    assert data["total"] == 1
    assert data["items"][0]["file_id"] == 100


def test_forensics_with_message_filter(fastapi_client):
    _seed(fastapi_client)
    r = fastapi_client.get("/api/forensics?with_message=true")
    data = r.json()
    assert data["total"] == 2
    assert {i["file_id"] for i in data["items"]} == {100, 200}


def test_forensics_search_by_filename(fastapi_client):
    _seed(fastapi_client)
    r = fastapi_client.get("/api/forensics?search=cloud-document")
    data = r.json()
    assert data["total"] == 1
    assert data["items"][0]["file_id"] == 200


def test_forensics_search_by_peer_name(fastapi_client):
    _seed(fastapi_client)
    r = fastapi_client.get("/api/forensics?search=alice")
    data = r.json()
    assert data["total"] == 1
    assert data["items"][0]["file_id"] == 100


def test_forensics_search_by_file_id(fastapi_client):
    _seed(fastapi_client)
    r = fastapi_client.get("/api/forensics?search=999")
    data = r.json()
    assert data["total"] == 1
    assert data["items"][0]["file_id"] == 999


def test_forensics_pagination(fastapi_client):
    _seed(fastapi_client)
    r = fastapi_client.get("/api/forensics?per_page=2&page=2")
    data = r.json()
    assert data["total"] == 3
    assert len(data["items"]) == 1
