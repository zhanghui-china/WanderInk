// TUI 风"生成中"指示:一排竖条错落跳动,借用终端进度指示的行为节奏,
// 但用项目既有的朱砂/金配色,不引入等宽字体或黑底绿字的终端观感。
const BARS = [
  { color: 'bg-cinnabar', delay: '0ms' },
  { color: 'bg-gold', delay: '90ms' },
  { color: 'bg-cinnabar', delay: '180ms' },
  { color: 'bg-gold', delay: '270ms' },
  { color: 'bg-cinnabar', delay: '360ms' },
]

export function GeneratingBars() {
  return (
    <span className="flex h-3.5 items-end gap-[3px]" aria-hidden="true">
      {BARS.map((b, i) => (
        <span
          key={i}
          className={`h-full w-[3px] origin-bottom animate-shy-wave rounded-full ${b.color}`}
          style={{ animationDelay: b.delay }}
        />
      ))}
    </span>
  )
}
