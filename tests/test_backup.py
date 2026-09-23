"""Backup rotation: which directories count, and which go."""

from __future__ import annotations

from datetime import UTC, datetime

from forge.engine.backup import latest, prune, taken_today


def make(tmp_path, *names):
    for name in names:
        (tmp_path / name).mkdir()
    return tmp_path


def test_keeps_the_newest_and_removes_the_rest(tmp_path):
    dest = make(
        tmp_path,
        "forge-20260920T030000Z",
        "forge-20260921T030000Z",
        "forge-20260922T030000Z",
    )
    removed = prune(dest, keep=2)
    assert [p.name for p in removed] == ["forge-20260920T030000Z"]
    assert sorted(p.name for p in dest.iterdir()) == [
        "forge-20260921T030000Z",
        "forge-20260922T030000Z",
    ]


def test_an_unfinished_backup_never_counts_as_the_latest(tmp_path):
    dest = make(tmp_path, "forge-20260921T030000Z", "forge-20260922T030000Z.partial")
    assert latest(dest).name == "forge-20260921T030000Z"


def test_an_unfinished_backup_does_not_push_a_real_one_out(tmp_path):
    dest = make(tmp_path, "forge-20260921T030000Z", "forge-20260922T030000Z.partial")
    assert prune(dest, keep=1) == []


def test_other_files_in_the_directory_are_left_alone(tmp_path):
    dest = make(tmp_path, "forge-20260921T030000Z", "my-notes")
    (tmp_path / "offsite.log").write_text("rsync ok")
    prune(dest, keep=0)
    assert sorted(p.name for p in dest.iterdir()) == ["my-notes", "offsite.log"]


def test_knows_whether_today_is_done(tmp_path):
    dest = make(tmp_path, "forge-20260922T030000Z")
    assert taken_today(dest, now=datetime(2026, 9, 22, 14, tzinfo=UTC))
    assert not taken_today(dest, now=datetime(2026, 9, 23, 3, tzinfo=UTC))
    assert not taken_today(tmp_path / "missing-dir-is-empty", now=datetime.now(UTC))
