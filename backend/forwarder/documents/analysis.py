"""Extract common forwarder fields and compare normalized document values."""

import re
import unicodedata
from decimal import Decimal, InvalidOperation
from itertools import combinations

KINDS = ("invoice", "packing", "bill")
DOCUMENT_LABELS = {"invoice": "商业发票", "packing": "装箱单", "bill": "提单"}
FIELD_LABELS = {
    "consignee": "收货人", "packages": "件数", "gross_kg": "毛重（kg）", "volume_cbm": "体积（m³）",
    "hs_code": "HS 编码", "amount": "申报金额", "currency": "币种",
}
_ALIASES = {
    "reference": ("document no", "document number", "invoice no", "packing list no", "bill of lading no", "b/l no", "hbl", "单据编号", "单号", "문서 번호", "송장 번호"),
    "consignee": ("consignee", "importer", "ship to", "收货人", "收件人", "수하인", "수취인", "수입자"),
    "packages": ("packages", "total packages", "number of packages", "no. of packages", "cartons", "包装件数", "件数", "箱数", "포장 수량", "포장수"),
    "gross_kg": ("gross weight", "gross wt", "gross kg", "g.w.", "毛重", "총중량", "총 중량"),
    "volume_cbm": ("volume", "measurement", "cbm", "total volume", "体积", "材积", "용적", "부피"),
    "hs_code": ("hs code", "h.s. code", "harmonized code", "hs tariff code", "HS编码", "HS 编码", "商品编码", "HS 코드", "품목번호"),
    "amount": ("amount", "invoice total", "total amount", "declared value", "invoice value", "发票总额", "申报金额", "总金额", "신고 금액", "총 금액"),
    "currency": ("currency", "币种", "货币", "통화"),
    "goods": ("goods", "description of goods", "commodity", "product name", "货物名称", "货物", "品名", "품명", "물품명"),
    "origin": ("country of origin", "origin", "原产国", "原产地", "원산지", "원산국"),
    "fta": ("fta preference", "preferential tariff", "fta", "优惠税率", "申请协定税率", "협정관세", "fta 신청"),
}
_REQUIRED = {
    "invoice": ("consignee", "packages", "hs_code", "amount", "currency"),
    "packing": ("consignee", "packages", "gross_kg", "volume_cbm"),
    "bill": ("consignee", "packages", "gross_kg", "volume_cbm"),
}
_TOLERANCE = {"gross_kg": Decimal("0.5"), "volume_cbm": Decimal("0.02"), "amount": Decimal("0.01")}
_CURRENCIES = re.compile(r"(?<![A-Z])(?:USD|CNY|RMB|KRW|EUR|JPY|GBP|HKD|SGD|AUD|CAD|CHF|VND|THB|MYR|TWD)(?![A-Z])")
_NUMBER = re.compile(r"^[+-]?[0-9]+(?:[.,][0-9]+)*")
_LABELS = {alias.casefold(): field for field, aliases in _ALIASES.items() for alias in aliases}
_FOLD_SEPARATORS = re.compile(r"[\W_]+")
_COMPACT_LABELS = {_FOLD_SEPARATORS.sub("", label): field for label, field in _LABELS.items()}
_LABEL_DELIMITER = re.compile(r"^([^:;=：]+)[:;=：](.*)$")
_LABEL_PATTERN = re.compile(
    r"^(" + "|".join(re.escape(alias) for alias in sorted(_LABELS, key=len, reverse=True))
    + r")(?:\s*[:;=：]\s*|\s{2,}|$)(.*)$",
    re.IGNORECASE,
)
_KB = "/kb?doc=import-documents#"


def extract_fields(text: str) -> dict:
    """Read labelled fields in Chinese, Korean or English from PDF or OCR text."""
    fields = {}
    pending = None
    for raw in unicodedata.normalize("NFKC", text).splitlines():
        line = raw.strip()
        if not line:
            continue
        explicit = _LABEL_DELIMITER.match(line)
        field = _COMPACT_LABELS.get(_FOLD_SEPARATORS.sub("", explicit[1].casefold())) if explicit else None
        if field:
            value = explicit[2]
        else:
            match = _LABEL_PATTERN.match(line)
            if match:
                field, value = _LABELS[match[1].casefold()], match[2]
        if field:
            value = value.strip()
            pending = None if value else field
            if value:
                fields.setdefault(field, value)
        elif pending:
            fields.setdefault(pending, line)
            pending = None
    return normalize_fields(fields)


def _number(value: str) -> tuple[Decimal, str] | None:
    match = _NUMBER.match(value.strip())
    if match is None or len(match[0]) > 24:
        return None
    token = match[0]
    if "," in token and "." in token:
        token = token.replace(",", "") if token.rfind(".") > token.rfind(",") else token.replace(".", "").replace(",", ".")
    elif "," in token:
        pieces = token.split(",")
        if all(len(piece) == 3 for piece in pieces[1:]):
            token = "".join(pieces)
        elif len(pieces) == 2:
            token = ".".join(pieces)
        else:
            return None
    try:
        return Decimal(token), value.strip()[match.end():].strip().casefold()
    except InvalidOperation:
        return None


def _currency(value: str) -> str | None:
    found = _CURRENCIES.search(value.upper())
    return ("CNY" if found[0] == "RMB" else found[0]) if found else None


def _normalize_amount(raw: str) -> float | None:
    stripped = _CURRENCIES.sub("", raw.upper()).lstrip(" $¥₩€£")
    parsed = _number(stripped)
    return float(parsed[0]) if parsed and parsed[0] >= 0 else None


def _normalize_measure(field: str, raw: str) -> float | int | None:
    parsed = _number(raw)
    if not parsed:
        return None
    number, unit = parsed
    if number <= 0:
        return None
    if field == "packages":
        return int(number) if number == number.to_integral_value() else None
    units = {
        "gross_kg": {"": "1", "kg": "1", "kgs": "1", "千克": "1", "公斤": "1", "g": "0.001",
                     "lb": "0.45359237", "lbs": "0.45359237", "t": "1000"},
        "volume_cbm": {"": "1", "m3": "1", "m^3": "1", "cbm": "1", "立方米": "1",
                       "l": "0.001", "liters": "0.001", "ft3": "0.028316846592"},
    }
    factor = units[field].get(unit.rstrip("."))
    return float(number * Decimal(factor)) if factor else None


def normalize_fields(fields: dict) -> dict:
    """Normalize manual extraction and model output through the same unit and type rules."""
    normalized = {}
    for field in _ALIASES:
        value = fields.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            continue
        raw = unicodedata.normalize("NFKC", str(value)).strip()[:300]
        if not raw:
            continue
        if field in ("packages", "gross_kg", "volume_cbm"):
            result = _normalize_measure(field, raw)
        elif field == "hs_code":
            compact = re.sub(r"[.\s-]", "", raw)
            result = compact if re.fullmatch(r"[0-9]{6,10}", compact) else None
        elif field == "amount":
            result = _normalize_amount(raw)
            embedded = _currency(raw)
            if embedded:
                normalized.setdefault("currency", embedded)
        elif field == "currency":
            result = _currency(raw)
        else:
            result = re.sub(r"\s+", " ", raw)
        if result is not None:
            normalized[field] = result
    return normalized


def _comparison(field: str, left, right) -> bool:
    if field == "consignee":
        canonical = lambda value: "".join(c for c in value.casefold() if c.isalnum())
        return canonical(left) == canonical(right)
    if field == "hs_code":
        precision = min(len(left), len(right))
        return left[:precision] == right[:precision]
    if field in _TOLERANCE:
        return abs(Decimal(str(left)) - Decimal(str(right))) <= _TOLERANCE[field]
    return left == right


def _requirements(documents: dict) -> list[dict]:
    goods = " ".join(fields.get("goods", "") for fields in documents.values()).casefold()
    codes = {fields.get("hs_code", "") for fields in documents.values()}
    rules = []
    if any(code.startswith(("8517", "8525", "8526")) for code in codes) or re.search(r"wi[ -]?fi|wireless|bluetooth|무선|블루투스|无线|蓝牙", goods):
        rules.append({
            "code": "kc_radio", "title": "KC 无线设备合规",
            "detail": "货名或 HS 编码提示可能包含无线设备。请按具体型号、用途和韩国国立电波研究院分类核对适合性评估。",
            "kb_url": _KB + "kc-radio", "source_url": "https://www.rra.go.kr/en/cas/intro.do",
        })
    if any(code.startswith(("01", "02", "06", "07", "08", "12")) for code in codes) or re.search(r"\b(?:seed|seeds|plant|plants|fruit|meat|wood)\b|종자|검역|식물|种子|鲜果|肉类|植物", goods):
        rules.append({
            "code": "quarantine", "title": "动植物检疫",
            "detail": "货名或 HS 编码提示可能涉及动植物产品。请按具体品种、加工状态及原产地核对检疫条件和证明。",
            "kb_url": _KB + "quarantine", "source_url": "https://ebook.qia.go.kr/src/viewer/main.php?category=1&host=main&page=243&site=20260107_101021",
        })
    preference = documents["invoice"].get("fta", "").casefold()
    if preference in {"yes", "requested", "apply", "申请", "是", "신청", "예"}:
        rules.append({
            "code": "fta_origin", "title": "协定税率与原产地证明",
            "detail": "发票注明拟申请协定税率。请核对适用协定和原产地规则，准备相应的有效原产地证明。",
            "kb_url": _KB + "fta-origin", "source_url": "https://www.customs.go.kr/engportal/cm/cntnts/cntntsView.do?cntntsId=2343&mi=7328",
        })
    return rules


def review_documents(documents: dict) -> dict:
    checks = []
    for field, label in FIELD_LABELS.items():
        values = {kind: documents[kind].get(field) for kind in KINDS}
        present = [value for value in values.values() if value is not None]
        currencies = {documents[kind].get("currency") for kind, value in values.items() if value is not None}
        if not present:
            status = "missing"
        elif len(present) == 1:
            status = "single"
        elif field == "amount" and (None in currencies or len(currencies) != 1):
            status = "incomparable"
        else:
            status = "match" if all(_comparison(field, a, b) for a, b in combinations(present, 2)) else "mismatch"
        checks.append({"field": field, "label": label, "status": status, "values": values})

    missing = [
        {"kind": kind, "label": FIELD_LABELS[field]}
        for kind, fields in _REQUIRED.items() for field in fields if documents[kind].get(field) is None
    ]
    differences = [check for check in checks if check["status"] == "mismatch"]
    amount_unverified = any(check["status"] == "incomparable" for check in checks)
    requirements = _requirements(documents)
    correction = ["您好，请协助核对本票货物的单据："]
    for check in differences:
        values = "、".join(f"{DOCUMENT_LABELS[kind]} {value}" for kind, value in check["values"].items() if value is not None)
        correction.append(f"- {check['label']}：{values}。请确认正确值并提供更新件。")
    if amount_unverified:
        correction.append("- 部分金额的币种不一致或缺失，请确认使用的币种和申报总额。")
    correction.extend(f"- {DOCUMENT_LABELS[item['kind']]}未读到{item['label']}，请核对原件。" for item in missing)
    if not differences and not missing and not amount_unverified:
        correction.append("目前未发现需要客户更正的字段。")

    customs = ["报关资料草稿", "资料：商业发票、装箱单、提单。"]
    for check in checks:
        value = next((value for value in check["values"].values() if value is not None), None)
        if value is None or check["status"] in {"mismatch", "incomparable"}:
            value = "待核对"
        customs.append(f"{check['label']}：{value}")
    customs.extend(f"核对事项：{rule['title']}。{rule['detail']}" for rule in requirements)
    return {
        "checks": checks, "missing": missing, "requirements": requirements,
        "drafts": {
            "correction": {"text": "\n".join(correction), "status": "draft"},
            "customs": {"text": "\n".join(customs), "status": "draft"},
        },
    }
