import type { RunRecord, RunStep } from '../api/types'
import { TOOL_LABEL } from '../labels'

type Props = { run: RunRecord | null, liveSteps: RunStep[] }

const argumentLabels: Record<string, string> = {
  hbl: '提单', customer_id: '客户', job_id: '作业单', attachment_id: '附件',
  pccc: '通关号码', action: '操作', cartons: '箱号', note: '备注', reason: '原因', query: '搜索词',
}

export function RunTimeline({ run, liveSteps }: Props) {
  const steps = liveSteps.length ? liveSteps : run?.steps ?? []
  const finished = !liveSteps.length && run?.status === 'finished'
  const decision = run?.decision === 'auto_sent' ? '自动发送'
    : run?.decision === 'draft' ? '草稿待确认'
      : run?.error ? '出错' : '本次未发送'
  const totalTokens = run?.usage.total_tokens ?? (run?.usage.prompt_tokens ?? 0) + (run?.usage.completion_tokens ?? 0)

  return <section className="wb-trace" aria-labelledby="wb-trace-heading">
    <h2 id="wb-trace-heading">AI 执行过程</h2>
    <ol className="wb-steps">
      {steps.map((step) => {
        if (step.kind === 'model') {
          return step.data.content?.trim() ? <li className="wb-step wb-step-model" key={step.index}>
            <strong>生成回复</strong>
          </li> : null
        }

        const { name, observation, kind, arguments: args } = step.data
        const fields = typeof args === 'object' && args !== null ? Object.entries(args) : []
        const params = fields.map(([key, value]) =>
          `${argumentLabels[key] ?? key} ${typeof value === 'string' ? value : JSON.stringify(value)}`,
        ).join(' · ')

        return <li className="wb-step" data-success={observation.ok} key={step.index}>
          <div className="wb-step-title"><strong>{TOOL_LABEL[name] ?? name}</strong>
            {observation.ok ? <span className="wb-step-ok" aria-label="成功">✓</span>
              : <span className="wb-step-error">✗ {observation.error ?? '调用失败'}</span>}
          </div>
          {params && <p className="wb-step-params">{params}</p>}
          {kind === 'write' && observation.ok && <span className="wb-staged">暂存 · 确认后生效</span>}
          {observation.data != null && <details className="wb-step-result">
            <summary>查看结果</summary>
            <pre>{JSON.stringify(observation.data, null, 2)}</pre>
          </details>}
        </li>
      })}
    </ol>
    {!steps.length && <p className="wb-placeholder">{run?.status === 'running' ? 'AI 正在处理…' : '暂无执行记录'}</p>}
    {finished && <div className="wb-run-result">
      <strong>{decision}</strong><span>{totalTokens.toLocaleString('zh-CN')} token</span>
      {run.error && <p className="wb-error">{run.error}</p>}
    </div>}
  </section>
}
