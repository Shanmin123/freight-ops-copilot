import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test } from 'vitest'
import { lastRequest, mockFetch } from '../../test/fetchMock'
import { AppHeader } from '../AppHeader'

test('shows the DeepSeek model and toggles auto reply', async () => {
  mockFetch({
    '/api/health': {
      status: 'ok', llm: { provider: 'deepseek', model: 'deepseek-flash', thinking: false },
      company: '货代', last_seq: 0,
    },
    '/api/settings': { auto_reply_readonly: true },
    'PUT /api/settings': { auto_reply_readonly: false },
  })
  render(<MemoryRouter><AppHeader /></MemoryRouter>)
  expect(await screen.findByText('DeepSeek · deepseek-flash')).toBeInTheDocument()
  expect(screen.getByText('货代 AI 客服中心')).toBeInTheDocument()
  const toggle = screen.getByRole('switch', { name: '查询类自动回复' })
  await waitFor(() => expect(toggle).toBeEnabled())
  await userEvent.click(toggle)
  expect(lastRequest('PUT', '/api/settings')).toEqual({ auto_reply_readonly: false })
  expect(toggle).toHaveAttribute('aria-checked', 'false')
})
