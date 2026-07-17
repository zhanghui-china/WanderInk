import { useEffect, useRef, useState } from 'react'
import { SCENIC_SPOTS } from '../data/scenicSpots'

const MAX_SUGGESTIONS = 8

// 新建作品「景区名」输入框 + 智能补全下拉:对全国 5A 景区名录做子串匹配提供建议,
// 但不强制——找不到匹配或想写名录之外的景区,照常自由输入提交。
export function ScenicSpotPicker({
  value,
  onChange,
  className,
  placeholder,
  required,
}: {
  value: string
  onChange: (v: string) => void
  className: string
  placeholder?: string
  required?: boolean
}) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)

  const query = value.trim()
  const matches = query
    ? SCENIC_SPOTS.filter(
        (s) => s.name.includes(query) || s.fullName.includes(query) || s.province.includes(query),
      ).slice(0, MAX_SUGGESTIONS)
    : []

  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false)
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDocClick)
    window.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDocClick)
      window.removeEventListener('keydown', onKey)
    }
  }, [])

  return (
    <div ref={rootRef} className="relative">
      <input
        className={className}
        value={value}
        onChange={(e) => {
          onChange(e.target.value)
          setOpen(true)
        }}
        onFocus={() => setOpen(true)}
        placeholder={placeholder}
        required={required}
        autoComplete="off"
      />
      {open && matches.length > 0 && (
        <ul className="absolute z-10 mt-1 max-h-64 w-full overflow-y-auto rounded-lg border border-line bg-white shadow-paper-lg">
          {matches.map((s) => (
            <li key={s.fullName}>
              <button
                type="button"
                onClick={() => {
                  onChange(s.name)
                  setOpen(false)
                }}
                className="flex w-full flex-col items-start gap-0.5 px-3 py-1.5 text-left transition hover:bg-kraft/40"
              >
                <span className="text-sm text-ink">
                  {s.name}
                  <span className="ml-1.5 rounded-full bg-kraft px-1.5 py-0.5 text-[10px] text-ink-soft">
                    {s.province}
                  </span>
                </span>
                <span className="text-[11px] text-muted">{s.fullName}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
