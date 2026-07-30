/** 作品/作业状态的**唯一**中文文案映射。
 *
 * 为什么要有这个文件:后端的 `pipeline` 一个字段同时充当"给机器判的状态量"和"给人看的
 * 诚实文本",取值形如 `done` / `done(降级:9/11 页真人解说,2 页静音兜底)` /
 * `partial: 尚未合成成片` / `error: 服务重启,生成中断` / `queued` / `running` /
 * `cancelled`,以及读取端的默认值 `pending`。此前 ProjectList 有一份中文映射,而
 * QueuePanel 直接渲染原值、ProgressSteps 只处理了三种、其余也落到原值——于是同一个状态
 * 在三处显示成三种样子,用户看到的是「running」和「已完成」混在一起(本次反馈的正是这个)。
 *
 * 判据只放显示层:`ACTIVE`(App.tsx)与各处 `startsWith('done'|'error'|'partial')` 一律
 * 不动。文案与判据是两件事,这次只统一前者。
 */

export interface PipelineLabel {
  /** 中文短标签,列表/队列那种紧凑位置只显示这个 */
  text: string
  /** 前缀后面那段原文(如「尚未合成成片」「服务重启,生成中断」),详情页附在标签后面。
   *  刻意保留:出错原因现在正是靠渲染原值带出来的,丢了就没法排查。
   *  注意 `error: {e}` 里的 {e} 是上游异常文本,**可能本身就是英文**——那是上游给的字,
   *  粉饰它只会丢线索,所以这里不翻译、原样透出。 */
  detail: string
  /** badge 配色,沿用 ProjectList 原有那套 */
  cls: string
}

const NEUTRAL = 'bg-kraft text-muted'
const WARM = 'bg-amber2/15 text-gold'

/** 取冒号后面那段详情;没有冒号则空串。
 *
 * 两个坑都是造齐 10 种真实取值、逐个看渲染时才抓到的:
 * 1. `done(降级:9/11 页…,2 页静音兜底)` 的冒号在括号**里面**,直接按冒号切会把结尾那个
 *    `)` 一起带出来,界面显示成「…静音兜底)」多一个括号。
 * 2. 括号剥离只能对 `done(` 这一种形状生效。写成通用的"剥掉最外层括号"会误伤错误信息——
 *    `error: connect failed (timeout)` 会被剥成 `timeout`、里面又没有冒号,detail 直接丢空,
 *    而那正是排查时最需要的那句话。 */
function detailOf(pipeline: string): string {
  const inner = pipeline.startsWith('done(') ? pipeline.slice('done('.length, -1) : pipeline
  const colon = inner.indexOf(':')
  return colon >= 0 ? inner.slice(colon + 1).trim() : ''
}

export function pipelineLabel(pipeline: string): PipelineLabel {
  const detail = detailOf(pipeline)
  // 降级成片形如 "done(降级:...)" —— 仍属已完成,单独标注,不能被当成失败
  if (pipeline.startsWith('done')) {
    return pipeline.includes('降级')
      ? { text: '已完成·降级', detail, cls: WARM }
      : { text: '已完成', detail: '', cls: 'bg-jade/12 text-jade' }
  }
  if (pipeline.startsWith('error')) return { text: '出错', detail, cls: 'bg-alarm/10 text-alarm' }
  if (pipeline.startsWith('partial')) return { text: '待合成', detail, cls: NEUTRAL }
  if (pipeline === 'running') return { text: '生成中', detail: '', cls: WARM }
  // 与 running 分开:用户看得出"还没开始"和"正在跑"的区别(本次反馈里明确列了这两个)
  if (pipeline === 'queued') return { text: '排队中', detail: '', cls: WARM }
  if (pipeline === 'cancelled') return { text: '已取消', detail: '', cls: NEUTRAL }
  // pending 是读取端的默认值(api 的 status.get("pipeline", "pending")),
  // 线上真有作品的 status 里没有这个键,会走到这里
  if (pipeline === 'pending' || !pipeline) return { text: '未开始', detail: '', cls: NEUTRAL }
  // 兜底:后端将来加了新取值,至少别把英文原文当中文标签显示在最显眼处
  return { text: '状态未知', detail: pipeline, cls: NEUTRAL }
}
