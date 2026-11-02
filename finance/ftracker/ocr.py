"""Reading text from a picture with the text recognition that is built into Windows 10 and 11.

Nothing here saves anything: it only returns the words it saw and where they were. If recognition is not
available the caller falls back to typing the numbers while looking at the picture.
"""
import asyncio
import subprocess
import sys

PACKAGES = ["winrt-runtime", "winrt-Windows.Foundation", "winrt-Windows.Foundation.Collections", "winrt-Windows.Globalization",
            "winrt-Windows.Graphics.Imaging", "winrt-Windows.Media.Ocr", "winrt-Windows.Storage", "winrt-Windows.Storage.Streams"]


def status() -> tuple[bool, str]:
    """(ready, what to tell the user)."""
    if sys.platform != "win32":
        return False, "Text recognition uses Windows, so on this computer you type the numbers while looking at the picture."
    try:
        from winrt.windows.media.ocr import OcrEngine  # noqa: F401
    except Exception:
        return False, "Not turned on yet."
    return True, "Ready."


def install() -> tuple[bool, str]:
    """One-time download of the small Windows helper packages into the app's own environment."""
    try:
        done = subprocess.run([sys.executable, "-m", "pip", "install", *PACKAGES], capture_output=True, text=True, timeout=600)
    except Exception as exc:
        return False, str(exc)
    return done.returncode == 0, (done.stdout + done.stderr)[-400:]


async def _read(path: str) -> tuple[list[dict], str]:
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage import FileAccessMode, StorageFile
    engine, used = None, ""
    for tag in ("he", "he-IL", "en-US"):
        try:
            engine = OcrEngine.try_create_from_language(Language(tag))
        except Exception:
            engine = None
        if engine:
            used = tag
            break
    if engine is None:
        engine = OcrEngine.try_create_from_user_profile_languages()
        used = "your Windows languages"
    if engine is None:
        raise RuntimeError("Windows has no text recognition language installed")
    f = await StorageFile.get_file_from_path_async(path)
    stream = await f.open_async(FileAccessMode.READ)
    bitmap = await (await BitmapDecoder.create_async(stream)).get_software_bitmap_async()
    result = await engine.recognize_async(bitmap)
    words = []
    for line in result.lines:
        for w in line.words:
            r = w.bounding_rect
            words.append({"text": w.text, "x": float(r.x), "y": float(r.y), "w": float(r.width), "h": float(r.height)})
    return words, used


def read_words(path) -> tuple[list[dict] | None, str]:
    """(words, note). words is None when it could not be read, and note says why."""
    ok, why = status()
    if not ok:
        return None, why
    try:
        words, lang = asyncio.run(_read(str(path)))
    except Exception as exc:
        return None, f"The picture could not be read ({exc})."
    if not words:
        return None, "No text was found in the picture."
    note = "" if lang in ("he", "he-IL") else ("Hebrew recognition is not installed on this computer, so Hebrew names may be wrong. Numbers are fine. "
                                              "To add it: Windows Settings, Time & language, Language & region, add Hebrew.")
    return words, note
