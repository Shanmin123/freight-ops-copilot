"""DemoMockModel: a rule-based ChatModel, so that the demo runs end to end without an API key.

It stands in for the model inside the real agent loop and decides from the messages alone: the ticket
text in the user message (latest customer message, earlier conversation, customer id) and the tool
calls this run has had answered. It never looks at the business world itself.

The first rule that matches the latest customer message picks the tools:
1. a PCCC (P and 12 digits): customs.submit_pccc for the H B/L in the message, or else for the one
   mentioned last in the earlier conversation;
2. an H B/L and a customs word: customs.get_progress;
3. an H B/L: shipment.get;
4. a repack word: shipment.list, warehouse.get_job for the first shipment with a warehouse job, then
   warehouse.request_action to repack the cartons its inspection found damaged;
5. a damage or inspection word: shipment.list, then warehouse.get_job;
6. anything else: ticket.handoff.
A rule that cannot go on (no H B/L for the PCCC, no customer id, no warehouse job) hands off instead.
After a failed tool call, a handoff, or the rule's last tool, the model replies from a template: in
Korean if the latest message has Hangul, in Chinese if it has Han characters, and for a message with
neither (a bare PCCC, say) in Korean if any of the customer's earlier lines has Hangul, else Chinese.
No reply holds a date, and a PCCC appears only masked, as the submit tool returned it.
"""

import json
import re
from dataclasses import dataclass

from forwarder.kb import KB_TOOL
from forwarder.language import detect_reply_language
from forwarder.quotes import quote_mock_reply, quote_mock_request

from .base import ModelReply, ToolCall, from_wire_name, to_wire_name, wire_assistant_message

# The rules' patterns. ASCII-only, as a bare \d also matches full-width and other Unicode digits.
_PCCC = re.compile(r"P\d{12}", re.ASCII)
_HBL = re.compile(r"FW[A-Z]{2,3}\d{7}", re.ASCII)
_HANGUL = re.compile(r"[\u1100-\u11ff\u3130-\u318f\uac00-\ud7a3]")  # jamo, compatibility jamo, syllables
_HAN = re.compile(r"[\u4e00-\u9fff]")  # CJK unified ideographs, everyday Chinese

_CUSTOMS_WORDS = ("清关", "海关", "卡", "통관", "반입", "세관")
_REPACK_WORDS = ("重新包装", "재포장", "repack")
_DAMAGE_WORDS = ("破损", "验货", "파손", "검수")
_KNOWLEDGE_TOPICS = (
    "知识库", "지식베이스", "个人通关", "通关号码", "통관고유부호", "통관번호", "pccc",
    "清单通关", "목록통관", "海关查验", "抽中查验", "세관 검사", "통관 검사",
    "关税", "税费", "관세", "관부가세", "통관 서류", "수입신고 서류", "商业发票", "装箱单",
    "进口申报材料", "清关规则", "通关规则", "通关流程", "통관 절차", "fba", "incoterms", "贸易条款",
)
_REROUTE = re.compile(r"(?:改地址[：: ]*|地址改为\s*|주소를?\s+)([^\n。!?]{5,200})")
_INVESTIGATION = ("丢件", "遗失调查", "분실", "받지 못했")
_REVIEW_ID = re.compile(r"DOC-[A-F0-9]{12}")

_HANDOFF_REASON = "需要人工处理"

# The ticket text as agent.context writes it: the 【工单】 line; 【此前对话】 with a "客户：…" or "客服：…"
# line per earlier message (more if it has line breaks), or "无" when there is none; then
# 【客户最新消息】 and the message itself.
_LATEST_MARK = "\n【客户最新消息】\n"
_TRANSCRIPT_MARK = "【此前对话】\n"
_CUSTOMER_SAID = "客户："
_AGENT_SAID = "客服："
_CUSTOMER_ID = re.compile(r"客户编号 ([^\s（]+)")

# Reply templates by language. "sep" joins the sentences of a reply, "list_sep" a list of carton ids.
_TEXTS = {
    "zh": {
        "sep": "",
        "list_sep": "、",
        "error": "抱歉，暂时没有查到相关信息，已请同事核实。",
        "handoff": "您好，您的问题已转给专人处理，同事会尽快与您联系。",
        "customs": "您好，已为您查询 H B/L {hbl} 的清关进度。",
        "no_hold": "目前没有暂扣。",
        "no_hold_up_to": "目前没有暂扣，最新完成的环节是「{step}」。",
        "resubmitted": "更正后的个人通关号码已提交给报关行，正在等待重新申报的结果，目前您无需再做什么。",
        "pccc_mismatch_reason": "申报的个人通关号码与收货人信息不一致，通关被暂扣。",
        "pccc_mismatch_next": "请提供收货人本人名下正确的个人通关号码（P 加 12 位数字），我们会转交报关行更正后重新申报。",
        "inspection_reason": "包裹被海关抽中查验，正在进行实物检查。",
        "inspection_next": "查验期间您无需做任何操作，有结果后我们会及时告知您。",
        "other_reason": "货物目前处于清关暂扣状态。",
        "other_next": "我们正在跟进处理，有进展会及时告知您。",
        "submitted": "您好，已为您把个人通关号码{number}提交给报关行，H B/L {hbl} 将在更正后重新申报，有结果后我们会及时告知您。",
        "number": "（{}）",
        "shipment": "您好，H B/L {hbl} 的最新动态是「{event}」。",
        "shipment_no_event": "您好，H B/L {hbl} 目前还没有运输动态。",
        "damaged": "您好，仓库作业单 {job_id} 验货时发现 {count} 箱破损（箱号 {cartons}）。请问您希望怎么处理？"
        "可以选择重新包装、重新贴标、暂缓出库或退回发货人。",
        "not_damaged": "您好，仓库作业单 {job_id} 目前没有发现破损的箱子。",
        "repacked": "您好，已为您安排仓库对作业单 {job_id} 的{what}重新包装。",
        "repack_cartons": "破损箱 {cartons} ",
        "repack_goods": "货物",
    },
    "ko": {
        "sep": " ",
        "list_sep": ", ",
        "error": "죄송합니다. 지금은 관련 정보를 확인하지 못해 담당자에게 확인을 요청했습니다.",
        "handoff": "안녕하세요. 문의하신 내용을 담당자에게 전달했습니다. 담당자가 곧 연락드리겠습니다.",
        "customs": "안녕하세요. H B/L {hbl}의 통관 진행 상황을 확인했습니다.",
        "no_hold": "현재 통관 보류는 없습니다.",
        "no_hold_up_to": "현재 통관 보류는 없으며, '{step}' 단계까지 완료되었습니다.",
        "resubmitted": "정정된 개인통관고유부호는 관세사에 제출되었고, 현재 재신고 결과를 기다리고 있습니다. "
        "지금은 따로 하실 일이 없습니다.",
        "pccc_mismatch_reason": "신고된 개인통관고유부호가 수하인 정보와 일치하지 않아 통관이 보류되었습니다.",
        "pccc_mismatch_next": "수하인 본인 명의의 올바른 개인통관고유부호(P와 숫자 12자리)를 보내 주시면 "
        "관세사를 통해 정정한 뒤 다시 신고하겠습니다.",
        "inspection_reason": "세관 검사 대상으로 선별되어 현품 검사를 진행하고 있습니다.",
        "inspection_next": "검사가 진행되는 동안 따로 하실 일은 없으며, 결과가 나오면 바로 안내드리겠습니다.",
        "other_reason": "현재 통관이 보류된 상태입니다.",
        "other_next": "담당자가 확인하고 있으며, 진행 상황이 생기면 바로 안내드리겠습니다.",
        "submitted": "안녕하세요. 보내 주신 개인통관고유부호{number}를 관세사에 제출했습니다. "
        "H B/L {hbl} 건은 정정 후 다시 신고되며, 결과가 나오면 안내드리겠습니다.",
        "number": "({})",
        "shipment": "안녕하세요. H B/L {hbl}의 최근 운송 현황은 '{event}'입니다.",
        "shipment_no_event": "안녕하세요. H B/L {hbl}의 운송 현황이 아직 등록되지 않았습니다.",
        "damaged": "안녕하세요. 창고 작업 {job_id} 검수에서 파손된 박스 {count}개({cartons})가 확인되었습니다. "
        "어떻게 처리할지 알려 주세요. 재포장, 라벨 재부착, 출고 보류, 발송인 반송 중에서 선택하실 수 있습니다.",
        "not_damaged": "안녕하세요. 창고 작업 {job_id}에서는 현재까지 파손된 박스가 확인되지 않았습니다.",
        "repacked": "안녕하세요. 창고 작업 {job_id}의 {what} 재포장하도록 창고에 요청했습니다.",
        "repack_cartons": "파손 박스({cartons})를",
        "repack_goods": "화물을",
    },
}


@dataclass(frozen=True)
class _Ticket:
    """What the model reads from the user message that opens the run."""

    latest: str  # the customer message to answer
    transcript: str  # the earlier conversation as written there, "无" when there was none
    customer_id: str | None  # from the 【工单】 line; None when it names none


@dataclass(frozen=True)
class _Answered:
    """A tool call of this run, with the observation it was answered with."""

    name: str  # dotted
    observation: dict  # {"ok": True, "data": ...} or {"ok": False, "error": ..., "message": ...}


@dataclass(frozen=True)
class _Call:
    """The tool call to make next."""

    name: str
    arguments: dict


class DemoMockModel:
    """ChatModel that answers by the rules above. It keeps no state: one instance can serve any run."""

    name = "mock-demo"

    def complete(self, messages: list[dict], tools: list[dict] | None) -> ModelReply:
        """The next tool call, or the final reply. tools is not consulted: the rules fix the tools."""
        answered, calls_made = _read_run(messages)
        available = {tool["function"]["name"] for tool in tools or ()}
        step = _next_step(_read_ticket(messages), answered, available)
        if isinstance(step, str):
            return _reply(step, [])
        # Numbered on from the calls already in the history, so every id is unique within the run.
        return _reply("", [ToolCall(f"call_{calls_made + 1}", step.name, step.arguments)])


def _reply(content: str, tool_calls: list[ToolCall]) -> ModelReply:
    wire_calls = [
        (call.id, to_wire_name(call.name), json.dumps(call.arguments, ensure_ascii=False))
        for call in tool_calls
    ]
    return ModelReply(
        content=content,
        tool_calls=tool_calls,
        reasoning_content=None,
        usage={},
        model=DemoMockModel.name,
        wire_message=wire_assistant_message(content, wire_calls, None),
    )


def _read_ticket(messages: list[dict]) -> _Ticket:
    """The ticket from the run's user message. Text not in the ticket format is all latest message."""
    text = next((m.get("content") or "" for m in messages if m.get("role") == "user"), "")
    head, mark, latest = text.partition(_LATEST_MARK)
    if not mark:
        return _Ticket(text, "", None)
    customer = _CUSTOMER_ID.search(head.split("\n", 1)[0])
    transcript = head.partition(_TRANSCRIPT_MARK)[2]
    return _Ticket(latest, transcript, customer[1] if customer else None)


def _read_run(messages: list[dict]) -> tuple[list[_Answered], int]:
    """The tool calls answered so far in the run, in order, and how many tool calls the run made."""
    names: dict[str, str] = {}  # dotted tool name by call id
    answered = []
    for message in messages:
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                # The wire message holds the "__" form of the name.
                names[call["id"]] = from_wire_name(call["function"]["name"])
        elif message.get("role") == "tool" and message.get("tool_call_id") in names:
            observation = _observation(message.get("content"))
            answered.append(_Answered(names[message["tool_call_id"]], observation))
    return answered, len(names)


def _observation(content: str | None) -> dict:
    """A tool message's content decoded; {} if it is not a JSON object, which reads as a failed call."""
    try:
        value = json.loads(content)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _language(ticket: _Ticket) -> str:
    """"ko" or "zh", as the latest message is written: by its Hangul, else by its Han characters. A
    message with neither, such as a bare PCCC, takes "ko" if any earlier customer line has Hangul."""
    if _HANGUL.search(ticket.latest):
        return "ko"
    if _HAN.search(ticket.latest):
        return "zh"
    said_by_customer = False  # whose message the line belongs to; a message can run over several lines
    for line in ticket.transcript.split("\n"):
        if line.startswith((_CUSTOMER_SAID, _AGENT_SAID)):
            said_by_customer = line.startswith(_CUSTOMER_SAID)
        if said_by_customer and _HANGUL.search(line):
            return "ko"
    return "zh"


def _next_step(ticket: _Ticket, answered: list[_Answered], available: set[str]) -> _Call | str:
    """The next tool call of the first rule that matches, or the reply text once there is none."""
    lang = _language(ticket)
    if answered and answered[-1].observation.get("ok") is not True:
        return _TEXTS[lang]["error"]
    if answered and answered[-1].name == "ticket.handoff":
        return _TEXTS[lang]["handoff"]
    message = ticket.latest
    words = message.lower()
    pccc = _PCCC.search(message)
    hbl = _HBL.search(message)
    if pccc:
        return _submit_pccc(lang, ticket, pccc[0], answered)
    if "quote.create" in available:
        request = quote_mock_request(message, lang)
        if isinstance(request, str):
            return request
        if request:
            name, arguments = request
            result = _result(answered, name)
            return _Call(name, arguments) if result is None else quote_mock_reply(result, lang)
    if hbl and "delivery.reroute" in available and (address := _REROUTE.search(message)):
        if _result(answered, "delivery.reroute") is None:
            return _Call("delivery.reroute", {"hbl": hbl[0], "address": address[1].strip()})
        return "배송지 변경을 접수했습니다." if lang == "ko" else "已登记配送地址更改请求。"
    if hbl and "carrier.open_investigation" in available and _mentions(words, _INVESTIGATION):
        if _result(answered, "carrier.open_investigation") is None:
            return _Call("carrier.open_investigation", {"hbl": hbl[0], "reason": message[:200]})
        return "운송사 조사 요청을 접수했습니다." if lang == "ko" else "已登记承运商调查请求。"
    if "document.get_findings" in available and (
        _REVIEW_ID.search(message) or _mentions(words, ("核对结果", "单据差异", "서류 대조 결과"))
    ):
        result = _result(answered, "document.get_findings")
        if result is None:
            review = _REVIEW_ID.search(message)
            return _Call("document.get_findings", {"review_id": review[0]} if review else {})
        differences = [item["label"] for item in result.get("checks", []) if item["status"] == "mismatch"]
        if lang == "ko":
            return f"서류 대조에서 {len(differences)}개 항목의 불일치가 확인되었습니다."
        return "单据核对发现差异：" + "、".join(differences) + "。" if differences else "单据核对未发现字段差异。"
    if hbl and _mentions(words, _CUSTOMS_WORDS):
        return _customs_progress(lang, hbl[0], answered)
    if hbl:
        return _shipment(lang, hbl[0], answered)
    if KB_TOOL in available and (
        _mentions(words, _KNOWLEDGE_TOPICS)
        or (_mentions(words, ("演示", "모의", "시연")) and _mentions(words, _REPACK_WORDS + _DAMAGE_WORDS))
    ):
        knowledge = _result(answered, KB_TOOL)
        if knowledge is None:
            return _Call(KB_TOOL, {"query": message[:500]})
        hits = knowledge["hits"]
        language = detect_reply_language([message, ticket.transcript])
        return f"{hits[0]['text'][language]} [1]" if hits else ""
    if _mentions(words, _REPACK_WORDS):
        return _warehouse(lang, ticket.customer_id, answered, repack=True)
    if _mentions(words, _DAMAGE_WORDS):
        return _warehouse(lang, ticket.customer_id, answered, repack=False)
    return _handoff()


def _mentions(text: str, words: tuple[str, ...]) -> bool:
    return any(word in text for word in words)


def _handoff() -> _Call:
    return _Call("ticket.handoff", {"reason": _HANDOFF_REASON})


def _result(answered: list[_Answered], name: str) -> dict | None:
    """The data of the tool's latest call in this run; None if it has not been called.

    Only the last call can have failed, and _next_step answers that with an apology before any rule
    asks for a result, so a call found here succeeded.
    """
    for call in reversed(answered):
        if call.name == name:
            data = call.observation.get("data")
            return data if isinstance(data, dict) else {}
    return None


def _last_label(items: list[dict], lang: str) -> str | None:
    """The label, in lang, of the last of the customs steps or tracking events given."""
    return items[-1].get(f"label_{lang}") if items else None


# Rule 1


def _submit_pccc(lang: str, ticket: _Ticket, pccc: str, answered: list[_Answered]) -> _Call | str:
    in_message = _HBL.search(ticket.latest)
    earlier = _HBL.findall(ticket.transcript)
    hbl = in_message[0] if in_message else earlier[-1] if earlier else None
    if hbl is None:
        return _handoff()
    submitted = _result(answered, "customs.submit_pccc")
    if submitted is None:
        return _Call("customs.submit_pccc", {"hbl": hbl, "pccc": pccc})
    texts = _TEXTS[lang]
    masked = submitted.get("pccc_masked")  # the only form of the number a reply may show
    number = texts["number"].format(masked) if masked else ""
    return texts["submitted"].format(number=number, hbl=hbl)


# Rule 2


def _customs_progress(lang: str, hbl: str, answered: list[_Answered]) -> _Call | str:
    record = _result(answered, "customs.get_progress")
    if record is None:
        return _Call("customs.get_progress", {"hbl": hbl})
    texts = _TEXTS[lang]
    parts = [texts["customs"].format(hbl=hbl)]
    hold = record.get("hold")
    if not hold:
        done = [step for step in record.get("steps") or [] if step.get("status") == "done"]
        step = _last_label(done, lang)
        parts.append(texts["no_hold_up_to"].format(step=step) if step else texts["no_hold"])
    elif hold.get("status") == "resubmitted":
        parts.append(texts["resubmitted"])
    else:
        code = hold.get("code") if hold.get("code") in ("pccc_mismatch", "inspection") else "other"
        parts += [hold.get(f"reason_{lang}") or texts[f"{code}_reason"], texts[f"{code}_next"]]
    return texts["sep"].join(parts)


# Rule 3


def _shipment(lang: str, hbl: str, answered: list[_Answered]) -> _Call | str:
    shipment = _result(answered, "shipment.get")
    if shipment is None:
        return _Call("shipment.get", {"hbl": hbl})
    event = _last_label(shipment.get("events") or [], lang)
    if event is None:
        return _TEXTS[lang]["shipment_no_event"].format(hbl=hbl)
    return _TEXTS[lang]["shipment"].format(hbl=hbl, event=event)


# Rules 4 and 5


def _warehouse(
    lang: str, customer_id: str | None, answered: list[_Answered], *, repack: bool
) -> _Call | str:
    listed = _result(answered, "shipment.list")
    if listed is None:
        return _Call("shipment.list", {"customer_id": customer_id}) if customer_id else _handoff()
    job = _result(answered, "warehouse.get_job")
    if job is None:
        shipments = listed.get("shipments") or []
        job_id = next((s["warehouse_job"] for s in shipments if s.get("warehouse_job")), None)
        return _Call("warehouse.get_job", {"job_id": job_id}) if job_id else _handoff()
    inspection = next((step for step in job.get("steps") or [] if step.get("key") == "inspection"), {})
    cartons = list(inspection.get("cartons") or [])  # the ones the inspection found damaged
    texts = _TEXTS[lang]
    if not repack:
        if not cartons:
            return texts["not_damaged"].format(job_id=job.get("job_id"))
        listing = texts["list_sep"].join(cartons)
        return texts["damaged"].format(job_id=job.get("job_id"), count=len(cartons), cartons=listing)
    requested = _result(answered, "warehouse.request_action")
    if requested is None:
        arguments = {"job_id": job.get("job_id"), "action": "repack", "cartons": cartons}
        return _Call("warehouse.request_action", arguments)
    repacked = requested.get("cartons") or []
    what = texts["repack_goods"]
    if repacked:
        what = texts["repack_cartons"].format(cartons=texts["list_sep"].join(repacked))
    return texts["repacked"].format(job_id=requested.get("job_id"), what=what)
