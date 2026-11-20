import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi } from 'vitest'
import DocumentsPage from '../DocumentsPage'

const report = {
  id: 'DOC-AAA000BBB111',
  ticket_id: null,
  documents: {
    invoice: { filename: 'invoice.jpg', method: 'image_ocr', fields: {} },
    packing: { filename: 'packing.pdf', method: 'pdf_text', fields: {} },
    bill: { filename: 'bill.pdf', method: 'pdf_text', fields: {} },
  },
  checks: [{ field: 'packages', label: '件数', values: { invoice: 40, packing: 42, bill: 40 }, status: 'mismatch' }],
  missing: [],
  requirements: [{ code: 'kc_radio', title: 'KC 无线设备合规', detail: '核对设备型号。',
    kb_url: '/kb?doc=import-documents#kc-radio', source_url: 'https://www.rra.go.kr/en/cas/intro.do' }],
  drafts: {
    correction: { status: 'draft', text: '请更正装箱单的件数。' },
    customs: { status: 'draft', text: '报关资料草稿' },
  },
}

test('shows real source links, a comparison, and confirms edited correction text', async () => {
  const fetchMock = vi.fn(async (url: string) => {
    if (url === '/api/tickets') return Response.json([])
    if (url === '/api/documents/samples') return Response.json({ samples: [], deepseek_available: false })
    if (url === '/api/documents/reviews') return Response.json({ reviews: [] })
    if (url === '/api/documents/demo') return Response.json(report)
    if (url.endsWith('/drafts/correction/confirm')) return Response.json({
      ...report,
      drafts: { ...report.drafts, correction: { status: 'confirmed', text: '请核对装箱单的件数。' } },
    })
    return Response.json({ detail: 'Unexpected route' }, { status: 404 })
  })
  vi.stubGlobal('fetch', fetchMock)
  render(<MemoryRouter><DocumentsPage /></MemoryRouter>)

  const user = userEvent.setup()
  await user.click(screen.getByRole('button', { name: '运行照片样例' }))
  expect(await screen.findByText('本地 OCR')).toBeInTheDocument()
  expect(within(screen.getByRole('row', { name: /件数/ })).getByText('42')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '知识库依据' })).toHaveAttribute('href', '/kb?doc=import-documents#kc-radio')
  const correction = screen.getByRole('region', { name: '请客户更正' })
  const input = within(correction).getByRole('textbox')
  await user.clear(input)
  await user.type(input, '请核对装箱单的件数。')
  await user.click(within(correction).getByRole('button', { name: '确认草稿' }))
  expect(await screen.findByText('已确认')).toBeInTheDocument()
  expect(fetchMock).toHaveBeenCalledWith('/api/documents/reviews/DOC-AAA000BBB111/drafts/correction/confirm', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: '请核对装箱单的件数。' }),
  })
})
