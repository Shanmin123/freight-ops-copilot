import type { Message, RunStep, TicketSummary } from '../api/types'

export function summary(overrides: Partial<TicketSummary> = {}): TicketSummary {
  return {
    id: 'T-001042', customer_id: 'C-KR-0412', customer_name: '김지우',
    channel: 'kakaotalk', subject: '통관 문의', status: 'awaiting_confirmation',
    updated_at: '2026-11-19T12:00:00+09:00', preview: '통관 문의', ...overrides,
  }
}

export function msg(overrides: Partial<Message> = {}): Message {
  return {
    id: 1, ticket_id: 'T-001042', role: 'agent', kind: 'normal', text: '제출했습니다.',
    status: 'draft', run_id: 'R-1', edited: false,
    created_at: '2026-11-19T12:00:00+09:00', sent_at: null, ...overrides,
  }
}

export function toolStep(index: number, name: string, observation: { ok: boolean; error?: string; note?: string }, kind: 'read' | 'write'): RunStep {
  return { index, kind: 'tool', data: { call_id: `call-${index}`, name, arguments: {}, kind, observation } }
}
