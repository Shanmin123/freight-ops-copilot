import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { getTickets } from '../api/client'
import type { TicketSummary } from '../api/types'
import './documents.css'

const documents = {
  invoice: { title: '商业发票', label: 'Commercial invoice' },
  packing: { title: '装箱单', label: 'Packing list' },
  bill: { title: '提单', label: 'Bill of lading' },
} as const
type DocKind = keyof typeof documents
const kinds = Object.keys(documents) as DocKind[]
type DraftKind = 'correction' | 'customs'
type Draft = { text: string; status: 'draft' | 'confirmed' }
type Value = string | number | null
type CheckStatus = 'match' | 'mismatch' | 'single' | 'incomparable' | 'missing'
type Review = {
  id: string
  ticket_id: string | null
  documents: Record<DocKind, { filename: string; method: string; fields: Record<string, Value> }>
  checks: { field: string; label: string; values: Record<DocKind, Value>; status: CheckStatus }[]
  missing: { kind: DocKind; label: string }[]
  requirements: { code: string; title: string; detail: string; kb_url: string; source_url: string }[]
  drafts: Record<DraftKind, Draft>
}
type History = Pick<Review, 'id' | 'ticket_id'> & { differences: number; created_at: string }
type Samples = { samples: { kind: string; label: string; url: string }[]; deepseek_available: boolean }

const states: Record<CheckStatus, string> = {
  match: '一致', mismatch: '有差异', single: '仅一份记录', incomparable: '币种待核对', missing: '未提取',
}
const methods: Record<string, string> = {
  pdf_text: 'PDF 文本', image_ocr: '本地 OCR', deepseek_text: 'DeepSeek 文本提取', deepseek_vision: 'DeepSeek 图片识别',
}
const draftTitles: Record<DraftKind, string> = { correction: '请客户更正', customs: '报关资料草稿' }
const acceptance = 'application/pdf,image/png,image/jpeg,image/webp,.pdf,.png,.jpg,.jpeg,.webp'
const maxBytes = 8 * 1024 * 1024

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch('/api/documents' + path, init)
  const body = await response.json().catch(() => null)
  if (!response.ok) throw new Error(typeof body?.detail === 'string' ? body.detail : '操作失败，请重试。')
  return body as T
}

function post<T>(path: string, body: unknown): Promise<T> {
  return api<T>(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
}

function DraftCard({ kind, draft, reviewId, pending, onConfirm }: {
  kind: DraftKind
  draft: Draft
  reviewId: string
  pending: boolean
  onConfirm: (text: string) => void
}) {
  const [text, setText] = useState(draft.text)
  const confirmed = draft.status === 'confirmed'

  function saveDraft() {
    const url = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }))
    const link = document.createElement('a')
    link.href = url
    link.download = `${reviewId}-${kind}.txt`
    link.click()
    setTimeout(() => URL.revokeObjectURL(url), 0)
  }

  return <section className="doc-draft" aria-label={draftTitles[kind]}>
    <div className="doc-section-head">
      <h3>{draftTitles[kind]}</h3>
      <span className={confirmed ? 'doc-tag doc-tag--match' : 'doc-tag'}>{confirmed ? '已确认' : '待确认'}</span>
    </div>
    <textarea aria-label={draftTitles[kind]} rows={8} value={text} readOnly={confirmed}
      disabled={pending} maxLength={6000} onChange={(event) => setText(event.target.value)} />
    {confirmed
      ? <button className="doc-secondary" type="button" onClick={saveDraft}>下载文本</button>
      : <button className="doc-primary" type="button" disabled={pending || !text.trim()} onClick={() => onConfirm(text)}>确认草稿</button>}
  </section>
}

export default function DocumentsPage() {
  const [files, setFiles] = useState<Partial<Record<DocKind, File>>>({})
  const [ticket, setTicket] = useState('')
  const [samples, setSamples] = useState<Samples>()
  const [tickets, setTickets] = useState<TicketSummary[]>([])
  const [history, setHistory] = useState<History[]>([])
  const [review, setReview] = useState<Review>()
  const [online, setOnline] = useState(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    void Promise.allSettled([
      api<Samples>('/samples').then((data) => { if (active) setSamples(data) }),
      api<{ reviews: History[] }>('/reviews').then(({ reviews }) => { if (active) setHistory(reviews) }),
      getTickets().then((data) => { if (active) setTickets(data) }),
    ]).then((results) => {
      if (active && results[0].status === 'rejected') setError('无法加载单据核对配置，请刷新后重试。')
    })
    return () => { active = false }
  }, [])

  async function operate(action: () => Promise<Review>, refreshHistory = false) {
    setPending(true)
    setError('')
    try {
      setReview(await action())
      if (refreshHistory) setHistory((await api<{ reviews: History[] }>('/reviews')).reviews)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '操作失败，请重试。')
    } finally {
      setPending(false)
    }
  }

  function runDemo(usePhoto: boolean) {
    void operate(() => post('/demo', { use_photo: usePhoto, ticket_id: ticket || null }), true)
  }

  function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData()
    for (const kind of kinds) {
      const file = files[kind]
      if (!file || file.size > maxBytes || !file.size) {
        setError(`请选择 8 MB 以内的${documents[kind].title}文件。`)
        return
      }
      data.append(kind, file)
    }
    if (ticket) data.append('ticket_id', ticket)
    data.append('mode', online ? 'deepseek' : 'local')
    setOnline(false)
    void operate(() => api('/reviews', { method: 'POST', body: data }), true)
  }

  const differenceCount = review?.checks.filter((check) => check.status === 'mismatch').length

  return <main className="documents-page">
    <div className="doc-page-head">
      <h1>单据核对</h1>
      <div className="doc-actions">
        <button className="doc-secondary" type="button" disabled={pending} onClick={() => runDemo(false)}>运行 PDF 样例</button>
        <button className="doc-secondary" type="button" disabled={pending} onClick={() => runDemo(true)}>运行照片样例</button>
      </div>
    </div>
    <div className="doc-grid">
      <aside className="doc-sidebar">
        <section className="doc-card">
          <h2>上传单据</h2>
          <p className="doc-help">PDF、PNG、JPEG 或 WebP，每份不超过 8 MB。</p>
          <form onSubmit={upload}>
            <div className="doc-upload-list">
              {kinds.map((kind) => <label className="doc-upload" key={kind}>
                <span>{documents[kind].title}</span>
                <input type="file" accept={acceptance} required disabled={pending}
                  onChange={(event) => setFiles((previous) => ({ ...previous, [kind]: event.target.files?.[0] }))} />
              </label>)}
            </div>
            <label className="doc-field">关联工单
              <select value={ticket} disabled={pending} onChange={(event) => setTicket(event.target.value)}>
                <option value="">独立核对</option>
                {tickets.map((item) => <option value={item.id} key={item.id}>{item.id} · {item.customer_name}</option>)}
              </select>
            </label>
            {samples?.deepseek_available && <label className="doc-opt-in">
              <input type="checkbox" checked={online} disabled={pending} onChange={(event) => setOnline(event.target.checked)} />
              <span>使用 DeepSeek 提取字段，并发送本次单据的文本或图片给该服务。</span>
            </label>}
            <button className="doc-primary doc-upload-submit" type="submit" disabled={pending}>开始核对</button>
          </form>
        </section>
        <section className="doc-card">
          <h2>样例下载</h2>
          <ul className="doc-samples">
            {samples?.samples.map((sample) => <li key={sample.kind}><a href={sample.url}>{sample.label}<span aria-hidden="true">↓</span></a></li>)}
          </ul>
        </section>
        {history.length > 0 && <section className="doc-card">
          <h2>核对记录</h2>
          <ul className="doc-history">
            {history.map((row) => <li key={row.id}>
              <button type="button" disabled={pending} aria-current={review?.id === row.id ? 'true' : undefined}
                onClick={() => void operate(() => api('/reviews/' + encodeURIComponent(row.id)))}>
                <span>{row.id}</span>
                <small>{row.ticket_id || '独立核对'} · {row.differences} 项差异</small>
              </button>
            </li>)}
          </ul>
        </section>}
      </aside>
      <div className="doc-results">
        {error && <p className="doc-error" role="alert">{error}</p>}
        {pending && <p className="doc-progress" role="status">正在处理单据…</p>}
        {!review && <section className="doc-card doc-empty">
          <h2>核对结果</h2>
          <p>上传三份单据，或运行样例查看差异和处理草稿。</p>
        </section>}
        {review && <>
          <section className="doc-card">
            <div className="doc-section-head doc-result-head">
              <div><h2>字段比对</h2><p>{review.id}{review.ticket_id ? ' · ' + review.ticket_id : ''}</p></div>
              <span className={differenceCount ? 'doc-tag doc-tag--mismatch' : 'doc-tag doc-tag--match'}>{differenceCount} 项差异</span>
            </div>
            <div className="doc-table-wrap">
              <table className="doc-table">
                <thead><tr>
                  <th scope="col">字段</th>
                  {kinds.map((kind) => <th scope="col" key={kind}>
                    <span>{documents[kind].title}</span>
                    <small>{methods[review.documents[kind].method] || review.documents[kind].method}</small>
                  </th>)}
                  <th scope="col">结果</th>
                </tr></thead>
                <tbody>
                  {review.checks.map((check) => <tr key={check.field} className={check.status === 'mismatch' ? 'doc-row-mismatch' : undefined}>
                    <th scope="row">{check.label}</th>
                    {kinds.map((kind) => <td key={kind}>{check.values[kind] ?? '—'}</td>)}
                    <td><span className={'doc-tag doc-tag--' + check.status}>{states[check.status]}</span></td>
                  </tr>)}
                </tbody>
              </table>
            </div>
            {review.missing.length > 0 && <ul className="doc-missing">
              {review.missing.map((item) => <li key={item.kind + item.label}>{documents[item.kind].title}缺少{item.label}</li>)}
            </ul>}
          </section>
          <section className="doc-card">
            <h2>进口要求提示</h2>
            {review.requirements.length ? <ul className="doc-rules">{review.requirements.map((item) => <li key={item.code}>
              <h3>{item.title}</h3><p>{item.detail}</p>
              <div className="doc-rule-links">
                <Link to={item.kb_url}>知识库依据</Link>
                <a href={item.source_url} target="_blank" rel="noreferrer">原始出处 ↗</a>
              </div>
            </li>)}</ul> : <p className="doc-help">当前字段未触发额外核查提示。</p>}
          </section>
          <section className="doc-card">
            <h2>处理草稿</h2>
            <div className="doc-drafts">
              {(['correction', 'customs'] as const).map((kind) => <DraftCard
                key={review.id + kind + review.drafts[kind].status}
                kind={kind} draft={review.drafts[kind]} reviewId={review.id} pending={pending}
                onConfirm={(text) => void operate(() => post(`/reviews/${encodeURIComponent(review.id)}/drafts/${kind}/confirm`, { text }))}
              />)}
            </div>
          </section>
        </>}
      </div>
    </div>
  </main>
}
