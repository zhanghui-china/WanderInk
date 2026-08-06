import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { CardHeadInline } from './decor'
import { STAGE_LABEL } from '../stages'
import type {
  AppConfigInput,
  AppConfigView,
  ConfigOverrideInput,
  ConfigOverrideView,
  Meta,
  UserAccount,
} from '../types'

// PUT 密钥语义:未改送此哨兵(后端保持已存值不变);与 runtime_config.py 的 _SENTINEL 对应
const SENTINEL = '__UNCHANGED__'

type Group = 'llm' | 'image' | 'tts' | 'music'
type FieldKey = keyof ConfigOverrideView

interface FieldDef {
  key: FieldKey
  label: string
  kind: 'text' | 'number' | 'select' | 'secret' | 'lora'
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
  { key: 'image_lora_model', label: 'LoRA 模型', kind: 'lora' },
]
const TTS_FIELDS: FieldDef[] = [
  { key: 'tts_base_url', label: '端点 Base URL', kind: 'text' },
  { key: 'tts_api_key', label: 'API Key', kind: 'secret' },
  { key: 'tts_model', label: '模型', kind: 'text' },
  { key: 'tts_voice', label: '默认音色', kind: 'text' },
  { key: 'tts_voices', label: '可选音色(逗号分隔)', kind: 'text' },
  { key: 'tts_voice_en', label: '英文轨音色', kind: 'text' },
]
const MUSIC_FIELDS: FieldDef[] = [
  { key: 'music_base_url', label: '端点 Base URL', kind: 'text' },
  { key: 'music_api_key', label: 'API Key', kind: 'secret' },
  { key: 'music_model', label: '模型', kind: 'text' },
]
const GROUP_FIELDS: Record<Group, FieldDef[]> = {
  llm: LLM_FIELDS,
  image: IMAGE_FIELDS,
  tts: TTS_FIELDS,
  music: MUSIC_FIELDS,
}
const GROUP_LABEL: Record<Group, string> = {
  llm: 'LLM(文本生成)',
  image: '图像生成',
  tts: '语音合成',
  music: '背景音乐',
}
const ALL_FIELDS: FieldDef[] = [...LLM_FIELDS, ...IMAGE_FIELDS, ...TTS_FIELDS, ...MUSIC_FIELDS]

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
  image_lora_model: null,
  tts_base_url: null,
  tts_api_key: null,
  tts_model: null,
  tts_voice: null,
  tts_voices: null,
  tts_voice_en: null,
  music_base_url: null,
  music_api_key: null,
  music_model: null,
}

function isGroup(g: string): g is Group {
  return g === 'llm' || g === 'image' || g === 'tts' || g === 'music'
}

function initValues(view: Partial<ConfigOverrideView> | undefined): Record<string, string> {
  const v = { ...EMPTY_OVERRIDE_VIEW, ...(view ?? {}) }
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

// scope 是这个面板的统一寻址方式:'global' / 环节名(s0…s5)/ `user:<登录名>`。
// 用同一套 getValue/buildOverride/renderField 走三种层,不为用户层另开一条渲染路径。
const USER_SCOPE = 'user:'
const userScope = (name: string) => USER_SCOPE + name
const scopeUser = (scope: string) => (scope.startsWith(USER_SCOPE) ? scope.slice(USER_SCOPE.length) : null)

export function SettingsPanel({
  meta,
  user,
  isAdmin,
  onClose,
}: {
  meta: Meta | null
  user: string
  isAdmin: boolean
  onClose: () => void
}) {
  const [cfg, setCfg] = useState<AppConfigView | null>(null)
  const [globalValues, setGlobalValues] = useState<Record<string, string>>({})
  const [userValues, setUserValues] = useState<Record<string, Record<string, string>>>({})
  const [stageValues, setStageValues] = useState<Record<string, Record<string, string>>>({})
  const [touched, setTouched] = useState<Set<string>>(new Set())
  const [expanded, setExpanded] = useState(false)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const applyConfig = (next: AppConfigView) => {
    setCfg(next)
    setGlobalValues(initValues(next.global))
    // 自己那条一定要有编辑态(哪怕后端还没存过);管理员另外拿到别人已存在的条目
    setUserValues(
      Object.fromEntries(
        [user, ...Object.keys(next.users)].map((u) => [u, initValues(next.users[u])])
      )
    )
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
    const u = scopeUser(scope)
    if (u !== null) return userValues[u]?.[key] ?? ''
    return stageValues[scope]?.[key] ?? ''
  }

  function setFieldValue(scope: string, key: FieldKey, val: string) {
    const u = scopeUser(scope)
    if (scope === 'global') {
      setGlobalValues((v) => ({ ...v, [key]: val }))
    } else if (u !== null) {
      setUserValues((v) => ({ ...v, [u]: { ...(v[u] ?? {}), [key]: val } }))
    } else {
      setStageValues((v) => ({ ...v, [scope]: { ...(v[scope] ?? {}), [key]: val } }))
    }
  }

  function markTouched(scope: string, key: FieldKey) {
    setTouched((t) => new Set(t).add(`${scope}::${key}`))
  }

  // 有效继承值:非密钥字段的 placeholder——全局区显示 defaults,用户区与环节区显示 global 覆盖 defaults。
  // 用户区不叠环节层、环节区也不叠用户层:那两层谁生效取决于"这是谁的作品",在配置面板里无从得知,
  // 与其显示一个可能是错的数字,不如只显示确定成立的那段继承链(界面上另有文字说明优先级)。
  function effectiveNonSecret(scope: string, key: FieldKey): string | number | null {
    if (!cfg) return null
    const fallback = cfg.defaults[key] as string | number | null
    if (scope === 'global') return fallback
    const g = cfg.global[key] as string | number | null
    return g != null ? g : fallback
  }

  // LoRA 只对本地 ComfyUI 后端有意义:判定标准与后端 runtime_config.image_concurrency()
  // 一致(hostname 是 127.0.0.1/localhost),避免用户在远程后端(如 tu-zi)误设一个不会生效的字段。
  // 优先用当前输入框里的值(用户刚填但还没保存也该立刻生效),没填时才退回继承链的有效值——
  // effectiveNonSecret 在 scope==='global' 时只返回 .env 基线,不含用户正在编辑但未保存的值。
  function isLocalImageBackend(scope: string): boolean {
    const base = getValue(scope, 'image_base_url') || effectiveNonSecret(scope, 'image_base_url')
    if (!base) return false
    try {
      const host = new URL(String(base)).hostname
      return host === '127.0.0.1' || host === 'localhost'
    } catch {
      return false
    }
  }

  // 该 scope 的密钥字段是否"已配置"(继承链上任一层有值即算,决定 placeholder 文案)
  function isSecretConfigured(scope: string, key: FieldKey): boolean {
    if (!cfg) return false
    const defaultsBool = Boolean(cfg.defaults[key])
    const globalSet = cfg.global[key] != null
    if (scope === 'global') return globalSet || defaultsBool
    const u = scopeUser(scope)
    if (u !== null) {
      const userSet = (cfg.users[u] as Record<string, unknown> | undefined)?.[key] != null
      return userSet || globalSet || defaultsBool
    }
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
    // 用户层只有 LLM:图像端点若能按人配,后端两处按 hostname 的单并发判定会静默失效
    // (见 runtime_config.UserOverride 的说明)。后端 extra="forbid" 会 422,这里也别画出来。
    if (scopeUser(scope) !== null) return LLM_FIELDS
    return (cfg?.stage_clients[scope] ?? []).filter(isGroup).flatMap((g) => GROUP_FIELDS[g])
  }

  // 只提交用户实际改过的字段(比照密钥字段的 touched 机制),未触碰的 key 一律不写入 payload,
  // 交由后端 exclude_unset 保留既有值——否则整表覆盖会静默抹掉并发/陈旧会话刚存的其它覆盖。
  // "清除→继承"仍可用:主动清空某字段会 markTouched,故空串会作为显式 null 发出。
  function buildOverride(scope: string): ConfigOverrideInput {
    const out: Record<string, string | number | null> = {}
    for (const field of fieldsForScope(scope)) {
      const isTouched = touched.has(`${scope}::${field.key}`)
      const raw = getValue(scope, field.key)
      if (field.kind === 'secret') {
        out[field.key] = isTouched ? raw : SENTINEL
      } else if (!isTouched) {
        continue
      } else if (field.kind === 'number') {
        out[field.key] = raw === '' ? null : Number(raw)
      } else {
        out[field.key] = raw === '' ? null : raw
      }
    }
    return out
  }

  // 该 scope(环节或用户)是否需要写入:已有持久化覆盖(保留,避免整份替换时丢失)或用户实际
  // 填了内容(非密钥非空 / 已改动的非空密钥)。避免给未定制的条目塞空覆盖污染 config.json。
  function scopeHasContent(scope: string): boolean {
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
      // 只发自己有权改的层:非管理员发 global/stages 会被后端 403,连带自己的改动一起丢。
      const users: Record<string, ConfigOverrideInput> = {}
      for (const u of Object.keys(userValues)) {
        if (!isAdmin && u !== user) continue
        if (cfg.users[u] !== undefined || scopeHasContent(userScope(u))) {
          users[u] = buildOverride(userScope(u))
        }
      }
      const payload: AppConfigInput = { users }
      if (isAdmin) {
        const stages: Record<string, ConfigOverrideInput> = {}
        for (const s of Object.keys(cfg.stage_clients)) {
          if (cfg.stages[s] !== undefined || scopeHasContent(s)) stages[s] = buildOverride(s)
        }
        payload.global = buildOverride('global')
        payload.stages = stages
      }
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
    if (field.kind === 'lora') {
      if (!isLocalImageBackend(scope)) return null   // 非本地 ComfyUI 后端不显示这个控件
      const eff = effectiveNonSecret(scope, field.key)
      return (
        <div key={field.key}>
          <label className={label}>{field.label}</label>
          <select
            className={fieldCls}
            value={value}
            disabled={ro}
            onChange={(e) => {
              setFieldValue(scope, field.key, e.target.value)
              markTouched(scope, field.key)
            }}
          >
            <option value="">默认(Real_ani_qwen){eff ? `(继承:${eff})` : ''}</option>
            {(meta?.loras ?? []).map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
          {/* 判据是"这一次生成有没有带参考图",不是"是漫画页还是三视图":带参考图才走
              ComfyUI 的 image_edit 工作流,只有那条工作流里有 LoRA 节点。
              ⚠️ 上一版写成"角色三视图不支持"是错的——用户给角色传了参考图时 S3 同样走 edit
              路径(见 s3_characters 的 TURNAROUND_REF_TMPL),LoRA 在那里是生效的。
              逐页的实际情况在漫画页卡上有标签,这里只讲规则。 */}
          <p className="mt-1 text-[11px] text-muted">
            仅对带参考图的生成生效(漫画页有出场角色、或角色已上传参考图);无参考图的页与三视图不适用
          </p>
        </div>
      )
    }
    if (field.kind === 'select') {
      const eff = effectiveNonSecret(scope, field.key)
      return (
        <div key={field.key}>
          <label className={label}>{field.label}</label>
          <select
            className={fieldCls}
            value={value}
            disabled={ro}
            onChange={(e) => {
              setFieldValue(scope, field.key, e.target.value)
              markTouched(scope, field.key)
            }}
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
            markTouched(scope, field.key)
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
          {/* 标题从「端点与模型配置」改成中性的「设置」:面板里现在还有账号那一块 */}
          <CardHeadInline glyph="设" title="设置" />
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

            {/* 自己的个人配置放最前:对普通用户这是唯一能改的一块,对管理员也是最常改的。 */}
            <div className="space-y-4">
              <h3 className="text-sm font-semibold tracking-wide text-ink">我的模型</h3>
              <p className="text-[11px] leading-relaxed text-muted">
                只对<b>你自己的作品</b>生效,不影响别人。只能配文本生成(LLM)——图像、配音、
                配乐由管理员全站统一配置,因为本机出图靠"端点是本地回环地址"来保证同时只跑一路,
                改成别的地址会让这个保护静默失效。
                {isAdmin && '管理员为某个环节单独钉死的配置优先级更高,会盖过这里。'}
              </p>
              {renderGroup(userScope(user), 'llm')}
            </div>

            {isAdmin && (
              <div className="space-y-4 border-t border-line pt-4">
                <h3 className="text-sm font-semibold tracking-wide text-ink">全局默认</h3>
                {renderGroup('global', 'llm')}
                {renderGroup('global', 'image')}
                {renderGroup('global', 'tts')}
                {renderGroup('global', 'music')}
              </div>
            )}

            {isAdmin && (
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
            )}

            {/* 管理员可代改别人的个人配置。只列**已存在**的条目:从零给某人建一条需要先知道
                有哪些账号,而这里没有账号列表接口——那种情况让本人自己在这个面板里设即可。 */}
            {isAdmin && Object.keys(cfg.users).filter((u) => u !== user).length > 0 && (
              <div className="space-y-4 border-t border-line pt-4">
                <h3 className="text-sm font-semibold tracking-wide text-ink">其他人的模型</h3>
                {Object.keys(cfg.users)
                  .filter((u) => u !== user)
                  .map((u) => (
                    <div key={u} className="space-y-3 rounded-lg border border-line p-3">
                      <h4 className="text-sm font-semibold tracking-wide text-ink">{u}</h4>
                      {renderGroup(userScope(u), 'llm')}
                    </div>
                  ))}
              </div>
            )}

            <AccountSection user={user} isAdmin={isAdmin} readonly={ro} />

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

// ---------- 账号 ----------
// 独立组件、独立的 busy/err state:**不能**复用面板底部那颗「保存」——它走 handleSave()
// 提交整份 AppConfigInput,与账号操作语义完全不同,混在一起会让人以为改密码也要点那颗才生效。
// 表单惯例照 LoginPage:busy/err 两个 state、错误条同一套类名、必填校验做在按钮 disabled 里。
function AccountSection({
  user,
  isAdmin,
  readonly,
}: {
  user: string
  isAdmin: boolean
  readonly: boolean
}) {
  const [oldPwd, setOldPwd] = useState('')
  const [newPwd, setNewPwd] = useState('')
  const [confirmPwd, setConfirmPwd] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const [users, setUsers] = useState<UserAccount[] | null>(null)
  const [newName, setNewName] = useState('')
  const [newUserPwd, setNewUserPwd] = useState('')
  const [newIsAdmin, setNewIsAdmin] = useState(false)

  const fieldCls =
    'w-full rounded-lg border border-line bg-white/70 px-3 py-2 text-sm text-ink outline-none transition focus:border-cinnabar focus:bg-white disabled:cursor-not-allowed disabled:opacity-50'
  const label = 'mb-1.5 block text-xs font-medium tracking-wide text-muted'
  const smallBtn =
    'rounded-md border border-line bg-white/50 px-2 py-1 text-[11px] text-ink-soft transition hover:border-cinnabar hover:text-cinnabar disabled:cursor-not-allowed disabled:opacity-40'

  const refreshUsers = useCallback(() => {
    if (!isAdmin) return
    api
      .listUsers()
      .then(setUsers)
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)))
  }, [isAdmin])

  useEffect(refreshUsers, [refreshUsers])

  // 后端 HTTPException 的 detail 会被 j<T>() 原样抛成 Error.message,直接显示即可
  async function run(fn: () => Promise<unknown>, ok: string) {
    setBusy(true)
    setErr(null)
    setNotice(null)
    try {
      await fn()
      setNotice(ok)
      refreshUsers()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const pwdMismatch = !!newPwd && !!confirmPwd && newPwd !== confirmPwd
  const canSubmitPwd = !readonly && !busy && !!oldPwd && newPwd.length >= 8 && !pwdMismatch

  return (
    <div className="space-y-4 border-t border-line pt-4">
      <h3 className="text-sm font-semibold tracking-wide text-ink">账号</h3>

      <div className="space-y-2.5">
        <p className="text-[11px] leading-relaxed text-muted">
          修改自己的登录密码。改完之后<b>你在其它设备上的登录会立刻失效</b>,需要用新密码重新登录。
        </p>
        <div className="grid grid-cols-3 gap-3">
          <div>
            <label className={label}>原密码</label>
            <input className={fieldCls} type="password" value={oldPwd} disabled={readonly}
                   onChange={(e) => setOldPwd(e.target.value)} />
          </div>
          <div>
            <label className={label}>新密码</label>
            <input className={fieldCls} type="password" value={newPwd} disabled={readonly}
                   onChange={(e) => setNewPwd(e.target.value)} />
          </div>
          <div>
            <label className={label}>确认新密码</label>
            <input className={fieldCls} type="password" value={confirmPwd} disabled={readonly}
                   onChange={(e) => setConfirmPwd(e.target.value)} />
          </div>
        </div>
        <button
          type="button"
          disabled={!canSubmitPwd}
          onClick={() =>
            run(() => api.setPassword(user, newPwd, oldPwd), '密码已修改,其它设备需重新登录').then(
              () => {
                setOldPwd('')
                setNewPwd('')
                setConfirmPwd('')
              },
            )
          }
          className={smallBtn}
        >
          {busy ? '处理中…' : '修改密码'}
        </button>
        {/* 按钮置灰但不说原因会让人以为功能坏了(ProjectDetail 里有过同样的教训),故显式提示 */}
        {!canSubmitPwd && !readonly && (
          <p className="text-[11px] text-muted">
            {pwdMismatch
              ? '两次输入的新密码不一致'
              : newPwd && newPwd.length < 8
                ? '新密码至少 8 位'
                : '请填写原密码与新密码'}
          </p>
        )}
      </div>

      {isAdmin && (
        <div className="space-y-2.5 border-t border-line pt-3">
          <h4 className="text-sm font-semibold tracking-wide text-ink">新增用户</h4>
          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className={label}>用户名</label>
              <input className={fieldCls} value={newName} disabled={readonly}
                     onChange={(e) => setNewName(e.target.value)} />
            </div>
            <div>
              <label className={label}>初始密码</label>
              <input className={fieldCls} type="password" value={newUserPwd} disabled={readonly}
                     onChange={(e) => setNewUserPwd(e.target.value)} />
            </div>
            <div className="flex items-end pb-1">
              <label className="flex items-center gap-1.5 text-xs text-muted">
                <input type="checkbox" checked={newIsAdmin} disabled={readonly}
                       onChange={(e) => setNewIsAdmin(e.target.checked)} />
                设为管理员
              </label>
            </div>
          </div>
          <button
            type="button"
            disabled={readonly || busy || !newName.trim() || newUserPwd.length < 8}
            onClick={() =>
              run(
                () => api.createUser(newName.trim(), newUserPwd, newIsAdmin),
                `已新增用户 ${newName.trim()}`,
              ).then(() => {
                setNewName('')
                setNewUserPwd('')
                setNewIsAdmin(false)
              })
            }
            className={smallBtn}
          >
            新增用户
          </button>
        </div>
      )}

      {isAdmin && users && users.length > 0 && (
        <div className="space-y-2 border-t border-line pt-3">
          <h4 className="text-sm font-semibold tracking-wide text-ink">用户</h4>
          {users.map((u) => (
            <div key={u.username}
                 className="flex flex-wrap items-center gap-2 rounded-lg border border-line px-3 py-2">
              <span className="text-sm text-ink">{u.username}</span>
              {u.is_admin && (
                <span className="rounded-full bg-gold/20 px-2 py-0.5 text-[10px] text-gold">管理员</span>
              )}
              {u.disabled && (
                <span className="rounded-full bg-kraft px-2 py-0.5 text-[10px] text-muted">已停用</span>
              )}
              {/* 自己那一行不给这些按钮:后端也拦(不能改自己的管理员标记/停用状态),
                  免得把最后一个管理员锁在门外。改自己的密码走上面那块。 */}
              {u.username !== user && (
                <span className="ml-auto flex gap-1.5">
                  <button type="button" className={smallBtn} disabled={readonly || busy}
                    onClick={() => {
                      const pwd = window.prompt(`给用户「${u.username}」设置新密码(至少 8 位):`)
                      if (!pwd) return
                      if (!window.confirm(
                        `确定重置「${u.username}」的密码?对方在所有设备上的登录会立刻失效,` +
                        `需要用新密码重新登录。此操作不可撤销。`)) return
                      run(() => api.setPassword(u.username, pwd, null), `已重置 ${u.username} 的密码`)
                    }}>
                    重置密码
                  </button>
                  <button type="button" className={smallBtn} disabled={readonly || busy}
                    onClick={() =>
                      run(() => api.patchUser(u.username, { is_admin: !u.is_admin }),
                        u.is_admin ? `已取消 ${u.username} 的管理员` : `已设 ${u.username} 为管理员`)
                    }>
                    {u.is_admin ? '取消管理员' : '设为管理员'}
                  </button>
                  <button type="button" className={smallBtn} disabled={readonly || busy}
                    onClick={() => {
                      if (!u.disabled && !window.confirm(
                        `确定停用「${u.username}」?对方将无法登录,现有登录也会立刻失效。` +
                        `其名下作品保持不变,随时可以重新启用。`)) return
                      run(() => api.patchUser(u.username, { disabled: !u.disabled }),
                        u.disabled ? `已启用 ${u.username}` : `已停用 ${u.username}`)
                    }}>
                    {u.disabled ? '启用' : '停用'}
                  </button>
                </span>
              )}
            </div>
          ))}
        </div>
      )}

      {notice && <p className="rounded-md bg-jade/10 px-3 py-2 text-sm text-jade">{notice}</p>}
      {err && <p className="rounded-md bg-alarm/8 px-3 py-2 text-sm text-alarm">{err}</p>}
    </div>
  )
}
