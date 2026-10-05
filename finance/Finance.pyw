"""Finance Tracker. Double-click this file.

The first time it prepares itself (a few minutes, with a progress window). After that it just opens the app.
Everything stays on this computer. If something goes wrong, the problem is written to launcher-log.txt
next to this file; send me that text.
"""
import os
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

APP = Path(__file__).resolve().parent
VENV = APP / ".venv"
LOG = APP / "launcher-log.txt"
LOCK = APP / ".setup.lock"
IS_WIN = os.name == "nt"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DETACHED = 0x00000008 if IS_WIN else 0
MIN_PYTHON = (3, 10)


class SetupFailed(Exception):
    pass


def venv_python(windowed: bool = False) -> Path:
    if IS_WIN:
        exe = VENV / "Scripts" / ("pythonw.exe" if windowed else "python.exe")
        return exe if exe.exists() else VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def ready() -> bool:
    py = venv_python()
    if not py.exists():
        return False
    result = subprocess.run([str(py), "-c", "import ftracker, openpyxl, xlrd, pdfplumber"],
                            capture_output=True, creationflags=NO_WINDOW)
    return result.returncode == 0


def start_app() -> None:
    """Start the app detached from this launcher, so closing the launcher does not stop it."""
    subprocess.Popen([str(venv_python(windowed=True)), "-m", "ftracker", "app"], cwd=str(APP), creationflags=DETACHED | NO_WINDOW,
                     close_fds=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def base_python() -> str:
    exe = Path(sys.executable)
    if IS_WIN and exe.name.lower() == "pythonw.exe":
        console = exe.with_name("python.exe")
        if console.exists():
            return str(console)
    return str(exe)


def run(cmd: list[str], log) -> None:
    log("> " + " ".join(cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
                            creationflags=NO_WINDOW, cwd=str(APP))
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            log(line)
    if proc.wait() != 0:
        raise SetupFailed(f"A setup step failed (exit code {proc.returncode}). The messages above say why.")


def set_up(log) -> None:
    if sys.version_info < MIN_PYTHON:
        raise SetupFailed(f"This computer's Python is {sys.version.split()[0]}. Finance Tracker needs Python 3.10 or newer: "
                          "install the latest from python.org/downloads (tick 'Add python to PATH') and double-click Finance.pyw again.")
    log("Step 1 of 3: creating a private environment for the app...")
    if not venv_python().exists():
        run([base_python(), "-m", "venv", str(VENV)], log)
    log("Step 2 of 3: installing the app. This is the slow part; text keeps moving while it works...")
    run([str(venv_python()), "-m", "pip", "install", "--upgrade", "pip"], log)
    run([str(venv_python()), "-m", "pip", "install", "-e", str(APP)], log)
    log("Step 3 of 3: checking the install...")
    if not ready():
        raise SetupFailed("The app installed but could not be loaded.")
    log("Done. Opening the app...")


def alert(title: str, message: str) -> None:
    if IS_WIN:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)
            return
        except Exception:
            pass
    print(f"{title}: {message}")


def setup_with_window() -> None:
    lines: list[str] = []

    def log(line: str) -> None:
        lines.append(line)
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    try:
        import tkinter as tk
        from tkinter import scrolledtext
    except Exception:
        # No window toolkit: do the same work quietly, then report only if it fails.
        try:
            set_up(log)
            start_app()
        except Exception as exc:
            alert("Finance Tracker could not set itself up", f"{exc}\n\nDetails were saved in:\n{LOG}")
        return

    root = tk.Tk()
    root.title("Finance Tracker - first-time setup")
    root.geometry("760x420")
    tk.Label(root, text="Setting up Finance Tracker (first time only, about 2 to 5 minutes).\nPlease leave this window open.",
             justify="left", anchor="w").pack(fill="x", padx=10, pady=(10, 4))
    box = scrolledtext.ScrolledText(root, height=18, font=("Consolas", 9), state="disabled")
    box.pack(fill="both", expand=True, padx=10, pady=4)
    status = tk.Label(root, text="Working...", anchor="w")
    status.pack(fill="x", padx=10, pady=(0, 10))
    outcome: dict = {}
    shown = [0]

    def pump() -> None:
        while shown[0] < len(lines):
            box.configure(state="normal")
            box.insert("end", lines[shown[0]] + "\n")
            box.see("end")
            box.configure(state="disabled")
            shown[0] += 1
        if outcome.get("done"):
            if outcome.get("error"):
                status.configure(text="Something went wrong. The text above says what. Details were saved to launcher-log.txt.")
                tk.Button(root, text="Copy the details", command=lambda: (root.clipboard_clear(), root.clipboard_append("\n".join(lines)))).pack(pady=(0, 10))
            else:
                status.configure(text="Done. The app is opening.")
                root.after(1200, root.destroy)
            return
        root.after(150, pump)

    def worker() -> None:
        try:
            set_up(log)
            start_app()
        except Exception as exc:
            log(str(exc) if isinstance(exc, SetupFailed) else traceback.format_exc())
            outcome["error"] = True
        outcome["done"] = True

    threading.Thread(target=worker, daemon=True).start()
    pump()
    root.mainloop()


def main() -> None:
    if ready():
        start_app()
        return
    # Only one setup at a time (a second double-click while it runs would fight the first).
    if LOCK.exists() and time.time() - LOCK.stat().st_mtime < 900:
        alert("Finance Tracker", "Setup is already running. Please wait for its window to finish.")
        return
    LOCK.write_text(str(os.getpid()))
    try:
        setup_with_window()
    finally:
        LOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        LOG.write_text(traceback.format_exc(), encoding="utf-8")
        alert("Finance Tracker", f"Something went wrong. The details were saved in:\n{LOG}")
