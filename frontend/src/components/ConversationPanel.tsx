import { useLayoutEffect, useRef } from 'react'
import type { Message } from '../api/types'
import { DraftCard } from './DraftCard'
import { MessageText } from './MessageText'

type Props = {
  messages: Message[]
  onConfirm: (messageId: number, text?: string) => Promise<void>
  onRegenerate: (messageId: number) => Promise<void>
}

const messageTime = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Seoul', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
})

export function ConversationPanel({ messages, onConfirm, onRegenerate }: Props) {
  const panel = useRef<HTMLDivElement>(null)
  const nearEnd = useRef(true)
  const last = messages.at(-1)

  useLayoutEffect(() => {
    const element = panel.current
    if (element && nearEnd.current) element.scrollTop = element.scrollHeight
  }, [last?.id, last?.status])

  return <div className="wb-conversation" ref={panel} onScroll={(event) => {
    const { scrollHeight, clientHeight, scrollTop } = event.currentTarget
    nearEnd.current = scrollHeight - clientHeight - scrollTop < 80
  }}>
    {messages.length ? (
      <ul className="wb-messages" aria-label="工单消息">
        {messages.map((message) => <li className={`wb-message wb-message-${message.role}`} key={message.id}>
          {message.status === 'draft' ? (
            <DraftCard
              message={message}
              onConfirm={(text) => onConfirm(message.id, text)}
              onRegenerate={() => onRegenerate(message.id)}
            />
          ) : message.status === 'discarded' ? (
            <details className="wb-discarded">
              <summary>已作废的草稿</summary>
              <p className="wb-message-text">{message.text}</p>
            </details>
          ) : message.kind === 'ack' ? (
            <p className="wb-ack">系统已自动回复：{message.text}</p>
          ) : (
            <>
              <div className="wb-message-meta">
                <span>{message.role === 'customer' ? '客户' : '客服'}</span>
                <time dateTime={message.created_at}>{messageTime.format(new Date(message.created_at))}</time>
                {message.edited && <span>员工已修改</span>}
              </div>
              <MessageText role={message.role} text={message.text} className="wb-bubble wb-message-text" />
            </>
          )}
        </li>)}
      </ul>
    ) : <p className="wb-placeholder">此工单暂无消息</p>}
  </div>
}
