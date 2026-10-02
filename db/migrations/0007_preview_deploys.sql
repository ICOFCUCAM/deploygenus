-- Whether a push to a branch other than production builds a preview.
--
-- On by default: every existing project keeps its behaviour until its owner
-- turns it off. Off is for a project whose changes all reach production
-- through merges, where each preview is the same build made twice
-- (docs/design/proposal-build-queue.md).

BEGIN;

ALTER TABLE projects
    ADD COLUMN preview_deploys boolean NOT NULL DEFAULT true;

-- The worker picks production builds before previews: this is the query.
CREATE INDEX deployments_queued ON deployments (created_at) WHERE status = 'queued';

COMMIT;
