import { useId, useState } from 'react'
import type { Message } from '../api/types'
import { MessageText } from './MessageText'

type Props = {
  message: Message
  onConfirm: (text?: string) => Promise<void>
  onRegenerate: () => Promise<void>
}

export function DraftCard({ message, onConfirm, onRegenerate }: Props) {
  const inputId = useId()
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(message.text)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')

  async function submit(action: () => Promise<void>) {
    setPending(true)
    setError('')
    try {
      await action()
      setEditing(false)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '操作失败，请重试。')
    } finally {
      setPending(false)
    }
  }

  return <section className="wb-draft" aria-busy={pending}>
    <h3>AI 草稿 · 发送前需要你确认</h3>
    {editing ? (
      <>
        <label className="wb-edit-label" htmlFor={inputId}>修改回复</label>
        <textarea id={inputId} autoFocus rows={5} value={text} disabled={pending} onChange={(event) => setText(event.target.value)} />
      </>
    ) : <MessageText role={message.role} text={message.text} className="wb-message-text" />}
    {error && <p className="wb-error" role="alert">{error}</p>}
    <div className="wb-draft-actions">
      {editing ? (
        <>
          <button type="button" className="wb-action wb-action-primary" disabled={pending || !text.trim()}
            onClick={() => void submit(() => onConfirm(text.trim()))}>发送修改后的回复</button>
          <button type="button" className="wb-action" disabled={pending} onClick={() => setEditing(false)}>取消</button>
        </>
      ) : (
        <>
          <button type="button" className="wb-action wb-action-primary" disabled={pending}
            onClick={() => void submit(() => onConfirm(undefined))}>确认发送</button>
          <button type="button" className="wb-action" disabled={pending}
            onClick={() => { setText(message.text); setEditing(true) }}>修改</button>
        </>
      )}
      <button type="button" className="wb-action" disabled={pending}
        onClick={() => void submit(onRegenerate)}>退回 AI 重写</button>
    </div>
  </section>
}
