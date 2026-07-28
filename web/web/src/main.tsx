import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'

// 标签页标题里的构建号由 vite.config.ts 的 titleWithBuild 插件在构建期写进 index.html,
// 不在这里改 document.title —— 那样会有一帧闪烁,且 curl/链接预览看不到。

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
