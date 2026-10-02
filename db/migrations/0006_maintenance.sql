-- Clean-ups and backups, and what the System page knows about the host.
--
-- Both used to happen only inside the worker, visible only in its log. The
-- System page now shows when each last ran and lets its owner start one, so
-- each run is a row: the worker writes one for every run, scheduled or not,
-- and the dashboard asks for one by inserting a row the worker then claims.
-- The dashboard never runs either itself: a backup takes minutes, and the
-- dashboard's container does not mount the backup directory.

BEGIN;

CREATE TABLE maintenance_runs (
    id           bigserial   PRIMARY KEY,
    kind         text        NOT NULL CHECK (kind IN ('cleanup', 'backup')),
    -- Who started it: the worker's own schedule, the System page, or the
    -- `deploypro backup` / `deploypro housekeeping` commands.
    origin       text        NOT NULL CHECK (origin IN ('schedule', 'dashboard', 'command')),
    status       text        NOT NULL
                             CHECK (status IN ('requested', 'running', 'succeeded', 'failed')),
    requested_at timestamptz NOT NULL DEFAULT now(),
    started_at   timestamptz,
    finished_at  timestamptz,
    worker       text,
    -- One sentence for people: "2 images and 1.8 GB of cache removed".
    summary      text        NOT NULL DEFAULT '',
    detail       jsonb       NOT NULL DEFAULT '{}'::jsonb,
    error        text
);

-- One of each kind at a time. Two clean-ups racing each other remove nothing
-- extra, but two backups write two copies of every volume at once.
CREATE UNIQUE INDEX maintenance_runs_one_open
    ON maintenance_runs (kind) WHERE status IN ('requested', 'running');

CREATE INDEX maintenance_runs_latest ON maintenance_runs (kind, id DESC);

-- Small facts about the host that only the worker can see — the backup
-- directory, the build cache's size — and the setup checklist's ticks.
-- One row per key; the worker overwrites its facts, the owner their ticks.
CREATE TABLE system_state (
    key        text        PRIMARY KEY,
    value      jsonb       NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

COMMIT;
