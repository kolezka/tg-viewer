"""GET /api/forensics — files joined across storage / log / message sources."""
from __future__ import annotations

from fastapi import APIRouter, Query, Request

from api.models import ForensicEntry, ForensicPage

router = APIRouter(prefix="/api", tags=["forensics"])


@router.get("/forensics", response_model=ForensicPage)
def list_forensics(
    request: Request,
    tombstone_only: bool = Query(False),
    with_message: bool = Query(False),
    with_log: bool = Query(False),
    account: str = Query(""),
    search: str = Query(""),
    page: int = Query(1, ge=1),
    per_page: int = Query(100, ge=1, le=1000),
) -> ForensicPage:
    state = request.app.state.app_state
    needle = search.lower()

    items: list[dict] = []
    for db_name, db_data in state.databases.items():
        if account and db_name != account:
            continue
        for entry in db_data.get("forensic_index", []):
            if tombstone_only and not entry.get("tombstone"):
                continue
            if with_message and not entry.get("message"):
                continue
            if with_log and not entry.get("log_event"):
                continue
            if needle:
                hay_parts = [
                    str(entry.get("file_id", "")),
                    " ".join(entry.get("filenames", [])),
                    (entry.get("message") or {}).get("peer_name") or "",
                ]
                if needle not in " ".join(hay_parts).lower():
                    continue
            items.append({**entry, "account": db_name})

    # Already pre-sorted by build_forensic_index (tombstones first, then
    # newest activity); search/filter passes preserve order.
    counts = {
        "all": len(items),
        "tombstone": sum(1 for i in items if i.get("tombstone")),
        "with_message": sum(1 for i in items if i.get("message")),
        "with_log": sum(1 for i in items if i.get("log_event")),
    }

    total = counts["all"]
    start = (page - 1) * per_page
    end = start + per_page

    return ForensicPage(
        items=[ForensicEntry(**i) for i in items[start:end]],
        total=total,
        page=page,
        per_page=per_page,
        counts=counts,
    )
