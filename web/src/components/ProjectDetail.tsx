import { Fragment, useEffect, useState } from 'react'
import { api, characterReferenceTarget, voiceSampleTarget, type VoiceSample } from '../api'
import { STYLE_LABEL } from '../styles'
import { useUpload } from '../useUpload'
import type { Meta, ProjectDetail as Detail, Character, Page } from '../types'
import { CardHead, Seal, mountFrame } from './decor'
import { CharacterRedrawDialog } from './CharacterRedrawDialog'
import { ImageLightbox } from './ImageLightbox'
import { ImagePicker } from './ImagePicker'
import { ProgressSteps } from './ProgressSteps'
import { VoiceRecorder } from './VoiceRecorder'
import { UploadDialog } from './UploadDialog'

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

// 哪些生成路径拿不到 LoRA。只有 edit(带参考图,走 ComfyUI 的 image_edit 工作流)那条路
// 的模板里有 LoRA 节点;text2img 的 Text2IMGKrea2 模板没有,chat_api 模式发的是
// /chat/completions、LoRA 字段由对端自行处置(本仓库无从判定,一律按"不保证生效"讲)。
// mixed 只出现在分格页:各格参考图按 panel.characters 逐格算,一页里可能半数格走 text2img。
const LORA_MISS_SHORT: Record<string, string | undefined> = {
  text2img: '无参考图 · LoRA 未生效',
  chat: '对话式接口 · LoRA 不保证生效',
  mixed: '部分格无参考图 · LoRA 未全生效',
}
const LORA_MISS: Record<string, string | undefined> = {
  text2img: '该页没有已出三视图的角色,走的是文生图工作流,该工作流没有 LoRA 节点,所选 LoRA 对这一页不生效',
  chat: '该页走的是对话式图像接口(image_api_mode=chat_api),LoRA 参数由对端模型自行处置,不保证生效',
  mixed: '这是分格页,其中部分格没有角色参考图、走了文生图工作流,那些格没有应用所选 LoRA',
}

// 语种码 -> 中文标签。后端 /api/meta 的 track_langs 决定出现哪些语种,这里只管显示名。
const TRACK_LABEL: Record<string, string> = { en: '英文版' }

// 字幕语种码 -> <track> 的 srclang / label
const SUB_LABEL: Record<string, string> = { zh: '中文', en: 'English' }

// 浏览器的 HTML5 <video> **不解析 MP4 容器内的 mov_text 字幕轨**(Chrome/Firefox/Edge
// 一律忽略),网页里显示字幕唯一的办法就是 <track> 外挂 WebVTT。MP4 内嵌轨仍然保留——
// 下载后用 VLC / 景区播放设备看时靠的是它。
function SubTracks({ subtitles, defaultLang }: {
  subtitles?: Record<string, string | null>
  defaultLang: string
}) {
  if (!subtitles) return null
  return (
    <>
      {Object.entries(subtitles).map(([lang, url]) =>
        url ? (
          <track
            key={lang}
            kind="subtitles"
            src={url}
            srcLang={lang}
            label={SUB_LABEL[lang] ?? lang}
            default={lang === defaultLang}
          />
        ) : null,
      )}
    </>
  )
}

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

// 图还没出来时的占位。生成期间脉动、文案改成"生成中…",让用户在内容区也看得出在跑
//(此前三种状态——从没跑过 / 正在为它生图 / 跑失败了——视觉上完全一样)。
//
// ⚠️ 只能表达"待生成/生成中"这个**合并态**:S3/S4 都是并发跑的(CONCURRENCY=3),
// 前端只知道"哪些已经有图",无从得知此刻正在画哪一张。做成"指认某一张正在画"会是
// 编出来的信息。这不是漏做,是刻意的边界。
function Placeholder({ text, generating, failed }: {
  text: string
  generating: boolean
  failed?: boolean
}) {
  const label = failed ? '生成失败' : generating ? '生成中…' : '未生成'
  return (
    <div
      className={`flex h-full flex-col items-center justify-center gap-1 ${
        generating && !failed ? 'animate-shy-pulse' : ''
      }`}
    >
      <span className="font-scrawl text-2xl text-band">{text}</span>
      <span className={`text-[11px] ${failed ? 'text-alarm' : 'text-muted'}`}>{label}</span>
    </div>
  )
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

// 自备故事原文按空行分段;段内的单换行仍交给 whitespace-pre-wrap 保留。
// 只排版、不改动用户原文的任何一个字——叫「原文」就得是原文。整段没有换行的
// 长文本因此仍是一整段,那是用户自己贴进来的样子,系统不替他改写。
function paragraphs(text: string): string[] {
  return text
    .split(/\n\s*\n/)
    .map((s) => s.trim())
    .filter(Boolean)
}

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
  const [s2Open, setS2Open] = useState(false)
  const [sourcesOpen, setSourcesOpen] = useState(false)
  // 原文不随详情下发(详情每 2 秒轮询一次),点开时才拉一次;null = 还没拿到
  const [storyText, setStoryText] = useState<string | null>(null)
  const [voiceOpen, setVoiceOpen] = useState(false)
  const [voicePicked, setVoicePicked] = useState<{ blob: Blob; filename: string } | null>(null)
  const [voiceBusy, setVoiceBusy] = useState(false)
  const voiceUpload = useUpload<VoiceSample>()

  const generating = project.pipeline === 'queued' || project.pipeline === 'running'
  const editable = !meta?.readonly && !generating
  const pendingCount = project.pages.filter(
    (p) => p.status === 'draft' || p.status === 'failed' || !p.audio,
  ).length

  // 展开「故事来源」。自备故事的原文按需拉:同一作品拉到后缓存,重复展开不再请求;
  // 失败折回并保持 storyText 为 null,让下次展开还能重试。
  async function toggleSources() {
    if (sourcesOpen) {
      setSourcesOpen(false)
      return
    }
    setSourcesOpen(true)
    if (!project.has_story || storyText !== null) return
    try {
      const r = await api.story(project.project_id)
      setStoryText(r.story ?? '')
    } catch (e) {
      setSourcesOpen(false)
      alert(e instanceof Error ? e.message : String(e))
    }
  }

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

  /** 用 textarea + execCommand 复制。**已废弃但不能删**:navigator.clipboard 只在
   *  安全上下文(HTTPS 或 localhost/127.0.0.1)下存在,而本项目的实际访问方式是
   *  `http://<DGX 局域网 IP>:5000` 和 cpolar 隧道(见 docs/ops-dgx.md 的"访问地址"),
   *  普通 HTTP 下浏览器根本不定义 navigator.clipboard——execCommand 是那里唯一还能用的
   *  复制手段。看到 deprecated 就顺手删掉的话,这个 bug 立刻复活。 */
  function copyViaTextarea(text: string): boolean {
    const ta = document.createElement('textarea')
    ta.value = text
    // 不能用 display:none / visibility:hidden —— 那样 select() 选不中,复制会静默失败。
    ta.style.cssText = 'position:fixed;top:-1000px;opacity:0'
    document.body.appendChild(ta)
    ta.select()
    try {
      return document.execCommand('copy')
    } catch {
      return false
    } finally {
      ta.remove()
    }
  }

  async function copyLink() {
    const url = window.location.href
    const done = () => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    }
    // 逐级降级。第一层是现代 API(HTTPS/localhost 走这条,行为与改动前一致);
    // 判存在性而不是靠 try/catch 兜——此前直接点 .writeText,undefined 上取属性抛的
    // TypeError 被 alert 出来,用户看到的就是那句 "Cannot read properties of undefined"。
    if (navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(url)
        done()
        return
      } catch (e) {
        console.error('clipboard.writeText 失败,回退 execCommand', e)
      }
    }
    if (copyViaTextarea(url)) {
      done()
      return
    }
    // 两条都不通:prompt 的输入框内容是预选中的,Ctrl+C 就能拿走——
    // 这是没有剪贴板权限时唯一还能把文本交到用户手里的原生手段(alert 里的字没法方便地选)。
    window.prompt('浏览器不允许自动复制,请手动复制这个链接:', url)
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

  // 「分镜」重跑会作废漫画页/配音/合成(角色三视图不受影响——它依赖剧本不依赖分镜),
  // 所以单独给它一个三出口弹窗,而不是让用户点完再自己去点三次。其余步骤沿用原确认框。
  async function handleStep(name: string, label: string) {
    if (name === 's2') {
      setS2Open(true)
      return
    }
    if (!window.confirm(`确定重新执行「${label}」?这会清空之后各步骤的产物。`)) return
    void doStep(name, false)
  }

  async function doStep(name: string, cascade: boolean) {
    setS2Open(false)
    setStepBusy(name)
    try {
      await api.runStep(project.project_id, name, cascade)
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
              {project.params.voice && (
                <> · 音色 {project.params.voice.startsWith('clone:') ? '自定义' : project.params.voice}</>
              )}
            </p>
            {/* 剧本/分镜这两步实际用的引擎(模型 + 是否走大师 skill)。勾了大师开关但该环节
                后端不是 hermes-agent 时后端会静默退化,退化原因也写在这两个值里——在此之前
                这件事只 print 到服务端 stdout,事后完全无法回答"这部作品到底走没走 skill"。
                历史作品没有这两个键,整行不渲染;绝不写"未知",那是在替旧数据编造结论。 */}
            {(project.status.s1_engine || project.status.s2_engine) && (
              <p className="mt-0.5 text-[11px] tracking-wide text-muted">
                {project.status.s1_engine && <>剧本 {project.status.s1_engine}</>}
                {project.status.s1_engine && project.status.s2_engine && ' · '}
                {project.status.s2_engine && <>分镜 {project.status.s2_engine}</>}
              </p>
            )}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {!meta?.readonly && (
            <button type="button" onClick={() => setVoiceOpen(true)} className={ghostBtn}>
              换音色
            </button>
          )}
          <button type="button" onClick={copyLink} className={ghostBtn}>
            {copied ? '已复制' : '复制链接'}
          </button>
        </div>
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
                      <video
                        src={url}
                        controls
                        crossOrigin="anonymous"
                        className="w-full rounded-xl border border-line bg-black"
                      >
                        {/* 用该语种成片**自己那套**字幕:两条成片的每页画面时长不同
                            (中英配音长短不同),拿主片那套挂过来末页会超出片长永不显示。
                            老作品没有 track_subtitles 时回落主片那套——不完美但比没有强。 */}
                        <SubTracks
                          subtitles={project.track_subtitles?.[lang] ?? project.subtitles}
                          defaultLang={lang}
                        />
                      </video>
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
            crossOrigin="anonymous"
            className="w-full rounded-xl border border-line bg-black"
          >
            <SubTracks subtitles={project.subtitles} defaultLang="zh" />
          </video>
          {/* 配乐状态原先在这里,但它挂在 `{project.mp4 && ...}` 里面 —— 没出片的作品完全
              看不到,包括最该被看到的 failed(S5 跑完、S6 还没跑的那段)。已挪到「生成进度」
              卡的「配音」那一行(配乐本来就是在 S5 里跑的),那里常驻可见。 */}
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
                generating={generating}
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
          {/* auto-rows-fr:容器高度不定时 1fr 会把**所有行**拉到最高那一行的高度,这是"全部
              等高"的标准做法,不必用 JS 量高度。Grid 默认就 stretch,所以同一行内本来已经等高;
              参差只出在行与行之间——驱动是「画面」「旁白」的折行数(实测单个作品内 28~105 字)。
              has-[textarea]:auto-rows-auto:任一卡片展开时退回自动行高,否则一张卡进编辑态会把
              整片一起撑高。用 :has() 而不是把 editing 状态上提到父层,是因为全文 5 处 <textarea>
              都只在展开态出现(PageCard 2 处、TrackRow 1 处在 {editing ? ...} 内,InsertPageForm
              2 处只在插页表单打开时渲染),判据精确等价且零 prop 改动,还顺带覆盖了译文编辑与
              插页表单这两种同样会撑高的情形。:has() 需 Chrome 105+/Safari 15.4+/Firefox 121+,
              老浏览器上这条不生效 → 编辑时仍等高,是降级不是损坏。 */}
          <div className="grid grid-cols-1 auto-rows-fr gap-4 has-[textarea]:auto-rows-auto sm:grid-cols-2">
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
                  generating={generating}
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

      {/* 传说来源 / 自备故事。渲染条件里的 has_story 不能省:S0 未跑完、或 from_text 因敏感词
          报错时 legend 恒为 null,而原文早已落盘——那恰恰是最需要看原文的时候。
          历史项目 story 为 null(改造前根本没落盘),整块不显示是预期行为。 */}
      {(project.legend || project.has_story) && (
        <div className={`relative ${card} border-l-[3px] border-l-cinnabar`}>
          <span className="absolute right-5 top-5">
            <Seal char="源" size={38} rot={6} />
          </span>
          <div className="mb-1 flex items-center gap-2">
            <h2 className="font-serif text-base font-semibold tracking-wide text-ink">
              {project.legend ? project.legend.title : '自备故事'}
            </h2>
            {project.legend && (
              <span className="rounded-full border border-line px-2 py-0.5 text-[11px] text-muted">
                来源 · {project.legend.source_type}
              </span>
            )}
          </div>
          {project.legend && (
            <p className="text-sm leading-loose text-ink-soft">{project.legend.summary}</p>
          )}
          {/* 自备故事时 legend.sources 只是占位词「用户自备文本」(s0_legend.from_text 写死的),
              列出来毫无信息量——用户点「故事来源」要的就是自己贴的那段文本,故优先展开原文。
              自动检索传说的作品没有 story,照旧列书目;S0 允许给不出可靠出处时留空,空列表不给按钮。 */}
          {(project.has_story || (project.legend && project.legend.sources.length > 0)) && (
            <div className="mt-3">
              <button className={toolBtn} onClick={toggleSources}>
                {sourcesOpen ? '收起' : '故事来源'}
              </button>
              {sourcesOpen &&
                (project.has_story ? (
                  // 原文最长两万字,就地展开必须限高滚动,否则把整页撑成一条长廊
                  <div className="mt-2 max-h-[60vh] space-y-3 overflow-y-auto text-sm leading-loose text-ink-soft">
                    {storyText === null ? (
                      <p>正在读取原文…</p>
                    ) : (
                      paragraphs(storyText).map((para, i) => (
                        <p key={i} className="indent-[2em] whitespace-pre-wrap">
                          {para}
                        </p>
                      ))
                    )}
                  </div>
                ) : (
                  <ul className="mt-2 space-y-1 text-sm leading-loose text-ink-soft">
                    {project.legend?.sources.map((s) => (
                      <li key={s} className="flex gap-2">
                        <span className="text-muted">·</span>
                        <span>{s}</span>
                      </li>
                    ))}
                  </ul>
                ))}
            </div>
          )}
        </div>
      )}
      {s2Open && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-ink/80 p-6"
          onClick={() => setS2Open(false)}
        >
          <div
            role="dialog"
            aria-modal="true"
            onClick={(e) => e.stopPropagation()}
            className="w-full max-w-md rounded-2xl border border-band bg-paper p-5 shadow-paper-lg"
          >
            <h3 className="font-serif text-sm font-semibold tracking-wide text-ink">
              重新生成分镜
            </h3>
            <p className="mt-2 text-xs text-ink-soft">
              分镜会被整体重写,已生成的<b>漫画页、配音、成片</b>(含英文版)随之作废,
              旧的图片与音频文件会被清理。
              <br />
              角色三视图<b>不受影响</b>——它依赖剧本,而这一步只换分镜。
            </p>
            <div className="mt-4 flex flex-wrap justify-end gap-2">
              <button type="button" onClick={() => setS2Open(false)} className={ghostBtn}>
                取消
              </button>
              <button
                type="button"
                onClick={() => void doStep('s2', false)}
                disabled={stepBusy !== null}
                className={ghostBtn}
              >
                只重跑分镜
              </button>
              <button
                type="button"
                onClick={() => void doStep('s2', true)}
                disabled={stepBusy !== null}
                className={primaryBtn}
              >
                分镜 + 漫画页 + 配音 + 合成
              </button>
            </div>
            <p className="mt-2 text-right text-[11px] text-muted">
              选「只重跑分镜」的话,之后需自己依次点「漫画页」「配音」「合成」
            </p>
          </div>
        </div>
      )}
      {voiceOpen && (
        <UploadDialog
          title="更换配音音色"
          glyph="音"
          hint="念一段 5–20 秒的话,或上传一段 wav / mp3。系统会克隆这个音色重新配音,已生成的配音会作废。"
          picker={
            <VoiceRecorder
              onPicked={(b, f) => setVoicePicked({ blob: b, filename: f })}
              disabled={voiceBusy || voiceUpload.phase === 'uploading' || voiceUpload.phase === 'processing'}
            />
          }
          ready={!!voicePicked && !voiceBusy}
          phase={voiceUpload.phase}
          progress={voiceUpload.progress}
          indeterminate={voiceUpload.indeterminate}
          error={voiceUpload.error}
          confirmLabel="用这个音色"
          phaseLabels={{ processing: '转码并注册音色…', done: '音色已就绪' }}
          onConfirm={() => {
            if (!voicePicked) return
            void voiceUpload
              .start(voiceSampleTarget(), voicePicked.blob, voicePicked.filename)
              .then(async (r) => {
                if (!r) return   // 失败信息已在 voiceUpload.error 里,弹窗自己显示
                setVoiceBusy(true)
                try {
                  await api.updateProjectVoice(project.project_id, r.voice)
                } finally {
                  setVoiceBusy(false)
                  setVoiceOpen(false)
                  setVoicePicked(null)
                  voiceUpload.reset()
                  onChanged()   // 换音色会作废下游,一律重拉以服务端为准
                }
              })
          }}
          onCancel={() => {
            const inFlight =
              voiceUpload.phase === 'uploading' || voiceUpload.phase === 'processing'
            voiceUpload.cancel()
            voiceUpload.reset()
            setVoiceOpen(false)
            setVoicePicked(null)
            // 与参考图同理:取消只切客户端,服务端可能已经注册完了,一律重拉对齐
            if (inFlight) onChanged()
          }}
        />
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
  generating,
  projectId,
  editable,
  onChanged,
}: {
  c: Character
  pages: Page[]
  generating: boolean
  projectId: string
  editable: boolean
  onChanged: () => void
}) {
  const [busy, setBusy] = useState(false)
  // 存 src 而非布尔:三视图与参考图共用这一个 lightbox,用标志位就得开两个 state,
  // 也就凭空多出"两个弹窗同时开着"这种状态。存 src 则天然互斥。
  const [lightbox, setLightbox] = useState<{ src: string; alt: string } | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  // 弹窗由两处触发,复用同一个 CharacterRedrawDialog:手动点"重绘"走 doRedraw,
  // 上传参考图成功后自动弹出则走 afterUpload(不再重新生成设定图,只决定是否连带重绘旧页)
  const [dialogMode, setDialogMode] = useState<'manual' | 'upload'>('manual')
  const [uploadOpen, setUploadOpen] = useState(false)
  // ImagePicker 选完先本地预览、确认后才真正上传,故此处只是"待上传"的暂存
  const [picked, setPicked] = useState<{ blob: Blob; filename: string } | null>(null)
  // 上传成功但应用(s3)被后端 409/429 挡住时的柔和提示;不能当错误弹出,否则用户会误以为要重传
  const [applyNotice, setApplyNotice] = useState<string | null>(null)
  const upload = useUpload<Detail>()

  // 独立于 toolBtn 定义(不叠加覆盖字号):卡片变宽后两个按钮各占一半、居中、不换行
  const charBtn =
    'flex-1 justify-center whitespace-nowrap rounded-md border border-line bg-white/50 px-2 py-1 text-center text-[10px] text-ink-soft transition hover:border-cinnabar hover:text-cinnabar disabled:cursor-not-allowed disabled:opacity-40'

  // 绑成局部常量,TS 才能在 onClick 闭包里收窄掉 null(c.image 是 props 上的属性,
  // 编译器不认为它在闭包执行时仍然非空)。这样不必写非空断言——本文件一处都没有。
  const artSrc = c.image
  const refSrc = c.reference_image

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
    setDialogMode('manual')
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

  // 上传参考图成功后自动触发:按需连带重绘旧页,再应用新参考图(s3)。
  // s3 若因作业冲突/队列满失败,只留柔和提示,不当错误弹窗——上传本身已经成功了。
  async function afterUpload(cascade: boolean) {
    setDialogOpen(false)
    setBusy(true)
    setApplyNotice(null)
    try {
      // 级联标记与 runStep 一样要接住错误:有作业在跑时 redrawCell 同样会 409,
      // 而它是**循环 N 次**、暴露面比 runStep 更大。此前只护住了 runStep,结果是第 k 页
      // 409 就整条链 reject —— 调用点是 void 调用,变成未处理的 rejection:弹窗关了、
      // busy 复位、既没有错误也没有提示、onChanged 也没跑,用户界面上什么都没发生,
      // 而前 k-1 页其实已经被标记重绘了。
      try {
        if (cascade) {
          for (const pg of affected) {
            await api.redrawCell(projectId, pg.index)
          }
        }
        await api.runStep(projectId, 's3')
      } catch {
        setApplyNotice('参考图已保存;当前有作业在跑,稍后点"按参考图重绘"即可应用')
      }
      onChanged()
    } finally {
      setBusy(false)
    }
  }

  // 上传成功(phase 变 done)后:关闭上传弹窗,有受影响页则弹重绘确认,否则直接应用s3
  useEffect(() => {
    if (upload.phase !== 'done') return
    // 驻留一下再关窗:弹窗在 done 相渲染的是「参考图已保存,正在重新生成三视图…」,
    // 立刻关掉的话 effect 在 paint 之后跑、这句话最多闪现一帧,用户拿不到成功确认,
    // 也不知道自己刚才那一下触发了几分钟的后台生成。
    const t = setTimeout(() => {
      setUploadOpen(false)
      setPicked(null)
      upload.reset()
      if (affected.length > 0) {
        setDialogMode('upload')
        setDialogOpen(true)
      } else {
        void afterUpload(false)
      }
    }, 400)
    return () => clearTimeout(t)
  }, [upload.phase])

  return (
    <figure className="overflow-hidden rounded-xl border border-line bg-white/60">
      <div className={`aspect-[3/2] bg-gradient-to-b from-kraft to-rice-deep ${mountFrame}`}>
        {c.image ? (
          <img src={c.image} alt={c.name} className="h-full w-full animate-shy-rise object-cover" />
        ) : (
          <Placeholder text="三视图" generating={generating} />
        )}
        <span className="absolute left-2 top-2 flex gap-1">
          <span className="rounded-full bg-ink/70 px-2 py-0.5 text-[10px] tracking-wide text-rice">
            三视图
          </span>
          {c.reference_image && (
            <span className="rounded-full bg-gold/85 px-2 py-0.5 text-[10px] tracking-wide text-rice">
              参考图
            </span>
          )}
        </span>
        <span className="absolute right-2 top-2 rounded bg-cinnabar/85 px-1.5 py-0.5 font-serif text-[10px] text-rice">
          正·侧·背
        </span>
        {/* 看参考图放在图片区左下,与右下的"换参考图"对称,**不能**并进下方 figcaption 那行:
            那行两颗按钮各 flex-1 占一半(见 charBtn 注释),挤成三等分后「按参考图重绘」
            配 whitespace-nowrap 会直接溢出卡片。
            也刻意不受 editable 约束——查看是只读动作,只读演示模式与生成过程中都该看得到
            自己传的图;右下那颗要改数据,才需要 editable。 */}
        {refSrc && (
          <button
            type="button"
            onClick={() => setLightbox({ src: refSrc, alt: `${c.name} 参考图` })}
            className="absolute bottom-2 left-2 rounded-full bg-ink/70 px-2 py-0.5 text-[10px] tracking-wide text-rice transition hover:bg-cinnabar/85"
          >
            看参考图
          </button>
        )}
        {editable && (
          <button
            type="button"
            onClick={() => setUploadOpen(true)}
            className="absolute bottom-2 right-2 rounded-full bg-ink/70 px-2 py-0.5 text-[10px] tracking-wide text-rice transition hover:bg-cinnabar/85"
          >
            {c.reference_image ? '换参考图' : '上传参考图'}
          </button>
        )}
      </div>
      <figcaption className="px-3 py-2.5">
        <div className="font-serif text-sm font-semibold tracking-wide text-ink">{c.name}</div>
        <div className="text-[11px] text-muted">{c.role}</div>
        <div className="mt-2 flex gap-1.5">
          {artSrc && (
            <button
              type="button"
              onClick={() => setLightbox({ src: artSrc, alt: c.name })}
              className={charBtn}
            >
              查看详情
            </button>
          )}
          {editable && (
            <button type="button" onClick={redraw} disabled={busy} className={charBtn}>
              {busy ? '重绘中…' : c.reference_image ? '按参考图重绘' : '重绘设定图'}
            </button>
          )}
        </div>
        {applyNotice && <div className="mt-1.5 text-[10px] text-muted">{applyNotice}</div>}
      </figcaption>
      {lightbox && (
        <ImageLightbox src={lightbox.src} alt={lightbox.alt} onClose={() => setLightbox(null)} />
      )}
      {dialogOpen && (
        <CharacterRedrawDialog
          characterName={c.name}
          affectedPages={affected.map((p) => ({ index: p.index, caption: p.caption }))}
          busy={busy}
          title={dialogMode === 'upload' ? `「${c.name}」参考图已上传` : undefined}
          intro={
            dialogMode === 'upload'
              ? `参考图已保存。以下 ${affected.length} 页漫画页是按旧设定图生成的,若不一并重绘,画面中该角色的形象会与新设定图不一致。`
              : undefined
          }
          onConfirm={(cascade) => void (dialogMode === 'upload' ? afterUpload(cascade) : doRedraw(cascade))}
          onCancel={() => {
            setDialogOpen(false)
            // 上传那条路必须刷新:此刻服务端已经落盘并解锁了该角色,而管线空闲时前端
            // 不轮询(App.tsx 只在 pipeline 活跃时续跑),不刷就一直显示旧数据——
            // 没有参考图徽标、按钮还是"重绘设定图",用户会以为上传根本没生效。
            if (dialogMode === 'upload') onChanged()
          }}
        />
      )}
      {uploadOpen && (
        <UploadDialog
          title={c.reference_image ? '换参考图' : '上传参考图'}
          glyph="人"
          hint="上传一张该角色的全身参考图,将以此为基础重新生成三视图设定图"
          // UploadDialog 没有独立的 children 插槽,"移除参考图" 借 picker 这个 ReactNode 槽
          // 一并塞进弹窗内容区,不在角色卡上占常驻位置。
          picker={
            <>
              <ImagePicker
                onPicked={(blob, filename) => setPicked({ blob, filename })}
                disabled={upload.phase === 'uploading' || upload.phase === 'processing'}
              />
              {c.reference_image && (
                <button
                  type="button"
                  className={`${ghostBtn} mt-2`}
                  disabled={upload.phase === 'uploading' || upload.phase === 'processing'}
                  onClick={async () => {
                    if (!window.confirm('确定移除该角色的参考图?后续重绘将改回文生图。')) return
                    setUploadOpen(false)
                    setBusy(true)
                    try {
                      await api.removeCharacterReference(projectId, c.name)
                      onChanged()
                    } catch (e) {
                      alert(e instanceof Error ? e.message : String(e))
                    } finally {
                      setBusy(false)
                    }
                  }}
                >
                  移除参考图
                </button>
              )}
            </>
          }
          ready={picked != null}
          phase={upload.phase}
          progress={upload.progress}
          indeterminate={upload.indeterminate}
          error={upload.error}
          confirmLabel={c.reference_image ? '换参考图' : '上传参考图'}
          onConfirm={() => {
            if (!picked) return
            void upload.start(characterReferenceTarget(projectId, c.name), picked.blob, picked.filename)
          }}
          onCancel={() => {
            // reset 而非只 cancel:cancel 只中断请求、phase 停在 error、error 字符串还在,
            // 关窗再打开会带着上一次的红色错误条,而此时用户一张图都还没选。
            const wasInFlight = upload.phase === 'uploading' || upload.phase === 'processing'
            upload.cancel()
            upload.reset()
            setUploadOpen(false)
            setPicked(null)
            // processing 相意味着字节已全部发出、服务端正在解码落盘,而 xhr.abort() 只切
            // 客户端——后端会照常写完文件并解锁角色。此时装作"什么都没发生"会与服务端
            // 不一致,所以一律重拉一次,以服务端为准。
            if (wasInFlight) onChanged()
          }}
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
        {/* 同上:语种轨这行的「配音 X.Xs」一并去掉,与中文行保持一致 */}
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
            maxLength={300}
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
        maxLength={120}
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
  generating,
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
  generating: boolean
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
    // 先按字段算出这次改动作废了什么,用来决定保存后问哪一句。必须在 updateCell 之前算:
    // 判据要和后端 editing.update_cell 的级联规则严格对齐——画面/人物作废图,旁白作废音,
    // 情绪不级联。比较用 trim 后的值,与真正提交上去的值一致,免得只多敲一个空格也弹窗。
    const nextCaption = caption.trim()
    const nextVisual = visualDesc.trim()
    const needRedraw =
      nextVisual !== pg.visual_desc ||
      characters.length !== pg.characters.length ||
      characters.some((c, i) => c !== pg.characters[i])
    const needRevoice = nextCaption !== pg.caption

    setBusy('save')
    setErr(null)
    try {
      await api.updateCell(projectId, pg.index, {
        caption: nextCaption,
        visual_desc: nextVisual,
        emotion,
        characters,
      })
      setEditing(false)
      if (needRedraw && needRevoice) {
        if (
          window.confirm(
            `已保存。第 ${pg.index} 页的图片和配音都已作废,现在一并重新生成吗?将调用生图与 TTS API,并清空已合成的成片。`,
          )
        ) {
          // 不能连着发两次 runStep:同一项目已有任务在跑时端点直接 409。改成两次标脏后
          // 只触发一次 S4 并带 cascade——api._INVALIDATES 里 s4 会带出 s5/s6,配音会接着跑。
          await api.redrawCell(projectId, pg.index)
          await api.revoiceCell(projectId, pg.index)
          await triggerStep('s4', true, '已标记重绘与重配音', '可点漫画页步骤重试')
        }
      } else if (needRedraw) {
        if (
          window.confirm(
            `已保存。第 ${pg.index} 页的图片已作废,现在重新生成吗?将调用配置的生图 API。`,
          )
        ) {
          await regen('redraw')
        }
      } else if (needRevoice) {
        if (
          window.confirm(
            `已保存。第 ${pg.index} 页的配音已作废,现在重新生成吗?将调用配置的 TTS API,并清空已合成的成片。`,
          )
        ) {
          await regen('revoice')
        }
      }
      onChanged()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
      // 保存本身可能已经成功、错在后面的标脏/触发那几步(典型:此刻项目已有作业在跑 → 409)。
      // 不刷新的话卡片会继续显示保存前的旧文案,而服务端其实已经改了——用户会以为没存上、
      // 再改一遍。刷新拿服务端真实状态;配合 err 在编辑态与展示态都渲染,失败才是可见的。
      onChanged()
    } finally {
      setBusy(null)
    }
  }

  /** 触发某步,并在触发失败时刷新 + 给出可自行重试的指引。
   *  标脏接口已经清掉了本页的 image/audio 与成片,触发失败时若不刷新,UI 会停在陈旧画面、
   *  让用户以为还在。返回 false 表示触发失败(调用方据此跳过后续动作)。 */
  async function triggerStep(name: 's4' | 's5', cascade: boolean, what: string, hint: string) {
    try {
      await api.runStep(projectId, name, cascade)
      return true
    } catch (e) {
      onChanged()
      alert(`${what},但触发生成失败:${e instanceof Error ? e.message : String(e)},${hint}`)
      return false
    }
  }

  /** 标脏 + 立即触发对应步骤(不含二次确认,确认由调用方负责)。
   *  S4/S5 对已完成的页幂等跳过,所以整步重跑实际只会重做本页。 */
  async function regen(kind: 'redraw' | 'revoice') {
    if (kind === 'redraw') {
      await api.redrawCell(projectId, pg.index)
      await triggerStep('s4', false, '已标记重绘', '可点漫画页步骤重试')
    } else {
      await api.revoiceCell(projectId, pg.index)
      await triggerStep('s5', false, '已标记重配音', '可点配音步骤重试')
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
      if (kind === 'delete') await api.deleteCell(projectId, pg.index)
      else await regen(kind)
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
          <img src={pg.image} alt={`第 ${pg.index} 页`}
               className="h-full w-full animate-shy-rise object-cover" />
        ) : (
          // 此前这里把 pg.status 的英文原样透出(draft/failed),对用户毫无意义
          <Placeholder text={`第 ${pg.index} 图`} generating={generating}
                       failed={pg.status === 'failed'} />
        )}
        {pg.scene_ref && (
          <span className="absolute left-2 top-2 rounded-md bg-ink/70 px-2 py-0.5 text-[10px] tracking-wide text-rice">
            {pg.scene_ref}
          </span>
        )}
        {/* 页码是这张卡上唯一没有底板的角标,而它钉死在右上角——正是国风水墨最常留白的位置。
            实测本机 50 张真实漫画页,右上角平均亮度 ≥200 的有 10 张(rice 自身 241),最亮那张
            246.4 比角标还白;占位态更糟:图框渐变 from-kraft via-rice to-rice-deep 的右上角
            恰好落在 via-rice 那一档,与旧的 text-rice 一模一样,对比度 1.00:1、整个消失。
            改成 85% 不透明的宣纸底 + 墨字后与底图彻底解耦,纯黑底图上最差也有 10.7:1。
            ⚠️ 和同卡另外三个角标(scene_ref/拖拽柄/编辑键,都是 bg-ink/70 深底浅字)配色相反,
            这是有意的:那三个是**控件**,该浮在画面之上;页码是**内容标签**,该像盖在纸上的印记。
            描金内环沿用 decor.mountFrame 的 ring-gold 语言,不是新造的样式。 */}
        <span className="absolute right-2 top-2 flex h-8 w-8 items-center justify-center rounded-md bg-rice/85 font-brush text-lg text-ink ring-1 ring-inset ring-gold/30">
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
              maxLength={120}
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
          {/* 展示态也要渲染 err:保存成功后 setEditing(false) 已经把编辑区收起来了,
              而后续标脏/触发若失败(典型 409),错误只挂在编辑态里就等于没有提示——
              用户看到的是弹窗一闪而过、什么都没发生。 */}
          {err && <p className="text-xs text-alarm">{err}</p>}
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
            {/* 这里原本还有一个「配音 X.Xs」——下面的 <audio controls> 本来就显示时长,
                重复且占位;用户明确要求去掉。duration_ms 字段仍在用(算成片时间轴),只是不展示。 */}
            {pg.image_gen_ms > 0 && (
              // image_lora 挂在这里而不是单独占位:它对 89% 的正常页都有值,单独一枚标签
              // 全是噪音;空串**不代表没用 LoRA**(后端会回落它自己的默认权重,shanhai 不知道是哪个),
              // 所以空串时干脆不提,绝不写成"无 LoRA"。
              <span
                className="text-muted"
                title={pg.image_lora ? `本次指定的 LoRA:${pg.image_lora}` : undefined}
              >
                生成 {(pg.image_gen_ms / 1000).toFixed(1)}s
              </span>
            )}
            {pg.silent && pg.audio && (
              <span className="rounded-full bg-kraft px-2 py-0.5 text-muted">静音兜底</span>
            )}
            {/* edit(有参考图)是常态(约 89% 的页),不加标签;只在 LoRA 确实没(全)生效时提示,
                免得给大多数页添噪音。必须同时看 pg.image:失败/被编辑作废的页图已经没了,
                此时挂一个描述"那张图怎么生成的"的标签就是在说一张不存在的图(审计实测复现过)。
                存量页 image_route 是空串,不会触发——这不是 bug,是老数据没有该字段的预期状态。
                已知取舍:远程后端(tu-zi 等)根本没有 LoRA 这回事,那种部署下这枚标签属于无用信息;
                但配置面板的 LoRA 控件本来就只对本地 ComfyUI 显示,多一枚灰标签的代价可接受。 */}
            {pg.image && LORA_MISS[pg.image_route] && (
              <span
                className="rounded-full bg-kraft px-2 py-0.5 text-muted"
                title={LORA_MISS[pg.image_route]}
              >
                {LORA_MISS_SHORT[pg.image_route]}
              </span>
            )}
            {/* 缺三视图锚点:这些角色画这一页时只有文字特征、没有参考图,长相不受约束。
                必须同时看 pg.image——图已被作废/失败的页,这条记录描述的是一张不存在的图
                (与上面 LORA_MISS 同一判据)。用 alarm 色而非 muted:这是实打实的质量问题,
                不是可有可无的元信息,补画三视图后重跑该页才会消失。 */}
            {pg.image && pg.missing_refs.length > 0 && (
              <span
                className="rounded-full bg-kraft px-2 py-0.5 text-alarm"
                title={`${pg.missing_refs.join('、')} 画这一页时没有三视图参考,长相不受约束。补出三视图后重画本页可修复。`}
              >
                缺参考 {pg.missing_refs.join('、')}
              </span>
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
