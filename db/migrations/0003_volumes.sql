-- Volumes, and how long a container is given to stop.
--
-- Until now everything a deployment wrote went away with its container, which
-- is right for a website and wrong for anything that keeps what it makes: an
-- app that records video and renders it later loses every recording on every
-- deploy. A volume is storage owned by the project rather than by any
-- deployment, so deployment 14 and deployment 15 see the same files.
--
-- Only the intent is recorded here. The Docker volume itself is created by the
-- engine the first time a container needs it, and is deliberately NOT removed
-- when a row is: deleting a project's data is something an operator does on
-- purpose, with `docker volume rm`, never a side effect of tidying up config.

BEGIN;

-- Ten seconds is Docker's own default and suits a web server. A worker that
-- renders for twenty minutes needs twenty minutes, or every deploy cuts the
-- render in progress off halfway through.
ALTER TABLE projects
    ADD COLUMN stop_timeout_seconds integer NOT NULL DEFAULT 10
    CHECK (stop_timeout_seconds BETWEEN 1 AND 86400);

CREATE TABLE volumes (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id  uuid        NOT NULL REFERENCES projects(id) ON DELETE CASCADE,

    -- Part of the Docker volume name, so the same rules as a process name.
    name        text        NOT NULL
                CHECK (name ~ '^[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?$'),

    -- Absolute and canonical (no trailing slash, no '..'), validated in
    -- forge.domain.storage. The check here is the backstop: no comma, because
    -- `docker run --mount` is comma-separated.
    mount_path  text        NOT NULL
                CHECK (mount_path ~ '^/[A-Za-z0-9._/-]+$' AND mount_path !~ '/$'),

    created_at  timestamptz NOT NULL DEFAULT now(),

    UNIQUE (project_id, name),
    -- Two volumes at one path would leave one of them silently unmounted.
    UNIQUE (project_id, mount_path)
);

CREATE INDEX volumes_project_idx ON volumes (project_id);

COMMIT;
