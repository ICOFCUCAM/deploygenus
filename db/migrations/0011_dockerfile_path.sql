-- A Dockerfile somewhere other than the build context's root.
--
-- Monorepos keep one Dockerfile per service in a subfolder and build every one
-- of them from the repository root, so that COPY can reach shared packages
-- (Render's `dockerfilePath` + `dockerContext: .`). DeployPro only looked for
-- `<root directory>/Dockerfile`, which builds from the subfolder and breaks
-- exactly those COPY lines. Relative to the repository root; empty means the
-- old behaviour. docs/design/plan-replace-render.md, G1.

BEGIN;

ALTER TABLE projects
    ADD COLUMN dockerfile_path text NOT NULL DEFAULT '';

COMMIT;
