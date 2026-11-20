import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { getKnowledgeArticles, searchKnowledge } from '../api/client'
import type { KnowledgeArticle, KnowledgeHit, KnowledgeSource } from '../api/types'
import type { ChatLanguage } from '../i18n'
import './knowledge.css'

const copy = {
  zh: {
    title: '知识库', catalog: '全部资料', search: '搜索', placeholder: '搜索清关、通关号码或仓库流程',
    results: '搜索结果', loading: '加载中…', catalogError: '资料加载失败，请重试。',
    searchError: '搜索失败，请重试。', retry: '重试', empty: '知识库里没有找到相关段落。',
    missing: '找不到这篇资料。', source: '原始出处', back: '返回资料', reviewed: '资料整理于',
  },
  ko: {
    title: '지식 베이스', catalog: '전체 자료', search: '검색', placeholder: '통관, 개인통관고유부호, 창고 업무 검색',
    results: '검색 결과', loading: '불러오는 중…', catalogError: '자료를 불러오지 못했습니다.',
    searchError: '검색하지 못했습니다. 다시 시도해 주세요.', retry: '다시 시도', empty: '관련 문단을 찾지 못했습니다.',
    missing: '해당 자료를 찾지 못했습니다.', source: '원문 출처', back: '자료로 돌아가기', reviewed: '정리 날짜',
  },
}

function documentLink(language: ChatLanguage, id?: string, section?: string) {
  const filters = new URLSearchParams()
  if (id) filters.set('doc', id)
  if (language === 'ko') filters.set('lang', language)
  return '/kb' + (filters.size ? '?' + filters : '') + (section ? '#' + encodeURIComponent(section) : '')
}

function sourceUrl(source: KnowledgeSource | null) {
  if (!source) return null
  try {
    const url = new URL(source.url)
    return url.protocol === 'https:' && !url.username && !url.password ? url.href : null
  } catch {
    return null
  }
}

type SearchResult = { query: string; hits: KnowledgeHit[] | null }

export default function KnowledgePage() {
  const location = useLocation()
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const language = params.get('lang') === 'ko' ? 'ko' : 'zh'
  const docId = params.get('doc')
  const query = params.get('q')?.trim() ?? ''
  const [input, setInput] = useState(query)
  const [articles, setArticles] = useState<KnowledgeArticle[] | null>()
  const [result, setResult] = useState<SearchResult>()
  const [catalogAttempt, setCatalogAttempt] = useState(0)
  const [searchAttempt, setSearchAttempt] = useState(0)
  const t = copy[language]
  const article = docId ? articles?.find((item) => item.id === docId) : articles?.[0]

  useEffect(() => {
    let active = true
    getKnowledgeArticles().then(
      ({ articles: items }) => { if (active) setArticles(items) },
      () => { if (active) setArticles(null) },
    )
    return () => { active = false }
  }, [catalogAttempt])

  useEffect(() => {
    if (!query) return
    let active = true
    searchKnowledge(query).then(
      ({ hits }) => { if (active) setResult({ query, hits }) },
      () => { if (active) setResult({ query, hits: null }) },
    )
    return () => { active = false }
  }, [query, searchAttempt])

  useEffect(() => { setInput(query) }, [query, docId])

  useEffect(() => {
    const section = article?.sections.find(({ id }) => '#' + encodeURIComponent(id) === location.hash)
    if (section) document.getElementById(section.id)?.scrollIntoView?.({ block: 'start' })
  }, [article, location.hash])

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const term = input.trim()
    if (term === query && term) {
      setResult(undefined)
      setSearchAttempt((attempt) => attempt + 1)
      return
    }
    const filters = new URLSearchParams()
    if (term) filters.set('q', term)
    if (language === 'ko') filters.set('lang', language)
    navigate({ pathname: '/kb', search: filters.toString() })
  }

  function switchLanguage(value: ChatLanguage) {
    const filters = new URLSearchParams(params)
    if (value === 'ko') filters.set('lang', value)
    else filters.delete('lang')
    navigate({ pathname: '/kb', search: filters.toString(), hash: location.hash }, { replace: true })
  }

  const hits = result?.query === query ? result.hits : undefined

  return <main className="knowledge-page" lang={language === 'ko' ? 'ko' : 'zh-CN'}>
    <div className="kb-heading">
      <h1>{t.title}</h1>
      <div className="chat-languages">
        <button type="button" lang="zh-CN" aria-pressed={language === 'zh'} onClick={() => switchLanguage('zh')}>中文</button>
        <button type="button" lang="ko" aria-pressed={language === 'ko'} onClick={() => switchLanguage('ko')}>한국어</button>
      </div>
    </div>
    <form role="search" className="kb-search" onSubmit={submitSearch}>
      <input type="search" aria-label={t.search} placeholder={t.placeholder} maxLength={200}
        value={input} onChange={(event) => setInput(event.target.value)} />
      <button type="submit">{t.search}</button>
    </form>
    <div className="kb-layout">
      <nav className="kb-catalog" aria-label={t.catalog}>
        <h2>{t.catalog}</h2>
        {articles === null && <div className="kb-feedback" role="alert">
          <p>{t.catalogError}</p>
          <button type="button" onClick={() => {
            setArticles(undefined)
            setCatalogAttempt((attempt) => attempt + 1)
          }}>{t.retry}</button>
        </div>}
        {articles === undefined && <p className="kb-feedback" role="status">{t.loading}</p>}
        {articles && <ul>
          {articles.map((item) => (
            <li key={item.id}>
              <Link to={documentLink(language, item.id)}
                aria-current={!query && article?.id === item.id ? 'page' : undefined}>{item.title[language]}</Link>
            </li>
          ))}
        </ul>}
      </nav>
      {query ? (
        <section className="kb-reader" aria-labelledby="kb-results-title">
          <h2 id="kb-results-title">{t.results} <span className="kb-query">{query}</span></h2>
          {hits === undefined && <p className="kb-feedback" role="status">{t.loading}</p>}
          {hits === null && <p className="kb-feedback" role="alert">{t.searchError}</p>}
          {hits?.length === 0 && <p className="kb-feedback" role="status">{t.empty}</p>}
          {!!hits?.length && <ul className="kb-hits">
            {hits.map((hit) => (
              <li key={hit.article_id + '/' + hit.section_id}>
                <div className="kb-hit-article">{hit.title[language]}</div>
                <Link className="kb-hit-title" to={documentLink(language, hit.article_id, hit.section_id)}>
                  {hit.section_title[language]}
                </Link>
                <p className="kb-excerpt">{hit.text[language]}</p>
              </li>
            ))}
          </ul>}
        </section>
      ) : (
        <article className="kb-reader">
          {article ? <>
            <h2>{article.title[language]}</h2>
            {article.reviewed_on && <p className="kb-feedback">{t.reviewed} <time dateTime={article.reviewed_on}>{article.reviewed_on}</time></p>}
            {article.sections.map((section) => {
              const url = sourceUrl(section.source)
              return <section className="kb-section" key={section.id} id={section.id}>
                <h3>{section.title[language]}</h3>
                <p>{section.text[language]}</p>
                {url && section.source && <div className="kb-source">
                  <span>{t.source}</span>
                  <a href={url} target="_blank" rel="noreferrer">{section.source.label}</a>
                </div>}
              </section>
            })}
          </> : articles && <div className="kb-feedback">
            <p>{docId ? t.missing : t.empty}</p>
            {docId && <Link to={documentLink(language)}>{t.back}</Link>}
          </div>}
        </article>
      )}
    </div>
  </main>
}
