// 与后端 shanhai/api.py 的 _serialize / 列表返回结构对应

export interface Meta {
  minutes: number[]
  audiences: string[]
  tones: string[]
  styles: string[]
  voices?: string[]
  loras?: string[]
  track_langs?: string[]
  readonly?: boolean
}

export interface ProjectSummary {
  project_id: string
  scenic_spot: string
  owner: string
  pipeline: string
  mp4: string | null
}

export interface QueueItem {
  project_id: string
  owner: string
  scenic_spot: string
  pipeline: string
}

export interface Page {
  index: number
  caption: string
  emotion: string
  status: 'draft' | 'confirmed' | 'failed'
  duration_ms: number
  image_gen_ms: number
  silent: boolean
  scene_ref: string
  visual_desc: string
  characters: string[]
  image: string | null
  audio: string | null
  // 本页实际走的生成路径:"edit"/"text2img"/"chat";空串=老数据或尚未生成。
  // text2img 模板没有 LoRA 节点,所选 LoRA 对这一页不生效——PageCard 靠这个字段判断要不要提示。
  image_route: string
  // 本次请求指定的 LoRA 短名;空串不等于"没用 LoRA",而是"未指定,后端回落自己的默认权重"
  // (模板里 LoRA 节点是焊死的,不存在"不用 LoRA")
  image_lora: string
  // 这一页生成时缺三视图参考的出场角色。非空 = 这些角色只有文字特征、没有视觉锚点,
  // 一致性无保证。空数组是正常态(老数据也是空的,不区分"没缺"和"没记录",不值得为此加字段)。
  missing_refs: string[]
  // 附加语种轨,key 是语种码(如 "en");没生成过就是空对象
  tracks: Record<string, LocalizedTrack>
}

export interface LocalizedTrack {
  caption: string
  duration_ms: number
  silent: boolean
  audio: string | null
}

export interface ContentSummary {
  total: number
  imaged: number
  narrated: number
  silent: number
  // 角色维度:分母是**本轮真的会画的**角色数(前 4 个主角 + 传了参考图的),
  // 不是角色总数——否则会永远停在 4/8 那样卡住不动。
  characters_total: number
  characters_imaged: number
}

export interface Character {
  name: string
  role: string
  image: string | null
  // 上传的参考图;后端 _file_url 为空串时归一为 null
  reference_image: string | null
}

export interface ProjectDetail {
  project_id: string
  scenic_spot: string
  owner: string
  style_preset: string
  params: { duration_min: number; audience: string; tone: string; voice?: string }
  status: Record<string, string>
  pipeline: string
  legend: { title: string; summary: string; source_type: string } | null
  script_title: string | null
  characters: Character[]
  pages: Page[]
  deliverable: boolean
  content_summary: ContentSummary
  mp4: string | null
  pdf: string | null
  zip: string | null
  track_mp4: Record<string, string | null>
  // 网页播放器用的 WebVTT 外挂字幕 {lang: url}。MP4 里那几条 mov_text 内嵌轨
  // 浏览器根本不解析,网页显示字幕只能靠 <track> + VTT。
  subtitles?: Record<string, string | null>
  // 附加语种成片各自的字幕(键是成片语种,值是该片内各语种轨):
  // {"en": {"zh": "...", "en": "..."}}。与 subtitles(主片那套)不能混用。
  track_subtitles?: Record<string, Record<string, string | null>>
}

export interface CellPatch {
  caption?: string
  visual_desc?: string
  emotion?: string
  characters?: string[]
}

export interface InsertCellFields {
  caption: string
  visual_desc: string
  emotion?: string
  characters?: string[]
}

export interface NewProjectInput {
  scenic_spot: string
  minutes: number
  audience: string
  tone: string
  style: string
  story?: string | null
  voice?: string
  speed?: number
  multi_panel?: boolean
  bgm?: boolean
  use_hermes_agent?: boolean
  master_skill?: boolean
}

// 与后端 shanhai/runtime_config.py 的 ConfigOverride / AppConfig 对应
export type LlmProvider = 'openai' | 'ollama'

// 单层覆盖的“查看”形态(GET 里 global/stages[*] 的结构):
// 密钥字段脱敏为 "••••••" | null,非密钥字段为实际值 | null(null=继承下层)
export interface ConfigOverrideView {
  base_url: string | null
  api_key: string | null
  llm_base_url: string | null
  llm_api_key: string | null
  llm_model: string | null
  llm_provider: LlmProvider | null
  llm_timeout: number | null
  image_base_url: string | null
  image_api_key: string | null
  image_model: string | null
  image_api_mode: string | null
  image_size: string | null
  image_lora_model: string | null
  tts_base_url: string | null
  tts_api_key: string | null
  tts_model: string | null
  tts_voice: string | null
  tts_voices: string | null
  tts_voice_en: string | null
  music_base_url: string | null
  music_api_key: string | null
  music_model: string | null
}

// .env 基线视图:非密钥字段=实际值,密钥字段=是否已配置(bool)
export interface ConfigDefaults {
  base_url: string
  api_key: boolean
  llm_base_url: string | null
  llm_api_key: boolean
  llm_model: string
  llm_provider: LlmProvider
  llm_timeout: number
  image_base_url: string | null
  image_api_key: boolean
  image_model: string
  image_api_mode: string
  image_size: string
  image_lora_model: string | null
  tts_base_url: string | null
  tts_api_key: boolean
  tts_model: string
  tts_voice: string
  tts_voices: string
  tts_voice_en: string
  music_base_url: string | null
  music_api_key: boolean
  music_model: string
}

export interface AppConfigView {
  readonly: boolean
  stage_clients: Record<string, string[]>
  defaults: ConfigDefaults
  global: ConfigOverrideView
  stages: Record<string, ConfigOverrideView>
}

// PUT 请求体:同 ConfigOverrideView 形状,字段可省略(=null=清除/继承)
export type ConfigOverrideInput = Partial<ConfigOverrideView>

export interface AppConfigInput {
  global?: ConfigOverrideInput
  stages?: Record<string, ConfigOverrideInput>
}
