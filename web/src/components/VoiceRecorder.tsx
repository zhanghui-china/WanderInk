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
  const [preview, setPreview] = useState<{ url: string; seconds: number } | null>(null)
  const [error, setError] = useState('')

  const recRef = useRef<MediaRecorder | null>(null)
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


  const pct = Math.min(100, (elapsed / MAX_SECONDS) * 100)

  if (blocked) {
    return (
      <div className="rounded-lg border border-line bg-white/40 px-3 py-4 text-center text-[11px] text-muted">
        {blocked}
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
              <span className="shrink-0 tabular-nums text-[11px] text-muted">
                {preview.seconds.toFixed(0)}s
              </span>
            </div>
          ) : (
            <div className="text-[11px] text-muted">
              点左侧按钮开始录音,念一段 {MIN_SECONDS}–{MAX_SECONDS} 秒的话即可
            </div>
          )}
        </div>
      </div>
      {error && <div className="mt-2 text-[11px] text-alarm">{error}</div>}
      {preview && !recording && (
        <div className="mt-2 text-[11px] text-muted">
          试听满意后点下方按钮上传;不满意可再点一次麦克风重录。
        </div>
      )}
    </div>
  )
}
