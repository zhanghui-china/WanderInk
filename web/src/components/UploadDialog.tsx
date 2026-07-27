import { useEffect, useState, type ReactNode } from 'react'
import { CardHeadInline } from './decor'
import { GeneratingBars } from './GeneratingBars'
import type { Phase } from '../useUpload'

// 按钮类名与 CharacterRedrawDialog 逐字一致:项目现状就是每个弹窗文件各自局部定义一份,
// 这里保持一致而不抽公共文件,免得只为两个弹窗多出一层。
const primaryBtn =
  'rounded-lg bg-cinnabar px-3 py-1.5 text-xs font-medium tracking-wide text-rice transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-50'
const ghostBtn =
  'rounded-lg border border-line px-3 py-1.5 text-xs text-ink-soft transition hover:border-cinnabar hover:text-cinnabar disabled:cursor-not-allowed disabled:opacity-40'

// 内网上传常常零点几秒就满,进度条一闪而过反而像闪屏。延迟 250ms 才显形:传得快就压根
// 没出现过,传得慢才淡入。注意这不是假进度——不做 trickle/匀速补满,项目通篇是诚实状态
// (partial、不保留旧图冒充成功),造一根编出来的条与之矛盾。
const REVEAL_MS = 250

const PHASE_LABEL: Partial<Record<Phase, string>> = {
  uploading: '上传中…',
  processing: '校验并处理图片…',
  done: '参考图已保存,正在重新生成三视图…',
}

// 通用上传弹窗外壳:遮罩 / ESC / 题头 / 进度条 / 错误条 / 按钮区。与 CharacterRedrawDialog 一致,
// 弹窗只展示,不发请求——请求由调用方持有(它才知道成功后要接着做什么)。
// picker 是可替换的槽:图片场景塞 ImagePicker,将来录音场景塞录音条,其余部分原样复用。
export function UploadDialog({
  title,
  glyph,
  hint,
  picker,
  ready,
  phase,
  progress,
  indeterminate,
  error,
  confirmLabel,
  onConfirm,
  onCancel,
}: {
  title: string
  glyph: string
  hint?: string
  picker: ReactNode
  ready: boolean
  phase: Phase
  progress: number
  indeterminate: boolean
  error: string
  confirmLabel?: string
  onConfirm: () => void
  onCancel: () => void
}) {
  const busy = phase === 'uploading' || phase === 'processing'
  const [revealed, setRevealed] = useState(false)

  useEffect(() => {
    if (!busy) {
      setRevealed(false)
      return
    }
    const t = window.setTimeout(() => setRevealed(true), REVEAL_MS)
    return () => window.clearTimeout(t)
  }, [busy])

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      // 上传途中按 ESC 不该中断:要取消得走"取消"按钮,避免手滑丢掉已传的字节。
      if (e.key === 'Escape' && !busy) onCancel()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onCancel, busy])

  const showBar = busy && revealed
  const pct = Math.round(Math.min(Math.max(progress, 0), 1) * 100)

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-ink/80 p-6"
      onClick={() => {
        if (!busy) onCancel()
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-md rounded-2xl border border-band bg-paper p-5 shadow-paper-lg"
      >
        <CardHeadInline glyph={glyph} title={title} />
        {hint && <p className="mt-2 text-xs text-ink-soft">{hint}</p>}

        <div className="mt-3">{picker}</div>

        {showBar && (
          <div className="mt-3 animate-shy-rise">
            <div className="flex items-center gap-2">
              <span className="text-[11px] text-muted truncate">{PHASE_LABEL[phase]}</span>
              {indeterminate ? (
                // total 不可信时不画条,用现成的"生成中"竖条,而不是编一个百分比。
                <span className="ml-auto">
                  <GeneratingBars />
                </span>
              ) : (
                <span className="ml-auto tabular-nums text-[11px] text-cinnabar">{pct}%</span>
              )}
            </div>
            {!indeterminate && (
              <div className="mt-1.5 h-1.5 w-full rounded-full bg-rice-deep">
                {/* 渐变方向与 CardHeadInline 的方印同款,让这根条属于同一套视觉语言 */}
                <div
                  className="h-full rounded-full bg-gradient-to-r from-cinnabar-bright to-cinnabar-deep transition-[width] duration-200 ease-out"
                  style={{ width: `${pct}%` }}
                />
              </div>
            )}
          </div>
        )}

        {phase === 'done' && (
          <p className="mt-3 text-[11px] text-cinnabar">{PHASE_LABEL.done}</p>
        )}

        {error && (
          <p className="mt-3 rounded-lg border border-cinnabar/40 bg-cinnabar/5 px-3 py-2 text-[11px] text-cinnabar">
            {error}
          </p>
        )}

        <div className="mt-4 flex flex-wrap justify-end gap-2">
          <button type="button" onClick={onCancel} className={ghostBtn}>
            {busy ? '取消上传' : '取消'}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={!ready || busy || phase === 'done'}
            className={primaryBtn}
          >
            {busy ? '处理中…' : (confirmLabel ?? '上传')}
          </button>
        </div>
      </div>
    </div>
  )
}
