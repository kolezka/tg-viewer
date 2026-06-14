"""Application state container — replaces module-level globals from old webui.py."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class AppState:
    """Holds loaded telegram data and the directories it was loaded from.

    Stored on the FastAPI app as `app.state.app_state` during lifespan startup.
    """
    telegram_data: dict[str, Any] = field(default_factory=dict)
    export_dir: Path | None = None
    backup_dir: Path | None = None

    # Memoized chat count for /api/stats, which otherwise rebuilds the full
    # chat list (O(N) over every message) just to take its len() on each
    # request. `telegram_data` is loaded once per process and only ever
    # *reassigned* on (re)load — never mutated in place by the running app —
    # so we key the cache on its object identity: a reassignment yields a new
    # id() and transparently invalidates the memo.
    _chat_count_cache: tuple[int, int] | None = field(
        default=None, repr=False, compare=False
    )

    @property
    def databases(self) -> dict[str, Any]:
        return self.telegram_data.get("databases", {})

    def chat_count(self) -> int:
        """len(compute_chats(self)), memoized while telegram_data is unchanged."""
        from api.chats_logic import compute_chats

        token = id(self.telegram_data)
        cache = self._chat_count_cache
        if cache is not None and cache[0] == token:
            return cache[1]
        count = len(compute_chats(self))
        self._chat_count_cache = (token, count)
        return count
