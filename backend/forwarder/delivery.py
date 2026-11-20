"""Staged requests for last-mile changes and carrier investigations."""

from forwarder.tools.registry import ToolContext, ToolError, ToolRegistry, ToolSpec

DELIVERY_TOOLS = ["delivery.reroute", "carrier.open_investigation"]


def _shipment(arguments: dict, context: ToolContext) -> dict:
    shipment = context.view.shipment(arguments["hbl"].strip().upper())
    if not shipment or shipment["customer_id"] != context.customer_id:
        raise ToolError("not_found", "没有找到这个单号的货物")
    return shipment


def _reroute(arguments: dict, context: ToolContext) -> dict:
    shipment = _shipment(arguments, context)
    address = arguments["address"].strip()
    if len(address) < 5:
        raise ToolError("invalid_address", "请提供完整的新配送地址")
    if shipment["direction"] != "import" or shipment["status"] in ("delivered", "returned", "cancelled"):
        raise ToolError("reroute_unavailable", "当前运输状态无法申请韩国国内段改址")
    return {"hbl": shipment["hbl"], "address": address, "status": "requested"}


def _investigate(arguments: dict, context: ToolContext) -> dict:
    shipment = _shipment(arguments, context)
    reason = arguments["reason"].strip()
    if not reason:
        raise ToolError("invalid_reason", "请提供调查原因")
    if shipment.get("carrier_investigation"):
        raise ToolError("investigation_exists", "该货物已有调查记录")
    return {"hbl": shipment["hbl"], "reason": reason, "status": "opened"}


def register_delivery_tools(registry: ToolRegistry) -> None:
    specs = (
        ("delivery.reroute", "address", 200, "客户明确要求韩国国内段改址时登记新地址，员工确认后才生效。", _reroute),
        ("carrier.open_investigation", "reason", 200, "客户报告可能丢件或要求调查时建立承运商调查，员工确认后才生效。", _investigate),
    )
    for name, field, limit, description, handler in specs:
        registry.register(ToolSpec(name, description, {
            "type": "object",
            "properties": {"hbl": {"type": "string", "minLength": 1, "maxLength": 30},
                           field: {"type": "string", "minLength": 1, "maxLength": limit}},
            "required": ["hbl", field],
            "additionalProperties": False,
        }, "write", handler))
