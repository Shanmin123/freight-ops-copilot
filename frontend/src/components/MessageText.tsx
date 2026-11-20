import type { Message } from '../api/types'

const knowledgeReference = /^(?:来源[：:]|출처[：:])\s*\[([^\[\]\r\n]+)\]\((\/kb\?doc=[a-z0-9-]+(?:&lang=ko)?#[a-z0-9-]+)\)$/u

type Props = Pick<Message, 'role' | 'text'> & { className?: string }

export function MessageText({ role, text, className }: Props) {
  if (role !== 'agent') return <p className={className}>{text}</p>

  const lines = text.trimEnd().split(/\r?\n/)
  const sources: { label: string; href: string }[] = []
  while (lines.length) {
    const match = knowledgeReference.exec(lines.at(-1) ?? '')
    if (!match) break
    sources.unshift({ label: match[1], href: match[2] })
    lines.pop()
  }

  if (!sources.length) return <p className={className}>{text}</p>
  const uniqueSources = [...new Map(sources.map((source) => [source.href, source])).values()]
  return <div className={className}>
    <p>{lines.join('\n').trimEnd()}</p>
    <ul className="message-sources">
      {uniqueSources.map((source) => (
        <li key={source.href}><a href={source.href} target="_blank" rel="noreferrer">{source.label}</a></li>
      ))}
    </ul>
  </div>
}
