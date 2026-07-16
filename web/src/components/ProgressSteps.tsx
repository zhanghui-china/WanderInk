import { CardHeadInline } from './decor'
import { GeneratingBars } from './GeneratingBars'
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
      dot: 'bg-alarm/20 text-alarm border border-alarm',
      label: 'text-alarm',
      sub: 'text-alarm/60',
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

// 秒数格式化:60s 内显示 "12.3s",超过则显示 "1分23秒"
function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(1)}s`
  const m = Math.floor(seconds / 60)
  const s = Math.round(seconds % 60)
  return `${m}分${s}秒`
}

// 当前时刻与某个 ISO 时间戳的差值(秒);解析失败返回 null,交由调用方决定兜底展示
function elapsedSince(iso: string | undefined): number | null {
  if (!iso) return null
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return null
  return Math.max(0, (Date.now() - t) / 1000)
}

// 总耗时:已结束(有 pipeline_finished_at)用两个时间戳的差值(稳定不再跳动);
// 进行中则用 started_at 到现在现算(靠轮询触发的 re-render 更新)。
function totalElapsedSeconds(status: Record<string, string>): number | null {
  const startedRaw = status['pipeline_started_at']
  if (!startedRaw) return null
  const started = Date.parse(startedRaw)
  if (Number.isNaN(started)) return null
  const finishedRaw = status['pipeline_finished_at']
  if (finishedRaw) {
    const finished = Date.parse(finishedRaw)
    if (Number.isNaN(finished) || finished < started) return null
    return (finished - started) / 1000
  }
  return Math.max(0, (Date.now() - started) / 1000)
}

export function ProgressSteps({ project }: { project: ProjectDetail }) {
  const running = project.pipeline === 'running' || project.pipeline === 'queued'
  const failed = project.pipeline.startsWith('error')
  // 第一个未完成步骤即“当前步”
  const currentIdx = STAGES.findIndex((s) => project.status[s.key] !== 'done')

  const totalElapsed = totalElapsedSeconds(project.status)
  const doneCount = STAGES.filter((s) => project.status[s.key] === 'done').length

  return (
    <div className="rounded-2xl border border-band bg-paper p-5 shadow-paper">
      <div className="mb-4 flex items-center gap-2.5">
        <CardHeadInline glyph="程" title="生成进度" />
        {running && <GeneratingBars />}
        <span className="text-xs tracking-wide text-muted">
          {running ? '正在生成…' : project.pipeline === 'done' ? '全部完成' : project.pipeline}
        </span>
        <span className="ml-auto flex items-center gap-3 text-xs tracking-wide text-muted">
          <span className="tabular-nums">
            {doneCount}/{STAGES.length} 环节
          </span>
          {totalElapsed !== null && <span className="tabular-nums">总耗时 {formatElapsed(totalElapsed)}</span>}
        </span>
      </div>

      <ol className="flex flex-col gap-3">
        {STAGES.map((s, i) => {
          const isCurrent = running && i === currentIdx
          const stepStatus =
            failed && i === currentIdx ? 'failed' : project.status[s.key]
          const c = cell(stepStatus, isCurrent)
          const elapsedRaw = project.status[`${s.key}_elapsed_s`]
          const startedAt = project.status[`${s.key}_started_at`]
          const elapsedNum = elapsedRaw !== undefined ? Number(elapsedRaw) : NaN

          let timeText: string | null = null
          if (!Number.isNaN(elapsedNum)) {
            timeText = formatElapsed(elapsedNum)
          } else if (isCurrent && startedAt) {
            const secs = elapsedSince(startedAt)
            if (secs !== null) timeText = `进行中 · 约 ${formatElapsed(secs)}`
          }

          // 悬停提示:该环节的起止时刻(本地时区)。结束时刻由 started_at + elapsed_s 推算,
          // 未存独立的 finished_at 字段;尚未结束(仅有 started_at)则只显示"开始"。
          const startedDate = startedAt ? new Date(startedAt) : null
          const finishedDate =
            startedDate && !Number.isNaN(elapsedNum)
              ? new Date(startedDate.getTime() + elapsedNum * 1000)
              : null
          const timeTooltip = startedDate
            ? `开始:${startedDate.toLocaleTimeString()}` +
              (finishedDate ? ` · 结束:${finishedDate.toLocaleTimeString()}` : ' · 进行中')
            : undefined

          // S4 逐页生图耗时较长,当前步是它时额外显示"已出图 N/M 页",不用干等一个笼统的"生成中"
          const pageProgress =
            isCurrent && s.key === 's4' && project.content_summary.total > 0
              ? `${project.content_summary.imaged}/${project.content_summary.total} 页`
              : null

          return (
            <li key={s.key} className="flex items-center gap-3" title={timeTooltip}>
              <span
                className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-sm font-bold ${c.dot}`}
              >
                {c.tag}
              </span>
              <span className="flex flex-1 items-baseline gap-2">
                <span className={`font-serif text-[13px] font-semibold tracking-wide ${c.label}`}>
                  {s.label}
                </span>
                <span className={`text-[9px] tracking-[2px] ${c.sub}`}>{s.sub}</span>
                {pageProgress && (
                  <span className="text-xs tabular-nums text-cinnabar">{pageProgress}</span>
                )}
              </span>
              {timeText && <span className="text-xs tabular-nums text-muted">{timeText}</span>}
            </li>
          )
        })}
      </ol>
    </div>
  )
}
