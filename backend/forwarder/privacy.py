"""Personal customs clearance codes (PCCC) hidden from what is stored and shown: P2305****7201."""

import re
from typing import TypeVar

T = TypeVar("T")

# P and 12 digits, found anywhere in a text. ASCII digits only: a bare \d also matches full-width and
# other Unicode digits, which no PCCC has.
_PCCC = re.compile(r"P\d{12}", re.ASCII)


def mask_pccc(text: str) -> str:
    """text with every PCCC cut to its first 5 and last 4 characters: P230518447201 -> P2305****7201."""
    return _PCCC.sub(lambda match: f"{match[0][:5]}****{match[0][-4:]}", text)


def mask_pccc_deep(obj: T) -> T:
    """A copy of obj with mask_pccc applied to every string in it, dict keys included.

    Dicts, lists and tuples are rebuilt, so obj itself is never changed; numbers, booleans, None and
    anything else that is not a string come back as they are.
    """
    if isinstance(obj, str):
        return mask_pccc(obj)
    if isinstance(obj, dict):
        return {mask_pccc_deep(key): mask_pccc_deep(value) for key, value in obj.items()}
    if isinstance(obj, list):
        return [mask_pccc_deep(item) for item in obj]
    if isinstance(obj, tuple):
        return tuple(mask_pccc_deep(item) for item in obj)
    return obj
