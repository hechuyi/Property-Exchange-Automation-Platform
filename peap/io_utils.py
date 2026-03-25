"""Input/output utility helpers."""

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

try:
    import chardet  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    chardet = None


@dataclass
class ReadTextResult:
    content: str
    encoding: str
    confidence: float


def _candidate_encodings(raw_data: bytes) -> List[str]:
    candidates = ["utf-8", "utf-8-sig", "gb18030", "gbk", "big5"]
    if chardet is not None:
        detected = chardet.detect(raw_data)
        guessed = (detected or {}).get("encoding")
        if guessed:
            guessed_lower = str(guessed).lower()
            if guessed_lower not in [c.lower() for c in candidates]:
                candidates.insert(0, guessed)
            else:
                # Keep guessed encoding first if already in the list.
                candidates = [guessed] + [c for c in candidates if c.lower() != guessed_lower]
    return candidates


def read_text_with_fallback(file_path: str) -> Optional[ReadTextResult]:
    """
    Read text using a deterministic encoding fallback chain.

    Returns None when file cannot be opened.
    """
    path = Path(file_path)
    try:
        return ReadTextResult(path.read_text(encoding="utf-8"), "utf-8", 1.0)
    except UnicodeDecodeError:
        pass
    except OSError:
        return None

    try:
        raw_data = path.read_bytes()
    except OSError:
        return None

    detected_confidence = 0.0
    if chardet is not None:
        detected_confidence = float((chardet.detect(raw_data) or {}).get("confidence") or 0.0)

    for encoding in _candidate_encodings(raw_data):
        try:
            return ReadTextResult(raw_data.decode(encoding), encoding, detected_confidence)
        except (UnicodeDecodeError, LookupError):
            continue

    # Last resort to avoid hard failure on badly encoded pages.
    return ReadTextResult(raw_data.decode("utf-8", errors="replace"), "utf-8/replace", 0.0)
