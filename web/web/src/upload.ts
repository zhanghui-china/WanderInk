import { ApiError, apiErrorFrom } from './api'

// 上传目标:url 是接口地址,field 是 multipart 字段名(默认 file),extra 是随表单一起带的文本字段。
export type UploadTarget = { url: string; field?: string; extra?: Record<string, string> }

// 必须用 XMLHttpRequest 而不是 fetch:fetch 至今没有上传进度事件(ReadableStream 请求体在
// 浏览器里仍不通用),而这个弹窗的全部意义就是把上传进度画出来。
//
// blob 和 filename 分开传,是给将来的录音场景留的口子:MediaRecorder 产出的是裸 Blob,没有
// .name;而 FormData.append(field, blob, filename) 对 File 和 Blob 通吃(File extends Blob)。
//
// 返回 {promise, abort} 而不是收 AbortSignal:调用方 99% 的形态就是"弹窗上一个取消按钮",
// 直接给一个函数比让每个调用方自己造 AbortController 短。
export function xhrUpload<T>(
  target: UploadTarget,
  blob: Blob,
  filename: string,
  onProgress: (loaded: number, total: number, lengthComputable: boolean) => void,
): { promise: Promise<T>; abort: () => void } {
  const xhr = new XMLHttpRequest()
  const form = new FormData()
  form.append(target.field ?? 'file', blob, filename)
  for (const [k, v] of Object.entries(target.extra ?? {})) form.append(k, v)

  const promise = new Promise<T>((resolve, reject) => {
    xhr.upload.onprogress = (e) => onProgress(e.loaded, e.total, e.lengthComputable)
    xhr.onload = () => {
      if (xhr.status >= 400) {
        // 与 fetch 路径共用 apiErrorFrom,否则同一个后端错误在两条路径上会给出不同的 message,
        // 调用方的 e instanceof Error ? e.message 就会退化成 "[object Object]"。
        reject(apiErrorFrom(xhr.responseText || null, xhr.status))
        return
      }
      try {
        resolve(JSON.parse(xhr.responseText) as T)
      } catch {
        reject(new ApiError('服务器返回了无法解析的内容', 200))
      }
    }
    // 非 HTTP 的终止态也统一包成 ApiError(status=0),让调用方只需处理一种错误类型。
    xhr.onerror = () => reject(new ApiError('网络中断,请重试', 0))
    xhr.ontimeout = () => reject(new ApiError('网络中断,请重试', 0))
    xhr.onabort = () => {
      const err = new ApiError('已取消', 0)
      // 额外打上 AbortError 标记:上层要靠它区分"用户主动取消"(静默回到 idle)与"真出错"(弹红条)。
      err.name = 'AbortError'
      reject(err)
    }
  })

  xhr.open('POST', target.url)
  // 不设 withCredentials:登录态是同源 cookie,XHR 同源请求默认就带 cookie;withCredentials
  // 只影响跨源请求,置 true 等于把语义从 'same-origin' 放宽成 'include',与 api.ts 的 CREDS 不一致。
  xhr.send(form)

  return { promise, abort: () => xhr.abort() }
}
