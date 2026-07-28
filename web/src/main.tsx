import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'
import { fmtBuild } from './version'

// 标签页标题带上前端构建号(标签页本来就是前端那一半)。放这里而不是 index.html:
// vite 的 %VAR% 只替换 env 变量,拿不到 __BUILD__ 这个对象。
document.title = `WanderInk · 景区传说有声连环画 · ${fmtBuild(__BUILD__)}`

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
