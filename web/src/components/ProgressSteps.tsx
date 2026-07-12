import { STAGES } from '../stages'
import type { ProjectDetail } from '../types'

type Cell = { dot: string; label: string; sub: string; tag: string }

function cell(status: string | undefined, running: boolean): Cell {
  if (status === 'done')
    return {
      dot: 'bg-cinnabar text-rice',
      label: 'text-ink',
      sub: 'text-muted',
      tag: '✓',
    }
  if (status === 'partial')
    return {
      dot: 'bg-amber2 text-white',
      label: 'text-ink',
      sub: 'text-muted',
      tag: '',
    }
  if (status === 'failed')
    return {
      dot: 'bg-cinnabar/20 text-cinnabar border border-cinnabar',
      label: 'text-cinnabar',
      sub: 'text-cinnabar/60',
      tag: '!',
    }
  // pending / running current
  if (running)
    return {
      dot: 'border-2 border-cinnabar text-cinnabar animate-shy-pulse',
      label: 'text-ink',
      sub: 'text-muted',
      tag: '',
    }
  return {
    dot: 'bg-kraft text-muted border border-line',
    label: 'text-muted',
    sub: 'text-band',
    tag: '',
  }
}

export function ProgressSteps({ project }: { project: ProjectDetail }) {
  const running = project.pipeline === 'running' || project.pipeline === 'queued'
  // 第一个未完成步骤即“当前步”
  const currentIdx = STAGES.findIndex((s) => project.status[s.key] !== 'done')

  return (
    <div className="rounded-2xl border border-band bg-paper p-5 shadow-paper">
      <div className="mb-4 flex items-center gap-2.5">
        <span className="font-serif text-base font-semibold tracking-wide text-ink">生成进度</span>
        {running && <span className="h-2 w-2 animate-shy-pulse rounded-full bg-cinnabar" />}
        <span className="text-xs tracking-wide text-muted">
          {running
            ? '正在生成…'
            : project.pipeline === 'done'
              ? '全部完成'
              : project.pipeline.startsWith('done(降级')
                ? '完成(部分页静音兜底)'
                : project.pipeline}
        </span>
      </div>

      <ol className="flex flex-wrap items-start gap-y-4">
        {STAGES.map((s, i) => {
          const isCurrent = running && i === currentIdx
          const c = cell(project.status[s.key], isCurrent)
          const done = project.status[s.key] === 'done'
          return (
            <li key={s.key} className="flex flex-1 items-center" style={{ minWidth: 92 }}>
              <div className="flex flex-col items-center gap-1.5 px-1">
                <span
                  className={`flex h-8 w-8 items-center justify-center rounded-full text-sm font-bold ${c.dot}`}
                >
                  {c.tag}
                </span>
                <span className={`font-serif text-[13px] font-semibold tracking-wide ${c.label}`}>
                  {s.label}
                </span>
                <span className={`text-[9px] tracking-[2px] ${c.sub}`}>{s.sub}</span>
              </div>
              {i < STAGES.length - 1 && (
                <span
                  className={`mx-1 h-px flex-1 ${done ? 'bg-cinnabar' : 'bg-line'}`}
                  style={{ minWidth: 12 }}
                />
              )}
            </li>
          )
        })}
      </ol>
    </div>
  )
}
