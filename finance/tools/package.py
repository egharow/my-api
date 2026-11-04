"""Build the zips you download. Run from the finance/ folder:  python tools/package.py OUT_DIR

FinanceApp.zip     first install: Finance.pyw, START-HERE.txt, app/ (the program, with your private starter files if present),
                   and an empty my-data/ folder.
FinanceUpdate.zip  later updates: Finance.pyw, START-HERE.txt and app/ only. It never contains my-data, so replacing
                   files can never touch your data.
"""
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRIVATE = ["starter_rules.json", "seed"]


def tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split("\n")
    return [f for f in out if f and not f.startswith(("tests/", "tools/", "server/", "docs/"))]


def crlf(name: str, data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n") if name.endswith((".txt", ".pyw")) else data


def build(out: Path, first_install: bool) -> Path:
    path = out / ("FinanceApp.zip" if first_install else "FinanceUpdate.zip")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in tracked():
            data = (ROOT / f).read_bytes()
            if f in ("Finance.pyw", "START-HERE.txt"):
                z.writestr(f"FinanceApp/{f}", crlf(f, data))
            else:
                z.writestr(f"FinanceApp/app/{f}", crlf(f, data))
        for name in (["data_fixes.json"] if not first_install else []):          # your own corrections travel with every update
            if (ROOT / name).is_file():
                z.write(ROOT / name, f"FinanceApp/app/{name}")
        if first_install:
            for name in PRIVATE + ["data_fixes.json"]:
                p = ROOT / name
                for q in ([p] if p.is_file() else sorted(p.rglob("*")) if p.exists() else []):
                    if q.is_file():
                        z.write(q, f"FinanceApp/app/{q.relative_to(ROOT).as_posix()}")
            z.writestr("FinanceApp/my-data/README.txt", "Your data lives here. Never replace or delete this folder.\r\n")
    return path


def build_server(out: Path) -> Path:
    """FinanceServer.zip: the program plus the install kit for an always-on Linux server. Never contains your data."""
    path = out / "FinanceServer.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in tracked() + [f for f in subprocess.run(["git", "ls-files", "server"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split("\n") if f]:
            if f in ("Finance.pyw", "START-HERE.txt"):
                continue
            info = zipfile.ZipInfo(f"FinanceServer/{f}")
            info.external_attr = (0o755 if f.endswith(".sh") else 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, (ROOT / f).read_bytes())
        for name in ["data_fixes.json"] + PRIVATE:
            p = ROOT / name
            for q in ([p] if p.is_file() else sorted(p.rglob("*")) if p.exists() else []):
                if q.is_file():
                    z.write(q, f"FinanceServer/{q.relative_to(ROOT).as_posix()}")
    return path


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    out.mkdir(parents=True, exist_ok=True)
    print(build(out, True), build(out, False), build_server(out))
