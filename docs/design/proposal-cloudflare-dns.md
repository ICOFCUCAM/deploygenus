# DeployPro — Proposal: DNS records made automatically on Cloudflare, manual everywhere else

**Status:** **REQUESTED 2026-10-02 and implemented**, with the recommended answer to each decision (§5).
**Builds on (locked, not reopened):** Phase 5 §5.2, the Domains page: its rows, the exact DNS record shown for a domain waiting for DNS, **Verify**, **Remove** and the Add form.

**Why:** adding a custom domain takes three steps, and the middle one is the only one done somewhere else:
1. add it in DeployPro
2. go to the DNS provider and create a record
3. come back and press Verify

The owner's domains are on Cloudflare, and DeployPro already holds a Cloudflare API token (`CF_DNS_API_TOKEN`, *Zone → Read* and *DNS → Edit*) for its wildcard certificate. When the domain is in a Cloudflare zone that token can see, DeployPro can do step 2 itself.

**The owner's condition:** the manual path stays, unchanged, for every domain that is not on Cloudflare, and for any domain where DeployPro can't or shouldn't make the record.

---

## 1. Adding a domain

```
Add example.com
  │
  ├─ Cloudflare not configured ──────────────────────────► manual (today's page)
  │
  ├─ no zone the token can see contains example.com ─────► manual, saying why
  │
  ├─ a record for example.com already exists
  │     ├─ and already points here ──────────────────────► verify now
  │     └─ points elsewhere ─────────────────────────────► manual, NOTHING changed
  │
  └─ no record ──► create:  A example.com → the server's address
                            (and AAAA if it has an IPv6 one)
                            DNS only (not proxied), comment "DeployPro: <project>"
                 └─► verify now; if DNS has not caught up, "Verify in a minute"
```

- **Never overwrite.** An existing record is someone's decision: mail, another site, a verification record. DeployPro creates a record only where there is none, and says what it found otherwise.
- **A records to the server's address,** the address the deployment domain resolves to. This is the second of the two records the manual instructions offer.
  - Not a CNAME: a CNAME can't share its name with any other record, and a domain's apex nearly always has some (mail, verification TXT records).
  - The cost: if the server's address ever changes, these records change with it, as the wildcard's own already must.
- **DNS only.** Proxied, the name would resolve to Cloudflare's addresses, verification would fail, and the certificate check would go through Cloudflare. The deployment domain's own records are DNS only for the same reason.
- **Verified straight away when it resolves.** One step instead of three.

## 2. The Domains page

Each domain says which path it is on:

| Domain | Shows |
|---|---|
| DNS made by DeployPro | `DNS · made on Cloudflare` in the row's detail. |
| Manual, waiting for DNS | Today's text: *"Add a CNAME record for {host} pointing to {deployment domain}, or an A record to this server's address, then verify."* |
| Manual, and its zone is on Cloudflare with no record yet (e.g. added before this existed) | The same text, plus a secondary **Create DNS record** button beside Verify. |

The Add form gains one line under it when Cloudflare is configured: *"Domains in a Cloudflare zone DeployPro can reach get their DNS record made automatically. Others show the record to add."*

## 3. Removing a domain

- A record **DeployPro made** is deleted with the domain, so nothing is left pointing at a server that no longer serves the name.
- It is deleted only if it is still the record DeployPro made: same record, same type, same address. If someone edited it since, it is left alone, and the confirmation message says so.
- A record **the owner made** is never touched.
- The remove confirmation says which will happen: *"{host} stops serving immediately. Its DNS record on Cloudflare is deleted too."*, or today's sentence when the record isn't DeployPro's.

## 4. What it needs

- **Nothing new from the owner,** for zones the existing token already covers. To cover more zones, edit the token on Cloudflare (*My Profile → API Tokens*) and add the zones under *Zone Resources*. DeployPro reads the zones the token can see; it never needs the token's value shown or changed.
- **No new setting.** Cloudflare is used when `DEPLOYPRO_DNS_PROVIDER=cloudflare` and the token is set, which is already the case on this server.

## 5. Decisions (recommended answers taken)

- **D1 · An existing record that points elsewhere:** leave it and fall back to manual. Never replace it.
- **D2 · Proxied or DNS only:** DNS only.
- **D2b · Record type:** A/AAAA to the server's address, not a CNAME (decided while building, for the reason in §1).
- **D3 · Delete the record on Remove:** yes, only DeployPro's own and only if unchanged.
- **D4 · Other DNS providers (Hetzner DNS, Route 53…):** not now. The structure keeps the provider separate, so a second one is an adapter, not a redesign.
