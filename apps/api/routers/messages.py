"""GET /api/messages — paginated/filterable messages with FTS dedup."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from api.models import Message, MessagesPage

router = APIRouter(prefix="/api", tags=["messages"])


@router.get("/messages", response_model=MessagesPage)
def list_messages(
    request: Request,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=1000),
    database: str = Query(""),
    search: str = Query(""),
    peer_id: str = Query(""),
) -> MessagesPage:
    state = request.app.state.app_state
    needle = search.lower()

    peer_id_set: set[str] = set(peer_id.split(",")) if peer_id else set()

    all_messages: list[dict[str, Any]] = []

    db_keys = [database] if database else list(state.databases.keys())

    for db in db_keys:
        db_data = state.databases.get(db, {})
        messages = db_data.get("messages", [])

        # Build a {filename → catalog_entry} lookup so we can enrich each
        # message's media[] items with media_type / mime_type — those fields
        # only live in media_catalog.json, but the frontend's MediaTile needs
        # them to decide between <img>, <video>, etc. (especially for secret
        # chats where filenames have no extension).
        catalog_by_filename: dict[str, dict[str, Any]] = {}
        for entry in db_data.get("media_catalog", []):
            fname = entry.get("filename")
            if fname:
                catalog_by_filename[fname] = entry

        t7_keys: set[tuple[str, str]] = set()

        for msg in messages:
            if peer_id_set and str(msg.get("peer_id", "")) not in peer_id_set:
                continue

            # Shallow-copy before annotating to avoid mutating loaded data.
            annotated = {**msg, "_database": db, "_account": db}

            # Enrich inline media[] with media_type / mime_type from the catalog.
            inline_media = annotated.get("media")
            if isinstance(inline_media, list) and inline_media:
                enriched: list[dict[str, Any]] = []
                for mi in inline_media:
                    if not isinstance(mi, dict):
                        enriched.append(mi)
                        continue
                    fname = mi.get("filename") or ""
                    cat = catalog_by_filename.get(fname)
                    if cat:
                        mi = {
                            **mi,
                            "media_type": mi.get("media_type") or cat.get("media_type"),
                            "mime_type": mi.get("mime_type") or cat.get("mime_type"),
                        }
                    enriched.append(mi)
                annotated["media"] = enriched

            text = msg.get("text", "")
            if text:
                pid = msg.get("peer_id", "")
                t7_keys.add((str(pid), text))
                # Also key on the bare lower-32-bit id so an FTS peer_ref (which
                # drops the namespace hi-word) dedupes against a namespaced peer.
                if isinstance(pid, int):
                    t7_keys.add((str(pid & 0xFFFFFFFF), text))

            # Search the message text and peer labels — NOT the serialized dict,
            # which would match on structural noise (field names, ids, flags).
            if needle:
                hay = " ".join(
                    str(annotated.get(k, ""))
                    for k in ("text", "peer_name", "peer_username")
                ).lower()
                if needle not in hay:
                    continue

            all_messages.append(annotated)

        fts_peer_refs = {f"p{pid}" for pid in peer_id_set} if peer_id_set else set()
        for fts_msg in db_data.get("messages_fts", []):
            ref = str(fts_msg.get("peer_ref", ""))
            if fts_peer_refs and ref not in fts_peer_refs:
                continue
            fts_text = fts_msg.get("text", "")
            peer_str = ref.lstrip("p")
            if (peer_str, fts_text) in t7_keys:
                continue
            if needle and needle not in fts_text.lower():
                continue
            all_messages.append(
                {
                    "text": fts_text,
                    "peer_id": peer_str,
                    "source": "fts",
                    "fts_id": fts_msg.get("fts_id"),
                    "msg_ref": fts_msg.get("msg_ref", ""),
                    "timestamp": 0,
                    "outgoing": None,
                    "_database": db,
                    "_account": db,
                }
            )

    def _ts(x: dict[str, Any]) -> int:
        ts = x.get("timestamp")
        return ts if isinstance(ts, (int, float)) else 0

    all_messages.sort(key=_ts, reverse=True)

    start = (page - 1) * per_page
    end = start + per_page
    paginated = all_messages[start:end]

    return MessagesPage(
        messages=[Message(**m) for m in paginated],
        total=len(all_messages),
        page=page,
        per_page=per_page,
        total_pages=(len(all_messages) + per_page - 1) // per_page if per_page else 1,
    )
