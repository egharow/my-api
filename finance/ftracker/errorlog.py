"""A plain text log of what went wrong, kept in your data folder so a problem can be understood later."""
import traceback
from datetime import datetime

from .config import Home

LIMIT = 200_000


def record(home: Home, where: str, exc: BaseException) -> None:
    try:
        path = home.root / "error-log.txt"
        old = path.read_text(encoding="utf-8")[-LIMIT:] if path.exists() else ""
        entry = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {where}\n" + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)) + "\n"
        path.write_text(old + entry, encoding="utf-8")
    except Exception:
        pass


def describe(exc: BaseException) -> str:
    """What to tell the user: the kind of problem and, for a file problem, which file."""
    name = getattr(exc, "filename", None)
    if isinstance(exc, PermissionError):
        return f"Windows would not let the app use {name or 'a file'} (error {exc.errno}). Close it in other programs, for example Excel, and try again."
    if isinstance(exc, OSError):
        return f"A file problem: {exc.strerror or exc}{' (' + str(name) + ')' if name else ''}."
    text = str(exc.args[0]) if exc.args else ""
    return f"{type(exc).__name__}: {text or 'no details'}"
