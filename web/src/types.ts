// 与后端 shanhai/api.py 的 _serialize / 列表返回结构对应

export interface Meta {
  minutes: number[]
  audiences: string[]
  tones: string[]
  styles: string[]
  readonly?: boolean
}

export interface ProjectSummary {
  project_id: string
  scenic_spot: string
  pipeline: string
  mp4: string | null
}

export interface Page {
  index: number
  caption: string
  emotion: string
  status: 'draft' | 'confirmed' | 'failed'
  duration_ms: number
  image: string | null
  audio: string | null
}

export interface Character {
  name: string
  role: string
  image: string | null
}

export interface ProjectDetail {
  project_id: string
  scenic_spot: string
  style_preset: string
  params: { duration_min: number; audience: string; tone: string }
  status: Record<string, string>
  pipeline: string
  legend: { title: string; summary: string; source_type: string } | null
  script_title: string | null
  characters: Character[]
  pages: Page[]
  mp4: string | null
}

export interface NewProjectInput {
  scenic_spot: string
  minutes: number
  audience: string
  tone: string
  style: string
  story?: string | null
}
