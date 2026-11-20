export type Channel = 'web' | 'wechat' | 'kakaotalk' | 'email' | 'web_form'
export type TicketStatus = 'processing' | 'ai_replied' | 'awaiting_confirmation' | 'replied' | 'needs_human'

export interface Ticket {
  id: string
  customer_id: string
  channel: Channel
  subject: string
  status: TicketStatus
  created_at: string
  updated_at: string
}

export type TicketSummary = Omit<Ticket, 'created_at'> & { customer_name: string; preview: string }

export interface Message {
  id: number
  ticket_id: string
  role: 'customer' | 'agent'
  kind: 'normal' | 'ack'
  text: string
  status: 'sent' | 'draft' | 'discarded'
  run_id: string | null
  edited: boolean
  created_at: string
  sent_at: string | null
}

type Usage = Record<string, number>
type Observation = { ok: boolean; data?: unknown; error?: string | null; note?: string | null }
export type RunStep =
  | { index: number; kind: 'model'; data: { content: string | null; tool_calls: unknown[]; usage: Usage } }
  | { index: number; kind: 'tool'; data: { call_id: string; name: string; arguments: unknown; kind: 'read' | 'write'; observation: Observation } }

export interface RunRecord {
  id: string
  ticket_id: string
  status: 'running' | 'finished'
  outcome: string | null
  decision: 'auto_sent' | 'draft' | 'none' | null
  model: string
  steps: RunStep[]
  usage: Usage
  write_calls: string[]
  error: string | null
  started_at: string
  finished_at: string | null
}

export interface Customer {
  id: string
  name: string
  type: string
  language: 'zh' | 'ko'
}

export interface CustomerProfile extends Customer {
  settlement: string
  tier: string | null
  credit_limit_usd: number | null
  credit_used_usd: number | null
}

export interface Shipment {
  delivery_reroute?: { status: string; pending: boolean }
  carrier_investigation?: { status: string; pending: boolean }
  hbl: string
  mode: string
  status: string
  origin: { name_zh: string }
  destination: { name_zh: string }
  customs: {
    hold: { status: string; code: string; reason_zh?: string; reason_ko?: string } | null
  } | null
}

export interface CustomerSummary {
  customer: CustomerProfile
  shipments: Shipment[]
  warehouse_jobs: Record<string, unknown>[]
}

export interface TicketDetail {
  quality?: { points: number; problems: string[] } | null
  ticket: Ticket
  customer: CustomerProfile | null
  messages: Message[]
  runs: RunRecord[]
  last_seq: number
}

export interface Conversation {
  ticket_id: string | null
  messages: Message[]
  last_seq: number
}

export interface Health {
  status: 'ok'
  llm: { provider: 'mock' | 'deepseek'; model: string; thinking: boolean; fallback_reason: string | null }
  company: string
  last_seq: number
}

export interface Settings {
  auto_reply_readonly: boolean
}

export interface AppEvent {
  seq: number
  type: string
  ticket_id: string | null
  payload: {
    message?: Message
    ticket?: Ticket
    typing?: boolean
    run_id?: string
    step?: RunStep
  }
  at: string
}

export type KnowledgeText = Record<'zh' | 'ko', string>

export interface KnowledgeSource {
  label: string
  url: string
}

export interface KnowledgeSection {
  id: string
  title: KnowledgeText
  text: KnowledgeText
  source: KnowledgeSource | null
}

export interface KnowledgeArticle {
  reviewed_on?: string
  id: string
  title: KnowledgeText
  sections: KnowledgeSection[]
}

export interface KnowledgeHit {
  article_id: string
  section_id: string
  title: KnowledgeText
  section_title: KnowledgeText
  text: KnowledgeText
  source: KnowledgeSource | null
}
