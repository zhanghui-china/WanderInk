import type { ProjectSummary } from '../types'

function badge(pipeline: string): { cls: string; text: string } {
  // 降级成片状态形如 "done(降级:N/M 页真人解说,K 页静音兜底)"——仍属已完成,单独标注
  if (pipeline.startsWith('done')) {
    return pipeline.includes('降级')
      ? { cls: 'bg-amber2/15 text-gold', text: '已完成·降级' }
      : { cls: 'bg-jade/12 text-jade', text: '已完成' }
  }
  if (pipeline.startsWith('error')) return { cls: 'bg-cinnabar/10 text-cinnabar', text: '出错' }
  if (pipeline === 'running' || pipeline === 'queued')
    return { cls: 'bg-amber2/15 text-gold', text: '生成中' }
  if (pipeline.startsWith('partial')) return { cls: 'bg-kraft text-muted', text: '待合成' }
  return { cls: 'bg-kraft text-muted', text: pipeline }
}

export function ProjectList({
  items,
  selectedId,
  onSelect,
}: {
  items: ProjectSummary[]
  selectedId: string | null
  onSelect: (id: string) => void
}) {
  return (
    <div className="rounded-2xl border border-band bg-paper p-5 shadow-paper">
      <div className="mb-3 flex items-center gap-2.5">
        <span className="flex h-7 w-7 items-center justify-center rounded-md bg-ink font-brush text-lg text-rice">
          集
        </span>
        <h2 className="font-serif text-base font-semibold tracking-wide text-ink">作品列表</h2>
      </div>

      {items.length === 0 && (
        <p className="py-6 text-center text-sm text-muted">还没有作品</p>
      )}

      <ul className="space-y-1.5">
        {items.map((p) => {
          const b = badge(p.pipeline)
          const on = selectedId === p.project_id
          return (
            <li key={p.project_id}>
              <button
                onClick={() => onSelect(p.project_id)}
                className={`flex w-full items-center justify-between gap-2 rounded-lg border px-3 py-2.5 text-left transition ${
                  on
                    ? 'border-cinnabar bg-white shadow-paper'
                    : 'border-transparent hover:border-line hover:bg-white/50'
                }`}
              >
                <span className="min-w-0">
                  <span className="block truncate font-serif text-sm text-ink">
                    {p.scenic_spot}
                    <span className="ml-1.5 text-[11px] font-sans font-normal text-muted">
                      {p.owner || '未知'}
                    </span>
                  </span>
                  <span className="block truncate text-[11px] text-muted">{p.project_id}</span>
                </span>
                <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] ${b.cls}`}>
                  {b.text}
                </span>
              </button>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
