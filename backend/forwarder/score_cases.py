"""Reproducible input cases and independently specified scoring rules."""

import json

from forwarder.config import BACKEND_DIR

_CUSTOMERS = {"kr": "C-KR-0412", "cn": "C-CN-3150", "seller": "C-CN-2207", "export": "C-KR-1088"}


def make_cases() -> list[dict]:
    cases: list[dict] = []

    def add(group, prompts, who, tool, status="ai_replied", **rule):
        for question in prompts:
            number = sum(case["group"] == group for case in cases) + 1
            cases.append({"id": f"{group}-{number:02d}", "group": group, "question": question,
                          "customer": _CUSTOMERS[who], "tool": tool, "status": status, **rule})

    knowledge = json.loads((BACKEND_DIR / "evals" / "knowledge_cases.json").read_text(encoding="utf-8"))
    for case in knowledge:
        add("knowledge", [case["question"]], "kr" if case["language"] == "ko" else "cn",
            "kb.search", reference=case["expected"], original_id=case["id"])
    add("knowledge", ["美国站 FBA 入仓箱标需要每箱都贴吗？"], "seller", "kb.search",
        reference="fba-box-labels/box-id")

    for hbl, who, prompts in (
        ("FWQD2611043", "kr", ["{hbl} 화물이 어디까지 왔나요?", "{hbl} 최근 배송 상태를 확인해 주세요"]),
        ("FWQD2611150", "cn", ["{hbl} 的货物现在在哪里？", "帮我查下 {hbl} 的运输轨迹"]),
        ("FWQD2611127", "seller", ["看看货 {hbl} 现在到哪了", "请查一下 {hbl} 的物流进度"]),
        ("FWICN2611088", "export", ["{hbl} 화물의 최근 위치를 알려 주세요", "{hbl} 운송 상태가 궁금합니다"]),
    ):
        add("shipment", [prompt.format(hbl=hbl) for prompt in prompts], who, "shipment.get", field=["hbl"], value=hbl)

    add("customs", [f"FWQD2611043 {text}" for text in (
        "통관이 왜 보류됐나요?", "세관 진행을 확인해 주세요", "통관 보류 사유가 뭔가요?", "반입 이후 통관 상황이 궁금합니다",
    )], "kr", "customs.get_progress", field=["hold", "code"], value="pccc_mismatch")
    add("customs", [f"FWQD2611150 {text}" for text in (
        "现在是什么清关状态？", "请查清关暂停的原因", "海关查验进行到哪里了？", "我还需要做什么？请查这票清关",
    )], "cn", "customs.get_progress", field=["hold", "code"], value="inspection")

    add("warehouse", ["到仓验货有破损吗？", "仓库验货发现几个箱子受损？", "发我这批货的验货结果。"],
        "seller", "warehouse.get_job")
    add("warehouse", ["外箱破损，麻烦重新包装。", "请把验货破损的箱子重新包装。", "可以重新包装这批货的受损箱吗？",
                      "需要重新包装破损纸箱。"], "seller", "warehouse.request_action", "awaiting_confirmation",
        field=["action"], value="repack")

    add("delivery", [f"FWQD2611043 {text}" for text in (
        "地址改为 韩国仁川演示路12号", "我想改地址：首尔演示街32号", "配送地址改为 仁川示例大道5号", "请改地址：首尔样例栋11号",
    )], "kr", "delivery.reroute", "awaiting_confirmation", field=["status"], value="requested")
    add("investigation", [f"FWQD2611150 {text}" for text in (
        "疑似丢件，请向承运商发起调查", "包裹未收到，请查丢件", "我怀疑丢件了，请调查", "一直没收到，请登记丢件调查",
    )], "cn", "carrier.open_investigation", "awaiting_confirmation", field=["status"], value="opened")
    add("pccc", [f"FWQD2611043 {text} P230518447201" for text in (
        "정정 번호입니다.", "개인통관고유부호를 수정해 주세요.", "이 번호로 다시 제출 부탁드립니다.", "확인한 올바른 부호는",
    )], "kr", "customs.submit_pccc", "awaiting_confirmation", field=["pccc_masked"], value="P2305****7201")

    for mode, label, term, quantities in (
        ("fcl", "海运整柜", "FOB", ("1×20GP", "2×40HQ", "3×40GP", "1×40HQ")),
        ("lcl", "海运拼箱", "FOB", ("1 CBM", "2.5 CBM", "5 CBM", "3 CBM")),
        ("ferry", "轮渡", "FOB", ("1 CBM", "2 CBM", "4 CBM", "3 CBM")),
        ("air", "空运", "FCA", ("0.5 CBM，80 kg", "2 CBM，100 kg", "4 CBM，240 kg", "1 CBM，200 kg")),
    ):
        for index, quantity in enumerate(quantities):
            discount = "，折扣 15%" if index == 3 else ""
            add("quote", [f"青岛到仁川{label}报价，货物：家居用品，明天出运，{term}，{quantity}{discount}。"],
                "seller", "quote.create", "awaiting_confirmation", mode=mode, manager=index == 3)

    for index, amount in enumerate((120, 300, 450, 760, 900, 1200)):
        add("claim", [f"申请理赔 FWQD2611127，理赔金额 USD {amount}，原因：外箱{'压坏' if index % 2 else '进水'}。"],
            "seller", "claim.open", "awaiting_confirmation", minimum=amount)

    for field, expected in (("consignee", "mismatch"), ("packages", "mismatch"), ("gross_kg", "mismatch"),
                            ("hs_code", "mismatch"), ("amount", "mismatch"), ("volume_cbm", "match"), ("kc_radio", "requirement")):
        add("document", [f"核对示例单据的 {field}"], "seller", "document.get_findings",
            document_field=field, document_expected=expected)
    return cases


if __name__ == "__main__":
    print(json.dumps(make_cases(), ensure_ascii=False, indent=2))
