"""Card logins, encrypted by Windows (DPAPI) for your Windows user on this computer.

The file lives in your Windows profile (AppData\\Local\\FinanceTracker), never in the app folder, my-data or a synced
folder, so updates, backups and Google Drive never see it. Only the encrypted form is stored. What is kept in plain text
is only which card company, whose login and when it was saved: never a password, ID or card digits.

An optional fetch PIN is mixed into the encryption, so even a program running as you cannot open the logins without it.
"""
import base64
import hashlib
import json
import os
import secrets
import sys
from datetime import datetime
from pathlib import Path

FILE = "card-logins.bin"
PIN_ROUNDS = 300_000


class VaultError(Exception):
    pass


class WrongPin(VaultError):
    pass


class _Dpapi:
    """Windows' own encryption for a user's secrets (the same one Chrome and Credential Manager use)."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        class Blob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        self.ctypes, self.Blob = ctypes, Blob
        self.crypt32, self.kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32

    def _in(self, data: bytes):
        buf = self.ctypes.create_string_buffer(data, len(data))
        return self.Blob(len(data), self.ctypes.cast(buf, self.ctypes.POINTER(self.ctypes.c_char))), buf

    def protect(self, data: bytes, entropy: bytes | None) -> bytes:
        c = self.ctypes
        blob_in, keep1 = self._in(data)
        ent, keep2 = self._in(entropy) if entropy else (None, None)
        out = self.Blob()
        if not self.crypt32.CryptProtectData(c.byref(blob_in), "Finance card login", c.byref(ent) if ent else None,
                                             None, None, 0x1, c.byref(out)):
            raise VaultError("Windows could not encrypt the login")
        try:
            return c.string_at(out.pbData, out.cbData)
        finally:
            self.kernel32.LocalFree(out.pbData)

    def unprotect(self, data: bytes, entropy: bytes | None) -> bytes:
        c = self.ctypes
        blob_in, keep1 = self._in(data)
        ent, keep2 = self._in(entropy) if entropy else (None, None)
        out = self.Blob()
        if not self.crypt32.CryptUnprotectData(c.byref(blob_in), None, c.byref(ent) if ent else None,
                                               None, None, 0x1, c.byref(out)):
            raise WrongPin("the saved logins could not be opened")
        try:
            return c.string_at(out.pbData, out.cbData)
        finally:
            self.kernel32.LocalFree(out.pbData)


BACKEND = None                     # tests put a stand-in here; on Windows it is DPAPI


def _backend():
    global BACKEND
    if BACKEND is None:
        if sys.platform != "win32":
            raise VaultError("Card logins can only be saved on the Windows computer the app runs on")
        BACKEND = _Dpapi()
    return BACKEND


def available() -> bool:
    try:
        _backend()
        return True
    except VaultError:
        return False


def base_dir() -> Path:
    """AppData\\Local\\FinanceTracker on Windows. Never inside the app folder or my-data."""
    override = os.environ.get("FTRACKER_SECRETS_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "FinanceTracker"
    return Path.home() / ".local" / "share" / "FinanceTracker"


def path() -> Path:
    return base_dir() / FILE


def _read() -> dict:
    p = path()
    if not p.is_file():
        return {"version": 1, "pin": None, "logins": []}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise VaultError(f"the saved logins file is damaged ({exc}); use Forget all logins and save them again") from exc
    d.setdefault("pin", None)
    d.setdefault("logins", [])
    return d


def _write(d: dict) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def _entropy(d: dict, pin: str | None) -> bytes | None:
    if not d.get("pin"):
        return None
    if not pin:
        raise WrongPin("type your fetch PIN")
    salt = base64.b64decode(d["pin"]["salt"])
    return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, PIN_ROUNDS)


def has_pin() -> bool:
    return bool(_read().get("pin"))


def check_pin(pin: str | None) -> None:
    """Raises WrongPin unless this is the fetch PIN (or there is no PIN)."""
    d = _read()
    if not d.get("pin"):
        return
    try:
        if _backend().unprotect(base64.b64decode(d["pin"]["check"]), _entropy(d, pin)) != b"ok":
            raise WrongPin("wrong fetch PIN")
    except VaultError:
        raise WrongPin("wrong fetch PIN")


def login_id(company: str, person: str) -> str:
    return f"{company}:{person}"


def list_logins() -> list[dict]:
    """Which logins are saved. Never includes any part of a login itself."""
    return [{k: v for k, v in e.items() if k != "blob"} for e in _read()["logins"]]


def save_login(company: str, person: str, fields: dict, pin: str | None = None) -> None:
    d = _read()
    check_pin(pin)
    blob = _backend().protect(json.dumps(fields, ensure_ascii=False).encode("utf-8"), _entropy(d, pin))
    lid = login_id(company, person)
    d["logins"] = [e for e in d["logins"] if e["id"] != lid]
    d["logins"].append({"id": lid, "company": company, "person": person,
                        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                        "blob": base64.b64encode(blob).decode("ascii")})
    _write(d)


def load_login(lid: str, pin: str | None = None) -> dict:
    d = _read()
    entry = next((e for e in d["logins"] if e["id"] == lid), None)
    if entry is None:
        raise VaultError("that login is not saved")
    try:
        return json.loads(_backend().unprotect(base64.b64decode(entry["blob"]), _entropy(d, pin)).decode("utf-8"))
    except WrongPin:
        raise
    except VaultError as exc:
        raise WrongPin(str(exc)) from exc


def forget(lid: str) -> bool:
    d = _read()
    before = len(d["logins"])
    d["logins"] = [e for e in d["logins"] if e["id"] != lid]
    if len(d["logins"]) == before:
        return False
    _write(d)
    return True


def forget_all() -> None:
    p = path()
    if p.is_file():
        try:
            p.write_bytes(secrets.token_bytes(max(p.stat().st_size, 64)))   # overwrite before deleting
        except OSError:
            pass
        p.unlink()


def set_pin(old: str | None, new: str | None) -> None:
    """Turn the fetch PIN on, change it, or (new empty) turn it off. Saved logins are re-encrypted, not lost."""
    if new is not None and new != "" and len(new) < 4:
        raise VaultError("use at least 4 characters for the fetch PIN")
    d = _read()
    check_pin(old)
    plain = [(e, _backend().unprotect(base64.b64decode(e["blob"]), _entropy(d, old))) for e in d["logins"]]
    if new:
        salt = secrets.token_bytes(16)
        d["pin"] = {"salt": base64.b64encode(salt).decode("ascii")}
        ent = _entropy(d, new)
        d["pin"]["check"] = base64.b64encode(_backend().protect(b"ok", ent)).decode("ascii")
    else:
        d["pin"], ent = None, None
    for e, data in plain:
        e["blob"] = base64.b64encode(_backend().protect(data, ent)).decode("ascii")
    _write(d)
