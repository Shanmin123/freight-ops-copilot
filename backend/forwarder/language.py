"""Reply language inferred from customer messages, newest first."""

import re
from collections.abc import Iterable
from typing import Literal

ReplyLanguage = Literal["zh", "ko"]
_HAN = re.compile(r"[\u3400-\u9fff]")
_HANGUL = re.compile(r"[\u1100-\u11ff\u3130-\u318f\uac00-\ud7a3]")


def detect_reply_language(
    messages: Iterable[str], *, fallback: ReplyLanguage = "zh",
) -> ReplyLanguage:
    for message in messages:
        han = len(_HAN.findall(message))
        hangul = len(_HANGUL.findall(message))
        if han or hangul:
            return "ko" if hangul >= han else "zh"
    return fallback
