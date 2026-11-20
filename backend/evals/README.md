# 知识库评测

从仓库根目录进入 `backend`，使用已安装项目依赖的 Python 环境运行。

```powershell
Set-Location backend
.\.venv\Scripts\python.exe -X utf8 -m evals.run_knowledge
```

15 条固定案例覆盖中文、韩文、夹杂外语术语的提问和资料未覆盖的问题。预期命中的案例记录对应文档和小节，未命中的案例预期明确说明查不到。

评测会从临时数据库创建独立会话，并走实际的客服与工单接口。检索按首条结果判定。客服回复须调用 `kb.search`、使用预期语言、包含回答要点，并正确引用命中的小节。资料不足时，回复须说明查不到，且不附引用。运行结果以 Markdown 表格输出，有失败时返回非零退出码。

默认使用已配置的 DeepSeek API。可重复传入 `--case` 选择案例。开发测试可通过 `--provider mock` 离线检查检索和工单逻辑。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m evals.run_knowledge --provider deepseek --case zh-correction --case ko-correction --case mixed-ko-term --case zh-uncovered-customs --case ko-clearance-exclusions
```

## 验收结果

| 模式 | 首条结果符合预期 | 客服回复通过 |
| --- | ---: | ---: |
| Mock 全量 | 15/15 | 15/15 |
| DeepSeek 五题抽测 | 5/5 | 5/5 |

最初实测中，DeepSeek 将夹杂韩文术语的中文问题翻成韩文后发起检索，命中了清单通关定义，应当命中排除品类。检索现在优先使用客户原问，回归用例固定了这条路径。
