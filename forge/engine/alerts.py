"""What is worth waking someone for, and saying it once.

A site that is down is checked every minute; an alert every minute would be
muted within the hour and then miss the next real outage. So a condition
*fires* once when it starts and *resolves* once when it ends, and nothing is
sent in between. One-off events — a failed deploy, a render killed at its
deadline — are sent as they happen.

State is in memory, per worker process. A worker restarted in the middle of an
outage alerts about it once more, which is the right side to err on.
"""

from __future__ import annotations

from forge.adapters import notify


class Alerts:
    def __init__(self, url: str) -> None:
        self._url = url
        self._firing: dict[str, str] = {}

    @property
    def enabled(self) -> bool:
        return bool(self._url)

    def is_firing(self, key: str) -> bool:
        return key in self._firing

    async def fire(
        self, key: str, *, title: str, detail: str = "", **fields: str
    ) -> bool:
        """Start a condition. Sends only if it was not already firing."""
        if key in self._firing:
            return False
        self._firing[key] = title
        await notify.send(
            self._url, title=title, detail=detail, level="critical", **fields
        )
        return True

    async def resolve(
        self, key: str, *, title: str, detail: str = "", **fields: str
    ) -> bool:
        """End a condition. Sends only if it was firing."""
        if self._firing.pop(key, None) is None:
            return False
        await notify.send(
            self._url, title=title, detail=detail, level="resolved", **fields
        )
        return True

    async def event(
        self, *, title: str, detail: str = "", level: str = "warning", **fields: str
    ) -> None:
        """Something that happened once, with nothing to resolve."""
        await notify.send(self._url, title=title, detail=detail, level=level, **fields)
