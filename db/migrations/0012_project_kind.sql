-- Whether a project serves a website, or runs in the background only.
--
-- Render's `type: worker`: a queue consumer built from its own Dockerfile,
-- with no port and nothing to route to (dispatch-worker, cineforge-worker).
-- A `background` project is built and deployed like any other, but is never
-- routed, never given domains or previews, and is healthy when it is still
-- running after it settles. docs/design/plan-replace-render.md, G2.

BEGIN;

ALTER TABLE projects
    ADD COLUMN kind text NOT NULL DEFAULT 'web'
        CHECK (kind IN ('web', 'background'));

COMMIT;
