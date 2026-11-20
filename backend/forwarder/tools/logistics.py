"""The nine phase-1 logistics tools. Each one sees only the ticket customer's own records."""

import re

from forwarder.privacy import mask_pccc
from forwarder.tools.registry import ToolContext, ToolError, ToolRegistry, ToolSpec

P1_TOOLS = [
    "crm.get_customer",
    "shipment.list",
    "shipment.get",
    "customs.get_progress",
    "warehouse.get_job",
    "attachment.inspect",
    "customs.submit_pccc",
    "warehouse.request_action",
    "ticket.handoff",
]

_WAREHOUSE_ACTIONS = ("repack", "relabel", "hold", "return_to_sender")

# A personal customs clearance code (PCCC) is P and 12 digits, "^P\d{12}$". It is matched with
# fullmatch, as "$" also matches before a trailing newline, and ASCII-only, as a bare \d also takes
# full-width and other Unicode digits.
_PCCC_FORMAT = re.compile(r"P\d{12}", re.ASCII)

# Asking for another customer's record fails exactly like asking for one that does not exist, with
# the same code and message, so a customer never learns whether someone else's number exists.
_NOT_OWN_CUSTOMER = "只能查询本工单客户自己的信息"
_NO_SHIPMENT = "没有找到这个单号的货物"
_NO_JOB = "没有找到这个仓库作业单"
_NO_ATTACHMENT = "没有找到这个附件"


def register_logistics_tools(registry: ToolRegistry) -> None:
    """Register the P1_TOOLS on registry."""
    for spec in _specs():
        registry.register(spec)


def _specs() -> list[ToolSpec]:
    """Specs of the P1_TOOLS, in that order; built anew on each call, so registries share no schema."""
    return [
        ToolSpec(
            name="crm.get_customer",
            description=(
                "查询本工单客户的档案：客户类型（卖家或个人收货人）、常用语言、等级、结算方式和信用额度。"
                "需要了解客户身份、账户或结算情况时使用。只能查本工单客户自己的编号。"
            ),
            parameters=_arguments({"customer_id": _string("本工单的客户编号")}),
            kind="read",
            handler=_get_customer,
        ),
        ToolSpec(
            name="shipment.list",
            description=(
                "列出本工单客户名下的货物，含 H B/L 单号、品名、当前状态、运输轨迹和关联的仓库作业单号"
                "（warehouse_job）。客户没有给出单号、需要先确定是哪票货时使用。只能查本工单客户自己的编号。"
            ),
            parameters=_arguments({"customer_id": _string("本工单的客户编号")}),
            kind="read",
            handler=_list_shipments,
        ),
        ToolSpec(
            name="shipment.get",
            description=(
                "按 H B/L 单号查询一票货物：品名、件数、重量、体积、申报价值、当前状态、预计离港和到港时间、"
                "运输轨迹，以及关联的仓库作业单号（warehouse_job）。客户给出单号、问货物情况或货到哪里了时使用。"
            ),
            parameters=_arguments({"hbl": _string("货物的 H B/L 单号")}),
            kind="read",
            handler=_get_shipment,
        ),
        ToolSpec(
            name="customs.get_progress",
            description=(
                "按 H B/L 单号查询货物在韩国的进口清关进度，获取已完成的清关环节、暂扣原因和客户需要做什么。"
                "暂扣记录未标出发生环节。客户问清关、通关或海关查验时使用。"
            ),
            parameters=_arguments({"hbl": _string("货物的 H B/L 单号")}),
            kind="read",
            handler=_get_customs_progress,
        ),
        ToolSpec(
            name="warehouse.get_job",
            description=(
                "按作业单号查询仓库作业：入库、验货、贴标、包装、存储、出库 6 步的进度，验货结果、破损箱号和"
                "照片附件编号，以及已经提出的处理要求。客户问仓库作业、验货结果或货物破损时使用；作业单号见"
                "货物信息里的 warehouse_job。"
            ),
            parameters=_arguments({"job_id": _string("仓库作业单号")}),
            kind="read",
            handler=_get_warehouse_job,
        ),
        ToolSpec(
            name="attachment.inspect",
            description=(
                "按附件编号查看照片或文件的内容说明，例如验货照片里拍到的破损情况。需要向客户说明照片内容时"
                "使用；附件编号见仓库作业验货结果里的 attachments。"
            ),
            parameters=_arguments({"attachment_id": _string("附件编号")}),
            kind="read",
            handler=_inspect_attachment,
        ),
        ToolSpec(
            name="customs.submit_pccc",
            description=(
                "把客户提供的正确个人通关号码转交报关行，更正后重新申报。只在清关因个人通关号码不一致被暂扣、"
                "客户已经给出正确号码时使用。个人通关号码格式为 P 加 12 位数字，提交前先核对格式。"
                "号码属于个人信息，回复客户时不要复述完整号码。"
            ),
            parameters=_arguments(
                {
                    "hbl": _string("被暂扣货物的 H B/L 单号"),
                    # A plain string on purpose: a malformed number must come back as invalid_pccc,
                    # and the registry checks the schema before the handler runs.
                    "pccc": _string("客户提供的个人通关号码，P 加 12 位数字"),
                }
            ),
            kind="write",
            handler=_submit_pccc,
        ),
        ToolSpec(
            name="warehouse.request_action",
            description=(
                "按客户的明确指示，请仓库处理作业单里的货物：repack 重新包装，relabel 重新贴标，"
                "hold 暂扣、先不出库，return_to_sender 退回发货人。可以用 cartons 指定箱号，用 note 写明"
                "客户的具体要求。只在客户明确提出处理要求时使用；货物出库后不能再提。"
            ),
            parameters=_arguments(
                {
                    "job_id": _string("仓库作业单号"),
                    "action": {
                        "type": "string",
                        "enum": list(_WAREHOUSE_ACTIONS),
                        "description": "处理方式：repack 重新包装，relabel 重新贴标，hold 暂扣，"
                        "return_to_sender 退回发货人",
                    },
                    "cartons": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "要处理的箱号，例如验货时发现破损的箱号",
                    },
                    "note": _string("给仓库的补充说明，例如客户的具体要求"),
                },
                optional=("cartons", "note"),
            ),
            kind="write",
            handler=_request_warehouse_action,
        ),
        ToolSpec(
            name="ticket.handoff",
            description=(
                "把工单转给人工客服，并写明原因。客户要求人工服务、问题超出这些工具能处理的范围，"
                "或者需要人工判断和决定时使用。"
            ),
            parameters=_arguments({"reason": _string("转人工的原因：客户的诉求，以及已经查到的情况")}),
            kind="write",
            handler=_hand_off,
        ),
    ]


def _get_customer(arguments: dict, ctx: ToolContext) -> dict:
    return _own_customer(arguments["customer_id"], ctx)


def _list_shipments(arguments: dict, ctx: ToolContext) -> dict:
    customer = _own_customer(arguments["customer_id"], ctx)
    return {"shipments": ctx.view.shipments_for(customer["id"])}


def _get_shipment(arguments: dict, ctx: ToolContext) -> dict:
    return _own_shipment(arguments["hbl"], ctx)


def _get_customs_progress(arguments: dict, ctx: ToolContext) -> dict:
    hbl = arguments["hbl"]
    _own_shipment(hbl, ctx)  # a customs record names no customer: it is the shipment owner's
    record = ctx.view.customs(hbl)
    if record is None:
        raise ToolError("not_found", "这票货物没有进口清关记录")
    return record


def _get_warehouse_job(arguments: dict, ctx: ToolContext) -> dict:
    return _owned(ctx.view.warehouse_job(arguments["job_id"]), ctx, _NO_JOB)


def _inspect_attachment(arguments: dict, ctx: ToolContext) -> dict:
    return _owned(ctx.view.attachment(arguments["attachment_id"]), ctx, _NO_ATTACHMENT)


def _submit_pccc(arguments: dict, ctx: ToolContext) -> dict:
    pccc = arguments["pccc"]
    if not _PCCC_FORMAT.fullmatch(pccc):
        # Not even a mistyped number is repeated back: it is personal data all the same.
        raise ToolError("invalid_pccc", "个人通关号码格式不对，应为 P 加 12 位数字")
    hbl = arguments["hbl"]
    _own_shipment(hbl, ctx)
    record = ctx.view.customs(hbl)
    hold = record.get("hold") if record else None
    if not hold or hold.get("code") != "pccc_mismatch":
        raise ToolError("no_pccc_hold", "这票货物没有因个人通关号码不一致被暂扣，不需要提交号码")
    # Only the masked number is staged, so the full one is neither stored nor shown to the model.
    return {"hbl": hbl, "pccc_masked": mask_pccc(pccc)}


def _request_warehouse_action(arguments: dict, ctx: ToolContext) -> dict:
    job_id = arguments["job_id"]
    job = _owned(ctx.view.warehouse_job(job_id), ctx, _NO_JOB)
    if any(step["key"] == "outbound" and step["status"] == "done" for step in job["steps"]):
        raise ToolError("action_not_allowed", "货物已经出库，不能再请仓库处理")
    return {
        "job_id": job_id,
        "action": arguments["action"],
        "cartons": arguments.get("cartons", []),
        "note": arguments.get("note"),
    }


def _hand_off(arguments: dict, ctx: ToolContext) -> dict:
    return {"reason": arguments["reason"]}


def _own_customer(customer_id: str, ctx: ToolContext) -> dict:
    """The ticket's customer. Any other id is not_found, whether such a customer exists or not."""
    customer = ctx.view.customer(customer_id) if customer_id == ctx.customer_id else None
    if customer is None:
        raise ToolError("not_found", _NOT_OWN_CUSTOMER)
    return customer


def _own_shipment(hbl: str, ctx: ToolContext) -> dict:
    return _owned(ctx.view.shipment(hbl), ctx, _NO_SHIPMENT)


def _owned(record: dict | None, ctx: ToolContext, message: str) -> dict:
    """record if the ticket's customer owns it; otherwise not_found, exactly as if there were none."""
    if record is None or record["customer_id"] != ctx.customer_id:
        raise ToolError("not_found", message)
    return record


def _arguments(properties: dict, optional: tuple[str, ...] = ()) -> dict:
    """Schema of an arguments object: these properties, each required unless optional, and no others."""
    return {
        "type": "object",
        "properties": properties,
        "required": [name for name in properties if name not in optional],
        "additionalProperties": False,
    }


def _string(description: str) -> dict:
    return {"type": "string", "description": description}
