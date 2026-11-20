import pytest

from forwarder.privacy import mask_pccc, mask_pccc_deep


@pytest.mark.parametrize(
    "text, masked",
    [
        ("P230518447201", "P2305****7201"),
        ("정정 번호는 P230518447201 입니다", "정정 번호는 P2305****7201 입니다"),
        ("新号码P190722310455，旧号码P230518447201。", "新号码P1907****0455，旧号码P2305****7201。"),
        # A longer run of digits still has the 12 that look like a PCCC hidden.
        ("P2305184472019", "P2305****72019"),
        # Not a PCCC: too few digits, a lower-case p, full-width digits.
        ("P12345", "P12345"),
        ("p230518447201", "p230518447201"),
        ("P２３０５１８４４７２０１", "P２３０５１８４４７２０１"),
        ("FWQD2611043 반입신고에서 멈췄어요", "FWQD2611043 반입신고에서 멈췄어요"),
    ],
)
def test_mask_pccc_hides_the_middle_of_every_pccc(text, masked):
    assert mask_pccc(text) == masked


def test_mask_pccc_deep_masks_every_string_and_leaves_the_rest():
    step = {
        "index": 1,
        "kind": "tool",
        "data": {
            "arguments": {"hbl": "FWQD2611043", "pccc": "P230518447201"},
            "observation": {"ok": True, "data": {"pccc_masked": "P2305****7201"}, "note": None},
            "calls": [("customs.submit_pccc", "P230518447201"), 3.5],
            "P230518447201": "key",
        },
    }
    assert mask_pccc_deep(step) == {
        "index": 1,
        "kind": "tool",
        "data": {
            "arguments": {"hbl": "FWQD2611043", "pccc": "P2305****7201"},
            "observation": {"ok": True, "data": {"pccc_masked": "P2305****7201"}, "note": None},
            "calls": [("customs.submit_pccc", "P2305****7201"), 3.5],
            "P2305****7201": "key",
        },
    }


def test_mask_pccc_deep_returns_a_new_structure():
    original = {"arguments": {"pccc": "P230518447201"}, "list": ["P230518447201"]}
    masked = mask_pccc_deep(original)
    assert original == {"arguments": {"pccc": "P230518447201"}, "list": ["P230518447201"]}
    assert masked["arguments"] is not original["arguments"] and masked["list"] is not original["list"]
    masked["list"].append("x")
    assert original["list"] == ["P230518447201"]
