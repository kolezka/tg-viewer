"""FastAPI app factory and lifespan."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from api.loader import load_telegram_data
from api.state import AppState


WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"


def create_app(data_dir: str | Path | None = None, account: str | None = None) -> FastAPI:
    """Build the FastAPI app, loading data_dir into app.state at startup.

    `data_dir` may be None for tests that override state directly via
    `app.state.app_state = AppState(...)` after construction.

    `account` (optional) restricts loading to a single `account-{id}` directory.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if data_dir is not None:
            app.state.app_state = load_telegram_data(data_dir, account=account)
        else:
            app.state.app_state = getattr(app.state, "app_state", AppState())
        yield

    app = FastAPI(
        title="tg-viewer",
        description="Telegram cache viewer API",
        version="1.0.0",
        lifespan=lifespan,
    )

    # API routers FIRST. The order matters because StaticFiles(html=True) below
    # is a catch-all that swallows any unmatched path.
    from api.routers import databases, users, chats, messages, media, stats, export_data, logs, storage, ghosts, forensics
    app.include_router(databases.router)
    app.include_router(users.router)
    app.include_router(chats.router)
    app.include_router(messages.router)
    app.include_router(media.router)
    app.include_router(storage.router)
    app.include_router(stats.router)
    app.include_router(export_data.router)
    app.include_router(logs.router)
    app.include_router(ghosts.router)
    app.include_router(forensics.router)

    # Mount the React bundle at /. If apps/web/dist/ is missing (e.g., in a fresh
    # CI checkout or direct `python -m api` without a build), surface a clear
    # message at / instead of FastAPI's default 404 — the API itself still works.
    if WEB_DIST.is_dir():
        app.mount("/", StaticFiles(directory=str(WEB_DIST), html=True), name="webdist")
    else:
        from fastapi.responses import HTMLResponse

        @app.get("/", response_class=HTMLResponse, include_in_schema=False)
        def _frontend_missing() -> HTMLResponse:
            return HTMLResponse(
                """<!doctype html><meta charset="utf-8">
<title>tg-viewer — frontend not built</title>
<style>body{font:14px system-ui;margin:40px;max-width:640px}code{background:#f4f4f4;padding:2px 6px;border-radius:3px}</style>
<h1>Frontend not built</h1>
<p>The React bundle at <code>apps/web/dist/</code> doesn't exist yet.</p>
<p>Run one of:</p>
<ul>
  <li><code>./tg-viewer webui &lt;DIR&gt;</code> — auto-installs Bun deps and builds.</li>
  <li><code>cd apps/web &amp;&amp; bun install &amp;&amp; bun run build</code> — manual build.</li>
</ul>
<p>The API itself is up — try <a href="/docs">/docs</a> or <a href="/api/stats">/api/stats</a>.</p>
""",
                status_code=503,
            )

    return app
