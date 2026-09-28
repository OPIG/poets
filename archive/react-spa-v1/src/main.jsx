import React, { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { ArrowDown, ArrowLeft, ArrowRight, BookOpen, ChevronLeft, ChevronRight, Feather, Menu, Search, X } from 'lucide-react'
import './style.css'

const GENRES = [
  { key: 'all', label: '全部作品' },
  { key: 'tang_poem', label: '唐诗' },
  { key: 'song_poem', label: '宋诗' },
  { key: 'song_ci', label: '宋词' },
]
const FIELDS = [
  { key: 'all', label: '综合检索' },
  { key: 'author', label: '作者姓名' },
  { key: 'title', label: '诗词名称' },
  { key: 'tag', label: '作品标签' },
]
const GENRE_LABEL = Object.fromEntries(GENRES.map(({ key, label }) => [key, label]))
const EXAMPLES = ['李白', '苏轼', '李清照', '明月']

async function request(url, signal) {
  const response = await fetch(url, { signal })
  const payload = await response.json()
  if (!response.ok) throw new Error(payload.detail || '暂时无法读取诗卷，请稍后重试。')
  return payload
}

function App() {
  const [meta, setMeta] = useState(null)
  const [draft, setDraft] = useState('')
  const [query, setQuery] = useState('')
  const [field, setField] = useState('all')
  const [genre, setGenre] = useState('all')
  const [page, setPage] = useState(1)
  const [results, setResults] = useState({ total: 0, items: [] })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [retryTick, setRetryTick] = useState(0)
  const [detail, setDetail] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)

  useEffect(() => {
    request('/api/meta').then(setMeta).catch(err => setError(err.message))
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    const params = new URLSearchParams({ q: query, field, genre, page: String(page), size: '12' })
    setLoading(true)
    setError('')
    request(`/api/works?${params}`, controller.signal)
      .then(setResults)
      .catch(err => { if (err.name !== 'AbortError') setError(err.message) })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [query, field, genre, page, retryTick])

  useEffect(() => {
    if (!detail) return undefined
    const close = event => { if (event.key === 'Escape') setDetail(null) }
    document.addEventListener('keydown', close)
    document.body.style.overflow = 'hidden'
    return () => { document.removeEventListener('keydown', close); document.body.style.overflow = '' }
  }, [detail])

  function search(value = draft, nextField = field) {
    setDraft(value)
    setField(nextField)
    setQuery(value.trim())
    setPage(1)
    document.getElementById('results')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  async function openWork(id) {
    setDetail({ id })
    setDetailLoading(true)
    try {
      setDetail(await request(`/api/works/${id}`))
    } catch (err) {
      setError(err.message)
      setDetail(null)
    } finally {
      setDetailLoading(false)
    }
  }

  const pages = Math.ceil(results.total / 12)
  const currentGenre = GENRES.find(item => item.key === genre)?.label

  return <>
    <div className="site-shell">
      <header className="topbar page-width">
        <a className="brand" href="/" onClick={e => { e.preventDefault(); setQuery(''); setDraft(''); setGenre('all'); setPage(1) }}>
          <span className="brand-mark">诗</span>
          <span className="brand-copy"><strong>诗卷</strong><small>中华诗词典藏</small></span>
        </a>
        <nav className={menuOpen ? 'top-nav open' : 'top-nav'} aria-label="主导航">
          <a href="#explore" onClick={() => setMenuOpen(false)}>探索诗词</a>
          <a href="#about" onClick={() => setMenuOpen(false)}>关于诗卷</a>
          <span className="nav-divider" />
          <span className="nav-badge"><span className="status-dot" />{meta?.mode === 'public' ? '公开馆藏' : '本地预览'}</span>
        </nav>
        <button className="mobile-menu" onClick={() => setMenuOpen(!menuOpen)} aria-label="展开菜单">{menuOpen ? <X size={23} /> : <Menu size={23} />}</button>
      </header>

      <main>
        <section className="hero" id="explore">
          <div className="hero-ornament hero-ornament-one" aria-hidden="true">山</div>
          <div className="hero-ornament hero-ornament-two" aria-hidden="true">水</div>
          <div className="page-width hero-inner">
            <div className="hero-copy">
              <div className="eyebrow"><span className="eyebrow-line" /> 万卷诗书，一处可寻</div>
              <h1>于千年诗行间，<br /><em>遇见</em>此刻心声。</h1>
              <p className="hero-subtitle">从唐诗的旷达、宋诗的深思，到宋词的婉转。<br />循一个名字、一首诗，或一种意境，开启你的阅读。</p>
              <a className="hero-cta" href="#results">走进诗词长廊 <ArrowRight size={18} /></a>
            </div>
            <div className="hero-art" aria-hidden="true">
              <div className="art-circle circle-back" />
              <div className="art-circle circle-front" />
              <div className="art-ridge ridge-one" /><div className="art-ridge ridge-two" /><div className="art-ridge ridge-three" />
              <div className="art-sun" />
              <div className="art-poem">江山如画<br />一时多少豪杰</div>
              <div className="art-seal">诗<br />卷</div>
            </div>
          </div>
          <div className="page-width hero-bottom"><span><span className="small-diamond" /> 汇集 {meta?.total?.toLocaleString('zh-CN') || '三十余万'} 篇诗词作品</span><a href="#results">向下探索 <ArrowDown size={16} /></a></div>
        </section>

        {meta?.mode !== 'public' && <div className="preview-note page-width"><span className="preview-icon">!</span><div><strong>本地数据预览</strong><span>原始资料尚未逐条完成权利审核，仅供本机整理与校对，请勿公开部署。</span></div></div>}

        <section className="browse page-width" id="results">
          <div className="section-heading"><div><span className="section-kicker">DISCOVER · 探索</span><h2>诗词长廊</h2><p>让每一次翻阅，都成为与古人的一场相遇。</p></div><div className="heading-aside"><Feather size={24} strokeWidth={1.3} /><span>诗中有天地<br />字里见山河</span></div></div>

          <div className="search-panel" aria-label="诗词检索条件">
            <div className="search-panel-heading"><strong>检索诗词</strong><span>关键词、范围与类别共同筛选结果</span></div>
            <form className="search-box" onSubmit={event => { event.preventDefault(); search() }}>
              <Search size={21} strokeWidth={1.8} className="search-icon" />
              <input aria-label="检索关键词" value={draft} onChange={event => setDraft(event.target.value)} placeholder="输入作者、诗词名或标签…" />
              <label className="field-filter" htmlFor="search-field"><span>范围</span><select id="search-field" value={field} onChange={event => { setField(event.target.value); setPage(1) }}>{FIELDS.map(item => <option key={item.key} value={item.key}>{item.label}</option>)}</select></label>
              <button type="submit">检索 <ArrowRight size={18} /></button>
            </form>
            <div className="hero-hints"><span>试试搜索</span>{EXAMPLES.map(item => <button key={item} onClick={() => search(item, item === '明月' ? 'all' : 'author')}>{item}</button>)}</div>
            <div className="genre-tabs" role="tablist" aria-label="按诗词类别筛选">{GENRES.map(item => <button type="button" role="tab" aria-selected={genre === item.key} className={genre === item.key ? 'active' : ''} key={item.key} onClick={() => { setGenre(item.key); setPage(1) }}>{item.label}</button>)}</div>
          </div>

          <div className="result-toolbar"><div><strong>{query ? `“${query}” 的检索结果` : `${currentGenre} · 浏览馆藏`}</strong><span>{FIELDS.find(item => item.key === field)?.label} · {currentGenre} · {loading ? '正在翻阅…' : `共找到 ${results.total.toLocaleString('zh-CN')} 篇作品`}</span></div>{(query || genre !== 'all' || field !== 'all') && <button className="clear-search" onClick={() => { setDraft(''); setQuery(''); setField('all'); setGenre('all'); setPage(1) }}><X size={14} /> 清除全部条件</button>}</div>

          {error && <div className="state-panel error-panel"><BookOpen size={32} /><strong>暂时无法翻阅</strong><p>{error}</p><button onClick={() => setRetryTick(value => value + 1)}>重新尝试</button></div>}
          {!error && loading && <div className="poem-grid">{Array.from({ length: 6 }, (_, i) => <div className="poem-card skeleton" key={i}><div /><div /><div /><div /></div>)}</div>}
          {!error && !loading && results.items.length === 0 && <div className="state-panel"><div className="empty-glyph">寻</div><strong>诗卷中暂未寻得此作</strong><p>不妨换个作者、诗题或标签，再试一次。</p><button onClick={() => search('', 'all')}>浏览全部作品 <ArrowRight size={15} /></button></div>}
          {!error && !loading && results.items.length > 0 && <div className="poem-grid">{results.items.map((item, index) => <button className="poem-card" key={item.id} onClick={() => openWork(item.id)}>
            <div className="card-top"><span className="card-genre">{GENRE_LABEL[item.genre]}</span><span className="card-index">{String((page - 1) * 12 + index + 1).padStart(3, '0')}</span></div>
            <h3>{item.title}</h3><div className="card-author"><span className="card-author-line" />{item.author}</div>
            <p className="card-excerpt">{item.excerpt || '此作暂未收录正文。'}</p>
            <div className="card-footer"><div className="card-tags">{item.tags.length > 0 && <small>标签</small>}{item.tags.slice(0, 2).map(tag => <span key={tag}>{tag}</span>)}</div><span className="read-more">阅读全文 <ArrowRight size={17} /></span></div>
          </button>)}</div>}

          {!loading && !error && pages > 1 && <div className="pagination"><button disabled={page === 1} onClick={() => { setPage(page - 1); document.getElementById('results')?.scrollIntoView({ behavior: 'smooth' }) }}><ChevronLeft size={17} /> 上一页</button><span>第 <strong>{page}</strong> / {pages.toLocaleString('zh-CN')} 页</span><button disabled={page >= pages} onClick={() => { setPage(page + 1); document.getElementById('results')?.scrollIntoView({ behavior: 'smooth' }) }}>下一页 <ChevronRight size={17} /></button></div>}
        </section>

        <section className="closing" id="about"><div className="page-width closing-inner"><div><span className="section-kicker">ABOUT THE COLLECTION</span><h2>让古典，不止于书页。</h2><p>以结构化的方式保存诗词原貌，以更轻盈的方式走近千年文心。<br />每一篇作品都保留来源线索，等待审慎校对与继续完善。</p></div><div className="closing-calligraphy" aria-hidden="true">读<br />诗</div></div></section>
      </main>
      <footer className="footer page-width"><div className="footer-brand"><span className="brand-mark">诗</span><span>诗卷 · 中华诗词典藏</span></div><span>在文字里，与山河重逢。</span><span>数据来源：chinese-poetry · 原文待核</span></footer>
    </div>

    {detail && <div className="drawer-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) setDetail(null) }}><aside className="detail-drawer" role="dialog" aria-modal="true" aria-label="诗词详情"><div className="drawer-top"><span>诗卷 / 作品阅读</span><button onClick={() => setDetail(null)} aria-label="关闭详情"><X size={22} /></button></div>{detailLoading || !detail.paragraphs ? <div className="drawer-loading">正在展开诗卷…</div> : <div className="drawer-content"><span className="drawer-genre">{GENRE_LABEL[detail.genre]}</span><h2>{detail.title}</h2><div className="drawer-author">{detail.author}<span className="drawer-author-rule" /></div>{detail.prologue && <p className="poem-prologue">{detail.prologue}</p>}<div className="poem-body">{detail.paragraphs.length ? detail.paragraphs.map((line, index) => <p key={index}>{line}</p>) : <p className="missing-body">原始资料暂未收录正文。</p>}</div>{detail.tags.length > 0 && <div className="drawer-tags"><span className="drawer-section-label">作品标签</span><div>{detail.tags.map(tag => <button key={tag} onClick={() => { setDetail(null); search(tag, 'tag') }}>{tag}</button>)}</div></div>}{detail.author_bio && <div className="author-profile"><span className="drawer-section-label">作者小传</span><p>{detail.author_bio}</p></div>}<div className="source-note"><span>数据来源</span>{detail.source_url ? <a className="source-reference" href={detail.source_url} target="_blank" rel="noopener noreferrer" title="查看原始数据项目（外部网站）">{detail.source || 'chinese-poetry'} <span aria-hidden="true">↗</span></a> : <strong>{detail.source || 'chinese-poetry'}</strong>}</div></div>}<div className="drawer-bottom"><span>千年文心 · 一卷可读</span><button onClick={() => setDetail(null)}>返回诗廊 <ArrowLeft size={17} /></button></div></aside></div>}
  </>
}

createRoot(document.getElementById('root')).render(<App />)
