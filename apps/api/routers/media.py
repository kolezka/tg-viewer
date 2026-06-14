"""GET /api/media (catalog) and /api/media/{account_id}/{filename} (file)."""
from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

from api.mime import detect_mime
from api.models import MediaItem, MediaPage

router = APIRouter(prefix="/api", tags=["media"])

ACCOUNT_RE = re.compile(r"^account-\d+$")


@router.get("/media", response_model=MediaPage)
def list_media(
    request: Request,
    search: str = Query(""),
    type: str = Query(""),
    account: str = Query(""),
    page: int = Query(1, ge=1),
    per_page: int = Query(60, ge=1, le=1000),
) -> MediaPage:
    state = request.app.state.app_state
    needle = search.lower()

    items: list[dict] = []
    for db_name, db_data in state.databases.items():
        if account and db_name != account:
            continue
        for entry in db_data.get("media_catalog", []):
            if type and entry.get("media_type") != type:
                continue
            if needle:
                hay_parts = [
                    entry.get("filename", ""),
                    entry.get("mime_type", ""),
                    entry.get("media_type", ""),
                ]
                linked = entry.get("linked_message") or {}
                hay_parts.append(linked.get("peer_name") or "")
                if needle not in " ".join(hay_parts).lower():
                    continue
            items.append({**entry, "account": db_name})

    def _sort_key(e: dict) -> int:
        linked = e.get("linked_message") or {}
        return -(linked.get("timestamp") or 0)

    items.sort(key=_sort_key)

    counts: dict[str, int] = {"all": 0}
    for db_data in state.databases.values():
        for entry in db_data.get("media_catalog", []):
            counts["all"] += 1
            mt = entry.get("media_type") or "document"
            counts[mt] = counts.get(mt, 0) + 1

    total = len(items)
    start = (page - 1) * per_page
    end = start + per_page

    return MediaPage(
        media=[MediaItem(**i) for i in items[start:end]],
        total=total,
        page=page,
        per_page=per_page,
        total_pages=(total + per_page - 1) // per_page if per_page else 1,
        counts=counts,
    )


@router.get("/media/{account_id}/{filename}")
def serve_media(account_id: str, filename: str, request: Request):
    if not ACCOUNT_RE.match(account_id):
        raise HTTPException(status_code=400, detail="Invalid account ID")
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    state = request.app.state.app_state
    if not state.backup_dir:
        raise HTTPException(status_code=404, detail="No backup directory configured")

    media_dir = state.backup_dir / account_id / "postbox" / "media"
    filepath = media_dir / filename

    # The filename-level check above ('..', '/', '\\') already prevents
    # URL-based traversal — that's the only attacker-controlled input here.
    # Telegram backups commonly include symlinks pointing elsewhere *within*
    # the backup tree (e.g. secret-chat media files), so a media file may be a
    # symlink whose resolved target still lives under backup_dir. We allow that
    # but reject any resolved path that escapes the backup root (defense in
    # depth). is_relative_to is available on Python 3.9+.
    resolved = filepath.resolve()
    root = state.backup_dir.resolve()
    if not resolved.is_relative_to(root):
        raise HTTPException(status_code=403, detail="Path outside backup root")

    if not filepath.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(
        str(filepath),
        media_type=detect_mime(filepath),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
