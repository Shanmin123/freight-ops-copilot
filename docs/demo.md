# 演示脚本

1. 打开客户聊天窗口，切换到 김지우 / KakaoTalk。发送 `FWQD2611043 통관이 왜 보류됐나요?`，然后发 `개인통관고유부호를 정정하는 방법은 무엇인가요?`。从回复打开原始出处。发送示例号码 `P230518447201`，确认工作台有草稿，员工确认后右栏显示更正资料已提交。
2. 在中文卖家会话发送 `青岛到仁川海运整柜报价，货物：家居用品，明天出运，FOB，2×40HQ，折扣 15%。`。员工确认草稿后，打开报价页批准经理审批，下载正式 PDF。换用拼箱、轮渡或空运可验证计费单位。
3. 打开单据核对页，运行 PDF 示例，再运行拍照版发票。查看收货人、件数和毛重差异，打开 KC 及原产地证资料，确认更正草稿或报关资料草稿。
4. 打开成绩单，勾选两个 DeepSeek 模型做快速抽测。查看通过率、封顶次数、平均步骤和用量。打开案例回放核对工具结果。评测消耗 API 用量。
5. 主应用和 n8n 同时运行时，打开自动化流程页并选择 n8n 工作流，发送测试邮件。确认工作台草稿前，模拟发件箱为空。确认后，模拟发件箱和客户记录均出现该邮件。
6. 在独立演示实例点「重置演示数据」，确认收件箱恢复为两条样例工单，报价、单据核对结果、评测批次和模拟发件箱已清空。

## 命令行评测

从 `backend` 运行，评测使用独立数据库。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m evals.run_knowledge
.\.venv\Scripts\python.exe -X utf8 -m evals.run_scorecard --profiles flash --sample 12
.\.venv\Scripts\python.exe -X utf8 -m evals.run_scorecard --profiles flash flash-thinking pro --sample 3
```

评测耗用估算引用 DeepSeek 官方高峰费率；实际费用以账号账单为准。[DeepSeek 费率](https://api-docs.deepseek.com/quick_start/pricing/)

## 本地验收结果

| 评测 | 通过 |
| --- | ---: |
| 本地自动化回归（全业务） | 80/80 |
| DeepSeek Flash 抽测 | 3/3 |
| DeepSeek Flash 思考模式抽测 | 3/3 |
| DeepSeek Pro 思考模式抽测 | 3/3 |
| 本地混合检索知识库案例 | 15/15 |

报价 PDF 的中韩文可提取，字体已嵌入。单据样例同时走过 PDF 抽取、本地拍照 OCR 和 DeepSeek 图片识别。

混合检索补足了部分无关键词命中的问法。例如「수입 물품 관세를 미리 계산하고 싶어요」在关键词模式下没有结果，混合模式下命中关税估算资料。

n8n 2.48.6 实机验收覆盖 Webhook 收件、按 Message-ID 去重、员工编辑后确认、恢复 Wait 节点、写入模拟发件箱及客户记录，以及重置。
