import type { TicketSummary } from '../api/types'
import { CHANNEL_LABEL } from '../labels'
import { StatusTag } from './StatusTag'

type Props = {
  tickets: TicketSummary[]
  selectedId: string | null
  onSelect: (id: string) => void
}

export function InboxList({ tickets, selectedId, onSelect }: Props) {
  if (!tickets.length) return <p className="wb-placeholder">暂无工单</p>

  return <ul className="wb-inbox">
    {tickets.map((ticket) => (
      <li key={ticket.id}>
        <button
          type="button" className="wb-ticket" aria-current={selectedId === ticket.id ? 'true' : undefined}
          onClick={() => onSelect(ticket.id)}
        >
          <span className="wb-ticket-meta">
            <span>{ticket.customer_name}</span>
            <span className="wb-id">#{ticket.id}</span>
          </span>
          <strong className="wb-ticket-subject">{ticket.subject}</strong>
          {ticket.preview && ticket.preview !== ticket.subject && (
            <span className="wb-ticket-preview">{ticket.preview}</span>
          )}
          <span className="wb-ticket-tags">
            <span className="wb-channel">{CHANNEL_LABEL[ticket.channel]}</span>
            <StatusTag status={ticket.status} />
          </span>
        </button>
      </li>
    ))}
  </ul>
}
