"""Running as an app: one window, one instance, and it closes itself when you close the window."""
import json
import os
import shutil
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

APP_ID = "finance-tracker"
PREFERRED_PORT = 8765
IDLE_SECONDS = 180          # no heartbeat from an open page for this long: the window was closed
NEVER_OPENED_SECONDS = 900  # the window never opened at all
WINDOWS = sys.platform.startswith("win")


def find_browser(env=None, which=shutil.which, exists=os.path.exists) -> str | None:
    """Edge ships with Windows 11 and opens as a chrome-less app window, which feels like a real app."""
    env = env if env is not None else os.environ
    names = ["msedge", "msedge.exe"]
    for n in names:
        found = which(n)
        if found:
            return found
    roots = [env.get("ProgramFiles(x86)"), env.get("ProgramFiles"), env.get("LOCALAPPDATA")]
    for root in roots:
        if not root:
            continue
        for rel in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"):
            p = os.path.join(root, rel)
            if exists(p):
                return p
    return None


def open_window(url: str, popen=subprocess.Popen, web_open=None, browser: str | None = "auto") -> str:
    """Open the app in its own window if a suitable browser exists, else the default browser."""
    import webbrowser
    exe = find_browser() if browser == "auto" else browser
    if exe:
        try:
            popen([exe, f"--app={url}", "--window-size=1280,900"])
            return "app-window"
        except OSError:
            pass
    (web_open or webbrowser.open)(url)
    return "browser"


def _free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) != 0


def is_ours(port: int, timeout: float = 1.0, opener=urllib.request.urlopen) -> bool:
    try:
        with opener(f"http://127.0.0.1:{port}/ping", timeout=timeout) as resp:
            return json.loads(resp.read().decode()).get("app") == APP_ID
    except Exception:
        return False


def pick_port(preferred: int = PREFERRED_PORT, is_free=_free, span: int = 30) -> int:
    for port in range(preferred, preferred + span):
        if is_free(port):
            return port
    raise OSError(f"no free port between {preferred} and {preferred + span - 1}")


class Watchdog:
    """Decides when the app should quit: nobody has kept a page open for a while."""
    def __init__(self, started: float, idle: float = IDLE_SECONDS, never: float = NEVER_OPENED_SECONDS):
        self.started, self.idle, self.never = started, idle, never
        self.last_ping: float | None = None

    def ping(self, now: float) -> None:
        self.last_ping = now

    def should_stop(self, now: float, active_requests: int = 0) -> bool:
        if active_requests:
            return False                       # never quit in the middle of an import
        if self.last_ping is None:
            return now - self.started > self.never
        return now - self.last_ping > self.idle


def create_desktop_shortcut(target: str, arguments: str, workdir: str, name: str = "Finance Tracker",
                            run=subprocess.run) -> str:
    """Put an icon on the desktop (Windows). Uses PowerShell, so no extra packages are needed."""
    if not WINDOWS:
        raise OSError("desktop shortcuts are only made on Windows")
    esc = lambda s: s.replace("'", "''")
    ps = ("$d=[Environment]::GetFolderPath('Desktop');"
          f"$p=Join-Path $d '{esc(name)}.lnk';"
          "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($p);"
          f"$s.TargetPath='{esc(target)}';$s.Arguments='{esc(arguments)}';$s.WorkingDirectory='{esc(workdir)}';"
          f"$s.Description='{esc(name)}';$s.Save();Write-Output $p")
    out = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, text=True, timeout=30,
              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if out.returncode != 0:
        raise OSError((out.stderr or out.stdout or "PowerShell failed").strip())
    return out.stdout.strip()


def app_dir() -> Path:
    """Folder that holds Finance.pyw (the project root), when known."""
    return Path(__file__).resolve().parent.parent
