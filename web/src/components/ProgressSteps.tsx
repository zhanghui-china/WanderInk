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

// 秒数格式化:统一"M分S秒"体系,但 0 分钟时省掉"0分"前缀——单步重跑后小数值(几秒)
// 变得常见,"0分2秒"读起来啰嗦,"2秒"更直观;分钟数不为 0 时体系不变。
function formatElapsed(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.round(seconds % 60)
  return m === 0 ? `${s}秒` : `${m}分${s}秒`
}

// 当前时刻与某个 ISO 时间戳的差值(秒);解析失败返回 null,交由调用方决定兜底展示
function elapsedSince(iso: string | undefined): number | null {
  if (!iso) return null
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return null
  return Math.max(0, (Date.now() - t) / 1000)
}

// 把 ISO 时间戳格式化为本地时间;若其本地日期不是今天,前面加上"MM-DD"——
// 长任务跨天、或翻看几天前的旧项目时,只有时分秒会让人误判是"刚刚"。
// 解析失败(Invalid Date)兜底返回原始字符串,不让页面崩掉。
function formatStamp(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const now = new Date()
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate()
  const time = d.toLocaleTimeString()
  if (sameDay) return time
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  return `${mm}-${dd} ${time}`
}

// 总耗时 = 各环节 elapsed_s 之和,不是墙钟 pipeline_started_at→finished_at 的差值——
// 单步重跑每次都会重置 pipeline_started_at,墙钟差值只会反映"最后一次重跑"的耗时,
// 与用户肉眼数着 7 行加起来的数对不上。这里必须严格等于下面渲染出的那几行之和。
// 正在跑的那一格现算计入,否则它跑完的瞬间总数会突然跳一大截。
// 注意 Number("") === 0,所以用 Number.isFinite 过滤空字符串/未定义,不能只判断真值。
function totalElapsedSeconds(status: Record<string, string>, currentIdx: number): number | null {
  let total = 0
  let hasAny = false
  STAGES.forEach((s, i) => {
    // 正在跑的那一格优先用 _running_since 现算,**不能**用 _elapsed_s——单步重跑时后端
    // 刻意保留着上一轮的 _elapsed_s(空跑守卫要拿它判断"以前跑过"),若先命中它,整个
    // 重跑期间总耗时会完全冻结、该行还会拿旧数字冒充"已完成"。
    const live = i === currentIdx ? elapsedSince(status[`${s.key}_running_since`]) : null
    if (live !== null) {
      total += live
      hasAny = true
      return
    }
    const raw = status[`${s.key}_elapsed_s`]
    const n = Number(raw)
    if (raw !== undefined && raw !== '' && Number.isFinite(n)) {
      total += n
      hasAny = true
    }
  })
  return hasAny ? total : null
}

export function ProgressSteps({ project }: { project: ProjectDetail }) {
  const running = project.pipeline === 'running' || project.pipeline === 'queued'
  const failed = project.pipeline.startsWith('error')
  // 第一个未完成步骤即“当前步”;partial 是合法降级完成态,视为已推进,
  // 否则当前步指针/失败标红会错位到上游 partial 环节而非真正出错的环节
  const currentIdx = STAGES.findIndex(
    (s) => project.status[s.key] !== 'done' && project.status[s.key] !== 'partial',
  )

  // 只有 running 时才把"正在跑的那一格"现算进总耗时;不在跑时 currentIdx 可能是上次
  // 失败残留的指针,不该被当成"正在进行"去累加一个不断增长的数字。
  const totalElapsed = totalElapsedSeconds(project.status, running ? currentIdx : -1)
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
          {totalElapsed !== null && (
            <span className="group/total relative tabular-nums">
              总耗时 {formatElapsed(totalElapsed)}
              {/* 头部这个总耗时是 Σ 各环节,和下面的墙钟时间(本次作业起止)不必相等——
                  中间可能有排队等待或人工停顿,两者并存不冲突,不是 bug。 */}
              {project.status['pipeline_started_at'] && (
                <span className="pointer-events-none absolute right-0 top-full z-10 mt-1 hidden whitespace-nowrap rounded-md bg-ink/90 px-2 py-1 text-[11px] normal-case tracking-normal text-rice shadow-paper-lg group-hover/total:block">
                  本次作业 {formatStamp(project.status['pipeline_started_at'])}
                  {' → '}
                  {project.status['pipeline_finished_at']
                    ? formatStamp(project.status['pipeline_finished_at'])
                    : '进行中'}
                </span>
              )}
            </span>
          )}
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
          const finishedAt = project.status[`${s.key}_finished_at`]
          // 正在跑的那一格用 _running_since 现算,优先级高于 _elapsed_s。后端在重跑期间
          // 刻意保留上一轮的 _elapsed_s(空跑守卫的判据),先读它会让这一行整场显示旧数字、
          // 看着像早就跑完了。_running_since 由后端在收尾时清掉,所以它有值就是真在跑。
          const liveSecs = isCurrent ? elapsedSince(project.status[`${s.key}_running_since`]) : null
          const elapsedNum = elapsedRaw !== undefined && elapsedRaw !== '' ? Number(elapsedRaw) : NaN

          let timeText: string | null = null
          if (liveSecs !== null) {
            timeText = `进行中 · 约 ${formatElapsed(liveSecs)}`
          } else if (!Number.isNaN(elapsedNum)) {
            timeText = formatElapsed(elapsedNum)
          }

          // 悬停提示恒存在,不再"没有 started_at 就不给提示"——环节被作废时计时键会被清掉,
          // 过去这种情况悬停什么都不出,用户因此以为这功能不存在。
          // 「尚未生成」一句同时覆盖"从未跑过"和"已作废待重生":两者在数据上无法区分
          //(clear_step_keys 把键全清了),与其猜一个可能说错的说法,不如给一句永远为真的。
          let timeTooltip: string
          if (liveSecs !== null) {
            timeTooltip = `开始:${formatStamp(project.status[`${s.key}_running_since`])} · 进行中`
          } else if (!startedAt) {
            timeTooltip = '尚未生成'
          } else if (!finishedAt) {
            // 07-17 之前的老作品只有 started_at、没有 finished_at。它们早就跑完了,
            // 说"进行中"是明确的错话,比不显示更容易被当成新 bug 报回来。
            timeTooltip = `开始:${formatStamp(startedAt)}` +
              (Number.isNaN(elapsedNum) ? '' : ` · 耗时 ${formatElapsed(elapsedNum)}`)
          } else {
            const dur = (Date.parse(finishedAt) - Date.parse(startedAt)) / 1000
            timeTooltip =
              `开始:${formatStamp(startedAt)} · 结束:${formatStamp(finishedAt)}` +
              (Number.isFinite(dur) && dur >= 0 ? ` · 耗时 ${formatElapsed(dur)}` : '')
          }

          // S3/S4 都是逐项生图、动辄几分钟,当前步是它们时显示实时计数,
          // 不用干等一个笼统的"生成中"。表以外的环节没有数字,行为不变。
          const cs = project.content_summary
          const counters: Record<string, [number, number, string] | undefined> = {
            s3: [cs.characters_imaged, cs.characters_total, '位角色'],
            s4: [cs.imaged, cs.total, '页'],
          }
          const counter = isCurrent ? counters[s.key] : undefined
          const pageProgress =
            counter && counter[1] > 0 ? `${counter[0]}/${counter[1]} ${counter[2]}` : null

          return (
            <li key={s.key} className="group relative flex items-center gap-3">
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
              {/* 原来用原生 title:约 1s 延迟才出、样式不可控,用户等不到就以为没这功能。
                  换成 CSS 悬浮层,零依赖、hover 即时出现。 */}
              <span className="pointer-events-none absolute left-11 top-full z-10 mt-1 hidden whitespace-nowrap rounded-md bg-ink/90 px-2 py-1 text-[11px] text-rice shadow-paper-lg group-hover:block">
                {timeTooltip}
              </span>
            </li>
          )
        })}
      </ol>
    </div>
  )
}
