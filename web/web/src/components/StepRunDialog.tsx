import { useEffect } from 'react'

// 按钮类名与 CharacterRedrawDialog / UploadDialog 逐字一致:项目现状就是每个弹窗文件
// 各自局部定义一份(见 UploadDialog.tsx 顶部说明),这里保持一致而不抽公共文件。
const primaryBtn =
  'rounded-lg bg-cinnabar px-3 py-1.5 text-xs font-medium tracking-wide text-rice transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-50'
const ghostBtn =
  'rounded-lg border border-line px-3 py-1.5 text-xs text-ink-soft transition hover:border-cinnabar hover:text-cinnabar disabled:cursor-not-allowed disabled:opacity-40'

// 「补全重生成」里点某一步时的三出口弹窗:取消 / 只跑这一步 / 这一步 + 它作废的下游。
//
// 为什么必须给出「跑完下游」这个出口:后端 _run_one_step 只要这一步真的重生成了,
// **无论 cascade 是真是假**都会清掉下游产物(mp4/pdf/zip 与各下游步骤状态)。所以
// 「只跑这一步」必然把作品留在半成品状态,用户得自己再点两三次。此前只有分镜有这个
// 弹窗,角色/漫画页/配音都只有一个单出口确认框——那个不一致没有理由,是本组件的由来。
//
// cascadeLabels 来自后端 /api/meta 的 step_cascade,不在前端硬编码:界面说会跑哪几步,
// 就得真的跑哪几步。空名单的步骤(s6 合成)根本不该弹本组件,调用方负责拦。
export function StepRunDialog({
  stepLabel,
  cascadeLabels,
  busy,
  note,
  onConfirm,
  onCancel,
}: {
  stepLabel: string
  cascadeLabels: string[]
  busy: boolean
  // 可选补充说明,如分镜那句"角色三视图不受影响"。只有确有反直觉之处时才传。
  note?: string
  onConfirm: (cascade: boolean) => void
  onCancel: () => void
}) {
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onCancel()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onCancel])

  const joined = cascadeLabels.join('、')
  const quoted = cascadeLabels.map((l) => `「${l}」`).join('')

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-ink/80 p-6"
      onClick={onCancel}
    >
      <div
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-md rounded-2xl border border-band bg-paper p-5 shadow-paper-lg"
      >
        <h3 className="font-serif text-sm font-semibold tracking-wide text-ink">
          重新生成{stepLabel}
        </h3>
        <p className="mt-2 text-xs text-ink-soft">
          {stepLabel}会被重新生成,已有的<b>{joined}</b>(含附加语种轨)随之作废,旧的文件会被清理。
          {note && (
            <>
              <br />
              {note}
            </>
          )}
        </p>
        <div className="mt-4 flex flex-wrap justify-end gap-2">
          <button type="button" onClick={onCancel} disabled={busy} className={ghostBtn}>
            取消
          </button>
          <button
            type="button"
            onClick={() => onConfirm(false)}
            disabled={busy}
            className={ghostBtn}
          >
            只重跑{stepLabel}
          </button>
          <button
            type="button"
            onClick={() => onConfirm(true)}
            disabled={busy}
            className={primaryBtn}
          >
            {stepLabel} + {cascadeLabels.join(' + ')}
          </button>
        </div>
        <p className="mt-2 text-right text-[11px] text-muted">
          选「只重跑{stepLabel}」的话,之后需自己依次点{quoted}
        </p>
      </div>
    </div>
  )
}
