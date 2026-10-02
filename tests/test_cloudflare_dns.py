"""Automatic DNS records on Cloudflare, with the manual path for everything else.

docs/design/proposal-cloudflare-dns.md. The engine and the adapter are run
together against a stand-in Cloudflare API that keeps real state, so what is
tested is what is sent, not what a mock was told to return.
"""

# ruff: noqa: F811 - the dashboard fixtures are imported, then used as arguments

from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest

from deploypro.adapters import cloudflare
from deploypro.engine import dns
from tests import fakes
from tests.test_dashboard import anon, app, client, repos  # noqa: F401

SERVER = "157.180.122.108"


class FakeCloudflare:
    """Zones and records, behind Cloudflare's envelope and URL shapes."""

    def __init__(self, zones=("example.com",), fail=False):
        self.zones = {f"zone-{name}": name for name in zones}
        self.records: dict[str, dict] = {}
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    def add(self, zone, type_, name, content, comment=""):
        rid = f"rec-{len(self.records) + 1}"
        self.records[rid] = {
            "id": rid,
            "zone_id": f"zone-{zone}",
            "type": type_,
            "name": name,
            "content": content,
            "comment": comment,
            "proxied": False,
        }
        return rid

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path))
        if self.fail:
            return httpx.Response(
                403,
                json={"success": False, "errors": [{"message": "Authentication error"}]},
            )
        parts = request.url.path.split("/client/v4/", 1)[1].split("/")
        if parts == ["zones"]:
            name = request.url.params["name"]
            found = [{"id": k, "name": v} for k, v in self.zones.items() if v == name]
            return ok(found)
        zone_id = parts[1]
        if len(parts) == 3:  # /zones/{id}/dns_records
            if request.method == "GET":
                name = request.url.params["name"]
                return ok(
                    [
                        r
                        for r in self.records.values()
                        if r["zone_id"] == zone_id and r["name"] == name
                    ]
                )
            body = json.loads(request.content)
            assert body["proxied"] is False
            rid = self.add(
                self.zones[zone_id],
                body["type"],
                body["name"],
                body["content"],
                body["comment"],
            )
            return ok(self.records[rid])
        rid = parts[3]
        if rid not in self.records:
            return httpx.Response(404, json={"success": False, "errors": []})
        if request.method == "DELETE":
            del self.records[rid]
            return ok({"id": rid})
        return ok(self.records[rid])


def ok(result) -> httpx.Response:
    return httpx.Response(200, json={"success": True, "errors": [], "result": result})


@pytest.fixture
def cf(monkeypatch):
    fake = FakeCloudflare()
    monkeypatch.setattr(cloudflare, "transport", httpx.MockTransport(fake))
    monkeypatch.setattr(dns.verify, "resolve", lambda host: _async((SERVER,)))
    return fake


SETTINGS = SimpleNamespace(
    cloudflare_token="cf-token",
    cloudflare_api_url="https://api.cloudflare.test/client/v4",
    deploy_domain="deploys.deploypro.us",
)


class TestMaking:
    def test_the_zone_is_looked_for_from_the_longest_name(self):
        assert cloudflare.candidate_zones("tv.shop.example.com") == [
            "tv.shop.example.com",
            "shop.example.com",
            "example.com",
        ]

    async def test_a_name_with_nothing_gets_an_a_record_dns_only(self, cf):
        outcome = await dns.ensure_record(SETTINGS, "tv.example.com", project_slug="vid")
        assert outcome.made == [{"id": "rec-1", "type": "A", "content": SERVER}]
        (record,) = cf.records.values()
        assert (
            record["name"] == "tv.example.com" and record["comment"] == "DeployPro: vid"
        )

    async def test_the_apex_works_beside_mail_records(self, cf):
        """Why A and not CNAME: a CNAME can't share its name with an MX."""
        cf.add("example.com", "MX", "example.com", "mail.example.com")
        outcome = await dns.ensure_record(SETTINGS, "example.com", project_slug="vid")
        assert [m["type"] for m in outcome.made] == ["A"]

    async def test_an_ipv6_address_gets_an_aaaa(self, cf, monkeypatch):
        monkeypatch.setattr(
            dns.verify, "resolve", lambda host: _async((SERVER, "2a01:4f9::1"))
        )
        outcome = await dns.ensure_record(SETTINGS, "tv.example.com", project_slug="vid")
        assert [m["type"] for m in outcome.made] == ["A", "AAAA"]

    async def test_a_record_pointing_elsewhere_is_never_replaced(self, cf):
        cf.add("example.com", "A", "tv.example.com", "203.0.113.9")
        outcome = await dns.ensure_record(SETTINGS, "tv.example.com", project_slug="vid")
        assert outcome.made == [] and not outcome.already_here
        assert "A 203.0.113.9" in outcome.detail and "does not replace" in outcome.detail
        assert [c for c in cf.calls if c[0] == "POST"] == []

    async def test_a_record_already_pointing_here_is_used(self, cf):
        cf.add("example.com", "CNAME", "tv.example.com", "deploys.deploypro.us")
        outcome = await dns.ensure_record(SETTINGS, "tv.example.com", project_slug="vid")
        assert outcome.already_here and outcome.made == []

    async def test_a_zone_the_token_cannot_see_is_the_manual_path(self, cf):
        outcome = await dns.ensure_record(SETTINGS, "tv.other.org", project_slug="vid")
        assert outcome.made == [] and "by hand" in outcome.detail
        assert "Zone Resources" in outcome.detail

    async def test_cloudflare_refusing_is_the_manual_path_too(self, cf):
        cf.fail = True
        outcome = await dns.ensure_record(SETTINGS, "tv.example.com", project_slug="vid")
        assert "Authentication error" in outcome.detail and "by hand" in outcome.detail

    async def test_without_cloudflare_nothing_is_called(self, cf):
        off = replace_ns(SETTINGS, cloudflare_token="")
        outcome = await dns.ensure_record(off, "tv.example.com", project_slug="vid")
        assert outcome.made == [] and cf.calls == []


class TestRemoving:
    def domain(self, records):
        return replace(
            fakes.domain("tv.example.com", verified=True, primary=False),
            dns_zone_id="zone-example.com",
            dns_records=tuple(records),
        )

    async def test_what_deploypro_made_is_deleted(self, cf):
        rid = cf.add("example.com", "A", "tv.example.com", SERVER, "DeployPro: vid")
        said = await dns.remove_records(
            SETTINGS, self.domain([{"id": rid, "type": "A", "content": SERVER}])
        )
        assert cf.records == {} and "deleted too" in said

    async def test_a_record_changed_since_is_left(self, cf):
        rid = cf.add("example.com", "A", "tv.example.com", "203.0.113.9")
        said = await dns.remove_records(
            SETTINGS, self.domain([{"id": rid, "type": "A", "content": SERVER}])
        )
        assert rid in cf.records and "left in place" in said

    async def test_a_manual_domain_touches_nothing(self, cf):
        said = await dns.remove_records(SETTINGS, fakes.domain("tv.example.com"))
        assert said == "" and cf.calls == []


class TestPages:
    async def test_the_manual_page_is_unchanged_without_cloudflare(self, client, repos):
        body = (await client.get("/projects/blog/config/domains")).text
        assert "Create DNS record" not in body
        assert "Add a CNAME record for" in body

    async def test_with_cloudflare_a_waiting_domain_offers_to_make_it(
        self, client, repos, monkeypatch
    ):
        monkeypatch.setattr(dns, "enabled", lambda settings: True)
        body = (await client.get("/projects/blog/config/domains")).text
        assert "Create DNS record" in body
        # The manual instructions stay beside it.
        assert "Add a CNAME record for" in body

    async def test_a_made_record_is_said_and_its_removal_warned(
        self, client, repos, monkeypatch
    ):
        made = replace(
            repos["domains"][1],
            dns_zone_id="zone-example.com",
            dns_records=({"id": "rec-1", "type": "A", "content": SERVER},),
        )
        repos["domains"][1] = made
        body = (await client.get("/projects/blog/config/domains")).text
        assert "DNS · made on Cloudflare" in body
        assert "Its DNS record on Cloudflare is deleted too." in body

    async def test_adding_a_domain_makes_its_record_and_verifies(
        self, client, repos, monkeypatch
    ):
        from deploypro.engine import verify

        seen = {}
        monkeypatch.setattr(dns, "enabled", lambda settings: True)

        async def ensure(settings, host, *, project_slug):
            return dns.Outcome(
                "Made its DNS record on Cloudflare (157.180.122.108).",
                made=[{"id": "rec-1", "type": "A", "content": SERVER}],
                zone_id="zone-example.com",
            )

        async def add_domain(project_id, host, *, primary):
            return fakes.domain(host, verified=False, primary=primary)

        async def set_dns(domain_id, *, zone_id, records):
            seen["records"] = records

        async def verified(host, *, expected_host):
            return verify.VerificationResult(True, "resolves here")

        async def marked(domain_id):
            seen["verified"] = True

        async def refresh(project, *, settings):
            seen["routed"] = True

        monkeypatch.setattr(dns, "ensure_record", ensure)
        monkeypatch.setattr("deploypro.web.routes.project_repo.add_domain", add_domain)
        monkeypatch.setattr("deploypro.web.routes.project_repo.set_domain_dns", set_dns)
        monkeypatch.setattr("deploypro.web.routes.verify.verify", verified)
        monkeypatch.setattr(
            "deploypro.web.routes.project_repo.mark_domain_verified", marked
        )
        monkeypatch.setattr("deploypro.web.routes.routing.refresh", refresh)
        response = await client.post(
            "/projects/blog/domains", data={"host": "tv.example.com"}
        )
        location = response.headers["location"]
        assert "ok=" in location and "Verified" in httpx.URL(location).params["ok"]
        assert seen == {
            "records": [{"id": "rec-1", "type": "A", "content": SERVER}],
            "verified": True,
            "routed": True,
        }


def replace_ns(ns, **changes):
    return SimpleNamespace(**{**vars(ns), **changes})


async def _async(value):
    return value
