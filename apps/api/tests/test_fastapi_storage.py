"""Endpoint tests for /api/storage backed by the mini-parsed fixture."""
from __future__ import annotations


def test_storage_lists_all(fastapi_client):
    r = fastapi_client.get("/api/storage")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 3
    assert {item["filename"] for item in data["items"]} == {
        "secret-file-111-4",
        "secret-file-333-4",
        "/abs/path/cached-blob",
    }
    # Tombstones float to the top (on_disk=False before on_disk=True).
    assert data["items"][0]["on_disk"] is False
    # Account tag is stamped on each item, just like /api/media.
    assert all(item["account"] == "account-1000000001" for item in data["items"])


def test_storage_tombstone_only(fastapi_client):
    r = fastapi_client.get("/api/storage?tombstone_only=true")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 2
    assert all(item["on_disk"] is False for item in data["items"])
    names = {item["filename"] for item in data["items"]}
    assert names == {"secret-file-333-4", "/abs/path/cached-blob"}


def test_storage_filter_by_source(fastapi_client):
    r = fastapi_client.get("/api/storage?source=cache-storage")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    assert data["items"][0]["source"] == "cache-storage"
    assert data["items"][0]["absolute_path"] == "/abs/path/cached-blob"


def test_storage_search_matches_absolute_path(fastapi_client):
    r = fastapi_client.get("/api/storage?search=cached")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    assert data["items"][0]["filename"] == "/abs/path/cached-blob"


def test_storage_search_matches_filename(fastapi_client):
    r = fastapi_client.get("/api/storage?search=333")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    assert data["items"][0]["filename"] == "secret-file-333-4"
    assert data["items"][0]["size_bytes"] == 73900


def test_storage_pagination(fastapi_client):
    r = fastapi_client.get("/api/storage?page=1&per_page=2")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 3
    assert len(data["items"]) == 2
    assert data["per_page"] == 2

    r2 = fastapi_client.get("/api/storage?page=2&per_page=2")
    assert r2.status_code == 200
    assert len(r2.json()["items"]) == 1
