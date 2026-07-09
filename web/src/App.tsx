import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import { NewProjectForm } from './components/NewProjectForm'
import { ProjectDetailView } from './components/ProjectDetail'
import { ProjectList } from './components/ProjectList'
import type { Meta, ProjectDetail, ProjectSummary } from './types'

const ACTIVE = new Set(['queued', 'running'])

export default function App() {
  const [meta, setMeta] = useState<Meta | null>(null)
  const [list, setList] = useState<ProjectSummary[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<ProjectDetail | null>(null)

  const refreshList = useCallback(async () => {
    try {
      setList(await api.list())
    } catch {
      /* 后端未启动时静默 */
    }
  }, [])

  useEffect(() => {
    api.meta().then(setMeta).catch(() => {})
    refreshList()
  }, [refreshList])

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
  }, [selectedId, refreshList])

  const onCreated = (id: string) => {
    setSelectedId(id)
    setDetail(null)
    refreshList()
  }

  const activeCount = list.filter((p) => ACTIVE.has(p.pipeline)).length

  return (
    <div className="min-h-screen">
      {/* 顶栏 */}
      <header className="sticky top-0 z-20 border-b border-line bg-[rgba(243,235,215,0.82)] backdrop-blur-md">
        <div className="mx-auto flex h-[66px] max-w-6xl items-center justify-between px-6">
          <div className="flex items-center gap-3.5">
            <div className="flex h-11 w-11 items-center justify-center rounded-[10px] bg-gradient-to-br from-cinnabar to-cinnabar-deep shadow-[0_3px_10px_rgba(138,43,34,0.28)]">
              <span className="font-brush text-3xl leading-none text-rice">山</span>
            </div>
            <div className="flex flex-col gap-0.5">
              <span className="font-serif text-xl font-bold leading-none tracking-[2px] text-ink">
                山海
              </span>
              <span className="text-[11px] leading-none tracking-[3px] text-muted">
                景区传说 · 有声连环画
              </span>
            </div>
          </div>
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
        </div>
      </header>

      <div className="mx-auto max-w-6xl px-6 py-8">
        <div className="grid grid-cols-1 gap-7 lg:grid-cols-[21rem_1fr]">
          <aside className="space-y-5">
            <NewProjectForm meta={meta} onCreated={onCreated} />
            <ProjectList items={list} selectedId={selectedId} onSelect={setSelectedId} />
          </aside>

          <main>
            {detail ? (
              <ProjectDetailView project={detail} />
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
