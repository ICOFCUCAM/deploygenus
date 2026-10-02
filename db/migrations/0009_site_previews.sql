-- A picture of each project's live site, for the Overview
-- (docs/design/amendment-overview-preview.md).
--
-- One row per project: the latest request and the latest result. Promotion
-- marks it requested; the worker claims it, captures the public address at
-- two sizes in a short-lived browser container, and records the files. A
-- failed capture keeps the previous picture, so the page can still show it,
-- labelled with when and from which deployment it was taken.

BEGIN;

CREATE TABLE site_previews (
    project_id     uuid        PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    status         text        NOT NULL
                               CHECK (status IN ('requested', 'capturing', 'captured', 'failed')),
    -- What the next capture is of.
    deployment_id  uuid        REFERENCES deployments(id) ON DELETE SET NULL,
    requested_at   timestamptz NOT NULL DEFAULT now(),
    started_at     timestamptz,
    -- The picture on file: which deployment, when, what address, which files.
    captured_deployment_id uuid REFERENCES deployments(id) ON DELETE SET NULL,
    captured_at    timestamptz,
    captured_url   text,
    desktop_file   text,
    mobile_file    text,
    error          text
);

COMMIT;
