import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, test, vi } from 'vitest'
import { msg } from '../../test/factories'
import { DraftCard } from '../DraftCard'

test('can confirm, edit and ask for a rewrite', async () => {
  const onConfirm = vi.fn().mockResolvedValue(undefined)
  const onRegenerate = vi.fn().mockResolvedValue(undefined)
  render(<DraftCard message={msg()} onConfirm={onConfirm} onRegenerate={onRegenerate} />)
  await userEvent.click(screen.getByRole('button', { name: '确认发送' }))
  expect(onConfirm).toHaveBeenCalledWith(undefined)
  await userEvent.click(screen.getByRole('button', { name: '修改' }))
  await userEvent.clear(screen.getByRole('textbox'))
  await userEvent.type(screen.getByRole('textbox'), '수정본')
  await userEvent.click(screen.getByRole('button', { name: '发送修改后的回复' }))
  expect(onConfirm).toHaveBeenLastCalledWith('수정본')
  await userEvent.click(screen.getByRole('button', { name: '退回 AI 重写' }))
  expect(onRegenerate).toHaveBeenCalledOnce()
})

test('keeps actions disabled until a request fails and shows the error', async () => {
  let rejectRequest!: (error: Error) => void
  const request = new Promise<void>((_, reject) => { rejectRequest = reject })
  render(<DraftCard message={msg()} onConfirm={() => request} onRegenerate={vi.fn()} />)
  await userEvent.click(screen.getByRole('button', { name: '确认发送' }))
  expect(screen.getByRole('button', { name: '修改' })).toBeDisabled()
  rejectRequest(new Error('草稿状态已变化'))
  expect(await screen.findByRole('alert')).toHaveTextContent('草稿状态已变化')
  expect(screen.getByRole('button', { name: '修改' })).toBeEnabled()
})
