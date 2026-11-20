"""Downloadable, fictional documents used by the reconciliation walkthrough."""

from io import BytesIO
from pathlib import Path

import reportlab
from PIL import Image, ImageDraw, ImageFont

from forwarder.pdf_support import render_text_pdf

_TITLE = {"invoice": "Commercial Invoice", "packing": "Packing List", "bill": "Bill of Lading"}
_FIELDS = {
    "invoice": ("INV-FW26041", "BLUE HARBOR RETAIL LTD", "40 CARTONS", "480.00 KG", "2.40 CBM", "851762", "2400.00"),
    "packing": ("PL-FW26041", "BLUE HARBOR RETAIL LTD", "42 CARTONS", "480.20 KG", "2.40 CBM", "8517620000", "2450.00"),
    "bill": ("BL-FW26041", "BLUE HARBOUR RETAIL LTD", "40 CARTONS", "487.00 KG", "2.41 CBM", "851769", "2400.00"),
}


def _sample_lines(kind: str) -> list[str]:
    reference, consignee, packages, gross, volume, hs_code, amount = _FIELDS[kind]
    return [
        f"Document No: {reference}", "Route: Qingdao to Incheon",
        f"Consignee: {consignee}", "Goods: Wi-Fi router demonstration shipment",
        f"Packages: {packages}", f"Gross Weight: {gross}", f"Volume: {volume}",
        f"HS Code: {hs_code}", f"Declared Value: USD {amount}",
        "Country of Origin: China", "FTA Preference: Requested" if kind == "invoice" else "FTA Preference: N/A",
    ]


def sample_file(kind: str) -> tuple[str, bytes, str]:
    if kind == "photo":
        return "commercial-invoice-photo.jpg", _sample_photo(), "image/jpeg"
    return f"{_TITLE[kind].lower().replace(' ', '-')}.pdf", render_text_pdf(_TITLE[kind], _sample_lines(kind)), "application/pdf"


def _sample_photo() -> bytes:
    # Rasterize only the printed text, so this image has no hidden readable text layer.
    paper = Image.new("RGB", (1180, 1360), "#f9f3e5")
    draw = ImageDraw.Draw(paper)
    font_path = Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"
    title = ImageFont.truetype(font_path, 60)
    body = ImageFont.truetype(font_path, 36)
    draw.text((79, 77), _TITLE["invoice"].upper(), font=title, fill="#20304b")
    draw.line((80, 170, 1098, 170), fill="#68867e", width=4)
    for index, line in enumerate(_sample_lines("invoice")):
        draw.text((82, 220 + index * 89), line, font=body, fill="#293330")
    rotated = paper.rotate(1.6, Image.Resampling.BICUBIC, expand=True, fillcolor="#b0ada3")
    background = Image.new("RGB", (rotated.width + 100, rotated.height + 100), "#b0ada3")
    background.paste(rotated, (48, 50))
    result = BytesIO()
    background.save(result, "JPEG", quality=88)
    return result.getvalue()
