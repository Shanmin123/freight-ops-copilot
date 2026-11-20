# 货代 AI 客服中心

货代客服和运营工作台，覆盖中韩文会话、清关及仓库协作、运费报价和审批、知识库、单据核对、模型评测与邮件自动化。工作台实时显示工具调用和回复结果。查询类回复默认自动发送，业务写入随员工确认生效。

**报价演示**　客户发起询价，AI 查运费并生成报价草稿，员工确认后按规则提交经理审批。审批通过即可下载正式 PDF。

![客服工作台全页，展示客户询价、完整报价草稿、员工确认和工具执行](docs/assets/demo/09-quote-chat-draft.png)

[作品说明与演示截图](docs/作品说明.md) · [系统 Harness 架构图](docs/figures/harness-architecture.svg) · [可复现演示脚本](docs/demo.md)

## 启动

需要 Python 3.10 或以上、Node.js 22.12 或以上的 22.x 版本。在仓库根目录运行以下 PowerShell 命令。

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1
```

打开 [员工工作台](http://127.0.0.1:8000/workbench)、[客户聊天窗口](http://127.0.0.1:8000/chat) 或 [知识库](http://127.0.0.1:8000/kb)。按 Ctrl+C 停止服务。可加 `-Port 8010` 更换端口，或加 `-Rebuild` 安装锁定的前端依赖并重新构建。

## DeepSeek API

启动前，把仓库根目录的 `.env.example` 复制为 `.env`，在本机填入自己的 `DEEPSEEK_API_KEY`。客服回复和成绩单都使用 DeepSeek API，工作台顶部会显示当前模型。密钥由后端读取，`.env` 已排除在版本管理之外。启动时缺少密钥会提示补齐配置。

## 演示清关流程

1. 在客户聊天窗口选择 **김지우** 和 **KakaoTalk**，发送 `FWQD2611043 반입신고에서 3일째 멈춰 있어요`。收到韩文回复后，在工作台打开对应工单，查看「查清关进度」。
2. 在同一会话追问 `개인통관고유부호를 정정하는 방법은 무엇인가요?`。点开回复下方的出处，查看知识库对应小节及原始来源。工作台会显示「检索知识库」。
3. 发送示例号码 `P230518447201`。客户先收到确认消息，工作台出现 AI 草稿以及标为「暂存 · 确认后生效」的工具步骤。
4. 在工作台点「确认发送」。客户窗口收到正式回复，工作台显示通关号码已重新提交。此动作仅更新演示数据。
5. 点「重置演示数据」，收件箱恢复为两条示例历史工单。

## 知识库

双语资料涵盖通关号码、清单通关、查验、税费、申报材料、仓库流程、FBA 入仓和贸易术语。检索结合 BM25 和本地多语言向量排序，向量模型缺少时自动使用关键词检索。答案附知识库小节，资料不足时会说明查不到。

参见 [知识库评测](backend/evals/README.md)，可复跑中文、韩文、混合术语和无资料问题。

## 业务演示

- **报价和理赔**支持海运整柜、拼箱、轮渡和空运。可以从询价工单或报价页创建参考报价，员工确认后，低毛利或超权限折扣交给经理处理。通过审批后可下载正式 PDF。
- **单据核对**可上传商业发票、装箱单和提单，样例支持直接生成 PDF 并核对。也可换用拍照版发票，使用本地 OCR 提取。比对结果可生成更正草稿和报关资料草稿。
- **成绩单**支持生成 80 条案例、多模型并发对比、估算用量、查看失败回放。运行 DeepSeek 评测时使用自己的本机密钥。
- **邮件自动化**支持模拟来信、员工确认、模拟发件箱和客户记录，另有可导入的 n8n 工作流。[工作流和启动方式](workflows/README.md)

完整录屏顺序和验收命令见 [演示脚本](docs/demo.md)。

## 本地模型与字体

安装项目依赖后，可按需下载开源资产。下面的命令下载 Noto Sans SC/KR 和量化的多语言 E5 模型，约 163 MB，保存在本地 `backend/var`。

```powershell
.\backend\.venv\Scripts\python.exe .\scripts\provision_assets.py --fonts --embeddings
```

## Docker

```sh
docker compose up --build -d
docker compose exec app python scripts/provision_assets.py --fonts --embeddings
docker compose restart app
docker compose --profile automation up -d n8n
```

应用绑定本机 8000 端口，n8n 绑定本机 5678 端口。容器初次启动会导入并发布演示工作流。删除 Compose 数据卷会移除本地工单、运行记录和模型资产。

## 测试

首次启动安装依赖后，可在仓库根目录运行以下命令。

```powershell
Push-Location backend
.\.venv\Scripts\python.exe -m pytest -q
Pop-Location
npm --prefix frontend test
npm --prefix frontend run build
```

## 项目来源

作者孙山珉。项目借鉴 Industrial Post-Training Training Release 的隔离写入和评测流水线设计，货代场景代码与演示数据由本项目独立维护。
