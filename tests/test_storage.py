"""Volumes: what a mount path may be, and what a volume is called."""

from __future__ import annotations

import pytest

from forge.domain.errors import InvalidRequest
from forge.domain.storage import (
    docker_volume_name,
    draining_deadline,
    draining_name,
    normalise_mount_path,
    validate_volume_name,
)


class TestMountPath:
    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            ("/data", "/data"),
            ("/data/", "/data"),
            ("  /app/storage//recordings/ ", "/app/storage/recordings"),
            ("/var/lib/app", "/var/lib/app"),
        ],
    )
    def test_is_stored_in_one_canonical_form(self, given, expected):
        assert normalise_mount_path(given) == expected

    @pytest.mark.parametrize(
        "path",
        [
            "data",  # relative
            "/",  # the whole filesystem
            "//",
            "/data/../etc",  # traversal
            "/./data",
            "/data,readonly",  # would be read as another --mount option
            "/data dir",
            "/etc",
            "/etc/app",
            "/proc/self",
            "/usr/local/data",
            "/dev/shm",
        ],
    )
    def test_refuses_paths_that_would_break_the_container(self, path):
        with pytest.raises(InvalidRequest):
            normalise_mount_path(path)

    def test_a_directory_merely_starting_with_a_reserved_name_is_fine(self):
        assert normalise_mount_path("/etcetera") == "/etcetera"
        assert normalise_mount_path("/devices") == "/devices"


class TestVolumeName:
    def test_is_lowercased(self):
        assert validate_volume_name(" Recordings ") == "recordings"

    @pytest.mark.parametrize("name", ["", "-a", "a-", "a_b", "a/b", "x" * 33])
    def test_refuses_names_docker_or_dns_would_not(self, name):
        with pytest.raises(InvalidRequest):
            validate_volume_name(name)


class TestDockerVolumeName:
    def test_is_owned_by_the_project_not_a_deployment(self):
        assert docker_volume_name("balancevid", "recordings") == (
            "forge_balancevid_recordings"
        )

    def test_two_projects_cannot_arrive_at_the_same_volume(self):
        # With a hyphen as the separator these would both be forge-a-b-c.
        assert docker_volume_name("a-b", "c") != docker_volume_name("a", "b-c")


class TestDrainingName:
    def test_round_trips_its_deadline(self):
        name = draining_name("forge-blog-mailer-0", deadline=1790000000)
        assert draining_deadline(name) == 1790000000
        # As `docker inspect` prints it, with a leading slash.
        assert draining_deadline("/" + name) == 1790000000

    @pytest.mark.parametrize(
        "name", ["forge-blog-mailer-0", "forge-blog-3f9a2c71", "forge-x.draining."]
    )
    def test_an_ordinary_container_is_not_draining(self, name):
        assert draining_deadline(name) is None
