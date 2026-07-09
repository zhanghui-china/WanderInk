# 山海 · 整库调研报告

> 分支:`feat/web-frontend`(= `main` 全部后端代码 + `web/` React 前端)
> 性质:只读调研,未改动任何文件。
> 说明:本报告由五路分区子代理结论汇总,并对照实际源码交叉校验。**其中若干“高危”项实为决策文档 0002/0003 的 FIX-BATCH 待办,现已在代码中落地**,本报告以当前代码真实状态为准重新定级。

---

## 1. 一句话定位与架构总览

**山海是一个“景区传说 → 有声连环画短视频”的生成器**:输入景区名(或自备故事),经 S0–S6 线性管线自动产出带角色一致性、字幕、AI 水印、来源标注、配音配乐的 1920×1080 MP4。

架构五要素:

- **S0→S6 线性管线**:七步顺序执行,每步读/写同一个 `Project` 对象,`store.save()` 每步落盘,天然支持断点续跑。
- **单一 `Project` 聚合根**:所有中间态(候选传说、剧本、分镜、角色卡、页产物、输出)都挂在一个 Pydantic `Project` 上,序列化为 `projects/<id>/project.json`,是唯一事实源。
- **OpenAI 兼容 provider 层**:LLM / Image / TTS 三个独立客户端,全部走 OpenAI 兼容协议,可插拔(注释明确未来替换为本地 ComfyUI 只需同签名 `generate()`)。
- **CLI / HTTP 双入口**:`shanhai`(Typer CLI)与 `shanhai-web`(FastAPI)复用同一套 `steps/*` 与 `cli._clients`;HTTP 端把管线丢进后台线程,前端轮询 `project.status`。
- **ffmpeg 合成**:`ffmpeg.py` 纯命令构造(函数式,不执行),`s6_compose.py` 按“片头卡→逐页→片尾卡→xfade 拼接→响度归一化+BGM”串联落地。

```
scenic_spot ─┐
             ▼
  S0 传说检索 → S1 剧本 → S2 分镜 → S3 角色三视图 → S4 逐页生图 → S5 配音 → S6 合成 → final.mp4
             └──────────── 全程读写单一 Project(每步 store.save 落盘)────────────┘
   provider:   LLM        LLM       LLM       LLM+Image       Image       TTS      ffmpeg
```

---

## 2. 模块地图(文件 → 职责)

| 文件 | 行数 | 职责 |
|---|---|---|
| `src/shanhai/schema.py` | ~79 | Pydantic 数据模型:`Project`(聚合根)/`Legend`/`Script`/`CharacterCard`/`StoryboardCell`/`GenerationParams`;`SourceType` 枚举 |
| `src/shanhai/store.py` | ~30 | 项目持久化:`create_project`/`save`/`load`/`project_dir`;**原子写**(写 `.tmp` 后 `os.replace`) |
| `src/shanhai/config.py` | ~30 | `Settings`(pydantic-settings,`SHANHAI_` 前缀);per-modality 端点回退属性 |
| `src/shanhai/cli.py` | ~167 | Typer CLI:`new`/`pick`/`step`/`run`/`status`;`_clients()` 装配三 provider;`_validate_params` 枚举校验 |
| `src/shanhai/api.py` | ~202 | FastAPI:`POST /api/projects`(后台线程跑管线)、`GET /api/projects[/{id}]`、`GET /api/meta`;`_serialize` 转 URL;StaticFiles 挂产物与前端 build |
| `src/shanhai/styles.py` | ~8 | `STYLE_PRESETS` 三档画风提示词前缀 |
| `src/shanhai/typeset.py` | ~90 | PIL 排版:`compose_page`(cover-crop 底图)、`overlay_layer`(透明字幕/水印层)、`title_card`/`credits_card` |
| `src/shanhai/ffmpeg.py` | ~230 | ffmpeg/ffprobe 命令构造(函数式);`sh()`/`probe_duration_ms` 是仅有的执行点 |
| `src/shanhai/providers/llm.py` | — | `LLMClient`:`chat()`/`structured[T]()`,重试+JSON 容错 |
| `src/shanhai/providers/image.py` | ~103 | `ImageClient`:`images_api`/`chat_api` 双形态,`generate()` 重试 |
| `src/shanhai/providers/tts.py` | ~26 | `TTSClient`:`synthesize()`,OpenAI `/audio/speech`,非音频响应拒绝 |
| `src/shanhai/steps/s0..s6` | 421 | 七步生成逻辑(见 §3) |
| `web/src/*` | — | React+Vite+Tailwind 前端:`App.tsx`/`api.ts`/`types.ts`/4 个组件 |

---

## 3. 生成管线 S0–S6

| 步骤 | 读 | 写 | provider | 关键逻辑 |
|---|---|---|---|---|
| **S0 传说检索** `s0_legend.py` | `scenic_spot` | `legend_candidates` 或 `legend` | LLM `structured`/`chat` | 无联网,靠 LLM 知识 + **强制来源标注**(正史/地方志/民间/文学);2~5 候选,无可靠传说返回空。`from_text()` 走自备故事分支,标 `source_type="原创演绎"` |
| **S1 剧本** `s1_script.py` | `legend` | `script` | LLM `structured` | 按时长映射目标字数(1/3/5min→210/650/1100 字 ±20%);冷开场钩子+起承转合叙事框架;**防御性剔除旁白/叙事者**(`is_narrator`),避免占三视图名额;主角≤4 |
| **S2 分镜** `s2_storyboard.py` | `script` | `storyboard[]` | LLM `structured` | 一页一格,页数按时长(8~10/20~24/32~40);硬约束“caption 连起来能独立讲通故事”;emotion 限 7 标签;同样剔除旁白入镜 |
| **S3 角色三视图** `s3_characters.py` | `script.characters` | 各角色 `feature_prompt`/`turnaround_image`/`locked` | LLM(浓缩特征)+ Image(生图) | 正/侧/背三视图纯白底;**`locked` 幂等**:已定稿且图存在则跳过;仅前 `MAX_TURNAROUND=4` 角色出三视图 |
| **S4 逐页生图** `s4_pages.py` | `storyboard`、角色三视图 | 各 cell `image`/`status` | Image(带参考图) | **参考图缩到 768px**(`_downscaled_ref`,含 mtime 校验+原子替换+线程唯一临时名)后传入 `generate(references=...)`;**并发 3**;每页独立 try + 重试 3 次,失败标 `failed`;无任一三视图时打印一致性告警 |
| **S5 配音配乐** `s5_audio.py` | `storyboard.caption` | 各 cell `audio`/`duration_ms`、`bgm` | TTS + ffmpeg | **按标点分句**逐句合成(避开小模型确定性截断)→**截断检测重合成取最长**(`TTS_TRIES=3`,低于 `字数×380ms` 判截断)→逐句 trim 静音 → concat;**三层兜底**:TTS 失败→按字数估时长生成静音轨,兜底再失败→留空由 S6 跳过;BGM 按主导情绪从 manifest 匹配 |
| **S6 合成** `s6_compose.py` | 各 cell 产物、`legend`、`bgm` | `output["mp4"]` | ffmpeg | 片头卡→逐页 confirmed cell→片尾卡;每页底图 Ken Burns + 静态 overlay 叠加;**产物缺失双重校验**(status + 文件存在);片尾按 `source_type` 标注来源(原创演绎显式标注)+“本片为 AI 生成内容”;xfade 溶解拼接 → loudnorm+BGM 混音 |

**依赖前提**:S1 需 `legend`,S2 需 `script`,S4 需 `script+storyboard`,顺序不可乱。每步写 `status["sN"]`,S4/S5 用 `done`/`partial` 表达部分成功。

---

## 4. 外部服务层与配置

### 4.1 provider 签名与端点

| Provider | 初始化 | 公开方法 | OpenAI 端点 | 重试 |
|---|---|---|---|---|
| `LLMClient` | `(base_url, api_key, model)` | `chat(system,user,temperature=0.7,retries=2)→str`;`structured[T](system,user,schema,retries=2)→T` | `POST /chat/completions` | 瞬时 `{429,500,502,503,504}` 指数退避 `2*(attempt+1)`;`structured` 额外把校验错误反馈给模型重试 |
| `ImageClient` | `(base_url, api_key, model, mode="images_api")` | `generate(prompt,size="1536x1024",references=None,retries=2)→bytes` | `chat_api`→`/chat/completions`;有参考图→`/images/edits`(multipart);否则→`/images/generations` | 同 LLM,额外捕 `TimeoutException`/`ConnectError`(S3 无外层重试) |
| `TTSClient` | `(base_url, api_key, model)` | `synthesize(text,voice,out)→None` | `POST /audio/speech`(`response_format:mp3`) | **provider 层无重试**;检查 content-type/首字节拒绝 JSON/文本/空响应 |

响应容错要点:LLM 结构化输出优先提 ```json``` 代码块、回退扫首个 `{`…`}`;图像 `chat_api` 逐级回退(`images[]` → content 内 base64 → http url);`_first`/`_decode` 对空 data / 未知格式抛 `ImageGenError`(非 IndexError)。

### 4.2 配置(`SHANHAI_` 环境变量,pydantic-settings,读 `.env`)

| 字段 | 环境变量 | 默认 |
|---|---|---|
| `base_url` / `api_key` | `SHANHAI_BASE_URL` / `_API_KEY` | **必需** |
| `llm_model` | `SHANHAI_LLM_MODEL` | `claude-sonnet-5` |
| `image_model` / `image_api_mode` / `image_size` | `SHANHAI_IMAGE_MODEL` / `_API_MODE` / `_SIZE` | `gemini-2.5-flash-image` / `chat_api` / `1536x1024` |
| `tts_model` / `tts_voice` | `SHANHAI_TTS_MODEL` / `_VOICE` | `gpt-4o-mini-tts` / `alloy` |
| `image_base_url`/`image_api_key`/`tts_base_url`/`tts_api_key` | 对应 `SHANHAI_*` | `None`(per-modality 覆盖) |

**端点回退**:`image_endpoint`/`tts_endpoint` 属性——per-modality URL/key 存在则优先,否则回退默认 `base_url`/`api_key`。这一机制正是本地 TTS(0005:Qwen3-TTS 挂 `localhost:8000`)不改业务代码即可接入的原因。

---

## 5. 渲染合成层

### 5.1 字幕/水印静态叠加层(核心设计)

底图与字幕**分层**:`compose_page()` 只出 1920×1080 cover-crop 满幅底图(锚点 `0.4` 偏上保住人物头部,不含文字);`overlay_layer()` 单独生成同尺寸透明 PNG(底部 240px 渐变遮罩承载白色描边字幕 `_wrap` 取前 2 行 + 右上角“AI 生成”**描边**水印)。ffmpeg 侧 `[0:v]kenburns[bg];[bg][1:v]overlay=0:0[v]`——overlay 在 zoompan **之后**,字幕/水印完全不参与缩放位移,始终锐利、位置恒定。**若把字幕烘进底图再 zoompan,文字会随镜头发虚抖动、水印可能被裁出画面,违反“AI 标识不可失效”的合规要求**。片头/片尾纯文字卡直接烘焙(无 zoompan/overlay)。

### 5.2 ffmpeg.py 函数表

| 函数 | 用途 | 关键点 |
|---|---|---|
| `sh` / `probe_duration_ms` | 唯一执行点 | `subprocess.run(check=True,capture_output)`;ffprobe 取时长 ms |
| `clip_duration_s` | 单 clip 目标时长 | 有解说 `+BUFFER_MS(0.5s)` 尾缓冲,静帧卡不加 |
| `_kenburns_vf` | Ken Burns 推拉 | 先 `scale=3840:2160` 放大 2× 防发虚,`zoompan` 线性驱动,`ZOOM_MAX=1.08`,奇偶页交替推近/拉远 |
| `page_clip_cmd` | 单页正文合成 | 底图+字幕层+解说音;无音频 `anullsrc` 占位,有音频 `-af apad` 补齐;统一 `-r 25 -c:a aac -ar 44100 -ac 2` |
| `still_clip_cmd` | 片头/尾静帧卡 | 无 zoompan/overlay,输出参数与正文对齐(便于 xfade 时基一致) |
| `silent_audio_cmd` | 静音兜底轨 | `anullsrc=r=44100:cl=stereo` |
| `concat_audio_cmd` | 分句 TTS 拼整页 | `-f concat -safe 0`,**重编码**避免各句参数不一致 |
| `trim_silence_cmd` | 修剪首尾静音+补停顿 | `silenceremove` 正放+`areverse` 反转两端裁,`detection=peak` 防吃字,`apad` 补 0.18s |
| `xfade_offsets`/`xfade_concat_cmd` | 全片溶解拼接 | 累积 offset `Σd_i-(k+1)T`;每路 `settb=AVTB,fps=25` 规整时基;全片首尾 `fade` 淡入淡出;音频 `acrossfade`;单 clip 退化直接转码 |
| `finalize_cmd` | 响度归一+BGM | `loudnorm=I=-16:TP=-1.5:LRA=11`;BGM `volume=0.18` + `amix` + `-stream_loop -1` + `-shortest`;`-c:v copy` |

常量:`FPS=25`、`BUFFER_MS=500`、`XFADE_S=0.5`、`FADE_S=0.5`、`ZOOM_MAX=1.08`、`SILENCE_THRESH=-45dB`。

### 5.3 AI 合规三处

1. 每页 overlay 右上角恒定“AI 生成”描边水印;
2. 片尾 credits 追加“本片为 AI 生成内容”整体声明;
3. 片尾按 `source_type` 标注来源——原创演绎显式“本故事为原创演绎”,不冠“传说来源”;`sources` 空时至少一行“来源:未标注”(`_credits_lines` 兜底,对齐 PRD F0②/§9.4)。

---

## 6. 数据模型、编排差异与前后端契约

### 6.1 Project 关系树

```
Project(project_id, scenic_spot, params, status{}, output{})
├── params: GenerationParams(duration_min∈{1,3,5}, audience∈{儿童,大众}, tone∈{温情,奇幻,悬疑})
├── legend_candidates: [Legend]         # S0 产
├── legend: Legend(title,summary,source_type∈SourceType,sources[])  # pick 选定
├── style_preset: str (默认 guofeng_ink)
├── script: Script(title,theme,acts[Act[Scene[Dialogue]]], characters[CharacterCard])
│         └── CharacterCard(name,role,personality,appearance, feature_prompt,turnaround_image,locked)
└── storyboard: [StoryboardCell(index,visual_desc,characters[],caption≤80,emotion,
                                image,audio,duration_ms,status∈{draft,confirmed,failed})]
```

### 6.2 CLI vs API 编排差异

| 维度 | CLI (`cli.py`) | API (`api.py`) |
|---|---|---|
| 传说选定 | `new`+`pick <序号>` 人工选,或 `run` 自动选第一个 | 永远自动选第一个候选(无交互选择接口) |
| 执行方式 | `run` 前台顺序跑并逐步 echo 耗时;`step` 单步跑 | `POST` 后立即返回 `project_id`,管线在 **daemon 后台线程** 跑 |
| 进度反馈 | 终端输出 | 写 `status["pipeline"]`(queued/running/done/error:…),前端轮询 `GET` |
| 失败判定 | `run` 末尾检查“无任何完整正文页”则 `Exit(1)` | 后台线程 `try/except` 兜住异常写入 `pipeline` 状态 |
| 参数校验 | `_validate_params` 抛 `typer.BadParameter` | `_validate` 抛 `HTTPException(400)` |
| 共享 | 两者复用 `_clients`、`steps/*`、`_MINUTES/_AUDIENCES/_TONES` 常量;api.py 直接 import cli 的常量 | 同左 |

### 6.3 前后端契约

- 前端 `web/src/types.ts` 显式对应后端 `api.py::_serialize`;`api.ts` 用相对 `/api` 路径(同源部署由后端 StaticFiles 托管 `web/dist`,dev 期 Vite 代理)。
- 契约面:`GET /api/meta`(枚举选项填表单)、`POST /api/projects`(建项目+启后台管线)、`GET /api/projects`(列表)、`GET /api/projects/{id}`(详情,`App.tsx` 每 2s 轮询直到 pipeline 离开 queued/running)。
- 产物 URL:`_file_url`/`_mp4_url` 把落盘相对路径转 `/files/<id>/...`(StaticFiles 挂 `projects/`)。前端拿到的是可直接 `<img>/<audio>/<video>` 的 URL,不暴露文件系统路径。

---

## 7. 测试与工程

- **测试规模**:17 个测试文件,**104 个用例**,~1266 行(源码 1386 行:core 775 + steps 421 + providers 190)。
- **Mock 风格**:`respx`(HTTP 虚拟化 LLM/Image/TTS)+ `@patch`(`ffmpeg.sh`/`probe_duration_ms`/`time.sleep`/`Settings`/`store`)+ `MagicMock`(provider 对象与调用计数断言)。

**覆盖矩阵(按用例数)**:

| 强(≥6) | test_ffmpeg(16)、test_cli(14)、test_s5(12)、test_image_provider(9)、test_s4(8)、test_llm_provider(8)、test_typeset(7)、test_api(6)、test_s6(6) |
|---|---|
| **弱(≤2)** | test_config(2)、test_s3(2)、test_s0(1)、test_store(1)、test_styles(1) |

- **Spike 门禁(decision 0001)**:`spike/consistency_test.py` 验证 M0 角色跨页一致性,2 角色×3 画风×8 图=24 张,门槛 75%,**三画风全 100% 通过**(零身份漂移,标志道具全程保留)。关键结论:参考图必须缩 768px 后上传否则 WriteTimeout;默认 `guofeng_ink`。
- **决策文档(5 份,0001–0005)**:
  - **0001** M0 角色一致性门禁(通过);
  - **0002** 整分支评审(21 条确认,分 A 现修 10 / B 延后 5 / C 记录 9);
  - **0003** 最终复审(22 条,9 条影响 T18 端到端);
  - **0004** T18 端到端验收(通过:`run 雷峰塔 --minutes 1` → final.mp4 3.7MB 71.7s,含 M2 优化:竖图模糊填充、S4 并发化 1429→1106s、外部调用重试补全);
  - **0005** 本地 TTS 接入(Qwen3-TTS/mlx-audio 挂 localhost:8000,仅改 `.env`)。
  > 注:任务描述提及“0006”不存在;实际决策文档止于 0005。
- **工程**:`uv` + `pyproject`,主依赖 httpx/pydantic/typer/pillow/fastapi/uvicorn/pydantic-settings;dev 依赖 pytest/ruff/respx;入口 `shanhai`(CLI)/`shanhai-web`(API);ruff per-file-ignores 放宽 tests/spike 风格。

---

## 8. 风险与技术债(去重、按当前代码真实状态定级)

> **交叉校验说明**:分区子代理把 0002/0003 决策文档的 FIX-BATCH 待办当作“当前高危未修”上报,但对照代码,**A1 参数校验、A2 run 中间态检查、A6 片尾来源标注、A7 S5 单页隔离、0002#5 locked 幂等、0002#6 参考图 mtime、image 空 data→ImageGenError(非 IndexError)、typeset 水印描边、chat() 重试** 均已落地。下表已剔除这些“已修”项,只列真实残留。

### 高

| 项 | 影响 | 建议 |
|---|---|---|
| **BGM 库为空** `assets/bgm/manifest.json = {"tracks":[]}` | S5 匹配逻辑存在但**真实成片永远无配乐**;finalize 的 BGM 混音分支从不触发 | 补授权曲目进 manifest(标注 emotions 标签);或明确降级为“暂无配乐”产品决策 |
| **API 无并发/资源治理** `api.py` | `_JOBS` 无上限、后台 daemon 线程无队列/限流;多用户并发建项目会同时打满上游(已观测 503),且 S4 内部再 ×3 并发,叠加放大过载 | 加全局作业并发上限/队列;或串行化管线线程 |

### 中

| 项 | 影响 | 建议 |
|---|---|---|
| **S3 参考图按 index<4 选主角** `s3/s4` | 主角若排在第 5+ 位则无三视图参考,一致性机制对其失效(0002/0003 B 档) | S1 保证 characters 按重要度排序,或加 `importance` 字段(T2) |
| **M0 门禁可绕过** `s4_pages.run` | 无任一三视图时仅 `print` 告警、不中断,`step s4` 单跑可跳过 S3 直接生图 | 视合规强度决定是否改硬失败 |
| **ffmpeg 音频参数手工重复** `ffmpeg.py` | `44100/stereo`、`anullsrc` 在多个函数各自硬编码,新增音频分支(如音效层)漏加会在 `acrossfade`/`amix` 处静默出时长/参数错乱 | 提取公共音频参数常量/封装函数 |
| **字体路径相对 cwd** `typeset.py FONT_PATH="assets/fonts/..."` | 依赖运行时 cwd 为项目根;跨目录调用找不到字体直接崩 | 用 `Path(__file__)` 解析为绝对路径 |
| **性能超目标** S4 逐页 | 端到端 ~30 分钟(0004),并发下仍受上游限流,>15 分钟目标 | 提高并发/换更快上游/本地 ComfyUI |

### 低

| 项 | 影响 | 建议 |
|---|---|---|
| **TTSClient provider 层无重试** `tts.py` | 与 LLM/Image 不一致;但 S5 有 `TTS_TRIES=3` 重合成 + 静音兜底两层缓解,实际影响已降低 | 可选:provider 层对齐瞬时重试 |
| **测试薄弱模块** store/config/s0/s3/styles ≤2 用例 | 缺边界/异常路径(损坏文件恢复、无效 URL、多候选排序、locked 重跑等);run 全流程 / S5·S6 续跑跳过分支无用例(0003#9/11/20) | 补关键路径与续跑分支用例 |
| **API CORS 全开** `allow_origins=["*"]` | 骨架期便利,生产需收敛 | 部署前限制来源 |
| **per-modality token 驻留内存** `Settings` | 若日志/异常序列化 Settings 可能泄露(当前无显式序列化) | 审查管线日志 |
| **`_wrap` 单字符换行** `typeset.py` | 中英混排长英文单词/数字可能中间截断,视觉不优雅(不影响功能) | 加连字符/标点断行规则 |
| **`xfade_concat_cmd` 未处理空 clips** | 当前强制片头+片尾至少 2 段不触发;若未来去掉强制卡则需补校验 | 加空校验 |

---

## 9. 亮点(同类项目常见难题,此处已解决)

1. **三视图角色一致性,无需 LoRA/微调**:S3 生成正/侧/背三视图作为参考图,S4 逐页以参考图约束身份;spike 实测三画风 100% 通过、零身份漂移、标志道具全程保留。纯 prompt+参考图工程,零训练成本。
2. **按语音时长排页,音画同步**:S5 用 ffprobe 取每页真实解说时长写回 `duration_ms`,S6 据此定每页时长并计算 xfade 累积 offset,画面时长严格贴合配音,不靠固定时长硬切。
3. **TTS 分句防截断 + 静音兜底**:按标点分句逐句合成规避小模型确定性提前停止,截断检测(低于 `字数×380ms`)重合成取最长,逐句 trim 静音再拼接;TTS 全不可用时按字数估时长生成静音轨——0004 实测 S5 十页全 503 仍产出完整音轨、成片不残缺。
4. **AI 水印 / 来源合规不可失效**:水印/字幕剥离为独立静态叠加层,不随 Ken Burns 缩放抖动或被裁出画面;片尾按 `source_type` 精确标注(原创演绎不冒充传说来源)+ 全局“AI 生成”声明,对齐 PRD 铁律。
5. **原子写 + 可重入续跑**:`store.save` 写 `.tmp` 后 `os.replace` 保证 project.json 永不半写;每步落盘 + S3 `locked` 幂等 + S4/S5 产物存在性校验,让任意步骤崩溃后重跑只补缺失部分,不重做已完成工作。
