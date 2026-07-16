import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import { LoginPage } from './components/LoginPage'
import { NewProjectForm } from './components/NewProjectForm'
import { ProjectDetailView } from './components/ProjectDetail'
import { ProjectList } from './components/ProjectList'
import { QueuePanel } from './components/QueuePanel'
import { SettingsPanel } from './components/SettingsPanel'
import { InkScape, Seal } from './components/decor'
import type { Meta, ProjectDetail, ProjectSummary } from './types'

const ACTIVE = new Set(['queued', 'running'])

export default function App() {
  const [meta, setMeta] = useState<Meta | null>(null)
  const [list, setList] = useState<ProjectSummary[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(
    () => new URLSearchParams(window.location.search).get('project'),
  )
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
  // 依赖里的 user 是必要的:分享链接(?project=<id>)可能在登录完成前就已经把
  // selectedId 从 URL 读出来了,未登录时这里会先吃一次 401(被 catch 悄悄吞掉),
  // 若不把 user 放进依赖数组,登录成功后不会重新拉取,详情页会一直空白。
  useEffect(() => {
    if (!selectedId || !user) return
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
      } catch (e) {
        // 永久错误(项目被删 404 / 会话过期 401)重试无意义,停止轮询避免无谓空转。
        // 仅对疑似瞬时错误(网络抖动/5xx)用比正常 2000 更长的退避重排,既能自愈又不高频空转;
        // 组件卸载时下方 clearTimeout 照常清理。
        const status = (e as { status?: number }).status
        if (alive && status !== 404 && status !== 401) timer = window.setTimeout(tick, 4000)
      }
    }
    tick()
    return () => {
      alive = false
      if (timer) window.clearTimeout(timer)
    }
  }, [selectedId, user, refreshList, refreshKey])

  // 把选中状态同步回地址栏(?project=<id>),这样复制当前 URL 就能分享给团队成员;
  // 用 replaceState 不产生历史记录,避免"后退"变成在项目之间来回跳
  useEffect(() => {
    const url = new URL(window.location.href)
    if (selectedId) url.searchParams.set('project', selectedId)
    else url.searchParams.delete('project')
    window.history.replaceState(null, '', url)
  }, [selectedId])

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
      {/* 顶栏:水墨云山题头 */}
      <header
        className="sticky top-0 z-20 overflow-hidden backdrop-blur-md"
        style={{ background: 'linear-gradient(120deg,#213029,#14201c 60%,#101a16)' }}
      >
        <InkScape className="absolute inset-x-0 bottom-0 h-24 w-full opacity-90" tone="dark" />
        <div className="relative mx-auto flex h-[66px] max-w-6xl items-center justify-between px-6">
          <div className="flex items-center gap-3.5">
            <div className="flex h-11 w-11 items-center justify-center rounded-[10px] bg-gradient-to-br from-cinnabar-bright to-cinnabar-deep shadow-[0_3px_10px_rgba(33,90,82,0.28)]">
              <span className="font-brush text-3xl leading-none text-rice">墨</span>
            </div>
            <div className="flex flex-col gap-0.5">
              <div className="flex items-baseline gap-2">
                <span className="font-serif text-xl font-bold leading-none tracking-[2px] text-rice">
                  WanderInk
                </span>
                <span className="font-brush text-base text-gold-pale">拾遗</span>
              </div>
              <span className="text-[11px] leading-none tracking-[3px] text-gold-pale/70">
                景区传说 · 有声连环画
              </span>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-2 rounded-full border border-gold/25 bg-white/5 px-3.5 py-[7px]">
              <span
                className={`h-[7px] w-[7px] rounded-full ${
                  activeCount > 0 ? 'animate-shy-pulse bg-cinnabar-bright' : 'bg-jade'
                }`}
              />
              <span className="text-[13px] text-rice/85">
                {activeCount > 0 ? `${activeCount} 部生成中` : `${list.length} 部作品`}
              </span>
            </div>
            <button
              type="button"
              onClick={() => setShowSettings(true)}
              aria-label="配置"
              className="flex h-8 w-8 items-center justify-center rounded-full border border-gold/25 bg-white/5 text-rice/80 transition hover:text-gold-pale"
            >
              ⚙
            </button>
            <div className="flex items-center gap-2 rounded-full border border-gold/25 bg-white/5 px-3 py-[7px]">
              <span className="text-[13px] text-rice/85">{user}</span>
              <button
                type="button"
                onClick={onLogout}
                className="text-[13px] text-gold-pale/70 transition hover:text-gold-pale"
              >
                退出
              </button>
            </div>
          </div>
        </div>
        <div className="meander relative opacity-90" />
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
              <ProjectDetailView
                key={detail.project_id}
                project={detail}
                meta={meta}
                onChanged={onDetailChanged}
              />
            ) : (
              <EmptyState />
            )}
          </main>
        </div>
      </div>

      <div className="meander mt-6 opacity-60" />
      <div className="pb-8 pt-5 text-center text-[11px] tracking-[3px] text-muted">
        山 川 入 卷 · 传 说 成 画 —— WanderInk
      </div>
    </div>
  )
}

function EmptyState() {
  return (
    <div className="flex h-[420px] flex-col items-center justify-center rounded-2xl border border-dashed border-band bg-[rgba(242,248,244,0.45)] text-center">
      <div className="relative mb-5 flex h-16 w-16 items-center justify-center rounded-2xl border border-line bg-paper">
        <span className="font-brush text-4xl text-cinnabar">遗</span>
        <span className="absolute -right-3 -top-3">
          <Seal char="卷" size={30} rot={12} />
        </span>
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
