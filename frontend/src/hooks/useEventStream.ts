import { useEffect, useLayoutEffect, useRef } from 'react'
import type { AppEvent } from '../api/types'

export const EVENT_TYPES = [
  'ticket.updated', 'ticket.typing', 'message.created', 'message.updated',
  'run.started', 'run.step', 'run.finished', 'demo.reset',
] as const

export function useEventStream(url: string | null, onEvent: (event: AppEvent) => void) {
  const handler = useRef(onEvent)
  useLayoutEffect(() => { handler.current = onEvent }, [onEvent])

  useEffect(() => {
    if (!url) return
    const source = new EventSource(url)
    const receive = (event: Event) => handler.current(JSON.parse((event as MessageEvent<string>).data) as AppEvent)
    for (const type of EVENT_TYPES) source.addEventListener(type, receive)
    return () => {
      for (const type of EVENT_TYPES) source.removeEventListener(type, receive)
      source.close()
    }
  }, [url])
}
