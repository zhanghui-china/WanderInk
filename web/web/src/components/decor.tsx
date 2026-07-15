// 「天青烟雨」画卷装饰基元:水墨山形、朱砂印章、竖排题字、卡片题头、图片描金边框。
// 纯视觉,不持有业务状态。
import type { ReactNode } from 'react'

export function InkScape({
  className,
  tone = 'light',
}: {
  className?: string
  tone?: 'light' | 'dark'
}) {
  const a =
    tone === 'light' ? ['#dfeae4', '#cfe0d8', '#bcd3c9'] : ['#2f463e', '#243830', '#182720']
  return (
    <svg
      className={className}
      viewBox="0 0 1440 320"
      preserveAspectRatio="xMidYMax slice"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      <path
        d="M0 240 C180 160 300 210 460 170 C640 125 760 205 940 165 C1120 125 1280 200 1440 175 L1440 320 L0 320 Z"
        fill={a[0]}
        opacity="0.5"
      />
      <path
        d="M0 275 C160 215 320 255 500 220 C700 180 820 250 1010 220 C1200 190 1320 250 1440 235 L1440 320 L0 320 Z"
        fill={a[1]}
        opacity="0.7"
      />
      <path
        d="M0 300 C220 270 360 292 560 275 C780 256 900 300 1120 285 C1280 274 1360 296 1440 290 L1440 320 L0 320 Z"
        fill={a[2]}
      />
    </svg>
  )
}

export function Seal({
  char,
  rot = -8,
  size = 44,
  className = '',
}: {
  char: string
  rot?: number
  size?: number
  className?: string
}) {
  return (
    <span
      className={`inline-flex items-center justify-center rounded-[7px] font-brush text-rice shadow-[0_3px_10px_rgba(127,32,24,.35)] animate-seal-in ${className}`}
      style={{
        width: size,
        height: size,
        fontSize: size * 0.56,
        lineHeight: 1,
        transform: `rotate(${rot}deg)`,
        background: 'linear-gradient(150deg,#c14631,#7d1f16)',
        border: '1.5px solid rgba(255,240,225,.35)',
      }}
    >
      {char}
    </span>
  )
}

export function VLabel({
  children,
  className = '',
}: {
  children: ReactNode
  className?: string
}) {
  return <span className={`vtext font-serif tracking-[3px] ${className}`}>{children}</span>
}

// 图片区边框:单线 + 一道细描金内圈
export const mountFrame = 'relative rounded-lg border border-line ring-1 ring-inset ring-gold/20'

export function CardHead({
  glyph,
  title,
  extra,
}: {
  glyph: string
  title: string
  extra?: string
}) {
  return (
    <div className="mb-4 flex items-center gap-3">
      <span className="flex h-9 w-9 items-center justify-center rounded-[7px] bg-gradient-to-br from-cinnabar-bright to-cinnabar-deep font-brush text-lg text-rice shadow-[0_3px_8px_rgba(127,32,24,.3)] ring-1 ring-inset ring-gold/30">
        {glyph}
      </span>
      <h2 className="font-serif text-lg font-bold tracking-[3px] text-ink">{title}</h2>
      {extra && (
        <span className="rounded-full bg-kraft/70 px-2.5 py-0.5 text-[11px] tracking-wide text-ink-soft">
          {extra}
        </span>
      )}
      <span className="ml-1 h-px flex-1 bg-gradient-to-r from-gold/60 via-line to-transparent" />
    </div>
  )
}

export function CardHeadInline({ glyph, title }: { glyph: string; title: string }) {
  return (
    <span className="flex items-center gap-2.5">
      <span className="flex h-8 w-8 items-center justify-center rounded-[7px] bg-gradient-to-br from-cinnabar-bright to-cinnabar-deep font-brush text-base text-rice ring-1 ring-inset ring-gold/30">
        {glyph}
      </span>
      <span className="font-serif text-lg font-bold tracking-[3px] text-ink">{title}</span>
    </span>
  )
}
