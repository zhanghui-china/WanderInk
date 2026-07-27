import { useEffect, useRef, useState } from 'react'
import { mountFrame } from './decor'

const ACCEPT = ['image/png', 'image/jpeg', 'image/webp']
const MAX_BYTES = 8 * 1024 * 1024

function formatSize(n: number) {
  return n >= 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${Math.round(n / 1024)} KB`
}

// 选图槽:可点可拖,选中后先在本地显示缩略图 + 文件名 + 大小,由用户确认后才真上传。
// 前端这层类型/大小预检不是闸门(后端才是),只是省用户一次白传和一趟往返。
export function ImagePicker({
  onPicked,
  disabled,
}: {
  onPicked: (blob: Blob, filename: string, previewUrl: string) => void
  disabled: boolean
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [picked, setPicked] = useState<{ name: string; size: number; url: string } | null>(null)
  const [hot, setHot] = useState(false)
  const [reject, setReject] = useState('')

  // createObjectURL 的 URL 不撤销就一直占着内存里的那份文件,换图和卸载时都要 revoke。
  const urlRef = useRef<string | null>(null)
  useEffect(() => {
    return () => {
      if (urlRef.current) URL.revokeObjectURL(urlRef.current)
    }
  }, [])

  function take(file: File | undefined) {
    if (!file) return
    if (!ACCEPT.includes(file.type)) {
      setReject('只支持 PNG / JPEG / WebP 图片')
      return
    }
    if (file.size > MAX_BYTES) {
      setReject(`图片超过 8 MB(当前 ${formatSize(file.size)}),请先压缩`)
      return
    }
    setReject('')
    if (urlRef.current) URL.revokeObjectURL(urlRef.current)
    const url = URL.createObjectURL(file)
    urlRef.current = url
    setPicked({ name: file.name, size: file.size, url })
    onPicked(file, file.name, url)
  }

  return (
    <div>
      <div
        onClick={() => !disabled && inputRef.current?.click()}
        // dragover 必须 preventDefault,否则浏览器不认这是可放置区域,drop 事件根本不会来。
        onDragOver={(e) => {
          e.preventDefault()
          if (!disabled) setHot(true)
        }}
        onDragLeave={() => setHot(false)}
        onDrop={(e) => {
          e.preventDefault()
          setHot(false)
          if (!disabled) take(e.dataTransfer.files[0])
        }}
        className={`flex cursor-pointer items-center gap-3 rounded-xl border border-dashed px-3 py-3 transition ${
          hot ? 'border-cinnabar bg-cinnabar/5' : 'border-line bg-white/50'
        } ${disabled ? 'cursor-not-allowed opacity-50' : ''}`}
      >
        {picked ? (
          <>
            <div className={`h-14 w-14 shrink-0 overflow-hidden ${mountFrame}`}>
              <img src={picked.url} alt="" className="h-full w-full object-cover" />
            </div>
            <div className="min-w-0">
              <div className="truncate text-xs text-ink">{picked.name}</div>
              <div className="text-[11px] text-muted">{formatSize(picked.size)} · 点击可更换</div>
            </div>
          </>
        ) : (
          <div className="text-xs text-ink-soft">
            拖拽图片到此处,或点击选择
            <div className="text-[11px] text-muted">PNG / JPEG / WebP,不超过 8 MB</div>
          </div>
        )}
      </div>
      {reject && <p className="mt-2 text-[11px] text-cinnabar">{reject}</p>}
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT.join(',')}
        className="hidden"
        onChange={(e) => {
          take(e.target.files?.[0])
          // 清空 value,否则连着选同一个文件不会再触发 change
          e.target.value = ''
        }}
      />
    </div>
  )
}
