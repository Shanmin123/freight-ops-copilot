import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi } from 'vitest'
import AutomationPage from '../AutomationPage'

test('registers a test email in n8n and points staff to its waiting ticket', async () => {
  const job = {
    id: 'M-0123', ticket_id: 'T-001042', customer_id: 'C-KR-0412', sender: 'sample@example.invalid',
    subject: '咨询清关进度', status: 'awaiting_confirmation', intent: 'customs', transport: 'n8n',
    elapsed_ms: 800, created_at: '2026-11-20T12:00:00+09:00',
    approved_at: null, sent_at: null, delivery_error: null,
  }
  let submitted = false
  vi.stubGlobal('fetch', vi.fn(async (path: string, request?: RequestInit) => {
    if (path === '/api/customers') return Response.json([{ id: 'C-KR-0412', name: '김지우' }])
    if (path === '/api/automation/test-email') {
      expect(request?.method).toBe('POST')
      expect(JSON.parse(String(request?.body))).toMatchObject({ transport: 'n8n', customer_id: 'C-KR-0412' })
      submitted = true
      return Response.json(job, { status: 201 })
    }
    return Response.json({ records: submitted ? [job] : [], customer_log: [], outbox: [] })
  }))

  render(<MemoryRouter><AutomationPage /></MemoryRouter>)
  const user = userEvent.setup()
  await screen.findByRole('option', { name: '김지우' })
  await user.selectOptions(screen.getByLabelText('运行方式'), 'n8n')
  await user.click(screen.getByRole('button', { name: '发送测试邮件' }))
  expect(await screen.findByText('待员工确认')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /T-001042/ })).toHaveAttribute('href', '/workbench?ticket=T-001042')
  const outbox = screen.getByRole('region', { name: /模拟发件箱/ })
  expect(within(outbox).getByText('员工确认后显示回信。')).toBeInTheDocument()
})
