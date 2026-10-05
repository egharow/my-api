import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Home:
    root: Path

    @property
    def inbox(self) -> Path:
        return self.root / "inbox"

    @property
    def unrecognised(self) -> Path:
        return self.inbox / "_unrecognised"

    @property
    def archive(self) -> Path:
        return self.root / "archive"

    @property
    def duplicates(self) -> Path:
        return self.archive / "_duplicates"

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def db_path(self) -> Path:
        return self.data / "finance.db"

    @property
    def backups(self) -> Path:
        return self.root / "backups"

    def ensure(self) -> None:
        for p in (self.inbox, self.unrecognised, self.archive, self.duplicates,
                  self.data, self.backups):
            p.mkdir(parents=True, exist_ok=True)


def resolve_home(explicit: str | None = None) -> Home:
    """Folder holding inbox/, archive/, data/ and backups/.

    Defaults to ~/Finance. Keep it out of a cloud-synced folder (OneDrive, Drive):
    syncing a live database file can corrupt it.
    """
    raw = explicit or os.environ.get("FINANCE_HOME")
    return Home(Path(raw).expanduser() if raw else Path.home() / "Finance")
