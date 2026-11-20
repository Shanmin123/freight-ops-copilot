import re

import pytest

from forwarder.agent.context import TicketContext, build_messages, load_system_prompt
from forwarder.delivery import DELIVERY_TOOLS
from forwarder.documents import DOCUMENT_TOOL
from forwarder.kb import KB_TOOL
from forwarder.quotes import QUOTE_TOOLS
from forwarder.tools.logistics import P1_TOOLS
from forwarder.tools.registry import STAGED_NOTE


def test_build_messages_puts_transcript_as_text():
    ctx = TicketContext("T-1", "kakaotalk", "C-KR-0412", "김지우", [("customer", "안녕하세요"), ("agent", "네")], "반입신고에서 멈췄어요")
    msgs = build_messages(ctx, "货代")
    assert [m["role"] for m in msgs] == ["system", "user"]
    assert "货代" in msgs[0]["content"]
    assert "客户：안녕하세요" in msgs[1]["content"] and msgs[1]["content"].endswith("반입신고에서 멈췄어요")


def test_user_message_follows_the_ticket_format():
    ctx = TicketContext("T-1", "kakaotalk", "C-KR-0412", "김지우", [("customer", "안녕하세요"), ("agent", "네")], "반입신고에서 멈췄어요")
    assert build_messages(ctx, "G")[1]["content"] == (
        "【工单】T-1 · 渠道 kakaotalk · 客户编号 C-KR-0412（김지우）\n"
        "【此前对话】\n"
        "客户：안녕하세요\n"
        "客服：네\n"
        "【客户最新消息】\n"
        "반입신고에서 멈췄어요"
    )


def test_empty_transcript_is_written_as_none():
    ctx = TicketContext("T-2", "wechat", "C-CN-3150", "林晓", [], "我的包裹到哪了")
    assert build_messages(ctx, "G")[1]["content"] == (
        "【工单】T-2 · 渠道 wechat · 客户编号 C-CN-3150（林晓）\n"
        "【此前对话】\n"
        "无\n"
        "【客户最新消息】\n"
        "我的包裹到哪了"
    )


def test_unknown_transcript_role_is_rejected():
    ctx = TicketContext("T-3", "web", "C-CN-3150", "林晓", [("staff", "内部备注")], "你好")
    with pytest.raises(ValueError, match="staff"):
        build_messages(ctx, "G")


def test_system_prompt_is_read_as_utf8_from_any_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    prompt = load_system_prompt("测试公司")
    assert "测试公司" in prompt and "{company}" not in prompt
    assert "个人通关号码" in prompt


def test_system_prompt_says_a_confirmed_write_is_worded_as_done():
    prompt = load_system_prompt("货代")
    # The rule quotes the note a successful write carries, so it must match the registry's text.
    assert "已为您提交" in prompt and STAGED_NOTE in prompt


def test_system_prompt_covers_customs_status_dates_and_number_only_followups():
    prompt = load_system_prompt("货代")
    assert "不写具体日期或时刻" in prompt
    assert "pending 只表示尚未完成" in prompt
    assert "暂扣原因和已完成环节分开表述" in prompt
    assert "仅含号码或单号时" in prompt


def test_system_prompt_names_only_phase1_tools():
    named = re.findall(r"`([a-z_]+\.[a-z_]+)`", load_system_prompt("G"))
    assert named and set(named) <= {*P1_TOOLS, KB_TOOL, *QUOTE_TOOLS, *DELIVERY_TOOLS, DOCUMENT_TOOL}
