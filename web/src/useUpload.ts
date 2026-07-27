import { useCallback, useEffect, useRef, useState } from 'react'
import { xhrUpload, type UploadTarget } from './upload'

// 用字面量联合而非 string:这个仓库没有前端测试框架,npm run build 的 tsgo 严格模式是唯一的
// 自动化保障,把相位写死能让写错的相名在构建期就炸。
// processing 不是幌子:上传字节走完后服务端真在做解码/旋正/缩放/重编码,那段时间没有进度可报。
export type Phase = 'idle' | 'uploading' | 'processing' | 'done' | 'error'

export function useUpload<T>(): {
  phase: Phase
  progress: number
  indeterminate: boolean
  error: string
  result: T | null
  start: (t: UploadTarget, blob: Blob, filename: string) => Promise<T | null>
  cancel: () => void
  reset: () => void
} {
  const [phase, setPhase] = useState<Phase>('idle')
  const [progress, setProgress] = useState(0)
  const [indeterminate, setIndeterminate] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<T | null>(null)

  const abortRef = useRef<(() => void) | null>(null)
  const aliveRef = useRef(true)

  // 组件卸载后继续 setState 会触发 React 警告并让 xhr 白跑,所以卸载时既断连也停手。
  useEffect(() => {
    aliveRef.current = true
    return () => {
      aliveRef.current = false
      abortRef.current?.()
    }
  }, [])

  const start = useCallback(async (t: UploadTarget, blob: Blob, filename: string) => {
    setPhase('uploading')
    setProgress(0)
    setIndeterminate(false)
    setError('')
    setResult(null)

    const { promise, abort } = xhrUpload<T>(t, blob, filename, (loaded, total, computable) => {
      if (!aliveRef.current) return
      // total 不可信时(某些代理会抹掉 Content-Length)退化成不确定态,而不是画一根假的条。
      if (!computable || total <= 0) {
        setIndeterminate(true)
        // 不画进度条,但相位照常推进——否则代理抹掉 Content-Length 时,字节早就发完了
        // 弹窗还一直显示"上传中…",直到响应回来才跳变。
        if (loaded > 0) setPhase('processing')
        return
      }
      setIndeterminate(false)
      setProgress(loaded / total)
      if (loaded >= total) setPhase('processing')
    })
    abortRef.current = abort

    try {
      const data = await promise
      if (!aliveRef.current) return null
      setResult(data)
      setPhase('done')
      return data
    } catch (e) {
      if (!aliveRef.current) return null
      // 用户主动取消不是错误:静默回 idle,不弹红条。
      if (e instanceof Error && e.name === 'AbortError') {
        setPhase('idle')
        return null
      }
      // 吞掉异常、把消息落进 error,免得每个调用方再写一遍 try/catch;需要链式动作的
      // 调用方看返回值非 null 即可。
      setError(e instanceof Error ? e.message : String(e))
      setPhase('error')
      return null
    } finally {
      abortRef.current = null
    }
  }, [])

  const cancel = useCallback(() => abortRef.current?.(), [])

  const reset = useCallback(() => {
    abortRef.current?.()
    setPhase('idle')
    setProgress(0)
    setIndeterminate(false)
    setError('')
    setResult(null)
  }, [])

  return { phase, progress, indeterminate, error, result, start, cancel, reset }
}
