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


DATA_FOLDER = "my-data"


def program_dir() -> Path:
    """The folder holding the app's own files (the one you replace when updating)."""
    return Path(__file__).resolve().parent.parent


def resolve_home(explicit: str | None = None) -> Home:
    """Folder holding inbox/, archive/, data/ and backups/.

    Order: --home, then $FINANCE_HOME. In the packaged layout (FinanceApp/app and FinanceApp/my-data) it is the
    my-data folder next to the app folder, so replacing app/ never touches your data. Otherwise ~/Finance.
    Keep it out of a cloud-synced folder (OneDrive, Drive): syncing a live database file can corrupt it.
    """
    raw = explicit or os.environ.get("FINANCE_HOME")
    if raw:
        return Home(Path(raw).expanduser())
    prog = program_dir()
    if prog.name == "app":
        return Home(prog.parent / DATA_FOLDER)
    return Home(Path.home() / "Finance")


def migrate_legacy(home: Home) -> str | None:
    """First start in the packaged layout: bring over the data an earlier version kept in ~/Finance (copied, never moved)."""
    import shutil
    legacy = Path.home() / "Finance"
    if home.root.name != DATA_FOLDER or home.db_path.exists() or legacy == home.root:
        return None
    if not (legacy / "data" / "finance.db").exists():
        return None
    for part in ("data", "inbox", "archive", "backups"):
        if (legacy / part).exists():
            shutil.copytree(legacy / part, home.root / part, dirs_exist_ok=True)
    return f"Copied your existing data from {legacy} into {home.root}. The old folder was left as it is."
