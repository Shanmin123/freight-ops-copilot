import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test } from 'vitest'
import { mockFetch } from '../../test/fetchMock'
import KnowledgePage from '../KnowledgePage'

const article = {
  id: 'pccc-correction',
  title: { zh: '通关号码更正', ko: '통관부호 정정' },
  sections: [{
    id: 'correction',
    title: { zh: '更正流程', ko: '정정 절차' },
    text: { zh: '联系报关行。', ko: '관세사에게 문의하세요.' },
    source: { label: 'Korea Customs Service', url: 'https://www.customs.go.kr/' },
  }],
}

test('opens a referenced section in Korean and exposes its original source', async () => {
  mockFetch({ '/api/kb/articles': { articles: [article] } })
  render(<MemoryRouter initialEntries={['/kb?doc=pccc-correction&lang=ko#correction']}><KnowledgePage /></MemoryRouter>)

  expect(await screen.findByRole('heading', { name: '정정 절차' })).toBeInTheDocument()
  expect(screen.getByText('관세사에게 문의하세요.')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Korea Customs Service' })).toHaveAttribute('href', 'https://www.customs.go.kr/')
})

test('searches from the catalog and opens a matching section', async () => {
  const [section] = article.sections
  mockFetch({
    '/api/kb/articles': { articles: [article] },
    '/api/kb/search': { hits: [{
      article_id: article.id, section_id: section.id, title: article.title,
      section_title: section.title, text: section.text, source: section.source,
    }] },
  })
  render(<MemoryRouter initialEntries={['/kb']}><KnowledgePage /></MemoryRouter>)

  const user = userEvent.setup()
  await user.type(await screen.findByRole('searchbox'), '更正号码')
  await user.click(screen.getByRole('button', { name: /^搜索$/ }))
  await user.click(await screen.findByRole('link', { name: /^更正流程$/ }))
  expect(await screen.findByRole('heading', { name: '更正流程' })).toBeInTheDocument()
})
