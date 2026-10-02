"""A custom domain's DNS record, made on Cloudflare when DeployPro can.

docs/design/proposal-cloudflare-dns.md. When the domain is in a Cloudflare
zone the installation's token can see, and nothing is at that name yet,
DeployPro makes the record itself: A (and AAAA) records to the addresses the
deployment domain resolves to, DNS only, with a comment saying who made
them. Every other case is the manual path the Domains page always had, and
the result says why it was taken.

A, not CNAME: a CNAME cannot share its name with any other record, and a
domain's apex nearly always has some — mail, verification TXT records. The
manual instructions offer the same A record.

NEVER OVERWRITE. A record already at the name is somebody's decision, so it
is either already pointing here (fine) or left alone with an explanation.
And removal deletes only what DeployPro made, and only while it is still
exactly what was made.
"""

from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass, field

from deploypro.adapters import cloudflare
from deploypro.config import Settings
from deploypro.domain.errors import DnsProviderError
from deploypro.domain.models import Domain
from deploypro.engine import verify

logger = logging.getLogger("deploypro.dns")

ADDRESS_TYPES = ("A", "AAAA")


def enabled(settings: Settings) -> bool:
    """Whether DeployPro can try to make records at all."""
    return bool(settings.cloudflare_token)


@dataclass(frozen=True, slots=True)
class Outcome:
    """What happened to a domain's DNS, in a sentence the page can show."""

    detail: str
    #: Records made just now: [{"id", "type", "content"}, ...].
    made: list[dict] = field(default_factory=list)
    zone_id: str | None = None
    #: Records at the name already point here, so verifying can go ahead.
    already_here: bool = False


def comment_for(project_slug: str) -> str:
    return f"DeployPro: {project_slug}"


async def ensure_record(settings: Settings, host: str, *, project_slug: str) -> Outcome:
    """Make `host`'s record on Cloudflare if DeployPro can and should."""
    if not enabled(settings):
        return Outcome("Add the DNS record below at your DNS provider, then verify.")
    api, token = settings.cloudflare_api_url, settings.cloudflare_token
    try:
        zone = await cloudflare.find_zone(api, token, host)
        if zone is None:
            return Outcome(
                f"{host} is not in a Cloudflare zone DeployPro's token can reach, so "
                "add its DNS record by hand, below. (To have DeployPro make it, add "
                "the zone to the token's Zone Resources on Cloudflare.)"
            )
        addresses = await verify.resolve(settings.deploy_domain)
        if not addresses:
            return Outcome(
                f"The platform's own domain {settings.deploy_domain} does not resolve, "
                "so there is no address to point at. Fix the wildcard DNS record first."
            )
        existing = [
            r
            for r in await cloudflare.records_at(api, token, zone["id"], host)
            if r.get("type") in (*ADDRESS_TYPES, "CNAME")
        ]
        if existing:
            if _points_here(existing, addresses, settings.deploy_domain):
                return Outcome(
                    f"{host} already has a DNS record pointing here.",
                    zone_id=zone["id"],
                    already_here=True,
                )
            found = ", ".join(f"{r['type']} {r.get('content', '')}" for r in existing)
            return Outcome(
                f"{host} already has a DNS record on Cloudflare ({found}). DeployPro "
                "does not replace records it did not make: change it to the record "
                "below, or delete it on Cloudflare and press Create DNS record."
            )
        made = []
        for address in addresses:
            type_ = "AAAA" if ipaddress.ip_address(address).version == 6 else "A"
            record = await cloudflare.create_record(
                api,
                token,
                zone["id"],
                type_=type_,
                name=host,
                content=address,
                comment=comment_for(project_slug),
            )
            made.append({"id": record["id"], "type": type_, "content": address})
        logger.info("made %s record(s) for %s in zone %s", len(made), host, zone["name"])
        return Outcome(
            f"Made its DNS record on Cloudflare ({', '.join(addresses)}).",
            made=made,
            zone_id=zone["id"],
        )
    except DnsProviderError as exc:
        return Outcome(f"{exc.message} Add the DNS record by hand, below.")


def _points_here(
    records: list[dict], addresses: tuple[str, ...], deploy_domain: str
) -> bool:
    for r in records:
        content = str(r.get("content", "")).rstrip(".").lower()
        if r.get("type") == "CNAME":
            if content != deploy_domain.lower():
                return False
        elif content not in addresses:
            return False
    return True


async def remove_records(settings: Settings, domain: Domain) -> str:
    """Delete the records DeployPro made for `domain`, if still unchanged.

    Returns a sentence for the confirmation, or "" when there was nothing of
    DeployPro's to remove.
    """
    if not domain.dns_managed or not domain.dns_zone_id:
        return ""
    if not enabled(settings):
        return (
            " Its DNS record on Cloudflare was left in place (Cloudflare is no "
            "longer configured)."
        )
    api, token = settings.cloudflare_api_url, settings.cloudflare_token
    kept = 0
    try:
        for made in domain.dns_records:
            current = await cloudflare.get_record(
                api, token, domain.dns_zone_id, made["id"]
            )
            if current is None:
                continue
            if (
                current.get("type") != made["type"]
                or current.get("content") != made["content"]
            ):
                kept += 1
                continue
            await cloudflare.delete_record(api, token, domain.dns_zone_id, made["id"])
    except DnsProviderError as exc:
        return (
            f" Its DNS record on Cloudflare could not be removed ({exc.message}); "
            "delete it there."
        )
    if kept:
        return (
            " Its DNS record on Cloudflare was changed since DeployPro made it, so "
            "it was left in place."
        )
    return " Its DNS record on Cloudflare was deleted too."
