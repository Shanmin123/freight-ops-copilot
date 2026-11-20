import { useCallback, useEffect, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useEventStream } from '../hooks/useEventStream'
import './quotes.css'

type Mode = 'fcl' | 'lcl' | 'ferry' | 'air'
type Status = 'draft' | 'pending_approval' | 'formal' | 'approved' | 'rejected'
type Lane = {
  origin: string; destination: string; origin_name: string; destination_name: string; mode: Mode;
  valid_until: string; rates: { unit: string; price_usd: number }[];
}
type Rule = { key: string; label: string; value: number; unit: string }
type Quote = {
  kind: 'quote'; amount_usd: number; subtotal_usd: number; discount_usd: number; valid_until: string;
  route: { origin: string; destination: string }; request: { goods: string; mode: Mode; incoterm: string };
  schedule: { departure: string; arrival: string }[];
  charges: { name: string; quantity: number; unit: string; rate_usd: number; amount_usd: number }[];
  approval_required: boolean;
  internal: { cost_usd: number; margin_pct: number; reasons: string[] };
}
type Claim = {
  kind: 'claim'; hbl: string; reason: string; amount_usd: number; approval_required: boolean;
  internal: { reasons: string[] };
}
type Entry = (Quote | Claim) & {
  id: string; status: Status; ticket_id: string | null; confirm_in_quotes: boolean;
  manager_note: string | null; approval_reason: string | null;
}
type QuoteForm = {
  mode: Mode; lane: string; goods: string; shipDate: string; incoterm: string;
  containerType: string; containerCount: string; volume: string; weight: string;
  goodsValue: string; discount: string; ticketId: string;
}

const modes: Record<Mode, string> = { fcl: '海运整柜', lcl: '海运拼箱', ferry: '轮渡', air: '空运' }
const statuses: Record<Status, string> = {
  draft: '待员工确认', pending_approval: '待经理审批', formal: '正式报价', approved: '理赔已登记',
  rejected: '已驳回',
}
const terms = ['EXW', 'FCA', 'FOB', 'CFR', 'CIF', 'CPT', 'CIP', 'DAP']
const currency = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', currencyDisplay: 'code' })

function today() {
  return new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Seoul' }).format(new Date())
}

function laneKey(lane: Lane) {
  return `${lane.origin}:${lane.destination}`
}

async function quoteRequest<T>(path: string, body?: object): Promise<T> {
  const response = await fetch('/api/quotes' + path, body === undefined ? undefined : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
  const data = await response.json()
  if (!response.ok) {
    const detail = typeof data.detail === 'string' ? data.detail : data.detail?.[0]?.msg
    throw new Error(detail || '报价服务暂时不可用')
  }
  return data
}

function Field({ label, children, wide = false }: { label: string; children: ReactNode; wide?: boolean }) {
  return <label className={wide ? 'quote-field quote-field-wide' : 'quote-field'}><span>{label}</span>{children}</label>
}

function ChargePreview({ quote }: { quote: Quote }) {
  return <section className="quote-panel quote-preview" aria-label="报价试算">
    <div className="quote-panel-title">
      <div><h2>参考报价</h2><p>{quote.route.origin} → {quote.route.destination} · {modes[quote.request.mode]}</p></div>
      <strong>{currency.format(quote.amount_usd)}</strong>
    </div>
    <div className="quote-table-wrap">
      <table className="quote-table">
        <thead><tr><th scope="col">费用项</th><th scope="col">计费量</th><th scope="col">单价</th><th scope="col">金额</th></tr></thead>
        <tbody>{quote.charges.map((charge) => (
          <tr key={charge.name}>
            <th scope="row">{charge.name}</th><td>{charge.quantity} {charge.unit}</td>
            <td>{currency.format(charge.rate_usd)}</td><td>{currency.format(charge.amount_usd)}</td>
          </tr>
        ))}{quote.discount_usd > 0 && <tr><th scope="row">折扣</th><td colSpan={2} /><td>−{currency.format(quote.discount_usd)}</td></tr>}</tbody>
      </table>
    </div>
    <dl className="quote-metrics">
      <div><dt>有效期至</dt><dd>{quote.valid_until}</dd></div>
      <div><dt>成本</dt><dd>{currency.format(quote.internal.cost_usd)}</dd></div>
      <div><dt>毛利率</dt><dd>{quote.internal.margin_pct}%</dd></div>
    </dl>
    {quote.approval_required && <p className="quote-notice">需经理审批 · {quote.internal.reasons.join('、')}</p>}
    <p className="quote-muted">不含关税和增值税。</p>
    {quote.schedule.length > 0 && <section className="quote-schedule">
      <h3>可选班次</h3>
      <ul>{quote.schedule.map(({ departure, arrival }) => (
        <li key={departure}><time>{departure}</time> → <time>{arrival}</time></li>
      ))}</ul>
      <p className="quote-muted">班次日期供订舱参考。</p>
    </section>}
  </section>
}

export function TicketQuoteApprovals({ ticketId, refreshKey }: { ticketId: string | null; refreshKey: number }) {
  const [records, setRecords] = useState<Entry[]>([])
  useEffect(() => {
    if (!ticketId) return
    let active = true
    void quoteRequest<{ records: Entry[] }>('?' + new URLSearchParams({ ticket_id: ticketId })).then(
      ({ records: entries }) => { if (active) setRecords(entries) },
      () => { if (active) setRecords([]) },
    )
    return () => { active = false }
  }, [ticketId, refreshKey])

  const visible = records.filter((record) => record.ticket_id === ticketId)
  if (!visible.length || !ticketId) return null
  return <section className="quote-ticket-approvals" aria-label="报价与理赔">
    <h2>报价与理赔</h2>
    <ul>{visible.map((record) => <li key={record.id}>
      <span>{record.kind === 'quote' ? '运费报价' : '货损理赔'} · {currency.format(record.amount_usd)}</span>
      <span className={`quote-status quote-status-${record.status}`}>{statuses[record.status]}</span>
    </li>)}</ul>
    <Link to={'/quotes?' + new URLSearchParams({ ticket_id: ticketId })}>查看报价和审批 →</Link>
  </section>
}

export default function QuotesPage() {
  const [searchParams] = useSearchParams()
  const requestedTicket = searchParams.get('ticket_id') ?? ''
  const [lanes, setLanes] = useState<Lane[]>([])
  const [rules, setRules] = useState<Rule[]>([])
  const [records, setRecords] = useState<Entry[]>([])
  const [quote, setQuote] = useState<Quote>()
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [form, setForm] = useState<QuoteForm>({
    mode: 'fcl', lane: 'CNTAO:KRINC', goods: '', shipDate: today(), incoterm: 'FOB',
    containerType: '40HQ', containerCount: '2', volume: '3', weight: '100',
    goodsValue: '', discount: '0', ticketId: requestedTicket,
  })

  const refresh = useCallback(async () => {
    const filter = requestedTicket ? '?' + new URLSearchParams({ ticket_id: requestedTicket }) : ''
    const data = await quoteRequest<{ records: Entry[] }>(filter)
    setRecords(data.records)
  }, [requestedTicket])

  useEffect(() => {
    setForm((current) => ({ ...current, ticketId: requestedTicket }))
  }, [requestedTicket])

  useEffect(() => {
    Promise.all([quoteRequest<{ rates: Lane[] }>('/rates'), quoteRequest<{ rules: Rule[] }>('/policy'), refresh()])
      .then(([catalog, policy]) => { setLanes(catalog.rates); setRules(policy.rules) })
      .catch((problem: Error) => setError(problem.message))
  }, [refresh])

  useEventStream('/api/events', (event) => {
    if (event.type === 'run.finished' || event.type === 'message.updated' || event.type === 'demo.reset') {
      void refresh().catch(() => setError('报价记录刷新失败'))
    }
  })

  const modeLanes = lanes.filter((lane) => lane.mode === form.mode)
  const activeLane = modeLanes.find((lane) => laneKey(lane) === form.lane) ?? modeLanes[0]
  const requiresInsurance = form.incoterm === 'CIF' || form.incoterm === 'CIP'

  function update<K extends keyof QuoteForm>(key: K, value: QuoteForm[K]) {
    setForm((current) => ({ ...current, [key]: value }))
    setQuote(undefined)
    setNotice('')
  }

  function chooseMode(mode: Mode) {
    setForm((current) => ({
      ...current, mode, lane: '',
      incoterm: mode === 'air' && ['FOB', 'CFR', 'CIF'].includes(current.incoterm) ? 'FCA' : current.incoterm,
    }))
    setQuote(undefined)
    setNotice('')
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy || !activeLane) return
    const action = (event.nativeEvent as SubmitEvent).submitter?.getAttribute('value') ?? 'preview'
    const payload = {
      origin: activeLane.origin, destination: activeLane.destination, mode: form.mode,
      goods: form.goods.trim(), ship_date: form.shipDate, incoterm: form.incoterm,
      discount_pct: Number(form.discount || 0),
      ...(form.mode === 'fcl' ? { container_type: form.containerType, container_count: Number(form.containerCount) }
        : { volume_cbm: Number(form.volume), ...(form.mode === 'air' && { weight_kg: Number(form.weight) }) }),
      ...(requiresInsurance && { declared_value_usd: Number(form.goodsValue) }),
    }
    setBusy(action)
    setError('')
    setNotice('')
    try {
      if (action === 'preview') {
        setQuote(await quoteRequest<Quote>('/calculate', payload))
      } else {
        const record = await quoteRequest<Entry>('', {
          ...payload, ...(form.ticketId.trim() && { ticket_id: form.ticketId.trim() }),
        })
        if (record.kind === 'quote') setQuote(record)
        await refresh()
        setNotice(`${record.id} 已保存，等待员工确认。`)
      }
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : '未能生成报价')
    } finally {
      setBusy('')
    }
  }

  async function act(record: Entry, action: 'confirm' | 'approved' | 'rejected') {
    if (busy) return
    setBusy(record.id)
    setError('')
    setNotice('')
    try {
      const next = await quoteRequest<Entry>(
        `/${encodeURIComponent(record.id)}/${action === 'confirm' ? 'confirm' : 'review'}`,
        action === 'confirm' ? {} : { decision: action },
      )
      await refresh()
      setNotice(`${next.id} 已更新为「${statuses[next.status]}」。`)
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : '未能更新报价')
    } finally {
      setBusy('')
    }
  }

  return <main className="quotes-page">
    <div className="quotes-title"><h1>运费报价与审批</h1><p>查询运价、试算费用，并在审批后出具正式报价。</p></div>
    {error && <p className="quote-error" role="alert">{error}</p>}
    {notice && <p className="quote-notice" role="status">{notice}</p>}
    <div className="quote-layout">
      <form className="quote-panel quote-form" onSubmit={(event) => void submit(event)}>
        <h2>询价信息</h2>
        <div className="quote-fields">
          <Field label="运输方式"><select value={form.mode} onChange={(event) => chooseMode(event.target.value as Mode)}>
            {Object.entries(modes).map(([mode, label]) => <option value={mode} key={mode}>{label}</option>)}
          </select></Field>
          <Field label="航线"><select value={activeLane ? laneKey(activeLane) : ''} onChange={(event) => update('lane', event.target.value)}>
            {modeLanes.map((lane) => (
              <option value={laneKey(lane)} key={laneKey(lane)}>{lane.origin_name} → {lane.destination_name}</option>
            ))}
          </select></Field>
          <Field label="货物种类"><input required maxLength={100} value={form.goods} placeholder="例如 家居用品"
            onChange={(event) => update('goods', event.target.value)} /></Field>
          <Field label="期望出运日"><input required type="date" min={today()} value={form.shipDate}
            onChange={(event) => update('shipDate', event.target.value)} /></Field>
          <Field label="贸易条款"><select value={form.incoterm} onChange={(event) => update('incoterm', event.target.value)}>
            {terms.filter((term) => form.mode !== 'air' || !['FOB', 'CFR', 'CIF'].includes(term)).map((term) => (
              <option key={term}>{term}</option>
            ))}
          </select></Field>
          <Field label="折扣（%）"><input type="number" min="0" max="40" step=".01" value={form.discount}
            onChange={(event) => update('discount', event.target.value)} /></Field>
          {form.mode === 'fcl' ? <>
            <Field label="柜型"><select value={form.containerType} onChange={(event) => update('containerType', event.target.value)}>
              {['20GP', '40GP', '40HQ'].map((container) => <option key={container}>{container}</option>)}
            </select></Field>
            <Field label="柜数"><input required type="number" min="1" max="50" value={form.containerCount}
              onChange={(event) => update('containerCount', event.target.value)} /></Field>
          </> : <Field label="体积（CBM）"><input required type="number" min=".001" max="9999" step=".001" value={form.volume}
            onChange={(event) => update('volume', event.target.value)} /></Field>}
          {form.mode === 'air' && <Field label="实际重量（kg）"><input required type="number" min=".01" max="100000" step=".01" value={form.weight}
            onChange={(event) => update('weight', event.target.value)} /></Field>}
          {requiresInsurance && <Field label="货值（USD）"><input required type="number" min=".01" max="1000000" step=".01" value={form.goodsValue}
            onChange={(event) => update('goodsValue', event.target.value)} /></Field>}
          <Field label="关联工单号（可选）" wide><input maxLength={32} value={form.ticketId} placeholder="T-001042"
            onChange={(event) => update('ticketId', event.target.value)} /></Field>
        </div>
        <div className="quote-actions">
          <button type="submit" value="preview" className="quote-button" disabled={!!busy || !activeLane}>试算费用</button>
          <button type="submit" value="draft" className="quote-button quote-primary" disabled={!!busy || !activeLane}>保存员工草稿</button>
        </div>
      </form>
      {quote ? <ChargePreview quote={quote} /> : <section className="quote-panel quote-preview">
        <h2>航线参考运价</h2>
        {activeLane ? <>
          <p className="quote-muted">{activeLane.origin_name} → {activeLane.destination_name}</p>
          <dl className="quote-tariffs">{activeLane.rates.map((rate) => (
            <div key={rate.unit}><dt>{rate.unit === 'cbm' ? '每 CBM' : rate.unit === 'kg' ? '每计费公斤' : rate.unit}</dt>
              <dd>{currency.format(rate.price_usd)}</dd></div>
          ))}</dl>
          <p className="quote-muted">参考运价有效期至 {activeLane.valid_until}。费用明细按贸易条款试算。</p>
        </> : <p className="quote-muted">正在加载演示运价。</p>}
        {rules.length > 0 && <div className="quote-rules">
          <h3>审批规则</h3>
          <ul>{rules.map((rule) => <li key={rule.key}>{rule.label}<strong>{rule.value} {rule.unit}</strong></li>)}</ul>
        </div>}
      </section>}
    </div>
    <section className="quote-panel quote-records" aria-labelledby="quote-records-title">
      <div className="quote-records-heading">
        <h2 id="quote-records-title">报价与理赔记录</h2>
        <div className="quote-record-actions">
          {requestedTicket && <Link className="quote-button" to="/quotes">显示全部</Link>}
          <button type="button" className="quote-button" onClick={() => void refresh().catch(() => setError('报价记录刷新失败'))}>刷新</button>
        </div>
      </div>
      {!records.length && <p className="quote-muted">暂无记录。</p>}
      {records.map((entry) => <article className="quote-record" key={entry.id}>
        <div className="quote-record-main">
          <span className="quote-record-id">{entry.id}</span>
          <h3>{entry.kind === 'quote' ? `${entry.route.origin} → ${entry.route.destination} · ${entry.request.goods}` : `货损理赔 · ${entry.hbl}`}</h3>
          {entry.ticket_id && <Link to={`/workbench?ticket=${encodeURIComponent(entry.ticket_id)}`}>工单 {entry.ticket_id}</Link>}
          {entry.internal.reasons.length > 0 && <p className="quote-muted">{entry.internal.reasons.join('、')}</p>}
          {entry.approval_reason && <p className="quote-muted">{entry.approval_reason}</p>}
        </div>
        <strong>{currency.format(entry.amount_usd)}</strong>
        <span className={`quote-status quote-status-${entry.status}`}>{statuses[entry.status]}</span>
        <div className="quote-record-actions">
          {entry.status === 'draft' && entry.confirm_in_quotes && <button type="button"
            className="quote-button quote-primary" disabled={!!busy} onClick={() => void act(entry, 'confirm')}>员工确认</button>}
          {entry.status === 'draft' && !entry.confirm_in_quotes && entry.ticket_id && (
            <Link className="quote-button" to={`/workbench?ticket=${encodeURIComponent(entry.ticket_id)}`}>到工单确认</Link>
          )}
          {entry.status === 'pending_approval' && <>
            <button type="button" className="quote-button quote-primary" disabled={!!busy} onClick={() => void act(entry, 'approved')}>经理批准</button>
            <button type="button" className="quote-button" disabled={!!busy} onClick={() => void act(entry, 'rejected')}>驳回</button>
          </>}
          {entry.status === 'formal' && <a className="quote-button" href={`/api/quotes/${encodeURIComponent(entry.id)}/pdf`}>下载报价 PDF</a>}
        </div>
      </article>)}
    </section>
  </main>
}
