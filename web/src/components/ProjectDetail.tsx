import { useState } from 'react'
import { api } from '../api'
import type { ProjectDetail as Detail, Character, Page } from '../types'
import { ProgressSteps } from './ProgressSteps'

const EMOTION_STYLE: Record<string, string> = {
  温情: 'bg-[#ede1c9] text-gold',
  惊变: 'bg-[#f0dad5] text-cinnabar',
  悲壮: 'bg-[#f0dad5] text-cinnabar',
  险境: 'bg-[#dee7de] text-jade',
  烟雨: 'bg-[#dce6e9] text-azurite',
  苍凉: 'bg-[#dce6e9] text-azurite',
}
function emotionCls(e: string): string {
  return EMOTION_STYLE[e] ?? 'bg-kraft text-ink-soft'
}

function SectionTitle({ glyph, children, extra }: { glyph: string; children: React.ReactNode; extra?: React.ReactNode }) {
  return (
    <div className="mb-4 flex items-center gap-2.5">
      <span className="flex h-7 w-7 items-center justify-center rounded-md bg-cinnabar font-brush text-lg text-rice">
        {glyph}
      </span>
      <h2 className="font-serif text-base font-semibold tracking-wide text-ink">{children}</h2>
      {extra && <span className="text-xs text-muted">{extra}</span>}
      <span className="ml-1 h-px flex-1 bg-gradient-to-r from-line to-transparent" />
    </div>
  )
}

const card = 'rounded-2xl border border-band bg-paper p-5 shadow-paper'

export function ProjectDetailView({ project }: { project: Detail }) {
  return (
    <div className="space-y-6">
      {/* 标题头 */}
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-center gap-3.5">
          <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-cinnabar to-cinnabar-deep font-brush text-2xl text-rice shadow-[0_3px_10px_rgba(138,43,34,0.28)]">
            {project.scenic_spot.slice(0, 1)}
          </span>
          <div>
            <h1 className="font-serif text-2xl font-bold tracking-[2px] text-ink">
              {project.script_title ?? project.scenic_spot}
            </h1>
            <p className="mt-1 text-[13px] tracking-wide text-muted">
              {project.scenic_spot} · {project.style_preset} · {project.params.duration_min} 分钟 ·{' '}
              {project.params.audience} · {project.params.tone}
            </p>
          </div>
        </div>
      </div>

      <ProgressSteps project={project} />

      {/* 成片 */}
      {project.mp4 && (
        <div className={card}>
          <SectionTitle glyph="片">有声连环画 · 成片</SectionTitle>
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
          <SectionTitle glyph="人" extra={`${project.characters.length} 位角色`}>
            人物设定 · 三视图
          </SectionTitle>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
            {project.characters.map((c) => (
              <CharacterCard key={c.name} c={c} />
            ))}
          </div>
        </div>
      )}

      {/* 漫画页 */}
      {project.pages.length > 0 && (
        <div className={card}>
          <SectionTitle glyph="画" extra={`共 ${project.pages.length} 页`}>
            连环画 · 逐页
          </SectionTitle>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            {project.pages.map((pg) => (
              <PageCard key={pg.index} pg={pg} />
            ))}
          </div>
        </div>
      )}

      {/* 传说来源 */}
      {project.legend && (
        <div className={`${card} border-l-[3px] border-l-cinnabar`}>
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
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mt-3 flex flex-wrap gap-2">
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

function CharacterCard({ c }: { c: Character }) {
  return (
    <figure className="overflow-hidden rounded-xl border border-line bg-white/60">
      <div className="relative aspect-[3/4] bg-kraft">
        {c.image ? (
          <img src={c.image} alt={c.name} className="h-full w-full object-cover" />
        ) : (
          <div className="flex h-full items-center justify-center text-xs text-muted">未生成</div>
        )}
        <span className="absolute left-2 top-2 rounded-full bg-ink/70 px-2 py-0.5 text-[10px] tracking-wide text-rice">
          三视图
        </span>
      </div>
      <figcaption className="px-3 py-2.5">
        <div className="font-serif text-sm font-semibold tracking-wide text-ink">{c.name}</div>
        <div className="text-[11px] text-muted">{c.role}</div>
      </figcaption>
    </figure>
  )
}

function PageCard({ pg }: { pg: Page }) {
  return (
    <div className="overflow-hidden rounded-xl border border-line bg-white/60">
      <div className="relative aspect-[4/3] bg-kraft">
        {pg.image ? (
          <img src={pg.image} alt={`第 ${pg.index} 页`} className="h-full w-full object-cover" />
        ) : (
          <div className="flex h-full items-center justify-center text-sm text-muted">
            第 {pg.index} 页 · {pg.status}
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
      </div>
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
          {pg.status === 'failed' && <span className="text-cinnabar">生成失败</span>}
        </div>
        {pg.audio && (
          <audio src={pg.audio} controls className="h-9 w-full" />
        )}
      </div>
    </div>
  )
}
