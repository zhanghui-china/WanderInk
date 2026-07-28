/** vite.config.ts 的 define 烧进来的编译期常量(来源:仓库根 version.json)。
 *  字段全必填 —— tsgo 严格模式下,漏改的渲染点会在 build 期就炸,这是前端唯一的自动化保障。 */
declare const __BUILD__: {
  build: number
  sha: string
  dirty: boolean
  stamped_at: string
}
