import type { TicketStatus } from '../api/types'
import { STATUS_LABEL } from '../labels'

export function StatusTag({ status }: { status: TicketStatus }) {
  return <span className={`status-tag status-tag--${status}`}>{STATUS_LABEL[status]}</span>
}
