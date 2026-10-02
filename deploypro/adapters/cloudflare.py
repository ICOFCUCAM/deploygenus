"""Cloudflare's DNS API: the few calls that make and remove a domain's record.

Only what adding and removing a custom domain needs: find the zone a name
lives in, list the records at that name, create one, read one back, delete
one. Every response is Cloudflare's envelope (`success`, `errors`,
`result`); a refusal is raised with Cloudflare's own message, which is
usually the most useful sentence available ("Authentication error",
"Record already exists").
"""

from __future__ import annotations

from typing import Any

import httpx

from deploypro.domain.errors import DnsProviderError

TIMEOUT = httpx.Timeout(15.0)

#: Tests replace this with an `httpx.MockTransport`.
transport: httpx.AsyncBaseTransport | None = None


async def _call(
    method: str,
    url: str,
    token: str,
    *,
    params: dict | None = None,
    json: dict | None = None,
    allow_404: bool = False,
) -> Any:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, transport=transport) as client:
            response = await client.request(
                method,
                url,
                params=params,
                json=json,
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.HTTPError as exc:
        raise DnsProviderError(f"Could not reach Cloudflare: {exc}") from exc
    if allow_404 and response.status_code == 404:
        return None
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code >= 400 or not body.get("success", False):
        errors = body.get("errors") or []
        said = "; ".join(str(e.get("message", e)) for e in errors) or response.text[:200]
        raise DnsProviderError(f"Cloudflare said {response.status_code}: {said}")
    return body.get("result")


def candidate_zones(host: str) -> list[str]:
    """`a.b.example.com` → `a.b.example.com`, `b.example.com`, `example.com`:
    every name the zone could be, longest first. Never a bare TLD."""
    labels = host.lower().rstrip(".").split(".")
    return [".".join(labels[i:]) for i in range(len(labels) - 1)]


async def find_zone(api_url: str, token: str, host: str) -> dict | None:
    """The active zone containing `host` that the token can see, or None."""
    for name in candidate_zones(host):
        zones = await _call(
            "GET", f"{api_url}/zones", token, params={"name": name, "status": "active"}
        )
        if zones:
            return zones[0]
    return None


async def records_at(api_url: str, token: str, zone_id: str, name: str) -> list[dict]:
    """Every record at exactly `name`, of any type."""
    return (
        await _call(
            "GET",
            f"{api_url}/zones/{zone_id}/dns_records",
            token,
            params={"name": name, "per_page": 100},
        )
        or []
    )


async def create_record(
    api_url: str,
    token: str,
    zone_id: str,
    *,
    type_: str,
    name: str,
    content: str,
    comment: str,
) -> dict:
    return await _call(
        "POST",
        f"{api_url}/zones/{zone_id}/dns_records",
        token,
        json={
            "type": type_,
            "name": name,
            "content": content,
            "ttl": 1,  # automatic
            "proxied": False,
            "comment": comment,
        },
    )


async def get_record(
    api_url: str, token: str, zone_id: str, record_id: str
) -> dict | None:
    return await _call(
        "GET",
        f"{api_url}/zones/{zone_id}/dns_records/{record_id}",
        token,
        allow_404=True,
    )


async def delete_record(api_url: str, token: str, zone_id: str, record_id: str) -> None:
    await _call(
        "DELETE",
        f"{api_url}/zones/{zone_id}/dns_records/{record_id}",
        token,
        allow_404=True,
    )
