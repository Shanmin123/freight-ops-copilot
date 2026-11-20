"""Deterministic demo tariffs, quote and claim approval, and Agent tools."""

import json
import re
import uuid
from datetime import date, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from forwarder.db import Database
from forwarder.privacy import mask_pccc
from forwarder.tools.registry import ToolContext, ToolError, ToolRegistry, ToolSpec
from forwarder.world.data import WORLD_TZ
from forwarder.world.ledger import COMMITTED, STAGED, Ledger, LedgerRecord

Mode = Literal["fcl", "lcl", "ferry", "air"]
Incoterm = Literal["EXW", "FCA", "FOB", "CFR", "CIF", "CPT", "CIP", "DAP"]
Container = Literal["20GP", "40GP", "40HQ"]

_PORTS = {
    "CNTAO": "青岛港",
    "CNSHA": "上海港",
    "KRINC": "仁川港",
    "KRPUS": "釜山港",
    "CNTAO-AIR": "青岛胶东机场",
    "KRICN-AIR": "仁川国际机场",
}

# Freight cost and list sale price in USD. These are fixed local demo tariffs.
_LANES = {
    ("CNTAO", "KRINC", "fcl"): {"20GP": (420, 570), "40GP": (710, 940), "40HQ": (790, 1040)},
    ("KRINC", "CNTAO", "fcl"): {"20GP": (400, 545), "40GP": (680, 900), "40HQ": (755, 995)},
    ("CNSHA", "KRPUS", "fcl"): {"20GP": (470, 630), "40GP": (745, 995), "40HQ": (820, 1090)},
    ("KRPUS", "CNSHA", "fcl"): {"20GP": (455, 610), "40GP": (730, 965), "40HQ": (810, 1060)},
    ("CNTAO", "KRINC", "lcl"): {"cbm": (46, 68)},
    ("KRINC", "CNTAO", "lcl"): {"cbm": (43, 64)},
    ("CNSHA", "KRPUS", "lcl"): {"cbm": (58, 79)},
    ("KRPUS", "CNSHA", "lcl"): {"cbm": (55, 75)},
    ("CNTAO", "KRINC", "ferry"): {"cbm": (54, 77)},
    ("KRINC", "CNTAO", "ferry"): {"cbm": (51, 73)},
    ("CNTAO-AIR", "KRICN-AIR", "air"): {"kg": ("2.35", "3.10")},
    ("KRICN-AIR", "CNTAO-AIR", "air"): {"kg": ("2.25", "2.95")},
}
_MODE_NAMES = {
    "zh": {"fcl": "海运整柜", "lcl": "海运拼箱", "ferry": "轮渡", "air": "空运"},
    "ko": {"fcl": "해상 FCL", "lcl": "해상 LCL", "ferry": "카페리", "air": "항공"},
}
_PORT_FEES = {"CNTAO": (55, 75), "CNSHA": (67, 92), "KRINC": (65, 90), "KRPUS": (72, 98)}
_REFERENCE_DAYS = 14
_MIN_MARGIN_PCT = Decimal("12")
_STAFF_DISCOUNT_PCT = Decimal("5")
_CLAIM_LIMIT_USD = Decimal("500")
_AIR_KG_PER_CBM = Decimal("167")
_CREATIONS = ("quote.create", "claim.open")
QUOTE_TOOLS = (
    "rate.search", "schedule.search", "quote.calculate", "policy.search",
    "quote.create", "approval.create_case", "claim.open",
)


def _today() -> date:
    return datetime.now(WORLD_TZ).date()


class QuoteInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    origin: str = Field(min_length=1, max_length=20)
    destination: str = Field(min_length=1, max_length=20)
    mode: Mode
    goods: str = Field(min_length=1, max_length=100)
    ship_date: date
    incoterm: Incoterm
    container_type: Container | None = None
    container_count: int | None = Field(default=None, ge=1, le=50)
    volume_cbm: Decimal | None = Field(default=None, gt=0, le=9999, decimal_places=3)
    weight_kg: Decimal | None = Field(default=None, gt=0, le=100000, decimal_places=2)
    declared_value_usd: Decimal | None = Field(default=None, gt=0, le=1000000, decimal_places=2)
    discount_pct: Decimal = Field(default=Decimal("0"), ge=0, le=40, decimal_places=2)

    @model_validator(mode="after")
    def validate_freight_inputs(self):
        if self.mode == "fcl" and (self.container_type is None or self.container_count is None):
            raise ValueError("整柜需要柜型和柜数")
        if self.mode in ("lcl", "ferry") and self.volume_cbm is None:
            raise ValueError("拼箱和轮渡需要货物体积")
        if self.mode == "air" and (self.weight_kg is None or self.volume_cbm is None):
            raise ValueError("空运需要实际重量和体积")
        if self.mode == "air" and self.incoterm in ("FOB", "CFR", "CIF"):
            raise ValueError("空运请使用 EXW、FCA、CPT、CIP 或 DAP")
        if self.incoterm in ("CIF", "CIP") and self.declared_value_usd is None:
            raise ValueError("含保险的贸易条款需要货值")
        return self


class QuoteDraft(QuoteInput):
    ticket_id: str | None = Field(default=None, min_length=1, max_length=32)


class ManagerDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision: Literal["approved", "rejected"]
    note: str = Field(default="", max_length=300)


def _money(amount: Decimal) -> Decimal:
    return amount.quantize(Decimal(".01"), rounding=ROUND_HALF_UP)


def list_rates(origin: str | None, destination: str | None, mode: str | None) -> list[dict]:
    valid_until = (_today() + timedelta(days=_REFERENCE_DAYS)).isoformat()
    return [
        {
            "origin": start, "destination": end, "mode": transport,
            "origin_name": _PORTS[start], "destination_name": _PORTS[end],
            "rates": [
                {"unit": unit, "price_usd": float(Decimal(sale))}
                for unit, (_, sale) in prices.items()
            ],
            "valid_until": valid_until,
        }
        for (start, end, transport), prices in _LANES.items()
        if (origin is None or origin == start)
        and (destination is None or destination == end)
        and (mode is None or mode == transport)
    ]


def list_sailings(origin: str, destination: str, mode: Mode, earliest: date) -> list[dict]:
    today = _today()
    if (origin, destination, mode) not in _LANES or earliest > today + timedelta(days=365):
        return []
    weekdays, transit = {
        "fcl": ((1, 4), 2), "lcl": ((2, 5), 3),
        "ferry": ((0, 2, 4), 1), "air": ((0, 1, 2, 3, 4, 5, 6), 1),
    }[mode]
    first_day = max(earliest, today)
    days = (first_day + timedelta(days=offset) for offset in range(14))
    return [
        {"departure": day.isoformat(), "arrival": (day + timedelta(days=transit)).isoformat()}
        for day in days if day.weekday() in weekdays
    ][:3]


def policies() -> list[dict]:
    return [
        {"key": "quote_margin", "label": "最低毛利率", "value": float(_MIN_MARGIN_PCT), "unit": "%"},
        {"key": "quote_discount", "label": "员工折扣权限", "value": float(_STAFF_DISCOUNT_PCT), "unit": "%"},
        {"key": "claim_limit", "label": "理赔经理审批线", "value": float(_CLAIM_LIMIT_USD), "unit": "USD"},
    ]


def calculate_quote(body: QuoteInput) -> dict:
    today = _today()
    tariffs = _LANES.get((body.origin, body.destination, body.mode))
    if not tariffs:
        raise ValueError("该航线和运输方式没有演示运价")
    if body.ship_date < today:
        raise ValueError("期望出运日不可早于今天")
    if body.ship_date > today + timedelta(days=365):
        raise ValueError("请选择一年内的期望出运日")

    if body.mode == "fcl":
        unit, quantity = body.container_type, Decimal(body.container_count)
        display_unit = f"{unit} 柜"
    elif body.mode == "air":
        unit = "kg"
        quantity = max(body.weight_kg, body.volume_cbm * _AIR_KG_PER_CBM).to_integral_value(rounding=ROUND_CEILING)
        display_unit = "计费公斤"
    else:
        unit, quantity = "cbm", max(Decimal("1"), body.volume_cbm)
        display_unit = "CBM"

    charges: list[dict] = []
    cost_total = Decimal(0)
    price_total = Decimal(0)

    def add(name: str, name_en: str, rates: tuple, qty: Decimal = Decimal(1), charge_unit: str = "票"):
        nonlocal cost_total, price_total
        cost_rate, sale_rate = (Decimal(rate) for rate in rates)
        cost_total += _money(qty * cost_rate)
        amount = _money(qty * sale_rate)
        price_total += amount
        charges.append({
            "name": name, "name_en": name_en, "unit": charge_unit, "quantity": float(qty),
            "rate_usd": float(sale_rate), "amount_usd": float(amount),
        })

    if body.incoterm == "EXW":
        add("提货服务", "Origin pickup", (62, 85))
    if body.incoterm not in ("FCA", "FOB"):
        add("起运港操作", "Origin terminal handling", _PORT_FEES.get(body.origin, (35, 50)))
    add("主运费", "International freight", tariffs[unit], quantity, display_unit)
    add("文件费", "Documentation", (16, 25))
    if body.incoterm in ("CIF", "CIP"):
        add("货运保险", "Cargo insurance", (
            max(Decimal("12"), _money(body.declared_value_usd * Decimal(".0025"))),
            max(Decimal("18"), _money(body.declared_value_usd * Decimal(".0035"))),
        ))
    if body.incoterm == "DAP":
        add("目的港操作", "Destination terminal handling", _PORT_FEES.get(body.destination, (40, 56)))
        add("末端派送", "Destination delivery", (85, 120))
    gross = price_total
    discount = _money(price_total * body.discount_pct / 100)
    price_total -= discount
    margin = _money((price_total - cost_total) * 100 / price_total)
    reasons = []
    if margin < _MIN_MARGIN_PCT:
        reasons.append("毛利低于底线")
    if body.discount_pct > _STAFF_DISCOUNT_PCT:
        reasons.append("折扣超过员工权限")
    valid_until = today + timedelta(days=_REFERENCE_DAYS)
    return {
        "kind": "quote", "currency": "USD", "request": {
            **body.model_dump(mode="json"), "goods": mask_pccc(body.goods),
        },
        "route": {"origin": _PORTS[body.origin], "destination": _PORTS[body.destination]},
        "charges": charges, "subtotal_usd": float(gross), "discount_usd": float(discount),
        "amount_usd": float(price_total), "valid_until": valid_until.isoformat(),
        "schedule": list_sailings(body.origin, body.destination, body.mode, body.ship_date),
        "excludes_tax": True,
        "approval_required": bool(reasons),
        "internal": {"cost_usd": float(cost_total), "margin_pct": float(margin), "reasons": reasons},
    }


def _record(row: dict) -> LedgerRecord:
    return LedgerRecord(**{**row, "payload": json.loads(row["payload"])})


class QuoteService:
    """The ledger stores employee drafts, approved effects, and a separate manager decision."""

    def __init__(self, db: Database, ledger: Ledger) -> None:
        self.db = db
        self.ledger = ledger

    def records(self) -> list[dict]:
        rows = self.db.query(
            "SELECT id, namespace, ticket_id, tool, payload, status, created_at FROM ledger "
            "WHERE tool IN ('quote.create', 'claim.open', 'approval.create_case', 'approval.review') "
            "AND status IN ('staged', 'committed') ORDER BY id",
        )
        entries = [_record(row) for row in rows]
        reviews = {
            record.payload["object_id"]: record
            for record in entries if record.tool == "approval.review" and record.status == COMMITTED
        }
        extra_approvals = {
            record.payload["object_id"]: record.payload["reason"]
            for record in entries if record.tool == "approval.create_case" and record.status == COMMITTED
        }
        results = []
        for entry in reversed(entries):
            if entry.tool not in _CREATIONS:
                continue
            payload = entry.payload
            object_id = payload["id"]
            review = reviews.get(object_id)
            required = payload["approval_required"] or object_id in extra_approvals
            decision = review.payload["decision"] if review else None
            status = "draft" if entry.status != COMMITTED else (
                "rejected" if decision == "rejected" else
                "pending_approval" if required and decision is None else
                "formal" if entry.tool == "quote.create" else "approved"
            )
            results.append({
                **payload,
                "approval_required": required,
                "status": status,
                "ticket_id": entry.ticket_id or None,
                "confirm_in_quotes": entry.namespace == f"manual:{object_id}",
                "manager_note": (review.payload["note"] if review else None),
                "approval_reason": extra_approvals.get(object_id),
                "created_at": entry.created_at,
            })
        return results

    def get(self, object_id: str) -> dict:
        match = next((record for record in self.records() if record["id"] == object_id), None)
        if match is None:
            raise HTTPException(404, "找不到报价或理赔")
        return match

    def create(self, body: QuoteDraft) -> dict:
        with self.db.transaction():
            ticket = None
            if body.ticket_id:
                ticket = self.db.query_one("SELECT customer_id FROM tickets WHERE id = ?", (body.ticket_id,))
                if not ticket:
                    raise HTTPException(404, "找不到关联工单")
            payload = quote_payload(body, ticket["customer_id"] if ticket else None)
            object_id = payload["id"]
            self.ledger.stage(
                namespace=f"manual:{object_id}", ticket_id=body.ticket_id or "",
                tool="quote.create", payload=payload,
            )
        return self.get(object_id)

    def confirm(self, object_id: str) -> dict:
        with self.db.transaction():
            record = self.get(object_id)
            if record["status"] != "draft" or not record["confirm_in_quotes"]:
                raise HTTPException(409, "此报价需要在对应工单确认，或已经处理")
            self.ledger.commit(f"manual:{object_id}")
        return self.get(object_id)

    def review(self, object_id: str, body: ManagerDecision) -> dict:
        with self.db.transaction():
            record = self.get(object_id)
            if record["status"] != "pending_approval":
                raise HTTPException(409, "此申请不在待经理审批状态")
            namespace = f"manager:{object_id}"
            self.ledger.stage(
                namespace=namespace, ticket_id=record["ticket_id"] or "",
                tool="approval.review",
                payload={"object_id": object_id, "decision": body.decision, "note": mask_pccc(body.note)},
            )
            self.ledger.commit(namespace)
        return self.get(object_id)


def quote_payload(body: QuoteInput, customer_id: str | None) -> dict:
    quote = calculate_quote(body)
    quote["request"].pop("ticket_id", None)
    return {**quote, "id": "Q-" + uuid.uuid4().hex[:10].upper(), "customer_id": customer_id}


def _public_quote_pdf(record: dict) -> bytes:
    from forwarder.pdf_support import render_text_pdf

    request = record["request"]
    title = f"正式货运报价 {record['id']}"
    lines = [
        f"航线：{record['route']['origin']} → {record['route']['destination']}",
        f"运输方式：{_MODE_NAMES['zh'][request['mode']]} · 贸易条款：{request['incoterm']}",
        f"货物：{request['goods']}",
        f"期望出运日：{request['ship_date']} · 报价有效期至：{record['valid_until']}",
        "",
        *(f"{line['name']}  {line['quantity']:g} {line['unit']} × USD {line['rate_usd']:.2f}"
          f"  = USD {line['amount_usd']:.2f}" for line in record["charges"]),
        f"折扣：USD {record['discount_usd']:.2f}",
        f"合计：USD {record['amount_usd']:.2f}",
        "本报价不含关税和增值税。班次为参考，实际以订舱确认为准。",
    ]
    return render_text_pdf(title, lines)


def build_quote_router(quotes: QuoteService) -> APIRouter:
    router = APIRouter(prefix="/api/quotes")

    @router.get("/rates")
    def rates(origin: str | None = None, destination: str | None = None, mode: Mode | None = None):
        return {"rates": list_rates(origin, destination, mode)}

    @router.get("/schedule")
    def schedule(origin: str, destination: str, mode: Mode, earliest: date | None = None):
        return {"sailings": list_sailings(origin, destination, mode, earliest or _today())}

    @router.get("/policy")
    def quote_policy():
        return {"rules": policies()}

    @router.post("/calculate")
    def calculate(body: QuoteInput):
        try:
            return calculate_quote(body)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("")
    def quote_list(ticket_id: str | None = Query(default=None, max_length=32)):
        return {"records": [
            record for record in quotes.records() if ticket_id is None or record["ticket_id"] == ticket_id
        ]}

    @router.post("", status_code=201)
    def create(body: QuoteDraft):
        try:
            return quotes.create(body)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/{object_id}/confirm")
    def confirm(object_id: str):
        return quotes.confirm(object_id)

    @router.post("/{object_id}/review")
    def review(object_id: str, body: ManagerDecision):
        return quotes.review(object_id, body)

    @router.get("/{object_id}/pdf")
    def pdf(object_id: str):
        record = quotes.get(object_id)
        if record["kind"] != "quote" or record["status"] != "formal":
            raise HTTPException(409, "正式确认后才可下载报价 PDF")
        if date.fromisoformat(record["valid_until"]) < _today():
            raise HTTPException(409, "此报价已过有效期，请重新试算")
        return Response(
            _public_quote_pdf(record), media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{object_id}.pdf"'},
        )

    return router


def _schema(properties: dict, required: tuple[str, ...] = ()) -> dict:
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


def _quote_tool_arguments() -> dict:
    return _schema({
        "origin": {"type": "string", "enum": list(_PORTS)},
        "destination": {"type": "string", "enum": list(_PORTS)},
        "mode": {"type": "string", "enum": list(_MODE_NAMES["zh"])},
        "goods": {"type": "string", "minLength": 1, "maxLength": 100},
        "ship_date": {"type": "string", "description": "客户期望出运日，YYYY-MM-DD"},
        "incoterm": {"type": "string", "enum": ["EXW", "FCA", "FOB", "CFR", "CIF", "CPT", "CIP", "DAP"]},
        "container_type": {"type": "string", "enum": ["20GP", "40GP", "40HQ"]},
        "container_count": {"type": "integer", "minimum": 1, "maximum": 50},
        "volume_cbm": {"type": "number", "exclusiveMinimum": 0},
        "weight_kg": {"type": "number", "exclusiveMinimum": 0},
        "declared_value_usd": {"type": "number", "exclusiveMinimum": 0},
        "discount_pct": {"type": "number", "minimum": 0, "maximum": 40},
    }, ("origin", "destination", "mode", "goods", "ship_date", "incoterm"))


def register_quote_tools(registry: ToolRegistry, quotes: QuoteService) -> None:
    def quote_handler(arguments: dict, context: ToolContext, *, create: bool):
        try:
            body = QuoteInput.model_validate(arguments)
            return quote_payload(body, context.customer_id) if create else calculate_quote(body)
        except (ValidationError, ValueError) as exc:
            raise ToolError("invalid_quote", str(exc)) from exc

    def schedule_handler(arguments: dict, _context: ToolContext):
        try:
            earliest = date.fromisoformat(arguments["earliest"]) if arguments.get("earliest") else _today()
        except ValueError as exc:
            raise ToolError("invalid_date", "出运日请使用 YYYY-MM-DD") from exc
        return {"sailings": list_sailings(arguments["origin"], arguments["destination"], arguments["mode"], earliest)}

    def claim_handler(arguments: dict, context: ToolContext):
        shipment = context.view.shipment(arguments["hbl"])
        if shipment is None or shipment["customer_id"] != context.customer_id:
            raise ToolError("not_found", "没有找到该工单可申请理赔的货物")
        amount = _money(Decimal(str(arguments["amount_usd"])))
        if amount <= 0:
            raise ToolError("invalid_amount", "理赔金额至少为 USD 0.01")
        required = amount > _CLAIM_LIMIT_USD
        return {
            "id": "CL-" + uuid.uuid4().hex[:10].upper(), "kind": "claim", "currency": "USD",
            "customer_id": context.customer_id, "hbl": shipment["hbl"], "amount_usd": float(amount),
            "reason": mask_pccc(arguments["reason"]), "approval_required": required,
            "internal": {"reasons": ["理赔金额超过员工权限"] if required else []},
        }

    def approval_handler(arguments: dict, context: ToolContext):
        object_id = arguments["object_id"]
        records = quotes.ledger.staged(context.namespace)
        target = next((record for record in records if record.tool in _CREATIONS
                       and record.status == STAGED and record.ticket_id == context.ticket_id
                       and record.payload["id"] == object_id
                       and record.payload["customer_id"] == context.customer_id), None)
        if target is None:
            raise ToolError("not_found", "没有找到本次运行新建且未确认的报价或理赔申请")
        if target.payload["approval_required"] or any(
            record.tool == "approval.create_case" and record.payload["object_id"] == object_id
            for record in records
        ):
            raise ToolError("already_requested", "该报价或理赔已经需要经理审批")
        return {"object_id": object_id, "reason": mask_pccc(arguments["reason"])}

    lane_properties = {
        "origin": {"type": "string", "enum": list(_PORTS)},
        "destination": {"type": "string", "enum": list(_PORTS)},
        "mode": {"type": "string", "enum": list(_MODE_NAMES["zh"])},
    }
    specs = (
        ToolSpec("rate.search", "查已有演示航线和参考运价，不报正式价格。", _schema(lane_properties), "read",
                 lambda args, _: {"rates": list_rates(args.get("origin"), args.get("destination"), args.get("mode"))}),
        ToolSpec("schedule.search", "查演示船期或航班，结果仅供订舱参考。", _schema({
            **lane_properties, "earliest": {"type": "string"},
        }, ("origin", "destination", "mode")), "read", schedule_handler),
        ToolSpec("quote.calculate", "内部试算，包含参考费用和审批提示。若客户要求报价，资料完整时创建报价草稿。",
                 _quote_tool_arguments(), "read", lambda args, ctx: quote_handler(args, ctx, create=False)),
        ToolSpec("policy.search", "查报价毛利、折扣和理赔的审批线。", _schema({}), "read",
                 lambda _args, _ctx: {"rules": policies()}),
        ToolSpec("quote.create", "创建待员工确认的参考报价；需要经理审批时仅称待审核，不能承诺正式价格。",
                 _quote_tool_arguments(), "write", lambda args, ctx: quote_handler(args, ctx, create=True)),
        ToolSpec("approval.create_case", "给本工单既有报价或理赔补充经理审批申请。只在确实需要额外审核时用。", _schema({
            "object_id": {"type": "string", "minLength": 1, "maxLength": 32},
            "reason": {"type": "string", "minLength": 1, "maxLength": 200},
        }, ("object_id", "reason")), "write", approval_handler),
        ToolSpec("claim.open", "为本工单客户的一票货物暂存理赔，请先核对单号、原因、美元金额；超额转经理，不承诺赔付。",
                 _schema({
                     "hbl": {"type": "string", "minLength": 1, "maxLength": 32},
                     "amount_usd": {"type": "number", "minimum": 0.01, "maximum": 100000},
                     "reason": {"type": "string", "minLength": 1, "maxLength": 300},
                 }, ("hbl", "amount_usd", "reason")), "write", claim_handler),
    )
    for spec in specs:
        registry.register(spec)


_MOCK_PORTS = {
    "CNTAO": ("青岛", "칭다오", "qingdao"),
    "CNSHA": ("上海", "상하이", "shanghai"),
    "KRINC": ("仁川", "인천", "incheon"),
    "KRPUS": ("釜山", "부산", "busan"),
}
_MOCK_MODES = {
    "air": ("空运", "항공", "air"),
    "ferry": ("轮渡", "카페리", "ferry"),
    "lcl": ("拼箱", "lcl"),
    "fcl": ("整柜", "fcl", "20gp", "40gp", "40hq"),
}


def quote_mock_request(text: str, lang: str) -> tuple[str, dict] | str | None:
    """Extract the supported quote and claim demo inputs without inferring missing shipping facts."""
    words = text.casefold()
    if any(term in words for term in ("理赔", "索赔", "손해배상", "보상 신청", "file a claim")):
        hbl = re.search(r"FW[A-Z]{2,3}[0-9]{7}", text, re.IGNORECASE)
        amount = re.search(r"(?:USD|US\$)\s*([0-9]+(?:\.[0-9]+)?)|([0-9]+(?:\.[0-9]+)?)\s*(?:USD|美元|달러)", text, re.IGNORECASE)
        reason = re.search(r"(?:原因|货损情况|사유|reason)\s*[:：]\s*([^\n;；]+)", text, re.IGNORECASE)
        if not (hbl and amount and reason):
            return (
                "请提供提单号、美元理赔金额和原因（例如「原因：外箱进水」）。" if lang == "zh"
                else "선하증권 번호, 미 달러 청구 금액 및 사유(예: 사유: 외부 상자 침수)를 알려주세요."
            )
        return "claim.open", {
            "hbl": hbl.group(0).upper(),
            "amount_usd": float(amount[1] or amount[2]),
            "reason": reason[1].strip()[:300],
        }
    if not any(term in words for term in ("询价", "报价", "参考价", "运价", "견적", "운임 문의", "quotation")):
        return None

    mode = next((name for name, terms in _MOCK_MODES.items() if any(term in words for term in terms)), None)
    port_matches = []
    for code, terms in _MOCK_PORTS.items():
        indexes = [words.find(term) for term in (code.casefold(), *terms)]
        found = [index for index in indexes if index >= 0]
        if found:
            port_matches.append((min(found), code))
    route = [code for _, code in sorted(port_matches)]
    if mode == "air":
        route = [code + "-AIR" if code == "CNTAO" else "KRICN-AIR" if code == "KRINC" else code for code in route]
    trade_term = re.search(
        r"(?<![a-z0-9])(?:EXW|FCA|FOB|CFR|CIF|CPT|CIP|DAP)(?![a-z0-9])", text, re.IGNORECASE,
    )
    ship_day = re.search(r"\b[0-9]{4}-[0-9]{2}-[0-9]{2}\b", text)
    relative_day = (
        _today() + timedelta(days=1) if any(word in words for word in ("明天", "내일")) else
        _today() if any(word in words for word in ("今天", "오늘")) else None
    )
    descriptions = (
        r"(?:货物|品名|화물|품목|goods)\s*[:：]\s*([^,，;；\n]+)",
        r"(?:货物是|货物为|화물은)\s*([^,，;；\n]+)",
    )
    goods = next((match[1] for pattern in descriptions if (match := re.search(pattern, text, re.IGNORECASE))), None)
    fcl = re.search(r"([0-9]+)\s*(?:[x×*]|柜|개)?\s*(20GP|40GP|40HQ)", text, re.IGNORECASE)
    volume = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(?:cbm|m³|立方米|입방미터)", text, re.IGNORECASE)
    weight = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(?:kg|千克|公斤|킬로그램)", text, re.IGNORECASE)
    declared = re.search(r"(?:货值|화물가격)\s*[:：]?\s*(?:USD\s*)?([0-9]+(?:\.[0-9]+)?)", text, re.IGNORECASE)
    discount = re.search(r"(?:折扣|할인)\s*[:：]?\s*([0-9]+(?:\.[0-9]+)?)\s*%", text)
    terms = trade_term[0].upper() if trade_term else None
    enough = (
        mode and len(route) == 2 and terms and (ship_day or relative_day) and goods and
        (fcl if mode == "fcl" else volume and (mode != "air" or weight)) and
        (terms not in ("CIF", "CIP") or declared)
    )
    if not enough:
        return (
            "请补齐出发地、目的地、运输方式、期望出运日和贸易条款。请用「货物：品名」注明货物。整柜提供柜型及柜数，"
            "拼箱或轮渡提供体积，空运提供重量及体积；CIF 或 CIP 还需要货值。" if lang == "zh" else
            "출발지, 도착지, 운송 방식, 희망 출발일과 인도조건을 알려주세요. 화물명을 '화물: 품목'으로 적고, "
            "FCL은 컨테이너 종류와 수량, LCL·카페리는 부피, 항공은 중량과 부피를 알려주세요. CIF·CIP는 물품 가격도 필요합니다."
        )
    arguments = {
        "origin": route[0], "destination": route[1], "mode": mode, "goods": goods.strip()[:100],
        "ship_date": ship_day[0] if ship_day else relative_day.isoformat(), "incoterm": terms,
    }
    if fcl and mode == "fcl":
        arguments.update(container_type=fcl[2].upper(), container_count=int(fcl[1]))
    if volume and mode != "fcl":
        arguments["volume_cbm"] = float(volume[1])
    if weight and mode == "air":
        arguments["weight_kg"] = float(weight[1])
    if declared:
        arguments["declared_value_usd"] = float(declared[1])
    if discount:
        arguments["discount_pct"] = float(discount[1])
    return "quote.create", arguments


def quote_mock_reply(observation: dict, lang: str) -> str:
    amount = f"USD {observation['amount_usd']:.2f}"
    needs_manager = observation["approval_required"]
    if observation["kind"] == "claim":
        if lang == "ko":
            return (f"{amount} 보상 신청을 접수했으며 관리자 검토가 필요합니다." if needs_manager else
                    f"{amount} 보상 신청을 접수했습니다. 검토 결과를 다시 안내하겠습니다.")
        return (f"已登记 {amount} 的理赔申请，待经理审核。" if needs_manager else
                f"已登记 {amount} 的理赔申请，后续会通知您处理结果。")
    if lang == "ko":
        suffix = " 관리자 검토 후 정식 견적을 안내하겠습니다." if needs_manager else ""
        return f"운임 참고 견적은 {amount}이며 관세와 부가세는 포함되지 않습니다.{suffix}"
    suffix = "经理审核通过后可出正式报价。" if needs_manager else ""
    return f"运费参考报价为 {amount}，不含关税和增值税。{suffix}"
