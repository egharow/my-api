"""Where a statement file is filed once it has been imported."""
from pathlib import Path

from .config import Home
from .parsers.base import ParsedFile


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    n = 2
    while True:
        candidate = path.with_name(f"{path.stem}-{n}{path.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def plan_path(home: Home, parsed: ParsedFile, original: Path, owners: dict[str, str]) -> Path:
    """owners maps last4 -> owner label (lower-case) for cards whose owner is known."""
    ext = original.suffix.lower()
    if parsed.file_kind in ("card_export", "card_fetch"):
        cards = [s for s in parsed.statements]
        month = max(s.billing_date for s in cards).strftime("%Y-%m")
        base = home.archive / "credit-cards" / parsed.issuer
        if len(cards) == 1:
            last4 = cards[0].last4
            folder = f"{last4}-{owners[last4]}" if owners.get(last4) else last4
            fetched = "_fetched" if parsed.file_kind == "card_fetch" else ""
            return _unique(base / folder / f"{month}_{parsed.issuer}-{last4}{fetched}{ext}")
        tag = "+".join(s.last4 for s in cards)
        if parsed.file_kind == "card_fetch":
            return _unique(base / "fetched" / f"{month}_{parsed.issuer}-fetched{ext}")
        return _unique(base / f"{month}_{parsed.issuer}-{tag}{ext}")
    st = parsed.statements[0]
    name = f"{st.period_start:%Y-%m-%d}_to_{st.period_end:%Y-%m-%d}_{parsed.issuer}-{st.last4}{ext}"
    return _unique(home.archive / "bank" / parsed.issuer / f"account-{st.last4}" / name)
