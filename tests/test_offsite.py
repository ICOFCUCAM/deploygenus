"""Backups copied off the server: a Hetzner Storage Box or any SFTP host.

docs/design/proposal-offsite-backups.md. The engine runs against a stand-in
for `sftp` that keeps a remote file tree, so what is tested is the batch that
would be sent and what it would leave there. The real client was also run
against a real sshd while building this (see the proposal).
"""

# ruff: noqa: F811 - the dashboard fixtures are imported, then used as arguments

from __future__ import annotations

import fnmatch
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from deploypro.adapters import sftp
from deploypro.domain.errors import InvalidRequest, OffsiteError
from deploypro.engine import offsite
from deploypro.web import views
from tests.test_dashboard import anon, app, client, repos  # noqa: F401

SETTINGS = SimpleNamespace(master_key=Fernet.generate_key().decode())
NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
HOST_KEY = "[u1.your-storagebox.de]:23 ssh-ed25519 AAAAhost\n"


class FakeBox:
    """A remote file tree behind sftp's batch commands."""

    def __init__(self, password="pw"):
        self.files: dict[str, str] = {}
        self.dirs: set[str] = set()
        self.password = password
        self.authorized: list[str] = []
        self.batches: list[list[str]] = []
        self.logins: list[str] = []
        self.fail_on = ""

    async def run(
        self, *, commands, known_hosts, private_key="", password="", local=None, **kw
    ):
        self.batches.append(commands)
        if password:
            if password != self.password:
                raise sftp.SftpFailed(
                    "u1@u1.your-storagebox.de: Permission denied (password)."
                )
            self.logins.append("password")
        else:
            if not any(private_key and line for line in self.authorized):
                raise sftp.SftpFailed("Permission denied (publickey).")
            self.logins.append("key")
        here = dict(local or {})
        out = []
        for command in commands:
            tolerant = command.startswith("-")
            verb, *args = command.lstrip("-").split()
            ok = self.do(verb, [a.strip('"') for a in args], here, out)
            if self.fail_on and self.fail_on in command:
                ok = False
            if not ok and not tolerant:
                raise sftp.SftpFailed(f"{command}: Failure")
        return sftp.Result("\n".join(out), here, known_hosts or HOST_KEY)

    def do(self, verb, args, here, out):
        if verb == "mkdir":
            if args[0] in self.dirs:
                return False
            self.dirs.add(args[0])
            return True
        if verb == "get":
            if args[0] not in self.files:
                return False
            here[args[1]] = self.files[args[0]]
            return True
        if verb == "put" and args[0] == "-r":
            self.dirs |= {args[2], f"{args[2]}/volumes"}
            self.files[f"{args[2]}/manifest.json"] = "{}"
            self.files[f"{args[2]}/volumes/v.tar.gz"] = "x"
            return True
        if verb == "put":
            self.files[args[1]] = here[args[0]]
            if args[1] == ".ssh/authorized_keys":
                self.authorized = here[args[0]].splitlines()
            return True
        if verb == "chmod":
            return True
        if verb == "rename":
            if args[1] in self.dirs or args[0] not in self.dirs:
                return False
            for name in [
                d for d in self.dirs if d == args[0] or d.startswith(args[0] + "/")
            ]:
                self.dirs.remove(name)
                self.dirs.add(args[1] + name[len(args[0]) :])
            for name in [f for f in self.files if f.startswith(args[0] + "/")]:
                self.files[args[1] + name[len(args[0]) :]] = self.files.pop(name)
            return True
        if verb == "rm":
            gone = [f for f in self.files if fnmatch.fnmatch(f, args[0])]
            for name in gone:
                del self.files[name]
            return bool(gone)
        if verb == "rmdir":
            if args[0] not in self.dirs or any(
                p.startswith(args[0] + "/") for p in [*self.dirs, *self.files]
            ):
                return False
            self.dirs.remove(args[0])
            return True
        if verb == "ls":
            folder = args[-1]
            if folder not in self.dirs:
                return False
            out.extend(d for d in sorted(self.dirs) if d.rsplit("/", 1)[0] == folder)
            return True
        raise AssertionError(f"unexpected {verb}")

    def backups(self, folder="deploypro"):
        return sorted(
            d.rsplit("/", 1)[1]
            for d in self.dirs
            if "/" in d and d.rsplit("/", 1)[0] == folder
        )


@pytest.fixture
def box(monkeypatch, repos):
    fake = FakeBox()
    monkeypatch.setattr(sftp, "run", fake.run)
    return fake


async def set_up(box, password="pw"):
    await offsite.configure(SETTINGS, user="u1", host="", port="23", folder="deploypro")
    return await offsite.install_key(SETTINGS, password)


def backup_dir(tmp_path, day):
    path = tmp_path / f"deploypro-202610{day:02d}T030000Z"
    path.mkdir()
    return path


class TestSettingUp:
    def test_a_storage_box_username_is_enough(self):
        assert offsite.normalise("u123456", "", "", "") == (
            "u123456",
            "u123456.your-storagebox.de",
            23,
            "deploypro",
        )
        assert offsite.normalise("u1-sub2", "", "23", "/x/y/")[1:] == (
            "u1.your-storagebox.de",
            23,
            "x/y",
        )

    @pytest.mark.parametrize(
        ("user", "host", "port", "folder"),
        [
            ("", "", "23", ""),
            ("bob", "", "23", ""),  # not a Storage Box: say where
            ("u1", "evil;rm -rf", "23", ""),
            ("u1", "", "99999", ""),
            ("u1", "", "23", "../etc"),
            ("u1", "", "23", "a b"),
        ],
    )
    def test_anything_else_is_refused_with_a_reason(self, user, host, port, folder):
        with pytest.raises(InvalidRequest):
            offsite.normalise(user, host, port, folder)

    async def test_the_key_is_made_once_and_kept_encrypted(self, box):
        first = await offsite.configure(
            SETTINGS, user="u1", host="", port="23", folder="a"
        )
        again = await offsite.configure(
            SETTINGS, user="u1", host="", port="23", folder="b"
        )
        assert again.public_key == first.public_key
        assert first.public_key.endswith(" deploypro-backups")
        assert "PRIVATE KEY" not in str(first.as_state())

    async def test_the_password_installs_the_key_and_is_not_kept(self, box, repos):
        box.files[".ssh/authorized_keys"] = (
            "ssh-ed25519 AAAAmine me@laptop\nssh-ed25519 AAAAold deploypro-backups\n"
        )
        box.authorized = box.files[".ssh/authorized_keys"].splitlines()
        target = await set_up(box)
        assert target.connected and target.known_hosts == HOST_KEY
        lines = box.files[".ssh/authorized_keys"].splitlines()
        # The owner's line stays; DeployPro's older key is replaced.
        assert lines == ["ssh-ed25519 AAAAmine me@laptop", target.public_key]
        assert "pw" not in str(repos["system"])
        # And the key, not the password, is what logs in from now on.
        assert box.logins[-1] == "key"

    async def test_a_wrong_password_says_so(self, box):
        await offsite.configure(SETTINGS, user="u1", host="", port="23", folder="")
        with pytest.raises(OffsiteError, match="refused the password"):
            await offsite.install_key(SETTINGS, "nope")
        assert not (await offsite.get()).connected

    async def test_a_new_server_learns_its_host_key_again(self, box):
        await set_up(box)
        moved = await offsite.configure(
            SETTINGS, user="u2", host="", port="23", folder="deploypro"
        )
        assert moved.known_hosts == "" and not moved.connected


class TestCopying:
    async def test_each_backup_is_uploaded_whole_then_renamed(self, box, tmp_path):
        await set_up(box)
        said = await offsite.copy(SETTINGS, backup_dir(tmp_path, 1), keep=7)
        assert "Copied deploypro-20261001T030000Z" in said
        assert box.backups() == ["deploypro-20261001T030000Z"]
        batch = box.batches[-2]  # the copy; the last is the listing for pruning
        put = next(i for i, c in enumerate(batch) if c.startswith("put -r"))
        rename = next(i for i, c in enumerate(batch) if c.startswith("rename"))
        assert ".partial" in batch[put] and put < rename
        last = (await offsite.get()).last
        assert last["ok"] and last["name"] == "deploypro-20261001T030000Z"

    async def test_the_target_keeps_the_newest_few(self, box, tmp_path):
        await set_up(box)
        box.dirs.add("deploypro/my-own-notes")
        box.dirs.add("deploypro/deploypro-20260901T030000Z.partial")
        for day in range(1, 5):
            await offsite.copy(SETTINGS, backup_dir(tmp_path, day), keep=2)
        # Only finished DeployPro copies count, and only those are removed.
        assert box.backups() == [
            "deploypro-20260901T030000Z.partial",
            "deploypro-20261003T030000Z",
            "deploypro-20261004T030000Z",
            "my-own-notes",
        ]

    async def test_a_retried_copy_replaces_the_old_one(self, box, tmp_path):
        await set_up(box)
        path = backup_dir(tmp_path, 1)
        await offsite.copy(SETTINGS, path, keep=7)
        await offsite.copy(SETTINGS, path, keep=7)
        assert box.backups() == ["deploypro-20261001T030000Z"]

    async def test_a_failed_copy_is_recorded_and_raised(self, box, tmp_path):
        await set_up(box)
        box.fail_on = "put -r"
        with pytest.raises(offsite.CopyFailed):
            await offsite.copy(SETTINGS, backup_dir(tmp_path, 1), keep=7)
        last = (await offsite.get()).last
        assert not last["ok"] and "Failure" in last["error"]
        # Never renamed: nothing there looks like a finished copy.
        assert all(name.endswith(".partial") for name in box.backups())

    async def test_nothing_is_sent_without_a_target(self, box, tmp_path):
        assert await offsite.copy(SETTINGS, backup_dir(tmp_path, 1), keep=7) == ""
        assert box.batches == []

    async def test_a_target_that_never_connected_is_not_tried(self, box, tmp_path):
        await offsite.configure(SETTINGS, user="u1", host="", port="23", folder="")
        with pytest.raises(offsite.CopyFailed, match="Not connected"):
            await offsite.copy(SETTINGS, backup_dir(tmp_path, 1), keep=7)
        assert box.batches == []


class TestSftpWords:
    def test_the_telling_line_wins_over_connection_closed(self):
        output = "u1@h: Permission denied (publickey).\r\nConnection closed\n"
        assert sftp._last_words(output) == "u1@h: Permission denied (publickey)."


def target(**kw):
    base = offsite.Target(
        user="u1",
        host="u1.your-storagebox.de",
        port=23,
        folder="deploypro",
        public_key="ssh-ed25519 AAAA deploypro-backups",
        private_key_encrypted="x",
    )
    return replace(base, **kw)


class TestPage:
    def test_the_fact_says_where_and_when(self):
        assert views.offsite_fact(None, NOW).mark.word == "Off"
        assert views.offsite_fact(target(), NOW).mark.word == "Not connected"
        ok = target(
            connected=True,
            last={"at": (NOW - timedelta(hours=1)).isoformat(), "ok": True, "name": "n"},
        )
        fact = views.offsite_fact(ok, NOW)
        assert fact.mark is None and "u1@u1.your-storagebox.de:23" in fact.text
        failed = replace(ok, last={**ok.last, "ok": False, "error": "no route"})
        assert views.offsite_fact(failed, NOW).mark.word == "Failed"

    def test_kept_on_names_the_box_once_it_is_connected(self):
        state = {
            "newest_at": NOW.isoformat(),
            "count": 1,
            "dir": "/var/backups/deploypro",
        }
        kept = lambda t: next(  # noqa: E731
            f
            for f in views.backups(
                state, None, None, enabled=True, hour=3, now=NOW, offsite=t
            ).facts
            if f.label == "Kept on"
        )
        assert "only" in kept(None).text
        assert "only" in kept(target()).text
        assert "u1@u1.your-storagebox.de" in kept(target(connected=True)).text

    def test_the_setup_step_is_done_by_connecting(self):
        items = views.setup_items(
            {}, alerts_on=False, backup_exists=True, backup_dir="", offsite_connected=True
        )
        step = next(i for i in items if i.key == "offsite")
        assert step.done and not step.ticked_by_owner

    async def test_the_page_offers_to_set_it_up(self, client, repos):
        body = (await client.get("/system")).text
        assert 'action="/system/offsite"' in body and "SSH support" in body

    async def test_waiting_for_the_key_asks_for_the_password_once(self, client, repos):
        repos["system"]["offsite"] = target().as_state()
        body = (await client.get("/system")).text
        assert 'action="/system/offsite/key"' in body
        assert 'type="password"' in body and "It is not stored." in body
        assert "ssh-ed25519 AAAA deploypro-backups" in body

    async def test_a_bad_username_is_said_on_the_page(self, client, repos):
        response = await client.post("/system/offsite", data={"user": "not valid!"})
        assert "err=" in response.headers["location"]


class TestWorker:
    async def test_a_failed_copy_alerts_as_a_copy_not_a_lost_backup(self, monkeypatch):
        import asyncio

        from deploypro import worker as module
        from deploypro.worker import Worker

        w = object.__new__(Worker)
        w._stopping = asyncio.Event()
        w._settings = SimpleNamespace(backup_dir="/var/backups/deploypro")
        sent = []

        async def event(**kw):
            sent.append(kw)

        w._alerts = SimpleNamespace(event=event)

        async def run_backup(settings, run):
            raise offsite.CopyFailed("u1@box: no route to host")

        monkeypatch.setattr(module.maintenance, "run_backup", run_backup)
        await w._take_backup(object())
        assert [a["title"] for a in sent] == ["Backup not copied off the server"]
        assert "The backup on this server is fine." in sent[0]["detail"]


def test_a_message_goes_before_the_anchor():
    from deploypro.web.routes import _redirect

    location = _redirect("/system#offsite", ok="Saved.").headers["location"]
    assert location == "/system?ok=Saved.#offsite"
