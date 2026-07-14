import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// dev 期把 /api 与 /files 代理到 FastAPI 后端(shanhai-web,:8080)
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8080',
      '/files': 'http://127.0.0.1:8080',
    },
  },
})
