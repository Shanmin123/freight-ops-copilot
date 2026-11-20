# 邮件自动化

「自动化流程」页面支持本地模拟和 n8n 两种运行方式。两种方式都为每封新来信建立独立工单，并在员工确认草稿后把确认版写入模拟发件箱和客户记录。工单确认前可以修改或退回重写。

## 运行 n8n 演示

请先启动项目，再在另一个 PowerShell 窗口运行以下命令。脚本为 n8n 单独选用 Node.js 24，并将 n8n 运行目录、数据和工作流留在项目的 `backend/var` 下。首次运行会下载固定版本的 n8n，导入并发布此邮件工作流。

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start-n8n.ps1
```

1. 打开本地项目的「自动化流程」页，将「运行方式」选为「n8n 工作流」，用预填样例发送测试邮件。
2. 新来信出现后打开其对应工单。确认 AI 草稿，可修改后再确认。
3. 返回自动化流程，检查该来信状态变为「已写入发件箱」，并核对模拟发件箱和客户记录各一条。

已用本机 n8n 2.48.6 验收。测试覆盖重复来信标识、确认前发件箱为空、修改后确认、Wait 恢复、回信正文、客户记录和演示重置。

后端如果使用其他端口，启动 n8n 时传入 `-BackendPort`。如果之前已经用不同后端端口导入，请加 `-ImportWorkflow` 更新本项目的演示流程。自定义 n8n 端口时，启动后端前设置 `N8N_DEMO_URL`，使地址的端口与脚本的 `-Port` 一致。

## 手动导入

在自己的 n8n 实例中可手动导入 [forwarder-mail.json](forwarder-mail.json)，核对两个 HTTP Request 节点的后端地址并发布。Webhook 路径是 `POST /webhook/forwarder-mail`，JSON 需要 `customer_id`、`sender`、`subject`、`text`。同一邮件重试时沿用原始 `message_id`。

Webhook 将邮件字段整理后交给后端。后端记录 n8n 本次执行的等待地址。员工确认后恢复 Wait 节点，最后的 HTTP Request 将已确认的回复写入发件箱和客户记录。n8n 的等待地址是按执行生成的。[Wait 节点文档](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.wait/)

需要接真实邮箱时，可另建 Email Trigger (IMAP) 流程，通过已知客户的发件地址映射到 `customer_id`，然后把归一化字段和原邮件的 Message-ID 交给上述 Webhook。真实回信可由使用自有凭据的 SMTP 流程读取已确认的模拟发件箱记录后发送，并按 `mail_id` 维护送达记录。项目内置的演示工作流只写模拟发件箱。[Email Trigger 文档](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.emailimap/) · [Send Email 文档](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.sendemail/)
