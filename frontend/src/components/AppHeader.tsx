import { useEffect, useState } from 'react'
import { Link, NavLink } from 'react-router-dom'
import { getHealth, getSettings, putSettings, resetDemo } from '../api/client'
import type { Health } from '../api/types'

export const NAV_ITEMS = [
  ['/workbench', '工作台'],
  ['/quotes', '报价'],
  ['/documents', '单据核对'],
  ['/kb', '知识库'],
  ['/evals', '成绩单'],
  ['/automation', '自动化流程'],
] as const

export function AppHeader() {
  const [health, setHealth] = useState<Health>()
  const [autoReply, setAutoReply] = useState<boolean>()
  const [saving, setSaving] = useState(false)
  const [resetting, setResetting] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    getHealth().then((value) => { if (active) setHealth(value) })
      .catch(() => { if (active) setError('无法连接客服服务') })
    getSettings().then((value) => { if (active) setAutoReply(value.auto_reply_readonly) })
      .catch(() => { if (active) setError('无法读取处理模式') })
    return () => { active = false }
  }, [])

  async function toggleAutoReply() {
    if (autoReply === undefined) return
    setSaving(true)
    setError('')
    try {
      const value = await putSettings(!autoReply)
      setAutoReply(value.auto_reply_readonly)
    } catch {
      setError('未能更新处理模式，请重试')
    } finally {
      setSaving(false)
    }
  }

  async function reset() {
    if (!window.confirm('重置演示数据？当前工单和模拟业务记录将恢复到初始状态。')) return
    setResetting(true)
    setError('')
    try {
      await resetDemo()
      window.location.reload()
    } catch {
      setError('未能重置演示数据，请重试')
      setResetting(false)
    }
  }

  return (
    <header className="site-header">
      <div className="header-top">
        <Link className="brand" to="/workbench">
          <span className="brand-mark" aria-hidden="true">AI</span>
          <span>{health?.company ?? '货代'} AI 客服中心</span>
        </Link>
        <div className="header-actions">
          {health?.llm.provider === 'deepseek' && (
            <span className="model-badge" title={health.llm.fallback_reason ?? undefined}>
              <span aria-hidden="true" />
              {`DeepSeek · ${health.llm.model}`}
            </span>
          )}
          <button className="quiet-button" type="button" onClick={() => void reset()} disabled={resetting}>
            {resetting ? '正在重置…' : '重置演示数据'}
          </button>
          <Link className="chat-link" to="/chat" target="_blank" rel="noreferrer">客户聊天窗口 <span aria-hidden="true">↗</span></Link>
        </div>
      </div>
      <div className="header-bottom">
        <nav className="site-nav" aria-label="主导航">
          {NAV_ITEMS.map(([path, label]) => <NavLink key={path} to={path}>{label}</NavLink>)}
        </nav>
        <button type="button" className="mode-switch" role="switch" aria-checked={autoReply ?? false}
          disabled={autoReply === undefined || saving} onClick={() => void toggleAutoReply()}>
          <span className="switch-track" aria-hidden="true" />
          查询类自动回复
        </button>
      </div>
      {error && <p className="header-error" role="alert">{error}</p>}
    </header>
  )
}
