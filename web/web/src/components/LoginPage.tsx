import { useState } from 'react'
import { api } from '../api'

// 整个 SPA 进门先登录:未登录时 App 顶层只渲染本页,不发起其它 API 请求。
export function LoginPage({ onLoggedIn }: { onLoggedIn: () => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setErr(null)
    try {
      await api.login(username.trim(), password)
      onLoggedIn()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  // 复用 NewProjectForm 的卡片/field/label 样式常量,保持视觉一致
  const field =
    'w-full rounded-lg border border-line bg-white/70 px-3 py-2 text-sm text-ink outline-none transition focus:border-cinnabar focus:bg-white'
  const label = 'mb-1.5 block text-xs font-medium tracking-wide text-muted'

  return (
    <div className="flex min-h-screen items-center justify-center px-6">
      <form
        onSubmit={submit}
        className="w-full max-w-sm space-y-4 rounded-2xl border border-band bg-paper p-6 shadow-paper"
      >
        <div className="flex items-center gap-2.5">
          <span className="flex h-9 w-9 items-center justify-center rounded-[10px] bg-gradient-to-br from-cinnabar to-cinnabar-deep font-brush text-2xl leading-none text-rice">
            W
          </span>
          <div className="flex flex-col gap-0.5">
            <h2 className="font-serif text-base font-semibold tracking-wide text-ink">
              WanderInk 登录
            </h2>
            <span className="text-[11px] leading-none tracking-[2px] text-muted">
              景区传说 · 有声连环画
            </span>
          </div>
        </div>

        <div>
          <label className={label}>用户名</label>
          <input
            className={field}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoFocus
            required
          />
        </div>

        <div>
          <label className={label}>密码</label>
          <input
            className={field}
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </div>

        {err && <p className="rounded-md bg-cinnabar/8 px-3 py-2 text-sm text-cinnabar">{err}</p>}

        <button
          type="submit"
          disabled={busy || !username.trim() || !password}
          className="flex w-full items-center justify-center gap-2 rounded-lg bg-gradient-to-br from-cinnabar to-cinnabar-deep px-4 py-2.5 font-serif text-sm font-semibold tracking-[3px] text-rice shadow-[0_4px_12px_rgba(138,43,34,0.28)] transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {busy ? '登录中…' : '登 录'}
        </button>
      </form>
    </div>
  )
}
