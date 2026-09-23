"""Sending an alert somewhere a person will see it.

One JSON POST to one URL. The body carries the message under both `text`
(what Slack's incoming webhooks read) and `content` (what Discord's read), so
the same setting works for either without DeployPro knowing which it is talking
to; anything else that accepts JSON gets the structured fields too.

Never raises. An alert that cannot be delivered is logged and dropped: the
thing being alerted about is already going wrong, and the alerting path must
not become a second failure on top of it.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger("deploypro.alerts")

#: Discord rejects messages over 2000 characters, Slack truncates around 40k.
MAX_MESSAGE = 1900


def render(*, title: str, detail: str, level: str) -> str:
    marker = {"critical": "🔴", "warning": "🟠", "resolved": "🟢"}.get(level, "•")
    message = f"{marker} DeployPro: {title}"
    if detail:
        message += f"\n{detail}"
    if len(message) > MAX_MESSAGE:
        message = message[: MAX_MESSAGE - 1] + "…"
    return message


async def send(url: str, *, title: str, detail: str, level: str, **fields: str) -> bool:
    if not url:
        return False
    message = render(title=title, detail=detail, level=level)
    body = {
        "text": message,
        "content": message,
        "deploypro": {"title": title, "detail": detail, "level": level, **fields},
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=body)
        if response.status_code >= 400:
            logger.warning(
                "alert %r was refused: %s %s",
                title,
                response.status_code,
                response.text[:200],
            )
            return False
    except httpx.HTTPError as exc:
        logger.warning("alert %r could not be sent: %s", title, exc)
        return False
    return True
