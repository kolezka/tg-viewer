"""GET /api/storage — the plaintext-sidecar storage catalog with tombstones."""
from __future__ import annotations

from fastapi import APIRouter, Query, Request

from api.models import StorageEntry, StoragePage

router = APIRouter(prefix="/api", tags=["storage"])


@router.get("/storage", response_model=StoragePage)
def list_storage(
    request: Request,
    tombstone_only: bool = Query(False),
    source: str = Query(""),
    search: str = Query(""),
    page: int = Query(1, ge=1),
    per_page: int = Query(100, ge=1, le=2000),
) -> StoragePage:
    state = request.app.state.app_state
    needle = search.lower()

    items: list[dict] = []
    for db_name, db_data in state.databases.items():
        for entry in db_data.get("storage_catalog", []):
            if tombstone_only and entry.get("on_disk", True):
                continue
            if source and entry.get("source") != source:
                continue
            if needle:
                hay_parts = [
                    entry.get("filename", ""),
                    entry.get("source", ""),
                    entry.get("absolute_path") or "",
                ]
                if needle not in " ".join(hay_parts).lower():
                    continue
            items.append({**entry, "account": db_name})

    # Tombstones first, then by filename — forensic signal up top.
    items.sort(key=lambda e: (bool(e.get("on_disk", True)), e.get("filename", "")))

    total = len(items)
    start = (page - 1) * per_page
    end = start + per_page

    return StoragePage(
        items=[StorageEntry(**i) for i in items[start:end]],
        total=total,
        page=page,
        per_page=per_page,
    )
