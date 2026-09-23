# The static binary is built by run.sh and committed to the test repository
# rather than compiled here, so the whole run needs no base image from any
# registry: FROM scratch is the only base.
FROM scratch
COPY app /app
# The one thing an image must do for a volume: create the mount point and
# give it to the user the app runs as.
COPY --chown=10001:10001 data/ /data/
USER 10001:10001
ENTRYPOINT ["/app"]
