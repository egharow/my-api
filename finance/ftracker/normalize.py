import re

_PUNCT = re.compile(r"[\"'`*()\[\]{}.,:;|/\\_]+")
_SPACES = re.compile(r"\s+")
_DIGITS = re.compile(r"\d+")


def norm_description(text: str) -> str:
    """Upper-case, strip punctuation and extra spaces. Digits stay (they can identify branches)."""
    return _SPACES.sub(" ", _PUNCT.sub(" ", text.upper())).strip()


def merchant_key(text: str) -> str:
    """Looser key for grouping the same merchant: also drops digits and a trailing '-י'."""
    s = _DIGITS.sub("", norm_description(text))
    s = re.sub(r"\s*-\s*[א-ת]?$", "", s)
    return _SPACES.sub(" ", s).strip()
