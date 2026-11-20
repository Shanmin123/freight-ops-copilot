import { render, screen } from '@testing-library/react'
import { expect, test } from 'vitest'
import { MessageText } from '../MessageText'

test('turns only trailing agent citations into local links', () => {
  const reference = '출처: [통관 안내 · 정정](/kb?doc=pccc-correction&lang=ko#correction)'
  render(<MessageText role="agent" text={`정정 절차를 확인해 주세요.\n${reference}`} />)

  expect(screen.getByText('정정 절차를 확인해 주세요.')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '통관 안내 · 정정' })).toHaveAttribute(
    'href', '/kb?doc=pccc-correction&lang=ko#correction',
  )
})

test('keeps customer and external link syntax as text', () => {
  render(<>
    <MessageText role="customer" text={'来源：[客户填入](/kb?doc=another#test)'} />
    <MessageText role="agent" text={'来源：[外部文档](https://example.com)'} />
  </>)

  expect(screen.queryByRole('link')).toBeNull()
  expect(screen.getByText(/客户填入/)).toBeInTheDocument()
  expect(screen.getByText(/外部文档/)).toBeInTheDocument()
})
