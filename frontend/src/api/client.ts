import type {
  Channel, Conversation, Customer, CustomerSummary, Health, KnowledgeArticle, KnowledgeHit, Message, Settings,
  TicketDetail, TicketSummary,
} from './types'

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message)
    this.name = 'ApiError'
  }
}

export async function request<T = void>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const options: RequestInit = { method }
  if (body !== undefined) {
    options.headers = { 'Content-Type': 'application/json' }
    options.body = JSON.stringify(body)
  }
  const response = await fetch(path, options)
  if (!response.ok) {
    const data = await response.json().catch(() => null)
    const detail = typeof data?.detail === 'string' ? data.detail : `请求失败（${response.status}）`
    throw new ApiError(response.status, detail)
  }
  const data = await response.text()
  return (data ? JSON.parse(data) : undefined) as T
}

export const getHealth = () => request<Health>('/api/health')
export const getSettings = () => request<Settings>('/api/settings')
export const putSettings = (autoReply: boolean) =>
  request<Settings>('/api/settings', 'PUT', { auto_reply_readonly: autoReply })
export const getCustomers = () => request<Customer[]>('/api/customers')
export const getCustomerSummary = (id: string) =>
  request<CustomerSummary>(`/api/customers/${encodeURIComponent(id)}/summary`)
export const getTickets = () => request<TicketSummary[]>('/api/tickets')
export const getTicket = (id: string) => request<TicketDetail>(`/api/tickets/${encodeURIComponent(id)}`)
export const confirmDraft = (tid: string, mid: number, text?: string) =>
  request<Message>(`/api/tickets/${encodeURIComponent(tid)}/messages/${mid}/confirm`, 'POST', { text })
export const regenerateDraft = (tid: string, mid: number) =>
  request(`/api/tickets/${encodeURIComponent(tid)}/messages/${mid}/regenerate`, 'POST')
export const postChatMessage = (customerId: string, channel: Channel, text: string) =>
  request<{ ticket_id: string; message: Message }>('/api/chat/messages', 'POST', { customer_id: customerId, channel, text })
export const getConversation = (customerId: string, channel: Channel) =>
  request<Conversation>(`/api/chat/conversation?${new URLSearchParams({ customer_id: customerId, channel })}`)
export const resetDemo = () => request<{ ok: true }>('/api/demo/reset', 'POST')
export const getKnowledgeArticles = () => request<{ articles: KnowledgeArticle[] }>('/api/kb/articles')
export const searchKnowledge = (q: string) =>
  request<{ hits: KnowledgeHit[] }>(`/api/kb/search?${new URLSearchParams({ q })}`)
