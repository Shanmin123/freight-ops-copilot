import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, test, vi } from 'vitest'
import { summary } from '../../test/factories'
import { InboxList } from '../InboxList'

test('shows status and channel labels and selects a ticket', async () => {
  const onSelect = vi.fn()
  render(<InboxList tickets={[summary()]} selectedId={null} onSelect={onSelect} />)
  expect(screen.getByText('#T-001042')).toBeInTheDocument()
  expect(screen.getByText('待你确认')).toBeInTheDocument()
  expect(screen.getByText('KakaoTalk')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button'))
  expect(onSelect).toHaveBeenCalledWith('T-001042')
})
