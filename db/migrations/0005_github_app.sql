-- The GitHub App: how DeployPro lists repositories, reads private ones and
-- hears about pushes without a deploy key or a webhook per repository.
--
-- One app per installation of DeployPro, made by its owner through GitHub's
-- manifest flow, so its private key and webhook secret exist nowhere else.
-- Both are encrypted with the master key before they arrive here.
--
-- An app is installed on one or more GitHub accounts (a user, or an
-- organisation); each installation is what grants access to that account's
-- repositories. A project imported through the app remembers which
-- installation reads it and the repository's full name, which is what a push
-- event names.

BEGIN;

CREATE TABLE github_app (
    -- There is only ever one row.
    id                       smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    app_id                   bigint      NOT NULL,
    slug                     text        NOT NULL,
    name                     text        NOT NULL,
    html_url                 text        NOT NULL,
    owner_login              text        NOT NULL,
    private_key_encrypted    bytea       NOT NULL,
    webhook_secret_encrypted bytea       NOT NULL,
    created_at               timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE github_installations (
    -- GitHub's own installation id.
    id            bigint      PRIMARY KEY,
    account_login text        NOT NULL,
    account_type  text        NOT NULL DEFAULT 'User',
    created_at    timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE projects
    ADD COLUMN github_installation_id bigint
        REFERENCES github_installations (id) ON DELETE SET NULL,
    ADD COLUMN github_repo text;

CREATE INDEX projects_github_repo ON projects (lower(github_repo));

COMMIT;
