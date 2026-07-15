import { useEffect, useState } from 'react'
import { api } from '../api'
import { CardHeadInline } from './decor'
import { STAGE_LABEL } from '../stages'
import type { AppConfigInput, AppConfigView, ConfigOverrideInput, ConfigOverrideView, Meta } from '../types'

// PUT 密钥语义:未改送此哨兵(后端保持已存值不变);与 runtime_config.py 的 _SENTINEL 对应
const SENTINEL = '__UNCHANGED__'

type Group = 'llm' | 'image' | 'tts'
type FieldKey = keyof ConfigOverrideView

interface FieldDef {
  key: FieldKey
  label: string
  kind: 'text' | 'number' | 'select' | 'secret'
}

const LLM_FIELDS: FieldDef[] = [
  { key: 'llm_base_url', label: '端点 Base URL', kind: 'text' },
  { key: 'llm_api_key', label: 'API Key', kind: 'secret' },
  { key: 'llm_model', label: '模型', kind: 'text' },
  { key: 'llm_provider', label: 'Provider', kind: 'select' },
  { key: 'llm_timeout', label: '超时(秒)', kind: 'number' },
]
const IMAGE_FIELDS: FieldDef[] = [
  { key: 'image_base_url', label: '端点 Base URL', kind: 'text' },
  { key: 'image_api_key', label: 'API Key', kind: 'secret' },
  { key: 'image_model', label: '模型', kind: 'text' },
  { key: 'image_api_mode', label: 'API 模式', kind: 'text' },
  { key: 'image_size', label: '图片尺寸', kind: 'text' },
]
const TTS_FIELDS: FieldDef[] = [
  { key: 'tts_base_url', label: '端点 Base URL', kind: 'text' },
  { key: 'tts_api_key', label: 'API Key', kind: 'secret' },
  { key: 'tts_model', label: '模型', kind: 'text' },
  { key: 'tts_voice', label: '默认音色', kind: 'text' },
  { key: 'tts_voices', label: '可选音色(逗号分隔)', kind: 'text' },
]
const GROUP_FIELDS: Record<Group, FieldDef[]> = { llm: LLM_FIELDS, image: IMAGE_FIELDS, tts: TTS_FIELDS }
const GROUP_LABEL: Record<Group, string> = { llm: 'LLM(文本生成)', image: '图像生成', tts: '语音合成' }
const ALL_FIELDS: FieldDef[] = [...LLM_FIELDS, ...IMAGE_FIELDS, ...TTS_FIELDS]

const EMPTY_OVERRIDE_VIEW: ConfigOverrideView = {
  base_url: null,
  api_key: null,
  llm_base_url: null,
  llm_api_key: null,
  llm_model: null,
  llm_provider: null,
  llm_timeout: null,
  image_base_url: null,
  image_api_key: null,
  image_model: null,
  image_api_mode: null,
  image_size: null,
  tts_base_url: null,
  tts_api_key: null,
  tts_model: null,
  tts_voice: null,
  tts_voices: null,
}

function isGroup(g: string): g is Group {
  return g === 'llm' || g === 'image' || g === 'tts'
}

function initValues(view: ConfigOverrideView | undefined): Record<string, string> {
  const v = view ?? EMPTY_OVERRIDE_VIEW
  const out: Record<string, string> = {}
  for (const f of ALL_FIELDS) {
    if (f.kind === 'secret') {
      out[f.key] = ''
      continue
    }
    const val = v[f.key]
    out[f.key] = val == null ? '' : String(val)
  }
  return out
}

export function SettingsPanel({ meta, onClose }: { meta: Meta | null; onClose: () => void }) {
  const [cfg, setCfg] = useState<AppConfigView | null>(null)
  const [globalValues, setGlobalValues] = useState<Record<string, string>>({})
  const [stageValues, setStageValues] = useState<Record<string, Record<string, string>>>({})
  const [touched, setTouched] = useState<Set<string>>(new Set())
  const [expanded, setExpanded] = useState(false)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const applyConfig = (next: AppConfigView) => {
    setCfg(next)
    setGlobalValues(initValues(next.global))
    setStageValues(
      Object.fromEntries(Object.keys(next.stage_clients).map((s) => [s, initValues(next.stages[s])]))
    )
    setTouched(new Set())
  }

  useEffect(() => {
    api
      .getConfig()
      .then(applyConfig)
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false))
  }, [])

  const ro = !!cfg?.readonly || !!meta?.readonly

  function getValue(scope: string, key: FieldKey): string {
    if (scope === 'global') return globalValues[key] ?? ''
    return stageValues[scope]?.[key] ?? ''
  }

  function setFieldValue(scope: string, key: FieldKey, val: string) {
    if (scope === 'global') {
      setGlobalValues((v) => ({ ...v, [key]: val }))
    } else {
      setStageValues((v) => ({ ...v, [scope]: { ...(v[scope] ?? {}), [key]: val } }))
    }
  }

  function markTouched(scope: string, key: FieldKey) {
    setTouched((t) => new Set(t).add(`${scope}::${key}`))
  }

  // 有效继承值:非密钥字段的 placeholder——全局区显示 defaults,环节区显示 global 覆盖 defaults
  function effectiveNonSecret(scope: string, key: FieldKey): string | number | null {
    if (!cfg) return null
    const fallback = cfg.defaults[key] as string | number | null
    if (scope === 'global') return fallback
    const g = cfg.global[key] as string | number | null
    return g != null ? g : fallback
  }

  // 该 scope 的密钥字段是否"已配置"(继承链上任一层有值即算,决定 placeholder 文案)
  function isSecretConfigured(scope: string, key: FieldKey): boolean {
    if (!cfg) return false
    const defaultsBool = Boolean(cfg.defaults[key])
    const globalSet = cfg.global[key] != null
    if (scope === 'global') return globalSet || defaultsBool
    const stageSet = cfg.stages[scope]?.[key] != null
    return stageSet || globalSet || defaultsBool
  }

  function placeholderFor(scope: string, field: FieldDef): string {
    if (field.kind === 'secret') {
      return isSecretConfigured(scope, field.key) ? '已配置 · 留空不变' : ''
    }
    const eff = effectiveNonSecret(scope, field.key)
    return eff == null || eff === '' ? '' : String(eff)
  }

  function fieldsForScope(scope: string): FieldDef[] {
    if (scope === 'global') return ALL_FIELDS
    return (cfg?.stage_clients[scope] ?? []).filter(isGroup).flatMap((g) => GROUP_FIELDS[g])
  }

  function buildOverride(scope: string): ConfigOverrideInput {
    const out: Record<string, string | number | null> = {}
    for (const field of fieldsForScope(scope)) {
      const raw = getValue(scope, field.key)
      if (field.kind === 'secret') {
        out[field.key] = touched.has(`${scope}::${field.key}`) ? raw : SENTINEL
      } else if (field.kind === 'number') {
        out[field.key] = raw === '' ? null : Number(raw)
      } else {
        out[field.key] = raw === '' ? null : raw
      }
    }
    return out
  }

  // 该环节是否需要写入:已有持久化覆盖(保留,避免整份替换时丢失)或用户实际填了内容
  // (非密钥非空 / 已改动的非空密钥)。避免给未定制的环节塞空覆盖污染 config.json。
  function stageHasContent(scope: string): boolean {
    for (const field of fieldsForScope(scope)) {
      const raw = getValue(scope, field.key)
      if (field.kind === 'secret') {
        if (touched.has(`${scope}::${field.key}`) && raw !== '') return true
      } else if (raw !== '') {
        return true
      }
    }
    return false
  }

  async function handleSave() {
    if (!cfg) return
    setSaving(true)
    setErr(null)
    try {
      const stages: Record<string, ConfigOverrideInput> = {}
      for (const s of Object.keys(cfg.stage_clients)) {
        if (cfg.stages[s] !== undefined || stageHasContent(s)) stages[s] = buildOverride(s)
      }
      const payload: AppConfigInput = { global: buildOverride('global'), stages }
      const next = await api.saveConfig(payload)
      applyConfig(next)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  function renderField(scope: string, field: FieldDef) {
    const value = getValue(scope, field.key)
    if (field.kind === 'select') {
      const eff = effectiveNonSecret(scope, field.key)
      return (
        <div key={field.key}>
          <label className={label}>{field.label}</label>
          <select
            className={fieldCls}
            value={value}
            disabled={ro}
            onChange={(e) => setFieldValue(scope, field.key, e.target.value)}
          >
            <option value="">继承{eff ? `(${eff})` : ''}</option>
            <option value="openai">openai</option>
            <option value="ollama">ollama</option>
          </select>
        </div>
      )
    }
    const isSecret = field.kind === 'secret'
    const configured = isSecret && isSecretConfigured(scope, field.key)
    // 已配置密钥留空不变(哨兵);置空+标记 touched 才会清除。故为已配置密钥提供显式"清除"入口,
    // 避免用户误以为"留空即可删除"(实则保持不变)。
    const clearing = isSecret && touched.has(`${scope}::${field.key}`) && value === ''
    return (
      <div key={field.key}>
        <label className={label}>{field.label}</label>
        <input
          className={fieldCls}
          type={isSecret ? 'password' : field.kind === 'number' ? 'number' : 'text'}
          value={value}
          placeholder={clearing ? '将清除 · 保存后继承' : placeholderFor(scope, field)}
          disabled={ro}
          onChange={(e) => {
            setFieldValue(scope, field.key, e.target.value)
            if (isSecret) markTouched(scope, field.key)
          }}
        />
        {configured && !clearing && !ro && (
          <button
            type="button"
            onClick={() => {
              setFieldValue(scope, field.key, '')
              markTouched(scope, field.key)
            }}
            className="mt-1 text-[11px] text-muted transition hover:text-cinnabar"
          >
            清除(改为继承)
          </button>
        )}
      </div>
    )
  }

  function renderGroup(scope: string, group: Group) {
    return (
      <div key={group} className="space-y-2.5">
        <h4 className="text-xs font-medium tracking-wide text-muted">{GROUP_LABEL[group]}</h4>
        <div className="grid grid-cols-2 gap-3">{GROUP_FIELDS[group].map((f) => renderField(scope, f))}</div>
      </div>
    )
  }

  const fieldCls =
    'w-full rounded-lg border border-line bg-white/70 px-3 py-2 text-sm text-ink outline-none transition focus:border-cinnabar focus:bg-white disabled:cursor-not-allowed disabled:opacity-50'
  const label = 'mb-1.5 block text-xs font-medium tracking-wide text-muted'

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/40 p-4" onClick={onClose}>
      <div
        className="max-h-[85vh] w-full max-w-2xl overflow-y-auto rounded-2xl border border-band bg-paper p-5 shadow-paper-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <CardHeadInline glyph="配" title="端点与模型配置" />
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭"
            className="flex h-7 w-7 items-center justify-center rounded-md text-ink-soft transition hover:text-cinnabar"
          >
            ×
          </button>
        </div>

        {loading ? (
          <p className="py-8 text-center text-sm text-muted">加载中…</p>
        ) : !cfg ? (
          <p className="py-8 text-center text-sm text-alarm">{err ?? '加载失败'}</p>
        ) : (
          <div className="space-y-5">
            {ro && (
              <p className="rounded-md bg-kraft px-3 py-2 text-center text-xs text-muted">
                只读模式:配置由所有者本机管理,此处仅可浏览
              </p>
            )}

            <div className="space-y-4">
              <h3 className="text-sm font-semibold tracking-wide text-ink">全局默认</h3>
              {renderGroup('global', 'llm')}
              {renderGroup('global', 'image')}
              {renderGroup('global', 'tts')}
            </div>

            <div className="border-t border-line pt-4">
              <button
                type="button"
                onClick={() => setExpanded((v) => !v)}
                className="text-sm font-semibold tracking-wide text-ink-soft transition hover:text-cinnabar"
              >
                {expanded ? '▾' : '▸'} 按环节覆盖
              </button>
              {expanded && (
                <div className="mt-4 space-y-5">
                  {Object.entries(cfg.stage_clients).map(([stage, groups]) => (
                    <div key={stage} className="space-y-3 rounded-lg border border-line p-3">
                      <h4 className="text-sm font-semibold tracking-wide text-ink">
                        {STAGE_LABEL[stage] ?? stage}
                      </h4>
                      {groups.filter(isGroup).map((g) => renderGroup(stage, g))}
                    </div>
                  ))}
                </div>
              )}
            </div>

            {err && <p className="rounded-md bg-alarm/8 px-3 py-2 text-sm text-alarm">{err}</p>}

            <button
              type="button"
              onClick={handleSave}
              disabled={saving || ro}
              className="flex w-full items-center justify-center gap-2 rounded-lg bg-gradient-to-br from-cinnabar to-cinnabar-deep px-4 py-2.5 font-serif text-sm font-semibold tracking-[3px] text-rice shadow-[0_4px_12px_rgba(138,43,34,0.28)] transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {saving ? '保存中…' : '保存'}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
