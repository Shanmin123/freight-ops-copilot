"""Text PDFs with selectable text and embedded Simplified Chinese and Korean fonts."""

from functools import lru_cache
from io import BytesIO
from itertools import groupby
from os import environ
from pathlib import Path
from threading import RLock
from xml.sax.saxutils import escape

from forwarder.config import BACKEND_DIR

_LOCK = RLock()


@lru_cache(maxsize=4)
def _font(path: Path, name: str, fallback: str) -> str:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFont

    pdfmetrics.registerFont(TTFont(name, str(path)) if path.is_file() else UnicodeCIDFont(fallback))
    return name if path.is_file() else fallback


def _is_hangul(char: str) -> bool:
    return "\uac00" <= char <= "\ud7ff" or "\u1100" <= char <= "\u11ff" or "\u3130" <= char <= "\u318f"


def render_text_pdf(title: str, lines: list[str]) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, SimpleDocTemplate

    path = Path(environ.get("FORWARDER_FONT_DIR", str(BACKEND_DIR / "var" / "fonts")))
    output = BytesIO()
    with _LOCK:
        sc = _font(path / "NotoSansSC.ttf", "Forwarder-SC", "STSong-Light")
        kr = _font(path / "NotoSansKR.ttf", "Forwarder-KR", "HYSMyeongJo-Medium")

        def markup(text: str) -> str:
            return "".join(
                f'<font name="{kr if hangul else sc}">{escape("".join(chars))}</font>'
                for hangul, chars in groupby(text, _is_hangul)
            )

        heading = ParagraphStyle("heading", fontName=sc, fontSize=21, leading=31, spaceAfter=18,
                                 textColor=colors.HexColor("#12443e"), wordWrap="CJK")
        text = ParagraphStyle("body", fontName=sc, fontSize=11, leading=18, spaceAfter=6,
                              textColor=colors.HexColor("#182b39"), wordWrap="CJK")
        page = SimpleDocTemplate(output, pagesize=A4, title=title, leftMargin=46, rightMargin=46,
                                 topMargin=55, bottomMargin=48)
        page.build([Paragraph(markup(title), heading), *(
            Paragraph(markup(line) or "&#160;", text) for line in lines
        )])
    return output.getvalue()
