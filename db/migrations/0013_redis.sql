-- A Redis (Valkey) for a project: the job queue Render ran beside its workers.
--
-- One row per project that has one. The container and its data volume are
-- made by the engine from this row and kept running by the monitor; nothing
-- here is read by a deploy, so a deploy, promotion or rollback never touches
-- Redis and queued jobs survive all three. docs/design/plan-replace-render.md §3.

BEGIN;

CREATE TABLE project_redis (
    project_id          uuid PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,

    -- The container's memory cap. Valkey's own maxmemory is set to 90% of it,
    -- so Valkey refuses writes before the kernel kills the container.
    memory_mb           integer NOT NULL DEFAULT 256
                        CHECK (memory_mb BETWEEN 64 AND 4096),

    -- What Valkey does when full. noeviction refuses new writes with an OOM
    -- error, which job queues (BullMQ) require: evicting would drop jobs.
    policy              text NOT NULL DEFAULT 'noeviction'
                        CHECK (policy IN ('noeviction', 'allkeys-lru', 'volatile-lru')),

    -- The variable names the app receives the URL under, space-separated.
    env_names           text NOT NULL DEFAULT 'REDIS_URL',

    -- Encrypted with DEPLOYPRO_MASTER_KEY like every other secret.
    password_encrypted  bytea NOT NULL,

    image               text NOT NULL,

    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

COMMIT;
