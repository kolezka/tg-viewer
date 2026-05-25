"""Endpoint tests for GET /api/logs.

We inject a small log_events fixture into the loaded state — no need to add
a real log_events.json to the on-disk fixture; that would also force us to
re-record real ones whenever the regex evolves. State-level injection keeps
this test focused on the router's filtering / pagination behaviour.
"""
from __future__ import annotations


def _seed_events(client):
    """Replace the loaded log_events on the lone fixture account."""
    db = client.app.state.app_state.databases["account-1000000001"]
    db["log_events"] = [
        {
            "event": "encrypted_message",
            "source_file": "log-A.txt",
            "source_line": 10,
            "log_timestamp": "2026-5-25 22:55:08.290",
            "data": {
                "random_id": 1,
                "chat_id": 100,
                "peer_id": (3 << 32) | 100,
                "date": 1779742508,
                "bytes_size": 104,
                "file": None,
            },
            "in_db": True,
            "db_match": {"peer_id": (3 << 32) | 100, "timestamp": 1779742508},
        },
        {
            "event": "encrypted_message",
            "source_file": "log-B.txt",
            "source_line": 20,
            "log_timestamp": "2026-5-25 23:02:03.604",
            "data": {
                "random_id": 2,
                "chat_id": 200,
                "peer_id": (3 << 32) | 200,
                "date": 1779742923,
                "bytes_size": 2264,
                "file": {"id": 5811993738297220291, "dcId": 4},
            },
            "in_db": False,  # ghost
            "db_match": None,
        },
        {
            "event": "upload_part",
            "source_file": "log-B.txt",
            "source_line": 25,
            "log_timestamp": "2026-5-25 23:01:28.436",
            "data": {"file_id": 4100810929828771308, "file_part": 0, "bytes_size": 16384},
        },
        {
            "event": "pending_removed",
            "source_file": "log-C.txt",
            "source_line": 30,
            "log_timestamp": "2026-5-25 22:52:08.119",
            "data": {
                "chat_id": 100,
                "peer_id": (3 << 32) | 100,
                "msg_index_a": 1,
                "msg_index_b": 8,
            },
        },
    ]


def test_logs_default_returns_all_events(fastapi_client):
    _seed_events(fastapi_client)
    r = fastapi_client.get("/api/logs")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 4
    assert data["counts"]["all"] == 4
    assert data["counts"]["encrypted_message"] == 2
    assert data["counts"]["upload_part"] == 1
    assert data["counts"]["pending_removed"] == 1
    assert data["counts"]["ghost"] == 1


def test_logs_filter_by_event_type(fastapi_client):
    _seed_events(fastapi_client)
    r = fastapi_client.get("/api/logs?event_type=upload_part")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    assert data["events"][0]["event"] == "upload_part"


def test_logs_ghost_only(fastapi_client):
    _seed_events(fastapi_client)
    r = fastapi_client.get("/api/logs?ghost_only=true")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    e = data["events"][0]
    assert e["event"] == "encrypted_message"
    assert e["in_db"] is False
    assert e["data"]["chat_id"] == 200


def test_logs_filter_by_peer_id(fastapi_client):
    _seed_events(fastapi_client)
    peer_id = (3 << 32) | 100
    r = fastapi_client.get(f"/api/logs?peer_id={peer_id}")
    assert r.status_code == 200
    data = r.json()
    # encrypted_message + pending_removed both for chat 100
    assert data["total"] == 2
    events_seen = {e["event"] for e in data["events"]}
    assert events_seen == {"encrypted_message", "pending_removed"}


def test_logs_search_against_source_file(fastapi_client):
    _seed_events(fastapi_client)
    r = fastapi_client.get("/api/logs?search=log-B")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 2  # B.txt has encrypted_message + upload_part


def test_logs_pagination(fastapi_client):
    _seed_events(fastapi_client)
    r = fastapi_client.get("/api/logs?per_page=2&page=1")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 4
    assert data["per_page"] == 2
    assert data["total_pages"] == 2
    assert len(data["events"]) == 2


def test_logs_account_filter_excludes_others(fastapi_client):
    _seed_events(fastapi_client)
    r = fastapi_client.get("/api/logs?account=account-does-not-exist")
    assert r.status_code == 200
    assert r.json()["total"] == 0


def test_logs_event_attaches_account_name(fastapi_client):
    _seed_events(fastapi_client)
    r = fastapi_client.get("/api/logs?event_type=upload_part")
    assert r.status_code == 200
    assert r.json()["events"][0]["account"] == "account-1000000001"


def test_logs_empty_when_no_log_events_loaded(fastapi_client):
    # Default fixture ships no log_events.json so the loader populates [].
    r = fastapi_client.get("/api/logs")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 0
    assert data["counts"]["all"] == 0
