-- The DNS records DeployPro made for a domain, when it made them.
--
-- A domain in a Cloudflare zone the installation's token can reach gets its
-- record made automatically; every other domain keeps the manual path
-- (docs/design/proposal-cloudflare-dns.md). What was made is remembered so
-- that removing the domain removes exactly that — and only while it is
-- unchanged: each entry keeps the type and content it was created with.

BEGIN;

ALTER TABLE domains
    ADD COLUMN dns_zone_id text,
    -- [{"id": "...", "type": "A", "content": "157.180.122.108"}, ...]
    ADD COLUMN dns_records jsonb NOT NULL DEFAULT '[]'::jsonb;

COMMIT;
