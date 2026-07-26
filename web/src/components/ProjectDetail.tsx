import { Fragment, useState } from 'react'
import { api } from '../api'
import { STYLE_LABEL } from '../styles'
import type { Meta, ProjectDetail as Detail, Character, Page } from '../types'
import { CardHead, Seal, mountFrame } from './decor'
import { CharacterRedrawDialog } from './CharacterRedrawDialog'
import { ImageLightbox } from './ImageLightbox'
import { ProgressSteps } from './ProgressSteps'

const EMOTION_STYLE: Record<string, string> = {
  温情: 'bg-[#dfeadf] text-jade',
  惊变: 'bg-[#f2ddd8] text-cinnabar',
  悲壮: 'bg-[#f2ddd8] text-cinnabar',
  险境: 'bg-[#dbe7dd] text-jade',
  烟雨: 'bg-[#d6e7e8] text-azurite',
  苍凉: 'bg-[#d6e7e8] text-azurite',
}
function emotionCls(e: string): string {
  return EMOTION_STYLE[e] ?? 'bg-kraft text-ink-soft'
}

const EMOTIONS = ['宁静', '温情', '惊变', '悲壮', '险境', '烟雨', '苍凉']

// 语种码 -> 中文标签。后端 /api/meta 的 track_langs 决定出现哪些语种,这里只管显示名。
const TRACK_LABEL: Record<string, string> = { en: '英文版' }

const STEP_ACTIONS: { name: string; label: string; destructive?: boolean }[] = [
  { name: 's2', label: '分镜', destructive: true },
  { name: 's3', label: '角色' },
  { name: 's4', label: '漫画页' },
  { name: 's5', label: '配音' },
  { name: 's6', label: '合成' },
]

// 各单步重跑按钮的真实前置条件,对应各 step 模块自己的守卫(s2/s3 需先完成 S1,
// s4/s5/s6 需先有分镜)。按钮不检查这个会让用户点了必然失败的操作——
// 后端 400 才提示"先完成 S1",体验上是死胡同(见 2026-07-14 zhanghui 花果山事件)。
function stepReady(name: string, project: Detail): boolean {
  const hasScript = project.script_title != null
  const hasPages = project.pages.length > 0
  if (name === 's2' || name === 's3') return hasScript
  return hasPages
}

const card = 'rounded-2xl border border-band bg-paper p-5 shadow-paper'
const fieldCls =
  'w-full rounded-lg border border-line bg-white/70 px-2.5 py-1.5 text-[13px] text-ink outline-none transition focus:border-cinnabar focus:bg-white'
const primaryBtn =
  'rounded-lg bg-cinnabar px-3 py-1.5 text-xs font-medium tracking-wide text-rice transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-50'
const ghostBtn =
  'rounded-lg border border-line px-3 py-1.5 text-xs text-ink-soft transition hover:border-cinnabar hover:text-cinnabar disabled:cursor-not-allowed disabled:opacity-40'
const toolBtn =
  'rounded-md border border-line bg-white/50 px-2 py-1 text-[11px] text-ink-soft transition hover:border-cinnabar hover:text-cinnabar disabled:cursor-not-allowed disabled:opacity-40'

export function ProjectDetailView({
  project,
  meta,
  onChanged,
}: {
  project: Detail
  meta: Meta | null
  onChanged: () => void
}) {
  const [dragIndex, setDragIndex] = useState<number | null>(null)
  const [insertAfter, setInsertAfter] = useState<number | null>(null)
  const [stepBusy, setStepBusy] = useState<string | null>(null)
  const [trackBusy, setTrackBusy] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  const generating = project.pipeline === 'queued' || project.pipeline === 'running'
  const editable = !meta?.readonly && !generating
  const pendingCount = project.pages.filter(
    (p) => p.status === 'draft' || p.status === 'failed' || !p.audio,
  ).length

  function handleDrop(targetIndex: number) {
    const from0 = dragIndex
    setDragIndex(null)
    if (from0 === null || from0 === targetIndex) return
    const order = project.pages.map((p) => p.index)
    const from = order.indexOf(from0)
    const to = order.indexOf(targetIndex)
    if (from === -1 || to === -1) return
    order.splice(from, 1)
    order.splice(to, 0, from0)
    api
      .reorderCells(project.project_id, order)
      .then(onChanged)
      .catch((e) => alert(e instanceof Error ? e.message : String(e)))
  }

  async function copyLink() {
    try {
      await navigator.clipboard.writeText(window.location.href)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e))
    }
  }

  async function handleTrack(lang: string) {
    if (!window.confirm(
      `确定生成${TRACK_LABEL[lang] ?? lang}?会把中文解说逐页翻译、合成该语种配音并另出一支成片,` +
      `耗时与配音+合成相当。中文成片不受影响。`)) return
    setTrackBusy(lang)
    try {
      await api.runTrack(project.project_id, lang)
      onChanged()
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e))
    } finally {
      setTrackBusy(null)
    }
  }

  async function handleStep(name: string, label: string) {
    if (!window.confirm(`确定重新执行「${label}」?这会清空之后各步骤的产物。`)) return
    setStepBusy(name)
    try {
      await api.runStep(project.project_id, name)
      onChanged()
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e))
    } finally {
      setStepBusy(null)
    }
  }

  return (
    <div className="space-y-6">
      {/* 标题头 */}
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-center gap-3.5">
          <span className="relative flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-cinnabar to-cinnabar-deep font-brush text-2xl text-rice shadow-[0_3px_10px_rgba(33,90,82,0.28)]">
            {project.scenic_spot.slice(0, 1)}
            <span className="absolute -bottom-2 -right-2">
              <Seal char="遗" size={26} rot={-10} />
            </span>
          </span>
          <div>
            <div className="mb-1 text-[11px] tracking-[3px] text-gold">卷 · 山海拾遗</div>
            <h1 className="font-serif text-2xl font-bold tracking-[2px] text-ink">
              {project.script_title ?? project.scenic_spot}
            </h1>
            <p className="mt-1 text-[13px] tracking-wide text-muted">
              {project.scenic_spot} · {STYLE_LABEL[project.style_preset] ?? project.style_preset} ·{' '}
              {project.params.duration_min} 分钟 ·{' '}
              {project.params.audience} · {project.params.tone}
            </p>
          </div>
        </div>
        <button type="button" onClick={copyLink} className={ghostBtn}>
          {copied ? '已复制' : '复制链接'}
        </button>
      </div>

      <ProgressSteps project={project} />

      {/* 编辑操作条 */}
      {!meta?.readonly && (
        <div className={card}>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs font-medium tracking-wide text-muted">补全重生成</span>
            {STEP_ACTIONS.map((s) => {
              const ready = stepReady(s.name, project)
              return (
                <button
                  key={s.name}
                  type="button"
                  onClick={() => handleStep(s.name, s.label)}
                  disabled={generating || stepBusy !== null || !ready}
                  title={ready ? undefined : '前置步骤尚未完成,暂不可执行'}
                  className={`${toolBtn} ${s.destructive ? 'text-alarm hover:border-alarm' : ''}`}
                >
                  {stepBusy === s.name ? '入队中…' : s.label}
                </button>
              )
            })}
            {pendingCount > 0 && (
              <span className="ml-auto rounded-full bg-amber2/15 px-2.5 py-1 text-xs text-gold">
                {pendingCount} 页待重生成
              </span>
            )}
          </div>
          {!generating && project.status.s0 === 'done' && project.script_title == null && (
            <p className="mt-3 text-xs text-alarm">
              剧本生成(S1)未完成,无法补全后续步骤,请新建项目重新生成。
            </p>
          )}
        </div>
      )}

      {/* 多语种轨:面向外国游客的另一支成片(共用同一套画面,只换译文与配音) */}
      {!meta?.readonly && (meta?.track_langs?.length ?? 0) > 0 && project.pages.length > 0 && (
        <div className={card}>
          <CardHead glyph="译" title="多语种" />
          <div className="space-y-3">
            {(meta?.track_langs ?? []).map((lang) => {
              const label = TRACK_LABEL[lang] ?? lang
              const url = project.track_mp4?.[lang]
              const translated = project.pages.filter((p) => p.tracks?.[lang]?.caption).length
              const voiced = project.pages.filter((p) => p.tracks?.[lang]?.audio).length
              return (
                <div key={lang} className="space-y-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-xs font-medium tracking-wide text-muted">{label}</span>
                    <span className="text-[11px] text-muted">
                      译文 {translated}/{project.pages.length} · 配音 {voiced}/{project.pages.length}
                    </span>
                    <button
                      type="button"
                      onClick={() => handleTrack(lang)}
                      disabled={generating || trackBusy !== null}
                      className={`${toolBtn} ml-auto`}
                    >
                      {trackBusy === lang ? '入队中…' : url ? `重新生成${label}` : `生成${label}`}
                    </button>
                  </div>
                  {url && (
                    <>
                      <video src={url} controls className="w-full rounded-xl border border-line bg-black" />
                      <a href={url} download className={ghostBtn}>
                        下载{label}成片
                      </a>
                    </>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* 成片 */}
      {project.mp4 && (
        <div className={card}>
          <CardHead glyph="片" title="有声连环画 · 成片" />
          <video
            src={project.mp4}
            controls
            className="w-full rounded-xl border border-line bg-black"
          />
          <ExportButtons project={project} />
        </div>
      )}

      {/* 角色三视图 */}
      {project.characters.length > 0 && (
        <div className={card}>
          <CardHead glyph="人" title="人物设定 · 三视图" extra={`${project.characters.length} 位角色`} />
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {project.characters.map((c) => (
              <CharacterCard
                key={c.name}
                c={c}
                pages={project.pages}
                projectId={project.project_id}
                editable={editable}
                onChanged={onChanged}
              />
            ))}
          </div>
        </div>
      )}

      {/* 漫画页 */}
      {project.pages.length > 0 && (
        <div className={card}>
          <CardHead glyph="画" title="连环画 · 逐页" extra={`共 ${project.pages.length} 页`} />
          {editable && (
            <button
              type="button"
              onClick={() => setInsertAfter(insertAfter === 0 ? null : 0)}
              className={`${ghostBtn} mb-3`}
            >
              + 插入首页
            </button>
          )}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            {insertAfter === 0 && (
              <InsertPageForm
                projectId={project.project_id}
                afterIndex={0}
                onDone={() => {
                  setInsertAfter(null)
                  onChanged()
                }}
                onCancel={() => setInsertAfter(null)}
              />
            )}
            {project.pages.map((pg) => (
              <Fragment key={pg.index}>
                <PageCard
                  pg={pg}
                  projectId={project.project_id}
                  trackLangs={meta?.track_langs ?? []}
                  editable={editable}
                  onChanged={onChanged}
                  dragIndex={dragIndex}
                  onDragStart={() => setDragIndex(pg.index)}
                  onDragOver={(e) => e.preventDefault()}
                  onDrop={() => handleDrop(pg.index)}
                  onDragEnd={() => setDragIndex(null)}
                  onInsertAfter={() => setInsertAfter(insertAfter === pg.index ? null : pg.index)}
                />
                {insertAfter === pg.index && (
                  <InsertPageForm
                    projectId={project.project_id}
                    afterIndex={pg.index}
                    onDone={() => {
                      setInsertAfter(null)
                      onChanged()
                    }}
                    onCancel={() => setInsertAfter(null)}
                  />
                )}
              </Fragment>
            ))}
          </div>
        </div>
      )}

      {/* 传说来源 */}
      {project.legend && (
        <div className={`relative ${card} border-l-[3px] border-l-cinnabar`}>
          <span className="absolute right-5 top-5">
            <Seal char="源" size={38} rot={6} />
          </span>
          <div className="mb-1 flex items-center gap-2">
            <h2 className="font-serif text-base font-semibold tracking-wide text-ink">
              {project.legend.title}
            </h2>
            <span className="rounded-full border border-line px-2 py-0.5 text-[11px] text-muted">
              来源 · {project.legend.source_type}
            </span>
          </div>
          <p className="text-sm leading-loose text-ink-soft">{project.legend.summary}</p>
        </div>
      )}
    </div>
  )
}

function ExportButtons({ project }: { project: Detail }) {
  const [pdf, setPdf] = useState(project.pdf)
  const [zip, setZip] = useState(project.zip)
  const [busy, setBusy] = useState(false)

  const btn =
    'inline-flex items-center gap-1.5 rounded-lg border border-line bg-white/60 px-3.5 py-2 text-xs font-medium tracking-wide text-ink-soft transition hover:border-cinnabar hover:text-cinnabar disabled:cursor-not-allowed disabled:opacity-50'

  async function handleClick(kind: 'pdf' | 'zip') {
    setBusy(true)
    try {
      const res = await api.exportProject(project.project_id)
      setPdf(res.pdf)
      setZip(res.zip)
      const url = kind === 'pdf' ? res.pdf : res.zip
      if (url) window.open(url, '_blank')
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mt-3 flex flex-wrap gap-2">
      <a href={project.mp4 ?? undefined} download className={btn}>
        下载完整成片
      </a>
      {pdf ? (
        <a href={pdf} download className={btn}>
          下载 PDF
        </a>
      ) : (
        <button type="button" onClick={() => handleClick('pdf')} disabled={busy} className={btn}>
          {busy ? '打包中…' : '下载 PDF'}
        </button>
      )}
      {zip ? (
        <a href={zip} download className={btn}>
          下载图片包
        </a>
      ) : (
        <button type="button" onClick={() => handleClick('zip')} disabled={busy} className={btn}>
          {busy ? '打包中…' : '下载图片包'}
        </button>
      )}
    </div>
  )
}

function CharacterCard({
  c,
  pages,
  projectId,
  editable,
  onChanged,
}: {
  c: Character
  pages: Page[]
  projectId: string
  editable: boolean
  onChanged: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [lightboxOpen, setLightboxOpen] = useState(false)
  const [dialogOpen, setDialogOpen] = useState(false)

  // 独立于 toolBtn 定义(不叠加覆盖字号):卡片变宽后两个按钮各占一半、居中、不换行
  const charBtn =
    'flex-1 justify-center whitespace-nowrap rounded-md border border-line bg-white/50 px-2 py-1 text-center text-[10px] text-ink-soft transition hover:border-cinnabar hover:text-cinnabar disabled:cursor-not-allowed disabled:opacity-40'

  // 该角色设定图重绘后,哪些已生成页会因画风不一致而过期(仅已成功出图的 confirmed 页算数)
  const affected = pages.filter(
    (p) => p.characters.includes(c.name) && p.status === 'confirmed' && p.image,
  )

  function redraw() {
    if (affected.length === 0) {
      if (!window.confirm('仅重绘设定图,已生成页面需自行重绘。确定继续?')) return
      void doRedraw(false)
      return
    }
    setDialogOpen(true)
  }

  async function doRedraw(cascade: boolean) {
    setDialogOpen(false)
    setBusy(true)
    try {
      await api.redrawCharacter(projectId, c.name)
      if (cascade) {
        for (const pg of affected) {
          await api.redrawCell(projectId, pg.index)
        }
      }
      onChanged()
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <figure className="overflow-hidden rounded-xl border border-line bg-white/60">
      <div className={`aspect-[3/2] bg-gradient-to-b from-kraft to-rice-deep ${mountFrame}`}>
        {c.image ? (
          <img src={c.image} alt={c.name} className="h-full w-full object-cover" />
        ) : (
          <div className="flex h-full items-center justify-center text-xs text-muted">
            <span className="font-scrawl text-2xl text-band">未生成</span>
          </div>
        )}
        <span className="absolute left-2 top-2 rounded-full bg-ink/70 px-2 py-0.5 text-[10px] tracking-wide text-rice">
          三视图
        </span>
        <span className="absolute right-2 top-2 rounded bg-cinnabar/85 px-1.5 py-0.5 font-serif text-[10px] text-rice">
          正·侧·背
        </span>
      </div>
      <figcaption className="px-3 py-2.5">
        <div className="font-serif text-sm font-semibold tracking-wide text-ink">{c.name}</div>
        <div className="text-[11px] text-muted">{c.role}</div>
        <div className="mt-2 flex gap-1.5">
          {c.image && (
            <button type="button" onClick={() => setLightboxOpen(true)} className={charBtn}>
              查看详情
            </button>
          )}
          {editable && (
            <button type="button" onClick={redraw} disabled={busy} className={charBtn}>
              {busy ? '重绘中…' : '重绘设定图'}
            </button>
          )}
        </div>
      </figcaption>
      {lightboxOpen && c.image && (
        <ImageLightbox src={c.image} alt={c.name} onClose={() => setLightboxOpen(false)} />
      )}
      {dialogOpen && (
        <CharacterRedrawDialog
          characterName={c.name}
          affectedPages={affected.map((p) => ({ index: p.index, caption: p.caption }))}
          busy={busy}
          onConfirm={(cascade) => void doRedraw(cascade)}
          onCancel={() => setDialogOpen(false)}
        />
      )}
    </figure>
  )
}

function TrackRow({
  projectId,
  pg,
  lang,
  editable,
  onChanged,
}: {
  projectId: string
  pg: Page
  lang: string
  editable: boolean
  onChanged: () => void
}) {
  const track = pg.tracks?.[lang]
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(track?.caption ?? '')
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const label = TRACK_LABEL[lang] ?? lang

  // 还没翻译过这一页就整行不渲染,避免每页都挂一条空壳
  if (!track?.caption && !editing) return null

  async function save() {
    setBusy('save')
    setErr(null)
    try {
      await api.patchCellTrack(projectId, pg.index, lang, text.trim())
      setEditing(false)
      onChanged()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  async function revoice() {
    if (!window.confirm(
      `确定重配第 ${pg.index} 页的${label}配音?改完后需要再点一次「重新生成${label}」才会合成。`
    )) return
    setBusy('revoice')
    try {
      await api.revoiceCellTrack(projectId, pg.index, lang)
      onChanged()
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="space-y-1.5 rounded-lg border border-line bg-white/40 p-2.5">
      <div className="flex items-center gap-2">
        <span className="text-[10px] tracking-[2px] text-muted">{label}</span>
        {track?.duration_ms ? (
          <span className="text-[11px] text-muted">{(track.duration_ms / 1000).toFixed(1)}s</span>
        ) : null}
        {editable && !editing && (
          <div className="ml-auto flex gap-1.5">
            <button
              type="button"
              onClick={() => {
                setText(track?.caption ?? '')
                setErr(null)
                setEditing(true)
              }}
              className={toolBtn}
            >
              校对
            </button>
            {track?.audio && (
              <button type="button" onClick={revoice} disabled={busy !== null} className={toolBtn}>
                {busy === 'revoice' ? '处理中…' : '重配音'}
              </button>
            )}
          </div>
        )}
      </div>
      {editing ? (
        <div className="space-y-1.5">
          <textarea
            className={`${fieldCls} h-16 resize-none`}
            value={text}
            maxLength={240}
            onChange={(e) => setText(e.target.value)}
          />
          <div className="flex items-center gap-1.5">
            <button type="button" onClick={save} disabled={busy !== null} className={primaryBtn}>
              {busy === 'save' ? '保存中…' : '保存'}
            </button>
            <button type="button" onClick={() => setEditing(false)} className={ghostBtn}>
              取消
            </button>
            <span className="text-[11px] text-muted">
              改动会作废本页{label}配音,需重新生成
            </span>
          </div>
          {err && <p className="text-[11px] text-alarm">{err}</p>}
        </div>
      ) : (
        <p className="text-[13px] leading-relaxed text-ink-soft">{track?.caption}</p>
      )}
      {track?.audio && !editing && (
        <audio src={track.audio} controls className="h-9 w-full" />
      )}
    </div>
  )
}

function InsertPageForm({
  projectId,
  afterIndex,
  onDone,
  onCancel,
}: {
  projectId: string
  afterIndex: number
  onDone: () => void
  onCancel: () => void
}) {
  const [caption, setCaption] = useState('')
  const [visualDesc, setVisualDesc] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  async function submit() {
    if (!caption.trim() || !visualDesc.trim()) return
    setBusy(true)
    setErr(null)
    try {
      await api.insertCell(projectId, afterIndex, {
        caption: caption.trim(),
        visual_desc: visualDesc.trim(),
      })
      onDone()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-2 rounded-xl border border-dashed border-cinnabar/40 bg-white/50 p-3.5 sm:col-span-2">
      <div className="text-[10px] tracking-[2px] text-muted">
        插入新页 · 位于第 {afterIndex} 页之后
      </div>
      <textarea
        className={`${fieldCls} h-14 resize-none`}
        placeholder="画面描述"
        value={visualDesc}
        onChange={(e) => setVisualDesc(e.target.value)}
      />
      <textarea
        className={`${fieldCls} h-14 resize-none`}
        placeholder="旁白"
        maxLength={80}
        value={caption}
        onChange={(e) => setCaption(e.target.value)}
      />
      {err && <p className="text-xs text-alarm">{err}</p>}
      <div className="flex gap-2">
        <button
          type="button"
          onClick={submit}
          disabled={busy || !caption.trim() || !visualDesc.trim()}
          className={primaryBtn}
        >
          {busy ? '插入中…' : '确定插入'}
        </button>
        <button type="button" onClick={onCancel} className={ghostBtn}>
          取消
        </button>
      </div>
    </div>
  )
}

function PageCard({
  pg,
  projectId,
  trackLangs,
  editable,
  onChanged,
  dragIndex,
  onDragStart,
  onDragOver,
  onDrop,
  onDragEnd,
  onInsertAfter,
}: {
  pg: Page
  projectId: string
  trackLangs: string[]
  editable: boolean
  onChanged: () => void
  dragIndex: number | null
  onDragStart: () => void
  onDragOver: (e: React.DragEvent) => void
  onDrop: () => void
  onDragEnd: () => void
  onInsertAfter: () => void
}) {
  const [editing, setEditing] = useState(false)
  const [caption, setCaption] = useState(pg.caption)
  const [visualDesc, setVisualDesc] = useState(pg.visual_desc)
  const [emotion, setEmotion] = useState(pg.emotion)
  const [characters, setCharacters] = useState<string[]>(pg.characters)
  const [charInput, setCharInput] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [lightboxOpen, setLightboxOpen] = useState(false)

  function startEdit() {
    setCaption(pg.caption)
    setVisualDesc(pg.visual_desc)
    setEmotion(pg.emotion)
    setCharacters(pg.characters)
    setCharInput('')
    setErr(null)
    setEditing(true)
  }

  async function save() {
    setBusy('save')
    setErr(null)
    try {
      await api.updateCell(projectId, pg.index, {
        caption: caption.trim(),
        visual_desc: visualDesc.trim(),
        emotion,
        characters,
      })
      setEditing(false)
      onChanged()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  async function act(kind: 'redraw' | 'revoice' | 'delete') {
    if (kind === 'delete' && !window.confirm(`确定删除第 ${pg.index} 页?此操作不可撤销。`)) return
    if (kind === 'redraw' &&
        !window.confirm(`确定重新生成第 ${pg.index} 页的图片?将调用配置的生图 API。`)) return
    if (kind === 'revoice' &&
        !window.confirm(`确定重新生成第 ${pg.index} 页的配音?将调用配置的 TTS API,并清空已合成的成片。`)) return
    setBusy(kind)
    try {
      if (kind === 'redraw') {
        await api.redrawCell(projectId, pg.index)
        try {
          await api.runStep(projectId, 's4') // 标记后立即触发 S4 重跑,只会重画本页等待中的页
        } catch (e) {
          // redrawCell 已清掉本页 image/output,若触发生成失败要刷新让用户看到已被清的状态,
          // 否则 UI 停在陈旧画面、以为还在;并给出可自行重试的明确指引。
          onChanged()
          alert(
            `已标记重绘,但触发生成失败:${e instanceof Error ? e.message : String(e)},可点漫画页步骤重试`,
          )
          return
        }
      }
      if (kind === 'revoice') {
        await api.revoiceCell(projectId, pg.index)
        try {
          await api.runStep(projectId, 's5') // 同 redraw:标记后立即触发 S5,只会重配本页(其余页幂等跳过)
        } catch (e) {
          // revoiceCell 已清掉本页 audio/output,若触发生成失败要刷新让用户看到已被清的状态。
          onChanged()
          alert(
            `已标记重配音,但触发生成失败:${e instanceof Error ? e.message : String(e)},可点配音步骤重试`,
          )
          return
        }
      }
      if (kind === 'delete') await api.deleteCell(projectId, pg.index)
      onChanged()
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  function addChar() {
    const v = charInput.trim()
    if (v && !characters.includes(v)) setCharacters([...characters, v])
    setCharInput('')
  }
  function removeChar(n: string) {
    setCharacters(characters.filter((c) => c !== n))
  }

  return (
    <div
      draggable={editable}
      onDragStart={onDragStart}
      onDragOver={onDragOver}
      onDrop={onDrop}
      onDragEnd={onDragEnd}
      className={`overflow-hidden rounded-xl border bg-white/60 transition ${
        dragIndex === pg.index ? 'border-cinnabar opacity-60' : 'border-line'
      }`}
    >
      <div className={`aspect-[4/3] bg-gradient-to-br from-kraft via-rice to-rice-deep ${mountFrame}`}>
        {pg.image ? (
          <img src={pg.image} alt={`第 ${pg.index} 页`} className="h-full w-full object-cover" />
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-1 text-sm text-muted">
            <span className="font-scrawl text-3xl text-band">第 {pg.index} 图</span>
            <span className="text-[11px]">{pg.status}</span>
          </div>
        )}
        {pg.scene_ref && (
          <span className="absolute left-2 top-2 rounded-md bg-ink/70 px-2 py-0.5 text-[10px] tracking-wide text-rice">
            {pg.scene_ref}
          </span>
        )}
        <span className="absolute right-2 top-2 flex h-8 w-8 items-center justify-center rounded-md border border-rice/60 font-brush text-lg text-rice">
          {pg.index}
        </span>
        {editable && (
          <span
            title="拖拽排序"
            className="absolute bottom-2 left-2 flex h-7 w-7 cursor-grab items-center justify-center rounded-md bg-ink/70 text-rice active:cursor-grabbing"
          >
            ⠿
          </span>
        )}
        {editable && (
          <button
            type="button"
            onClick={() => (editing ? setEditing(false) : startEdit())}
            title="编辑"
            className="absolute bottom-2 right-2 flex h-7 w-7 items-center justify-center rounded-md bg-ink/70 text-rice transition hover:bg-cinnabar"
          >
            ✎
          </button>
        )}
      </div>

      {editing ? (
        <div className="space-y-2.5 p-3.5">
          <div>
            <span className="text-[10px] tracking-[2px] text-muted">画面</span>
            <textarea
              className={`${fieldCls} mt-0.5 h-16 resize-none`}
              draggable={false}
              onMouseDown={(e) => e.stopPropagation()}
              value={visualDesc}
              onChange={(e) => setVisualDesc(e.target.value)}
            />
          </div>
          <div>
            <span className="text-[10px] tracking-[2px] text-muted">旁白</span>
            <textarea
              className={`${fieldCls} mt-0.5 h-16 resize-none`}
              maxLength={80}
              draggable={false}
              onMouseDown={(e) => e.stopPropagation()}
              value={caption}
              onChange={(e) => setCaption(e.target.value)}
            />
          </div>
          <div className="flex items-center gap-2">
            <span className="text-[10px] tracking-[2px] text-muted">情绪</span>
            <select
              className={`${fieldCls} w-auto py-1`}
              value={emotion}
              onChange={(e) => setEmotion(e.target.value)}
            >
              {EMOTIONS.map((em) => (
                <option key={em} value={em}>
                  {em}
                </option>
              ))}
            </select>
          </div>
          <div>
            <span className="text-[10px] tracking-[2px] text-muted">人物</span>
            <div className="mt-1 flex flex-wrap items-center gap-1.5">
              {characters.map((n) => (
                <span
                  key={n}
                  className="flex items-center gap-1 rounded-full border border-line bg-white/50 px-2 py-0.5 text-[10px] text-ink-soft"
                >
                  {n}
                  <button
                    type="button"
                    onClick={() => removeChar(n)}
                    className="text-muted hover:text-cinnabar"
                  >
                    ×
                  </button>
                </span>
              ))}
              <input
                className="w-16 rounded-full border border-dashed border-line bg-transparent px-2 py-0.5 text-[10px] text-ink outline-none focus:border-cinnabar"
                value={charInput}
                placeholder="+ 添加"
                onChange={(e) => setCharInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault()
                    addChar()
                  }
                }}
                onBlur={addChar}
              />
            </div>
          </div>
          {err && <p className="text-xs text-alarm">{err}</p>}
          <div className="flex gap-2 pt-1">
            <button type="button" onClick={save} disabled={busy === 'save'} className={primaryBtn}>
              {busy === 'save' ? '保存中…' : '保存'}
            </button>
            <button type="button" onClick={() => setEditing(false)} className={ghostBtn}>
              取消
            </button>
          </div>
        </div>
      ) : (
        <div className="space-y-2.5 p-3.5">
          {pg.visual_desc && (
            <div>
              <span className="text-[10px] tracking-[2px] text-muted">画面</span>
              <p className="mt-0.5 text-[13px] leading-relaxed text-ink-soft">{pg.visual_desc}</p>
            </div>
          )}
          <div>
            <span className="text-[10px] tracking-[2px] text-muted">旁白</span>
            <p className="mt-0.5 font-serif text-sm leading-relaxed text-ink">{pg.caption}</p>
          </div>
          {pg.characters.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {pg.characters.map((n) => (
                <span
                  key={n}
                  className="rounded-full border border-line bg-white/50 px-2 py-0.5 text-[10px] text-ink-soft"
                >
                  {n}
                </span>
              ))}
            </div>
          )}
          <div className="flex items-center gap-2 text-[11px]">
            <span className={`rounded-full px-2 py-0.5 tracking-wide ${emotionCls(pg.emotion)}`}>
              {pg.emotion}
            </span>
            {pg.duration_ms > 0 && (
              <span className="text-muted">{(pg.duration_ms / 1000).toFixed(1)}s</span>
            )}
            {pg.image_gen_ms > 0 && (
              <span className="text-muted">生成 {(pg.image_gen_ms / 1000).toFixed(1)}s</span>
            )}
            {pg.silent && pg.audio && (
              <span className="rounded-full bg-kraft px-2 py-0.5 text-muted">静音兜底</span>
            )}
            {pg.status === 'failed' && <span className="text-alarm">生成失败</span>}
          </div>
          {pg.audio && <audio src={pg.audio} controls className="h-9 w-full" />}

          {trackLangs.map((lang) => (
            <TrackRow
              key={lang}
              projectId={projectId}
              pg={pg}
              lang={lang}
              editable={editable}
              onChanged={onChanged}
            />
          ))}

          {(pg.image || editable) && (
            <div className="flex flex-wrap items-center gap-1.5 border-t border-line pt-2.5">
              {pg.image && (
                <button
                  type="button"
                  onClick={() => setLightboxOpen(true)}
                  className={toolBtn}
                >
                  查看漫画页
                </button>
              )}
              {editable && (
                <>
                  <button
                    type="button"
                    onClick={() => act('redraw')}
                    disabled={busy !== null}
                    className={toolBtn}
                  >
                    {busy === 'redraw' ? '重绘中…' : '重绘'}
                  </button>
                  <button
                    type="button"
                    onClick={() => act('revoice')}
                    disabled={busy !== null}
                    className={toolBtn}
                  >
                    {busy === 'revoice' ? '配音中…' : '重配音'}
                  </button>
                  <button
                    type="button"
                    onClick={onInsertAfter}
                    disabled={busy !== null}
                    className={toolBtn}
                  >
                    + 插入下一页
                  </button>
                  <button
                    type="button"
                    onClick={() => act('delete')}
                    disabled={busy !== null}
                    className={`${toolBtn} ml-auto text-alarm hover:border-alarm`}
                  >
                    {busy === 'delete' ? '删除中…' : '删除'}
                  </button>
                </>
              )}
            </div>
          )}
        </div>
      )}
      {lightboxOpen && pg.image && (
        <ImageLightbox src={pg.image} alt={`第 ${pg.index} 页`} onClose={() => setLightboxOpen(false)} />
      )}
    </div>
  )
}
