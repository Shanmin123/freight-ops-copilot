import { useEffect, useState } from 'react'
import { request } from '../api/client'
import { RunTimeline } from '../components/RunTimeline'
import type { RunStep } from '../api/types'
import './scorecard.css'

type Profile = { id: string; label: string; available: boolean }
type Catalog = { total: number; pricing_source: string; profiles: Profile[] }
type History = { id: string; status: string }
type Row = { profile: string; case_id: string; group: string; question: string; points: number; capped: boolean; tokens: number }
type Summary = { profile: string; label: string; count: number; passed: number; mean: number; cap_count: number; average_steps: number; tokens: number; peak_usd: number }
type Batch = History & { threshold: number; sample: number; profiles: string[]; rows: Row[]; summary: Summary[] }
type Replay = Row & { model: string; reply: string; steps: RunStep[]; problems: string[] }

const STATES: Record<string, string> = { running: '运行中', complete: '已完成', failed: '运行失败', interrupted: '已中断' }
const GROUPS: Record<string, string> = {
  knowledge: '知识库', shipment: '货物追踪', customs: '清关', warehouse: '仓库', delivery: '改址',
  investigation: '丢件调查', pccc: '通关号码', quote: '报价', claim: '理赔', document: '单据',
}

export default function ScorecardPage() {
  const [catalog, setCatalog] = useState<Catalog>()
  const [history, setHistory] = useState<History[]>([])
  const [profiles, setProfiles] = useState<string[]>([])
  const [sample, setSample] = useState(12)
  const [threshold, setThreshold] = useState(70)
  const [batch, setBatch] = useState<Batch>()
  const [replay, setReplay] = useState<Replay>()
  const [failuresOnly, setFailuresOnly] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    Promise.all([request<Catalog>('/api/evals/catalog'), request<History[]>('/api/evals/batches')])
      .then(([details, batches]) => {
        setCatalog(details)
        setHistory(batches)
        const first = details.profiles.find((profile) => profile.available)
        if (first) setProfiles([first.id])
      })
      .catch((problem: Error) => setError(problem.message))
  }, [])

  const batchId = batch?.id
  const running = batch?.status === 'running'
  useEffect(() => {
    if (!running || !batchId) return
    let stopped = false
    let timer: number
    async function refresh() {
      try {
        const next = await request<Batch>('/api/evals/batches/' + batchId)
        if (stopped) return
        setBatch(next)
        if (next.status === 'running') timer = window.setTimeout(refresh, 1100)
        else setHistory((existing) => existing.map((entry) => entry.id === batchId ? next : entry))
      } catch (problem) {
        if (!stopped) {
          setError((problem as Error).message)
          timer = window.setTimeout(refresh, 3000)
        }
      }
    }
    timer = window.setTimeout(refresh, 800)
    return () => { stopped = true; window.clearTimeout(timer) }
  }, [running, batchId])

  async function start() {
    setBusy(true)
    setError('')
    setReplay(undefined)
    try {
      const started = await request<Batch>('/api/evals/batches', 'POST', { profiles, sample, threshold })
      setBatch(started)
      setHistory((previous) => [started, ...previous])
    } catch (problem) {
      setError((problem as Error).message)
    } finally {
      setBusy(false)
    }
  }

  function openBatch(id: string) {
    setReplay(undefined)
    setError('')
    if (id) request<Batch>('/api/evals/batches/' + id).then(setBatch).catch((problem: Error) => setError(problem.message))
    else setBatch(undefined)
  }

  function openReplay(row: Row) {
    request<Replay>(`/api/evals/batches/${batchId}/replay/${row.profile}/${row.case_id}`)
      .then(setReplay).catch((problem: Error) => setError(problem.message))
  }

  return <main className="score-page">
    <header className="score-heading">
      <h1>模型成绩单</h1>
      <label>历史批次 <select value={batchId ?? ''} onChange={(event) => openBatch(event.target.value)}>
        <option value="">选择评测批次</option>
        {history.map((entry) => <option key={entry.id} value={entry.id}>{entry.id} · {STATES[entry.status]}</option>)}
      </select></label>
    </header>
    <form className="score-settings" onSubmit={(event) => { event.preventDefault(); void start() }}>
      <fieldset>
        <legend>评测模型</legend>
        {catalog?.profiles.map((profile) => <label key={profile.id} className="score-model">
          <input type="checkbox" checked={profiles.includes(profile.id)} disabled={!profile.available || busy || running}
            onChange={(event) => setProfiles((current) => event.target.checked ? [...current, profile.id] : current.filter((id) => id !== profile.id))} />
          {profile.label}{!profile.available && <span>未配置</span>}
        </label>)}
      </fieldset>
      <div className="score-options">
        <label>案例数量 <select value={sample} onChange={(event) => setSample(Number(event.target.value))}>
          <option value={12}>快速抽测 · 12 条</option>
          <option value={30}>分组抽测 · 30 条</option>
          <option value={catalog?.total ?? 80}>全量 · {catalog?.total ?? 80} 条</option>
        </select></label>
        <label>通过线 <input type="number" min={1} max={100} value={threshold} onChange={(event) => setThreshold(Number(event.target.value))} /></label>
        <button type="submit" disabled={!catalog || !profiles.length || busy || running}>{running ? '评测运行中' : '运行评测'}</button>
      </div>
      <p>DeepSeek 会使用已配置的 API 密钥。费用按官方高峰费率估算，非高峰为该估算的一半。
        {catalog && <a href={catalog.pricing_source} target="_blank" rel="noreferrer">费率出处</a>}
      </p>
    </form>
    {error && <p className="score-error" role="alert">{error}</p>}
    {batch && <section className="score-results" aria-label="评测结果">
      <div className="score-heading"><h2>模型对比</h2>
        <span role="status">{STATES[batch.status]} · {batch.rows.length} / {batch.sample * batch.profiles.length} 条</span>
      </div>
      <div className="score-cards">
        {batch.summary.map((item) => <article key={item.profile}>
          <h3>{item.label}</h3>
          <strong>{item.mean.toFixed(1)}<small> / 100</small></strong>
          <dl>
            <dt>通过率</dt><dd>{item.count ? Math.round(100 * item.passed / item.count) : 0}% · {item.passed}/{item.count}</dd>
            <dt>封顶</dt><dd>{item.cap_count} 条</dd>
            <dt>平均步骤</dt><dd>{item.average_steps}</dd>
            <dt>Token</dt><dd>{item.tokens.toLocaleString()}</dd>
            <dt>高峰估算</dt><dd>USD {item.peak_usd.toFixed(6)}</dd>
          </dl>
        </article>)}
      </div>
      <div className="score-heading"><h2>案例结果</h2><label>
        <input type="checkbox" checked={failuresOnly} onChange={(event) => setFailuresOnly(event.target.checked)} /> 只看失败
      </label></div>
      <div className="score-table-wrap"><table>
        <thead><tr><th>案例</th><th>类别</th><th>模型</th><th>得分</th><th>结果</th><th>详情</th></tr></thead>
        <tbody>{batch.rows.filter((item) => !failuresOnly || item.points < batch.threshold).map((item) => <tr key={item.profile + item.case_id}>
          <td>{item.case_id}</td><td>{GROUPS[item.group] ?? item.group}</td>
          <td>{batch.summary.find(({ profile }) => profile === item.profile)?.label}</td>
          <td>{item.points}</td><td>{item.capped ? '封顶' : item.points >= batch.threshold ? '通过' : '失败'}</td>
          <td><button type="button" onClick={() => openReplay(item)}>回放</button></td>
        </tr>)}</tbody>
      </table></div>
    </section>}
    {replay && <section className="score-replay" aria-label="案例回放">
      <div className="score-heading"><h2>{replay.case_id} · {replay.model}</h2>
        <button type="button" onClick={() => setReplay(undefined)}>关闭回放</button></div>
      <p>{replay.question}</p>
      {replay.problems.length > 0 && <ul>{replay.problems.map((problem) => <li key={problem}>{problem}</li>)}</ul>}
      {replay.reply && <pre>{replay.reply}</pre>}
      {replay.steps.length > 0 && <RunTimeline run={null} liveSteps={replay.steps} />}
    </section>}
  </main>
}
