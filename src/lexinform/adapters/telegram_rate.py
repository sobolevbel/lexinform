"""Shared pacing and flood-control deadlines for one Telegram client."""

from collections.abc import Callable
from threading import Lock


class TelegramRateLimiter:
    CHAT_INTERVAL = 3.1
    GLOBAL_INTERVAL = 0.051
    COOLDOWN_MARGIN = 0.1

    def __init__(self, *, monotonic: Callable[[], float], sleep: Callable[[float], None]) -> None:
        self._monotonic = monotonic
        self._sleep = sleep
        self._lock = Lock()
        self._cooldown = 0.0
        self._global_next = 0.0
        self._chat_next: dict[str, float] = {}
        self._aliases: dict[str, str] = {}

    def wait(self, chat_id: str | None) -> None:
        while True:
            with self._lock:
                now = self._monotonic()
                deadline = self._cooldown
                key = self._key(chat_id) if chat_id is not None else None
                if key is not None:
                    deadline = max(deadline, self._global_next, self._chat_next.get(key, 0.0))
                delay = deadline - now
                if delay <= 0:
                    if key is not None:
                        self._global_next = now + self.GLOBAL_INTERVAL
                        self._chat_next[key] = now + self.CHAT_INTERVAL
                    return
            self._sleep(delay)

    def defer(self, seconds: float) -> None:
        with self._lock:
            self._cooldown = max(self._cooldown, self._monotonic() + seconds + self.COOLDOWN_MARGIN)

    def identify(self, chat_id: str, numeric_id: int, username: str | None) -> None:
        with self._lock:
            canonical = str(numeric_id)
            aliases = [chat_id, canonical]
            if username:
                aliases.append(f"@{username}")
            keys = {self._key(alias) for alias in aliases}
            deadline = max(self._chat_next.get(key, 0.0) for key in keys)
            for key in keys:
                self._aliases[key] = canonical
            self._chat_next[canonical] = deadline

    def _key(self, chat_id: str) -> str:
        key = chat_id.lower() if chat_id.startswith("@") else chat_id
        return self._aliases.get(key, key)
