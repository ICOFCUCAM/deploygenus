-- Build arguments: plain switches passed to `docker build --build-arg`, for a
-- Dockerfile with ARG lines (BalanceVid's WITH_TEXT=1, for one).
--
-- Not secrets, and kept apart from environment variables on purpose: a build
-- argument is recorded in the image's metadata and `docker history` prints
-- it, whereas variables reach a build only as a BuildKit secret that is in
-- no layer. Stored as the owner wrote them, one NAME=value per line, already
-- checked (deploypro.domain.build_args).

BEGIN;

ALTER TABLE projects
    ADD COLUMN build_args text NOT NULL DEFAULT '';

COMMIT;
