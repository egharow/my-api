"""Files dropped onto the dashboard. Parsed by hand so nothing outside the standard library is needed."""
import re
from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import HTTP
from pathlib import Path

ALLOWED = {".xlsx", ".xls", ".pdf"}
MAX_BYTES = 60 * 1024 * 1024
_BAD = re.compile(r'[\x00-\x1f<>:"/\\|?*]')


class UploadError(Exception):
    pass


@dataclass
class UploadedFile:
    name: str
    data: bytes


def safe_name(raw: str) -> str:
    raw = raw.replace("\\", "/").rsplit("/", 1)[-1]          # no folders, whatever the browser sent
    name = _BAD.sub("_", raw).strip(" .")
    return name[:150] or "file"


def parse_multipart(content_type: str, body: bytes) -> list[UploadedFile]:
    if "multipart/form-data" not in content_type.lower() or "boundary=" not in content_type.lower():
        raise UploadError("expected a file upload")
    msg = BytesParser(policy=HTTP).parsebytes(b"Content-Type: " + content_type.encode("latin-1") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body)
    files = []
    for part in msg.iter_parts():
        name = part.get_filename()
        if not name:
            continue
        try:                                                  # browsers send UTF-8 names; undo the header decoding
            name = name.encode("utf-8", "surrogateescape").decode("utf-8")
        except UnicodeError:
            pass
        files.append(UploadedFile(safe_name(name), part.get_payload(decode=True) or b""))
    return files


def save_to_inbox(files: list[UploadedFile], inbox: Path) -> tuple[list[str], list[str]]:
    """Returns (saved names, rejected names with reasons). Existing names are never overwritten."""
    inbox.mkdir(parents=True, exist_ok=True)
    saved, rejected = [], []
    for f in files:
        ext = Path(f.name).suffix.lower()
        if ext not in ALLOWED:
            rejected.append(f"{f.name} (only .xlsx, .xls and .pdf statements are accepted)")
            continue
        if not f.data:
            rejected.append(f"{f.name} (empty file)")
            continue
        if len(f.data) > MAX_BYTES:
            rejected.append(f"{f.name} (too large)")
            continue
        dest = inbox / f.name
        n = 2
        while dest.exists():
            dest = inbox / f"{Path(f.name).stem}-{n}{ext}"
            n += 1
        dest.write_bytes(f.data)
        saved.append(dest.name)
    return saved, rejected
