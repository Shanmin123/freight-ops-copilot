import { useCallback, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { confirmDraft, getHealth, getTicket, getTickets, regenerateDraft } from '../api/client'
import type { AppEvent, RunStep, TicketSummary } from '../api/types'
import { ContextPanel } from '../components/ContextPanel'
import { ConversationPanel } from '../components/ConversationPanel'
import { InboxList } from '../components/InboxList'
import { RunTimeline } from '../components/RunTimeline'
import { StatusTag } from '../components/StatusTag'
import { useEventStream } from '../hooks/useEventStream'
import { CHANNEL_LABEL } from '../labels'
import { TicketQuoteApprovals } from './QuotesPage'
import './workbench.css'

type TicketDetail = Awaited<ReturnType<typeof getTicket>>
type LiveRun = { id: string, steps: RunStep[] }

export default function WorkbenchPage() {
  const [params] = useSearchParams()
  const requestedTicket = params.get('ticket')
  const [tickets, setTickets] = useState<TicketSummary[] | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(requestedTicket)
  const [detail, setDetail] = useState<TicketDetail | null>(null)
  const [detailVersion, setDetailVersion] = useState(0)
  const [contextVersion, setContextVersion] = useState(0)
  const [liveRuns, setLiveRuns] = useState<Record<string, LiveRun>>({})
  const [since, setSince] = useState<number | null>(null)
  const [inboxError, setInboxError] = useState(false)
  const [detailError, setDetailError] = useState(false)
  const listTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const listRequest = useRef(0)

  useEffect(() => {
    if (requestedTicket) setSelectedId(requestedTicket)
  }, [requestedTicket])

  const loadInbox = useCallback(async (initial = false) => {
    const request = ++listRequest.current
    try {
      // Capture the replay cursor before loading the snapshot so events during the fetch can replay.
      const health = initial ? await getHealth() : null
      const latest = await getTickets()
      if (request !== listRequest.current) return
      setTickets(latest)
      setSelectedId((current) => latest.some((ticket) => ticket.id === current) ? current : latest[0]?.id ?? null)
      setInboxError(false)
      if (health) setSince(health.last_seq)
    } catch {
      if (request === listRequest.current) setInboxError(true)
    }
  }, [])

  useEffect(() => {
    void loadInbox(true)
    return () => {
      listRequest.current++
      if (listTimer.current !== null) clearTimeout(listTimer.current)
      listTimer.current = null
    }
  }, [loadInbox])

  useEffect(() => {
    if (!selectedId) return
    let active = true
    void getTicket(selectedId).then((latest) => {
      if (active) {
        setDetail(latest)
        setDetailError(false)
      }
    }).catch(() => { if (active) setDetailError(true) })
    return () => { active = false }
  }, [selectedId, detailVersion])

  function selectTicket(id: string) {
    setDetailError(false)
    setSelectedId(id)
  }

  function scheduleInbox() {
    if (listTimer.current !== null) return
    listTimer.current = setTimeout(() => {
      listTimer.current = null
      void loadInbox()
    }, 300)
  }

  function handleEvent(event: AppEvent) {
    scheduleInbox()
    if (event.type === 'demo.reset') {
      listRequest.current++
      setTickets(null)
      setSelectedId(null)
      setDetail(null)
      setDetailError(false)
      setLiveRuns({})
      setContextVersion((version) => version + 1)
      return
    }

    const ticketId = event.ticket_id
    if (!ticketId) return
    const runId = event.payload.run_id
    const step = event.payload.step

    if (event.type === 'run.started' && typeof runId === 'string') {
      setLiveRuns((current) => ({ ...current, [ticketId]: { id: runId, steps: [] } }))
    }
    if (event.type === 'run.step' && typeof runId === 'string' && step) {
      setLiveRuns((current) => {
        const previous = current[ticketId]
        const steps = previous?.id === runId ? previous.steps : []
        if (steps.some((saved) => saved.index === step.index)) return current
        return { ...current, [ticketId]: { id: runId, steps: [...steps, step] } }
      })
    }
    if (event.type === 'run.finished') {
      setLiveRuns((current) => {
        if (current[ticketId]?.id !== runId) return current
        const latest = { ...current }
        delete latest[ticketId]
        return latest
      })
    }
    if (ticketId === selectedId) {
      setDetailVersion((version) => version + 1)
      if (event.type === 'run.finished' || event.type === 'message.updated') {
        setContextVersion((version) => version + 1)
      }
    }
  }

  useEventStream(since === null ? null : `/api/events?since=${since}`, handleEvent)

  async function confirm(messageId: number, text?: string) {
    if (!selectedId) return
    try {
      await confirmDraft(selectedId, messageId, text)
    } finally {
      // Refresh after a conflict too, so staff see the latest pending draft.
      setDetailVersion((version) => version + 1)
      setContextVersion((version) => version + 1)
      scheduleInbox()
    }
  }

  async function regenerate(messageId: number) {
    if (!selectedId) return
    try {
      await regenerateDraft(selectedId, messageId)
    } finally {
      setDetailVersion((version) => version + 1)
      scheduleInbox()
    }
  }

  const currentDetail = detail?.ticket.id === selectedId ? detail : null
  const selected = tickets?.find((ticket) => ticket.id === selectedId)
  const ticket = currentDetail?.ticket ?? selected
  const customerId = ticket?.customer_id ?? null
  const latestRun = currentDetail?.runs.at(-1) ?? null
  const liveSteps = selectedId ? liveRuns[selectedId]?.steps ?? [] : []

  return <div className="workbench">
    <h1>客服工作台</h1>
    <div className="wb-grid">
      <section className="wb-panel wb-inbox-panel" aria-labelledby="wb-inbox-heading">
        <header className="wb-panel-header">
          <h2 id="wb-inbox-heading">多渠道收件箱</h2>
          {tickets && <span className="wb-count">{tickets.length} 个工单</span>}
        </header>
        <div className="wb-inbox-scroll">
          {inboxError && <div className="wb-load-failure">
            <p role="alert" className="wb-error">工单列表加载失败。</p>
            <button type="button" className="wb-action" onClick={() => void loadInbox(since === null)}>重试</button>
          </div>}
          {tickets ? <InboxList tickets={tickets} selectedId={selectedId} onSelect={selectTicket} /> :
            !inboxError && <p className="wb-placeholder">正在加载工单…</p>}
        </div>
      </section>
      <section className="wb-panel wb-dialog-panel" aria-labelledby="wb-dialog-heading">
        <header className="wb-panel-header wb-dialog-header">
          <div>
            {ticket && <p className="wb-dialog-meta"><span className="wb-id">#{ticket.id}</span><span>{CHANNEL_LABEL[ticket.channel]}</span></p>}
            <h2 id="wb-dialog-heading">{ticket?.subject ?? '客户对话'}</h2>
          </div>
          {ticket && <StatusTag status={ticket.status} />}
        </header>
        {detailError && <div className="wb-load-failure">
          <p className="wb-error" role="alert">工单详情加载失败。</p>
          <button type="button" className="wb-action" onClick={() => setDetailVersion((version) => version + 1)}>重试</button>
        </div>}
        {currentDetail ? (
          <ConversationPanel key={selectedId} messages={currentDetail.messages} onConfirm={confirm} onRegenerate={regenerate} />
        ) : !detailError && <p className="wb-placeholder">{selectedId ? '正在加载对话…' : '从收件箱选择一条工单'}</p>}
      </section>
      <aside className="wb-panel wb-side-panel" aria-label="业务详情">
        <ContextPanel key={customerId} customerId={customerId} refreshKey={contextVersion} />
        <TicketQuoteApprovals ticketId={selectedId} refreshKey={detailVersion} />
        <RunTimeline run={latestRun} liveSteps={liveSteps} />
        {currentDetail?.quality && <section className="wb-audit">
          <h2>自动质检 <span>{currentDetail.quality.points} / 100</span></h2>
          {currentDetail.quality.problems.length > 0 && <ul>
            {currentDetail.quality.problems.map((problem) => <li key={problem}>{problem}</li>)}
          </ul>}
        </section>}
      </aside>
    </div>
  </div>
}
