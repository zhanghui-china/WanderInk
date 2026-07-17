import { useState } from 'react'
import { api } from '../api'
import { CardHead } from './decor'
import { STYLE_LABEL } from '../styles'
import type { Meta } from '../types'

export function NewProjectForm({
  meta,
  onCreated,
}: {
  meta: Meta | null
  onCreated: (id: string) => void
}) {
  const [spot, setSpot] = useState('')
  const [minutes, setMinutes] = useState(3)
  const [audience, setAudience] = useState('大众')
  const [tone, setTone] = useState('温情')
  const [style, setStyle] = useState('guofeng_ink')
  const [story, setStory] = useState('')
  const [voice, setVoice] = useState('')
  const [speed, setSpeed] = useState(1.0)
  const [multiPanel, setMultiPanel] = useState(false)
  const [masterSkill, setMasterSkill] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const ro = !!meta?.readonly   // 公开演示只读:禁用生成

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setErr(null)
    try {
      const { project_id } = await api.create({
        scenic_spot: spot.trim(),
        minutes,
        audience,
        tone,
        style,
        story: story.trim() || null,
        voice,
        speed,
        multi_panel: multiPanel,
        master_skill: masterSkill,
      })
      setSpot('')
      setStory('')
      onCreated(project_id)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const field =
    'w-full rounded-lg border border-line bg-white/70 px-3 py-2 text-sm text-ink outline-none transition focus:border-cinnabar focus:bg-white'
  const label = 'mb-1.5 block text-xs font-medium tracking-wide text-muted'

  const chips = ['雷峰塔', '黄鹤楼', '莫高窟', '峨眉山']

  return (
    <form
      onSubmit={submit}
      className="space-y-4 rounded-2xl border border-band bg-paper p-5 shadow-paper"
    >
      <CardHead glyph="新" title="新建作品" />

      <div>
        <label className={label}>景区名</label>
        <input
          className={field}
          value={spot}
          onChange={(e) => setSpot(e.target.value)}
          placeholder="如：雷峰塔、黄鹤楼"
          required
        />
        <div className="mt-2 flex flex-wrap gap-1.5">
          {chips.map((c) => (
            <button
              key={c}
              type="button"
              onClick={() => setSpot(c)}
              className="rounded-full border border-line bg-white/50 px-2.5 py-1 text-xs text-ink-soft transition hover:border-cinnabar hover:text-cinnabar"
            >
              {c}
            </button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className={label}>时长(分钟)</label>
          <select className={field} value={minutes} onChange={(e) => setMinutes(+e.target.value)}>
            {(meta?.minutes ?? [1, 3, 5]).map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className={label}>受众</label>
          <select className={field} value={audience} onChange={(e) => setAudience(e.target.value)}>
            {(meta?.audiences ?? ['大众']).map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className={label}>基调</label>
          <select className={field} value={tone} onChange={(e) => setTone(e.target.value)}>
            {(meta?.tones ?? ['温情']).map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className={label}>画风</label>
          <select className={field} value={style} onChange={(e) => setStyle(e.target.value)}>
            {(meta?.styles ?? ['guofeng_ink']).map((s) => (
              <option key={s} value={s}>
                {STYLE_LABEL[s] ?? s}
              </option>
            ))}
          </select>
        </div>
        {meta?.voices && meta.voices.length > 0 && (
          <div>
            <label className={label}>音色</label>
            <select className={field} value={voice} onChange={(e) => setVoice(e.target.value)}>
              <option value="">默认</option>
              {meta.voices.map((v) => (
                <option key={v} value={v}>
                  {v}
                </option>
              ))}
            </select>
          </div>
        )}
        <div>
          <label className={label}>语速</label>
          <select className={field} value={speed} onChange={(e) => setSpeed(+e.target.value)}>
            <option value={0.8}>0.8</option>
            <option value={1.0}>1.0</option>
            <option value={1.2}>1.2</option>
          </select>
        </div>
      </div>

      <div className="flex items-center gap-2">
        <input
          id="multi-panel"
          type="checkbox"
          checked={multiPanel}
          onChange={(e) => setMultiPanel(e.target.checked)}
          className="h-4 w-4 rounded border-line accent-cinnabar"
        />
        <label htmlFor="multi-panel" className="text-xs text-ink-soft">
          启用分格排版(日式分镜)
        </label>
      </div>

      <div className="flex items-center gap-2">
        <input
          id="master-skill"
          type="checkbox"
          checked={masterSkill}
          onChange={(e) => setMasterSkill(e.target.checked)}
          className="h-4 w-4 rounded border-line accent-cinnabar"
        />
        <label htmlFor="master-skill" className="text-xs text-ink-soft">
          使用编剧/导演大师skill
        </label>
      </div>

      <div>
        <label className={label}>自备故事(可选,留空则自动检索传说)</label>
        <textarea
          className={`${field} h-20 resize-none`}
          value={story}
          onChange={(e) => setStory(e.target.value)}
          placeholder="粘贴一段故事文本…"
        />
      </div>

      {err && (
        <p className="rounded-md bg-alarm/8 px-3 py-2 text-sm text-alarm">{err}</p>
      )}

      <button
        type="submit"
        disabled={busy || !spot.trim() || ro}
        className="flex w-full items-center justify-center gap-2 rounded-lg bg-gradient-to-br from-cinnabar to-cinnabar-deep px-4 py-2.5 font-serif text-sm font-semibold tracking-[3px] text-rice shadow-[0_4px_12px_rgba(138,43,34,0.28)] transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-40"
      >
        {ro ? '公开演示 · 仅浏览' : busy ? '提交中…' : '开始生成'}
      </button>
      {ro && (
        <p className="text-center text-xs text-muted">生成在所有者本机进行,此处仅浏览已有作品</p>
      )}
    </form>
  )
}
