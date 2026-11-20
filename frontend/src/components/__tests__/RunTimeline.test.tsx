import { render, screen } from '@testing-library/react'
import { expect, test } from 'vitest'
import { toolStep } from '../../test/factories'
import { RunTimeline } from '../RunTimeline'

test('marks staged writes and errors', () => {
  render(<RunTimeline run={null} liveSteps={[
    toolStep(0, 'customs.submit_pccc', { ok: true, note: '该操作将在员工确认回复后生效' }, 'write'),
    toolStep(1, 'shipment.get', { ok: false, error: 'not_found' }, 'read'),
  ]} />)
  expect(screen.getByText('暂存 · 确认后生效')).toBeInTheDocument()
  expect(screen.getByText(/not_found/)).toBeInTheDocument()
})
