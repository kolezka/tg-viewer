"""GET /api/ghosts — messages that diverged between two parsed_data snapshots."""
from __future__ import annotations

from fastapi import APIRouter, Query, Request

from api.models import GhostEntry, GhostPage

router = APIRouter(prefix="/api", tags=["ghosts"])

_KINDS = {"removed", "added", "modified"}


@router.get("/ghosts", response_model=GhostPage)
def list_ghosts(
    request: Request,
    account: str = Query(""),
    kind: str = Query("removed"),
    search: str = Query(""),
    page: int = Query(1, ge=1),
    per_page: int = Query(60, ge=1, le=1000),
) -> GhostPage:
    state = request.app.state.app_state
    if kind not in _KINDS:
        kind = "removed"
    needle = search.lower()

    previous_snapshot: str | None = None
    items: list[dict] = []
    for db_name, db_data in state.databases.items():
        if account and db_name != account:
            continue
        history = db_data.get("ghosts_history") or {}
        if not previous_snapshot:
            previous_snapshot = history.get("previous_snapshot")
        for entry in history.get(kind, []):
            # `modified` is {old, new} wrappers — flatten to the new side for
            # display, keep both via the extra fields on the model.
            if kind == "modified":
                flat = {**entry.get("new", {}), "old": entry.get("old", {})}
            else:
                flat = dict(entry)
            if needle:
                hay = " ".join(
                    str(flat.get(k, "")) for k in ("text", "peer_name", "peer_id")
                ).lower()
                if needle not in hay:
                    continue
            items.append({**flat, "account": db_name})

    items.sort(key=lambda e: -(e.get("timestamp") or 0))
    total = len(items)
    start = (page - 1) * per_page
    end = start + per_page

    return GhostPage(
        items=[GhostEntry(**i) for i in items[start:end]],
        total=total,
        page=page,
        per_page=per_page,
        kind=kind,
        previous_snapshot=previous_snapshot,
    )
