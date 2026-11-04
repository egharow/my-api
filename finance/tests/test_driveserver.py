import subprocess
from datetime import date

from ftracker import cloudbackup, driveserver, watchfolder


class FakeRclone:
    """Stands in for rclone: 'copy REMOTE DIR' drops a file into DIR, and every call is recorded."""
    def __init__(self, fail=False, files=None):
        self.calls, self.fail, self.files = [], fail, files or {}

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        if self.fail:
            return subprocess.CompletedProcess(cmd, 1, "", "Failed to create file system: token expired\n")
        src, dst = cmd[2], cmd[3]
        if src.startswith("gdrive:"):
            from pathlib import Path
            for name, data in self.files.items():
                (Path(dst) / name).write_bytes(data)
        return subprocess.CompletedProcess(cmd, 0, "", "")


def test_configure_points_the_watch_and_backup_folders_at_local_copies(home, conn):
    driveserver.configure(conn, home, "gdrive:Statements", "gdrive:Finance")
    assert watchfolder.get(conn) == str(driveserver.in_dir(home).resolve())
    assert cloudbackup.get(conn) == str(driveserver.out_dir(home).resolve())


def test_pull_asks_rclone_for_statement_files_only_and_never_deletes(home, conn):
    driveserver.configure(conn, home, "gdrive:Statements", None)
    fake = FakeRclone()
    ok, err = driveserver.pull(conn, home, fake)
    cmd = fake.calls[0]
    assert ok and cmd[:3] == ["rclone", "copy", "gdrive:Statements"] and "--include" in cmd and "*.pdf" in cmd
    assert "sync" not in cmd and "move" not in cmd and "--delete-after" not in cmd


def test_a_failed_pull_is_reported_in_plain_words(home, conn):
    driveserver.configure(conn, home, "gdrive:Statements", None)
    msg = driveserver.cycle(conn, home, date(2026, 10, 5), FakeRclone(fail=True))
    assert "Could not read your Drive folder" in msg and "token expired" in msg
    assert "Could not read your Drive folder" in driveserver.last_result(conn)


def test_rclone_missing_does_not_crash(home, conn):
    driveserver.configure(conn, home, "gdrive:Statements", None)

    def boom(*a, **k):
        raise FileNotFoundError
    ok, err = driveserver.pull(conn, home, boom)
    assert not ok and "not installed" in err


def test_backup_is_copied_to_drive_after_it_is_made(home, conn):
    driveserver.configure(conn, home, "gdrive:Statements", "gdrive:Finance")
    fake = FakeRclone()
    driveserver.cycle(conn, home, date(2026, 10, 5), fake, force=True)
    pushes = [c for c in fake.calls if c[2].endswith("FinanceBackup")]
    assert pushes and pushes[0][3] == "gdrive:Finance/FinanceBackup"


def test_nothing_configured_means_nothing_runs(home, conn):
    fake = FakeRclone()
    assert driveserver.cycle(conn, home, date(2026, 10, 5), fake) == "" and fake.calls == []
