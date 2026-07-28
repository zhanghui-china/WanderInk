import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// 构建标识:读仓库根的 version.json(唯一写者是 scripts/stamp-version.py),烧成编译期常量。
// 这样每跑一次 npm run build 就自动带上当时的版本,不可能忘记打戳。
// 读不到就降级成 dev(新克隆、还没跑过 stamp-version、或 tar 包解出来的目录),不阻断构建。
function buildInfo() {
  try {
    return JSON.parse(readFileSync(fileURLToPath(new URL('../version.json', import.meta.url)), 'utf-8'))
  } catch {
    return { build: 0, sha: 'dev', dirty: true, stamped_at: '' }
  }
}

// 构建期把版本写进 <title>。比在 main.tsx 里改 document.title 好三点:无闪烁、
// curl/链接预览/书签都能看到、不依赖 JS 执行。
function titleWithBuild() {
  const b = buildInfo()
  const tag = b.sha === 'dev' ? 'dev' : `b${b.build}·${b.sha}${b.dirty ? '·dirty' : ''}`
  return {
    name: 'title-with-build',
    transformIndexHtml: (html: string) =>
      html.replace(/<title>(.*?)<\/title>/, `<title>$1 · ${tag}</title>`),
  }
}

// dev 期把 /api 与 /files 代理到 FastAPI 后端(shanhai-web,:8080)
export default defineConfig({
  plugins: [react(), titleWithBuild()],
  define: { __BUILD__: JSON.stringify(buildInfo()) },
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8080',
      '/files': 'http://127.0.0.1:8080',
    },
  },
})
