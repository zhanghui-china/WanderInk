/** 版本号的展示格式 —— 标签标题、顶栏角标、页脚三处共用同一个函数,别各写各的。 */
export type BuildInfo = { build: number; sha: string; dirty: boolean; stamped_at: string }

/** 例:b190·ebcea85 / b190·ebcea85·dirty / dev */
export function fmtBuild(b: BuildInfo | null): string {
  if (!b || b.sha === 'dev') return 'dev'
  return `b${b.build}·${b.sha}${b.dirty ? '·dirty' : ''}`
}

/** 前后端是分两次 rsync 的,真会漂移(曾发生过代码传输超时中断而 dist 成功)。
 *  但 dev 侧不算漂移:本机 npm run dev 时前端恒为 dev、后端读得到 version.json,
 *  那是正常开发态,不该天天标红。 */
export function isDrifted(front: BuildInfo, back: BuildInfo | null): boolean {
  if (!back) return false
  if (front.sha === 'dev' || back.sha === 'dev') return false
  return front.sha !== back.sha
}
