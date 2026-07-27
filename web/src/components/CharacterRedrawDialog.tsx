import { useEffect } from 'react'

const primaryBtn =
  'rounded-lg bg-cinnabar px-3 py-1.5 text-xs font-medium tracking-wide text-rice transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-50'
const ghostBtn =
  'rounded-lg border border-line px-3 py-1.5 text-xs text-ink-soft transition hover:border-cinnabar hover:text-cinnabar disabled:cursor-not-allowed disabled:opacity-40'

const MAX_LISTED = 6

// 重绘角色三视图时,若已有按旧设定图生成好的漫画页引用该角色,弹窗警示并让用户
// 选择是否把这些页一并标记重绘,否则用户容易忘记、留下形象不一致的成片。
// 仿 ImageLightbox 的裸弹窗写法,不引入弹窗库。
export function CharacterRedrawDialog({
  characterName,
  affectedPages,
  busy,
  title,
  intro,
  onConfirm,
  onCancel,
}: {
  characterName: string
  affectedPages: { index: number; caption: string }[]
  busy: boolean
  // 可选:供上传参考图后复用本弹窗时覆盖文案,不传则与原文案一字不差
  title?: string
  intro?: string
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

  const shown = affectedPages.slice(0, MAX_LISTED)
  const more = affectedPages.length - shown.length

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-ink/80 p-6"
      onClick={onCancel}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-md rounded-2xl border border-band bg-paper p-5 shadow-paper-lg"
      >
        <h3 className="font-serif text-sm font-semibold tracking-wide text-ink">
          {title ?? `重绘「${characterName}」设定图`}
        </h3>
        <p className="mt-2 text-xs text-ink-soft">
          {intro ??
            `以下 ${affectedPages.length} 页漫画页是按旧设定图生成的,设定图重绘后若不一并重绘,
          画面中该角色的形象会与新设定图不一致。`}
        </p>
        <ul className="mt-2 max-h-40 space-y-1 overflow-y-auto rounded-lg border border-line bg-white/50 px-3 py-2 text-[11px] text-ink-soft">
          {shown.map((p) => (
            <li key={p.index} className="truncate">
              第 {p.index} 页 · {p.caption}
            </li>
          ))}
          {more > 0 && <li className="text-muted">等共 {more} 页未列出</li>}
        </ul>
        <div className="mt-4 flex flex-wrap justify-end gap-2">
          <button type="button" onClick={onCancel} disabled={busy} className={ghostBtn}>
            取消
          </button>
          <button type="button" onClick={() => onConfirm(false)} disabled={busy} className={ghostBtn}>
            {busy ? '处理中…' : '仅重绘设定图'}
          </button>
          <button type="button" onClick={() => onConfirm(true)} disabled={busy} className={primaryBtn}>
            {busy ? '处理中…' : '设定图 + 一并标记这些页面重绘'}
          </button>
        </div>
      </div>
    </div>
  )
}
