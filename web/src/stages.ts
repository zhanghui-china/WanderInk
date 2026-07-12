// 环节 key→中文标签的单一真源:进度条(ProgressSteps)与按环节覆盖(SettingsPanel)共用。
// 后端 runtime_config.STAGE_CLIENTS 决定各环节用哪些 client;S6(合成)无端点,仅在进度条出现。
export const STAGES: { key: string; label: string; sub: string }[] = [
  { key: 's0', label: '传说', sub: 'LEGEND' },
  { key: 's1', label: '剧本', sub: 'SCRIPT' },
  { key: 's2', label: '分镜', sub: 'BOARD' },
  { key: 's3', label: '角色', sub: 'ROLE' },
  { key: 's4', label: '漫画页', sub: 'PAGES' },
  { key: 's5', label: '配音', sub: 'VOICE' },
  { key: 's6', label: '合成', sub: 'FILM' },
]

export const STAGE_LABEL: Record<string, string> = Object.fromEntries(
  STAGES.map((s) => [s.key, s.label]),
)
