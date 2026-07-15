import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import { LoginPage } from './components/LoginPage'
import { NewProjectForm } from './components/NewProjectForm'
import { ProjectDetailView } from './components/ProjectDetail'
import { ProjectList } from './components/ProjectList'
import { QueuePanel } from './components/QueuePanel'
import { SettingsPanel } from './components/SettingsPanel'
import type { Meta, ProjectDetail, ProjectSummary } from './types'

const ACTIVE = new Set(['queued', 'running'])

export default function App() {
  const [meta, setMeta] = useState<Meta | null>(null)
  const [list, setList] = useState<ProjectSummary[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<ProjectDetail | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)
  const [showSettings, setShowSettings] = useState(false)
  const [user, setUser] = useState<string | null>(null)
  const [authChecked, setAuthChecked] = useState(false)

  const refreshList = useCallback(async () => {
    try {
      setList(await api.list())
    } catch {
      /* 后端未启动时静默 */
    }
  }, [])

  // 进门先判断登录态(GET /api/me):401 则 user 留空、只渲染登录页,不发起其它 API 请求
  useEffect(() => {
    api
      .me()
      .then((m) => setUser(m.username))
      .catch(() => setUser(null))
      .finally(() => setAuthChecked(true))
  }, [])

  // 已登录才加载表单枚举与作品列表
  useEffect(() => {
    if (!user) return
    api.meta().then(setMeta).catch(() => {})
    refreshList()
  }, [user, refreshList])

  const onLoggedIn = useCallback(() => {
    api
      .me()
      .then((m) => setUser(m.username))
      .catch(() => setUser(null))
  }, [])

  const onLogout = useCallback(async () => {
    try {
      await api.logout()
    } catch {
      /* ignore */
    }
    setUser(null)
    setSelectedId(null)
    setDetail(null)
    setList([])
  }, [])

  // 选中项目:拉详情;若管线在跑则轮询
  useEffect(() => {
    if (!selectedId) return
    let alive = true
    let timer: number | undefined

    const tick = async () => {
      try {
        const d = await api.get(selectedId)
        if (!alive) return
        setDetail(d)
        if (ACTIVE.has(d.pipeline)) {
          timer = window.setTimeout(tick, 2000)
        } else {
          refreshList()
        }
      } catch {
        /* ignore */
      }
    }
    tick()
    return () => {
      alive = false
      if (timer) window.clearTimeout(timer)
    }
  }, [selectedId, refreshList, refreshKey])

  // 编辑操作(改字面/重绘/重配音/增删/重排/单步重跑)后调用:强制重拉一次详情,
  // 若管线因此进入 queued/running 则上面的 effect 会继续自行轮询
  const onDetailChanged = useCallback(() => setRefreshKey((k) => k + 1), [])

  const onCreated = (id: string) => {
    setSelectedId(id)
    setDetail(null)
    refreshList()
  }

  const activeCount = list.filter((p) => ACTIVE.has(p.pipeline)).length

  if (!authChecked) return null // 登录态未知前先不渲染,避免闪现登录页
  if (!user) return <LoginPage onLoggedIn={onLoggedIn} />

  return (
    <div className="min-h-screen">
      {/* 顶栏 */}
      <header className="sticky top-0 z-20 border-b border-line bg-[rgba(243,235,215,0.82)] backdrop-blur-md">
        <div className="mx-auto flex h-[66px] max-w-6xl items-center justify-between px-6">
          <div className="flex items-center gap-3.5">
            <div className="flex h-11 w-11 items-center justify-center rounded-[10px] bg-gradient-to-br from-cinnabar to-cinnabar-deep shadow-[0_3px_10px_rgba(138,43,34,0.28)]">
              <span className="font-brush text-3xl leading-none text-rice">W</span>
            </div>
            <div className="flex flex-col gap-0.5">
              <span className="font-serif text-xl font-bold leading-none tracking-[2px] text-ink">
                WanderInk
              </span>
              <span className="text-[11px] leading-none tracking-[3px] text-muted">
                景区传说 · 有声连环画
              </span>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-2 rounded-full border border-line bg-[rgba(251,246,234,0.7)] px-3.5 py-[7px]">
              <span
                className={`h-[7px] w-[7px] rounded-full ${
                  activeCount > 0 ? 'animate-shy-pulse bg-cinnabar' : 'bg-jade'
                }`}
              />
              <span className="text-[13px] text-ink-soft">
                {activeCount > 0 ? `${activeCount} 部生成中` : `${list.length} 部作品`}
              </span>
            </div>
            <button
              type="button"
              onClick={() => setShowSettings(true)}
              aria-label="配置"
              className="flex h-8 w-8 items-center justify-center rounded-full border border-line bg-paper text-ink-soft transition hover:text-cinnabar"
            >
              ⚙
            </button>
            <div className="flex items-center gap-2 rounded-full border border-line bg-[rgba(251,246,234,0.7)] px-3 py-[7px]">
              <span className="text-[13px] text-ink-soft">{user}</span>
              <button
                type="button"
                onClick={onLogout}
                className="text-[13px] text-muted transition hover:text-cinnabar"
              >
                退出
              </button>
            </div>
          </div>
        </div>
      </header>

      {showSettings && <SettingsPanel meta={meta} onClose={() => setShowSettings(false)} />}

      <div className="mx-auto max-w-6xl px-6 py-8">
        <div className="grid grid-cols-1 gap-7 lg:grid-cols-[21rem_1fr]">
          <aside className="space-y-5">
            <NewProjectForm meta={meta} onCreated={onCreated} />
            <QueuePanel user={user} onSelect={setSelectedId} />
            <ProjectList items={list} selectedId={selectedId} onSelect={setSelectedId} />
          </aside>

          <main>
            {detail ? (
              <ProjectDetailView project={detail} meta={meta} onChanged={onDetailChanged} />
            ) : (
              <EmptyState />
            )}
          </main>
        </div>
      </div>
    </div>
  )
}

function EmptyState() {
  return (
    <div className="flex h-[420px] flex-col items-center justify-center rounded-2xl border border-dashed border-band bg-[rgba(251,246,234,0.45)] text-center">
      <div className="mb-5 flex h-16 w-16 items-center justify-center rounded-2xl border border-line bg-paper">
        <span className="font-brush text-4xl text-cinnabar">遗</span>
      </div>
      <p className="font-serif text-lg tracking-[2px] text-ink">拾起一座景区的传说</p>
      <p className="mt-2 max-w-xs text-sm leading-relaxed text-muted">
        在左侧输入景区名，从方志、诗词与民间口述中检索传说，
        <br />
        改写剧本、设计人物、逐页绘成有声连环画。
      </p>
    </div>
  )
}
