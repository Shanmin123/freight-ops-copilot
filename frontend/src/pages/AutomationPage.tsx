import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { getCustomers, request } from '../api/client'
import type { Customer } from '../api/types'
import './automation.css'

type Transport = 'local' | 'n8n'

interface MailRecord {
  id: string
  ticket_id: string | null
  customer_id: string
  sender: string
  subject: string
  status: string
  intent: string
  transport: Transport
  elapsed_ms: number
  created_at: string
  approved_at: string | null
  sent_at: string | null
  delivery_error: string | null
}

interface MailHistory {
  records: MailRecord[]
  outbox: { mail_id: string; ticket_id: string; recipient: string; subject: string; body: string; created_at: string }[]
  customer_log: { mail_id: string; customer_id: string; ticket_id: string; created_at: string }[]
}

const blank: MailHistory = { records: [], outbox: [], customer_log: [] }
const intentLabels: Record<string, string> = {
  quote: '询价', claim: '理赔', customs: '清关', warehouse: '仓库', tracking: '查货', general: '一般咨询',
}
const statusLabels: Record<string, string> = {
  processing: '草稿生成中', awaiting_confirmation: '待员工确认', awaiting_delivery: '等待工作流回写',
  sent: '已写入发件箱', needs_human: '需人工接手', replied: '已确认', ai_replied: '已回复',
}
const time = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Seoul', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })

function errorMessage(error: unknown, defaultMessage: string) {
  return error instanceof Error ? error.message : defaultMessage
}

function duration(ms: number) {
  const seconds = Math.floor(ms / 1000)
  return seconds < 60 ? `${seconds} 秒` : `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`
}

export default function AutomationPage() {
  const [history, setHistory] = useState<MailHistory>()
  const [customers, setCustomers] = useState<Customer[]>([])
  const [customerId, setCustomerId] = useState('')
  const [transport, setTransport] = useState<Transport>('local')
  const [sender, setSender] = useState('sample@example.invalid')
  const [subject, setSubject] = useState('咨询清关进度')
  const [body, setBody] = useState('您好，请问 FWQD2611043 的清关进度怎么样，为什么还在暂扣？')
  const [error, setError] = useState('')
  const [historyError, setHistoryError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [lastJob, setLastJob] = useState<string>()
  const [retrying, setRetrying] = useState<string>()
  const messageId = useRef('')

  useEffect(() => {
    let active = true
    getCustomers().then((items) => {
      if (!active) return
      setCustomers(items)
      setCustomerId(items.find((item) => item.id === 'C-KR-0412')?.id ?? items[0]?.id ?? '')
    }).catch(() => { if (active) setError('无法读取客户资料。') })
    async function refresh() {
      try {
        const data = await request<MailHistory>('/api/automation')
        if (active) { setHistory(data); setHistoryError('') }
      } catch {
        if (active) setHistoryError('无法读取邮件流程记录。')
      }
    }
    void refresh()
    const interval = window.setInterval(() => { void refresh() }, 2000)
    return () => { active = false; window.clearInterval(interval) }
  }, [])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!customerId || submitting) return
    setSubmitting(true)
    setError('')
    messageId.current ||= crypto.randomUUID()
    try {
      const job = await request<MailRecord>('/api/automation/test-email', 'POST', {
        customer_id: customerId, sender: sender.trim(), subject: subject.trim(), text: body.trim(),
        transport, message_id: messageId.current,
      })
      messageId.current = ''
      setLastJob(job.id)
      setHistory(await request<MailHistory>('/api/automation'))
    } catch (problem) {
      setError(errorMessage(problem, '未能登记来信。'))
    } finally {
      setSubmitting(false)
    }
  }

  async function retry(id: string) {
    setRetrying(id)
    setError('')
    try {
      await request<MailRecord>(`/api/automation/${encodeURIComponent(id)}/retry`, 'POST')
      setHistory(await request<MailHistory>('/api/automation'))
    } catch (problem) {
      setError(errorMessage(problem, '无法恢复邮件流程。'))
    } finally {
      setRetrying(undefined)
    }
  }

  const data = history ?? blank
  const focused = data.records.find((item) => item.id === lastJob) ?? data.records[0]
  const currentStep = !focused ? -1 : focused.sent_at ? 3 : focused.approved_at ? 2
    : focused.status === 'awaiting_confirmation' ? 1 : 0

  return (
    <main className="automation-page">
      <header className="automation-header">
        <div>
          <h1>邮件自动化</h1>
          <p>把来信转成工单，员工确认后记录回信和客户跟进。</p>
        </div>
        <Link className="automation-quiet" to="/workbench">打开工作台 <span aria-hidden="true">↗</span></Link>
      </header>
      <ol className="automation-flow" aria-label="邮件流程">
        {['登记来信', '生成草稿', '员工确认', '记录回信'].map((step, index) => (
          <li key={step} data-complete={index <= currentStep}>
            <span className="automation-step-icon" aria-hidden="true">{index <= currentStep ? '✓' : '○'}</span>
            <span>{step}</span>
          </li>
        ))}
      </ol>
      <div className="automation-grid">
        <section className="automation-panel" aria-labelledby="automation-form-title">
          <h2 id="automation-form-title">模拟来信</h2>
          <form className="automation-form" onSubmit={submit} onChange={() => { messageId.current = '' }}>
            <div className="automation-fields">
              <label>客户
                <select value={customerId} onChange={(event) => setCustomerId(event.target.value)} required>
                  {!customers.length && <option value="">加载客户…</option>}
                  {customers.map((customer) => <option key={customer.id} value={customer.id}>{customer.name}</option>)}
                </select>
              </label>
              <label>运行方式
                <select value={transport} onChange={(event) => setTransport(event.target.value as Transport)}>
                  <option value="local">本地模拟</option>
                  <option value="n8n">n8n 工作流</option>
                </select>
              </label>
            </div>
            <label>发件人
              <input type="email" value={sender} onChange={(event) => setSender(event.target.value)} required maxLength={254} />
            </label>
            <label>主题
              <input value={subject} onChange={(event) => setSubject(event.target.value)} required maxLength={180} />
            </label>
            <label>正文
              <textarea rows={5} value={body} onChange={(event) => setBody(event.target.value)} required maxLength={4000} />
            </label>
            <button className="automation-primary" type="submit" disabled={submitting || !customerId}>
              {submitting ? '正在登记…' : '发送测试邮件'}
            </button>
            {error && <p className="automation-error" role="alert">{error}</p>}
          </form>
        </section>

        <section className="automation-panel" aria-labelledby="automation-record-title">
          <h2 id="automation-record-title">邮件处理记录 <span>{data.records.length}</span></h2>
          {historyError && <p className="automation-error" role="alert">{historyError}</p>}
          {data.records.length === 0 && <p className="automation-empty">{history ? '暂无处理记录。' : '正在加载…'}</p>}
          <ul className="automation-records">
            {data.records.map((record) => (
              <li key={record.id}>
                <div className="automation-record-head">
                  <strong>{record.subject}</strong>
                  <span className="automation-status" data-status={record.status}>{statusLabels[record.status] ?? record.status}</span>
                </div>
                <p>{record.sender} · {intentLabels[record.intent] ?? record.intent} · {record.transport === 'n8n' ? 'n8n' : '本地模拟'}</p>
                <div className="automation-record-links">
                  {record.ticket_id && <Link to={`/workbench?ticket=${encodeURIComponent(record.ticket_id)}`}>{record.ticket_id} ↗</Link>}
                  <span>耗时 {duration(record.elapsed_ms)}</span>
                </div>
                {record.delivery_error && (
                  <div className="automation-retry">
                    <p>{record.delivery_error}</p>
                    <button type="button" className="automation-quiet" onClick={() => { void retry(record.id) }} disabled={retrying === record.id}>重试</button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        </section>
      </div>

      <div className="automation-grid automation-bottom">
        <section className="automation-panel" aria-labelledby="automation-outbox-title">
          <h2 id="automation-outbox-title">模拟发件箱 <span>{data.outbox.length}</span></h2>
          {data.outbox.length === 0 && <p className="automation-empty">员工确认后显示回信。</p>}
          {data.outbox.map((item) => (
            <details key={item.mail_id} className="automation-reply">
              <summary><strong>{item.subject}</strong><span>{item.recipient}</span></summary>
              <p>{item.body}</p>
            </details>
          ))}
        </section>
        <section className="automation-panel" aria-labelledby="automation-log-title">
          <h2 id="automation-log-title">客户记录 <span>{data.customer_log.length}</span></h2>
          {data.customer_log.length === 0 && <p className="automation-empty">暂无客户跟进记录。</p>}
          <ul className="automation-records">
            {data.customer_log.map((item) => <li key={item.mail_id}>
              <div className="automation-record-head"><strong>{customers.find((customer) => customer.id === item.customer_id)?.name ?? item.customer_id}</strong><time dateTime={item.created_at}>{time.format(new Date(item.created_at))}</time></div>
              <p>记录模拟回信 · {item.ticket_id}</p>
            </li>)}
          </ul>
        </section>
      </div>
    </main>
  )
}
