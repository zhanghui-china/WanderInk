import { useEffect, useRef, useState } from 'react'

export const MAX_SECONDS = 20
const MIN_SECONDS = 5

// 浏览器实际能录出什么格式由它自己定,这里按优先级挑一个它支持的。MediaRecorder 在
// Chrome/Edge 上给 webm/opus,Safari 给 mp4/aac——**不是 wav**,wav 由后端转码产出。
// 这里挑出的 mimeType 会随 Blob 一起交给后端,后端据它选 demuxer(绝不自动探测)。
const PREFERRED = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4']

function pickMime(): string | null {
  if (typeof MediaRecorder === 'undefined') return null
  return PREFERRED.find((m) => MediaRecorder.isTypeSupported(m)) ?? null
}

// getUserMedia 只在安全上下文(https 或 localhost)可用。内网 http 直连时它压根不存在,
// 点了没反应会让人以为坏了,所以要提前判出来、把按钮置灰并说清原因。
function micBlockedReason(): string {
  if (typeof navigator === 'undefined' || !navigator.mediaDevices?.getUserMedia) {
    return window.isSecureContext
      ? '当前浏览器不支持录音'
      : '录音需要 HTTPS 访问(浏览器限制),请用 https 地址打开本站'
  }
  if (!pickMime()) return '当前浏览器不支持录音编码'
  return ''
}

/** 录音槽:与 ImagePicker 同形的 onPicked 契约,可直接塞进 UploadDialog 的 picker。 */
export function VoiceRecorder({
  onPicked,
  disabled,
}: {
  onPicked: (blob: Blob, filename: string, previewUrl: string) => void
  disabled: boolean
}) {
  const [blocked] = useState(micBlockedReason)
  const [recording, setRecording] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  // name 只有"上传文件"这一路有值;seconds=0 表示读不出时长(不拦,交给后端判)
  const [preview, setPreview] = useState<
    { url: string; seconds: number; name?: string } | null
  >(null)
  const [error, setError] = useState('')

  const recRef = useRef<MediaRecorder | null>(null)
  const fileRef = useRef<HTMLInputElement | null>(null)
  // elapsed 同时用 ref 存一份:onstop 是闭包捕获的旧 state,只有 ref 能拿到最新值
  const elapsedRef = useRef(0)
  const urlRef = useRef<string | null>(null)
  const timerRef = useRef<number | null>(null)

  // 三样东西都必须在卸载时收干净:objectURL 不撤销会一直占着内存里的音频;
  // 麦克风轨不 stop 浏览器标签页会一直显示"正在录音"的红点;定时器不清会继续 setState。
  function cleanupStream() {
    recRef.current?.stream.getTracks().forEach((t) => t.stop())
    recRef.current = null
    if (timerRef.current !== null) {
      window.clearInterval(timerRef.current)
      timerRef.current = null
    }
  }
  useEffect(() => {
    return () => {
      cleanupStream()
      if (urlRef.current) URL.revokeObjectURL(urlRef.current)
    }
  }, [])

  async function start() {
    setError('')
    const mime = pickMime()
    if (!mime) return
    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch (e) {
      // 用户点了"拒绝",或系统层面没有可用麦克风。区分开来,前者是可恢复的。
      const name = e instanceof Error ? e.name : ''
      setError(
        name === 'NotAllowedError'
          ? '麦克风权限被拒绝,请在浏览器地址栏的权限设置里允许后重试'
          : name === 'NotFoundError'
            ? '没有找到麦克风设备'
            : `无法开始录音:${e instanceof Error ? e.message : String(e)}`,
      )
      return
    }
    const chunks: BlobPart[] = []
    const rec = new MediaRecorder(stream, { mimeType: mime })
    rec.ondataavailable = (ev) => ev.data.size > 0 && chunks.push(ev.data)
    rec.onstop = () => {
      const seconds = Math.min(elapsedRef.current, MAX_SECONDS)
      cleanupStream()
      setRecording(false)
      if (seconds < MIN_SECONDS) {
        setError(`录音太短(${seconds.toFixed(0)} 秒),至少需要 ${MIN_SECONDS} 秒`)
        return
      }
      // mimeType 带 codecs 参数,原样交给后端;后端只取分号前那段查 demuxer 白名单。
      const blob = new Blob(chunks, { type: mime })
      if (urlRef.current) URL.revokeObjectURL(urlRef.current)
      const url = URL.createObjectURL(blob)
      urlRef.current = url
      setPreview({ url, seconds })
      onPicked(blob, `voice-sample.${mime.includes('mp4') ? 'm4a' : 'webm'}`, url)
    }
    recRef.current = rec
    setPreview(null)
    setElapsed(0)
    elapsedRef.current = 0
    rec.start()
    setRecording(true)
    timerRef.current = window.setInterval(() => {
      elapsedRef.current += 0.1
      setElapsed(elapsedRef.current)
      // 录满自动停:20 秒是后端的硬截断上限,让用户看到它自己停,而不是被服务端悄悄切掉
      if (elapsedRef.current >= MAX_SECONDS) recRef.current?.stop()
    }, 100)
  }


  /** 本地读时长:纯粹为了在上传前把"这文件多长"告诉用户。读不出返回 0——
   *  元数据缺失的文件不少见,这里不拦,后端的 probe_duration_ms 才是判据。 */
  function probeSeconds(url: string): Promise<number> {
    return new Promise((resolve) => {
      const a = new Audio()
      a.preload = 'metadata'
      a.onloadedmetadata = () => resolve(Number.isFinite(a.duration) ? a.duration : 0)
      a.onerror = () => resolve(0)
      a.src = url
    })
  }

  async function takeFile(file: File | undefined) {
    if (!file) return
    setError('')
    // 录音与上传互斥:选了文件就把录音那份丢掉,免得两个来源都有值、不知道用哪个
    if (recording) recRef.current?.stop()
    if (urlRef.current) URL.revokeObjectURL(urlRef.current)   // 换一次 revoke 一次(同 ImagePicker)
    const url = URL.createObjectURL(file)
    urlRef.current = url
    const seconds = await probeSeconds(url)
    if (seconds && seconds < MIN_SECONDS) {
      // 前端这层不是闸门(后端 MIN_VOICE_MS 才是),只是省用户一次白传和一趟往返
      URL.revokeObjectURL(url)
      urlRef.current = null
      setPreview(null)
      setError(`这段音频只有 ${seconds.toFixed(1)} 秒,至少需要 ${MIN_SECONDS} 秒才能克隆出像的音色`)
      return
    }
    setPreview({ url, seconds, name: file.name })
    // filename 与 blob 分开传:File 本身就带 name,但契约统一成显式传,与录音那路一致
    onPicked(file, file.name, url)
  }

  const pct = Math.min(100, (elapsed / MAX_SECONDS) * 100)

  // 文件上传入口。**blocked 分支也必须有它**:getUserMedia 需要安全上下文,
  // 而线上是内网 HTTP 直连,浏览器会直接拒掉麦克风——那种部署下上传是唯一的出路。
  const fileInput = (
    <input
      ref={fileRef}
      type="file"
      accept="audio/wav,audio/mpeg,audio/mp4,audio/x-m4a,.wav,.mp3,.m4a"
      className="hidden"
      onChange={(e) => {
        void takeFile(e.target.files?.[0])
        e.target.value = ''   // 清空,否则连着选同一个文件不会再触发 change
      }}
    />
  )
  const uploadBtn = (
    <button
      type="button"
      disabled={disabled || recording}
      onClick={() => fileRef.current?.click()}
      className="shrink-0 rounded-lg border border-line bg-white/60 px-3 py-1.5 text-xs text-ink-soft transition hover:border-cinnabar hover:text-cinnabar disabled:cursor-not-allowed disabled:opacity-40"
    >
      上传文件
    </button>
  )

  if (blocked) {
    return (
      <div className="rounded-lg border border-line bg-white/40 p-3">
        <div className="text-[11px] text-muted">{blocked}</div>
        <div className="mt-2 flex items-center gap-2">
          {uploadBtn}
          <span className="min-w-0 flex-1 text-[11px] text-muted">
            {preview ? preview.name : `可改为上传一段 ${MIN_SECONDS}–${MAX_SECONDS} 秒的 wav / mp3`}
          </span>
        </div>
        {preview && <audio src={preview.url} controls className="mt-2 h-8 w-full" />}
        {preview && preview.seconds > MAX_SECONDS && (
          <div className="mt-1 text-[11px] text-cinnabar">
            这段有 {preview.seconds.toFixed(0)} 秒,将只使用前 {MAX_SECONDS} 秒
          </div>
        )}
        {error && <div className="mt-2 text-[11px] text-alarm">{error}</div>}
        {fileInput}
      </div>
    )
  }

  return (
    <div className="rounded-lg border border-line bg-white/40 p-3">
      <div className="flex items-center gap-3">
        <button
          type="button"
          disabled={disabled}
          onClick={() => (recording ? recRef.current?.stop() : void start())}
          className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-lg transition disabled:cursor-not-allowed disabled:opacity-40 ${
            recording
              ? 'animate-shy-pulse bg-alarm text-rice'
              : 'bg-gradient-to-br from-cinnabar-bright to-cinnabar-deep text-rice ring-1 ring-inset ring-gold/30'
          }`}
          aria-label={recording ? '停止录音' : '开始录音'}
        >
          {recording ? '■' : '●'}
        </button>
        {uploadBtn}
        <div className="min-w-0 flex-1">
          {recording ? (
            <>
              <div className="flex items-baseline justify-between">
                <span className="text-[11px] text-muted">正在录音…</span>
                <span className="tabular-nums text-[11px] text-cinnabar">
                  {elapsed.toFixed(1)}s / {MAX_SECONDS}s
                </span>
              </div>
              <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-rice-deep">
                <div
                  className="h-full rounded-full bg-gradient-to-r from-cinnabar-bright to-cinnabar-deep"
                  style={{ width: `${pct}%` }}
                />
              </div>
            </>
          ) : preview ? (
            <div className="flex items-center gap-2">
              <audio src={preview.url} controls className="h-8 min-w-0 flex-1" />
              <span className="shrink-0 truncate tabular-nums text-[11px] text-muted">
                {preview.name ? `${preview.name} · ` : ''}
                {preview.seconds ? `${preview.seconds.toFixed(0)}s` : '时长未知'}
              </span>
            </div>
          ) : (
            <div className="text-[11px] text-muted">
              点红色按钮录一段 {MIN_SECONDS}–{MAX_SECONDS} 秒的话,或「上传文件」选一个 wav / mp3
            </div>
          )}
        </div>
      </div>
      {error && <div className="mt-2 text-[11px] text-alarm">{error}</div>}
      {preview && !recording && preview.seconds > MAX_SECONDS && (
        // 后端的 -t 20 是硬截断、取的是**前** 20 秒。上传一首歌只用开头 20 秒会很意外,
        // 所以在这里明说,而不是让服务端悄悄切掉。
        <div className="mt-2 text-[11px] text-cinnabar">
          这段有 {preview.seconds.toFixed(0)} 秒,将只使用前 {MAX_SECONDS} 秒
        </div>
      )}
      {preview && !recording && (
        <div className="mt-2 text-[11px] text-muted">
          试听满意后点下方按钮上传;不满意可重录或换一个文件。
        </div>
      )}
      {fileInput}
    </div>
  )
}
