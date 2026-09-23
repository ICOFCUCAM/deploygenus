-- Deploy keys: how a project reads a private repository.
--
-- One SSH key per project, made by DeployPro. The public half is shown to the
-- owner to add to the repository as a read-only deploy key; the private half
-- is encrypted with the master key before it arrives here, exactly like an
-- environment variable, so a leaked database backup is not a leaked key.
--
-- Per project rather than one for the whole platform: GitHub accepts a given
-- deploy key on one repository only, and a key that can read one repository
-- is the smallest thing that can leak.

BEGIN;

ALTER TABLE projects
    ADD COLUMN deploy_key_public    text,
    ADD COLUMN deploy_key_encrypted bytea,
    ADD CONSTRAINT projects_deploy_key_pair CHECK (
        (deploy_key_public IS NULL) = (deploy_key_encrypted IS NULL)
    );

COMMIT;
