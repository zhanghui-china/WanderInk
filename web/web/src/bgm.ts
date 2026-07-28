// 配乐结果文案的单一真源:进度卡的 S5 行标签用 short,悬停提示用 text。
// 形制照 stages.ts / version.ts —— 独立小模块,不从 ProjectDetail 导出:
// ProjectDetail 已经 import 了 ProgressSteps,反向 import 会成环。
//
// short 与 text 都写全,关键信息**不放在只有 hover 才看得到的地方**:short 自己就区分了
// AI/曲库/未配乐/失败四态,text 只是补充说明。进度卡的悬停提示在移动端拿不到(项目已接受
// 这个取舍),但"配乐到底成没成"不该跟着一起丢。
//
// 为什么非要显示:改造前配乐失败是完全静默的,music-shim 的模板路径写错了一行,
// 攒到 33 个无配乐的作品才被用户发现。
export const BGM_NOTE: Record<string, { short: string; text: string; alarm?: boolean }> = {
  ai: { short: 'AI 配乐', text: '已配乐(AI 生成)' },
  manifest: { short: '曲库配乐', text: '已配乐(曲库选曲)' },
  skipped: { short: '未配乐', text: '建作品时未勾选配乐' },
  failed: { short: '配乐失败', text: '配乐生成失败,本片无背景音乐', alarm: true },
}
