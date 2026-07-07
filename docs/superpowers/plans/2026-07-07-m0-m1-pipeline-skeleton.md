# 山海 M0+M1 实施计划:角色一致性验证 + CLI 流水线骨架

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 验证"角色跨页一致性"这一最大技术风险(PRD §8 第 1 条),并搭建从景区名到 MP4 的 CLI 端到端流水线骨架(S0~S6,无 Web UI)。

**Architecture:** Python 单体包 `shanhai`,所有外部 AI 能力(LLM/图像/TTS)通过 OpenAI 兼容的 `base_url + api_key` Provider 层调用,接口留给未来本地 ComfyUI 替换。每步产物落盘到 `projects/<id>/`,项目状态存单个 JSON,支持断点续跑。合成用系统 FFmpeg。

**Tech Stack:** Python 3.12 + uv / httpx / pydantic v2 / typer / Pillow / FFmpeg(系统安装)/ pytest + respx。

## Global Constraints(来自 PRD,每个任务隐含遵守)

- **页面文字一律程序排版叠加,不让模型画字**(PRD §5 铁律)。
- **AI 生成内容标识为合规硬性要求,不可关闭**(PRD F6)。
- 传说必须标注来源类型(正史/地方志/民间传说/文学作品),不得将传说包装为史实(PRD F0)。
- 主要角色 ≤4 个(PRD F3)。
- 单页生成失败自动重试 ≤2 次(PRD F4)。
- 每页时长 = 该页解说音频时长 + 0.5s 缓冲(PRD F6)。
- 响度对齐 -16 LUFS(PRD F5)。
- 可重入:任何一步失败,项目状态可恢复(PRD §6)。
- 分镜文案 ≤80 字,连起来"只听不看能懂"(PRD F2)。
- 所有外部模型调用必须走 Provider 抽象(将来换成本地 DGX Spark / ComfyUI 不改业务代码)。

## 多 Agent 执行策略

按 superpowers:subagent-driven-development 执行,每个任务派一个全新子代理,用 Agent 工具的 `model` 参数指定模型:

| 模型 | 承担任务 | 理由 |
|---|---|---|
| **Haiku** | Task 1, 2(脚手架、配置、资产下载) | 机械性工作,便宜快 |
| **Sonnet** | Task 4, 6~13, 15, 17(标准实现 + 测试) | 主力实现层 |
| **Opus** | Task 3, 5, 14, 16, 18(图像 Provider、一致性验证判读、页面生成、FFmpeg 合成、端到端验收) | 全项目最难/最贵返工的部分 |

代码评审:Task 3/14/16/18 用 Opus 评审,其余用 Sonnet 评审。

**依赖图(可并行的组):**

```
M0: T1 → T2 → T3 → T4 → T5(门禁,人工评分)
M1: T6 → T7 ─┐
    T8(依赖 T2)├→ T9 → T10 → T11(串行,共享 prompt 链约定)
              └→ T12(依赖 T3+T8+T11) → T14(依赖 T13)
    T13(依赖 T1,可与 T9~T12 并行)
    T15(依赖 T2+T11,可与 T12~T14 并行)
    T16(依赖 T13+T15)
    T17(依赖 T9~T16) → T18(人工验收)
```

**M0 是门禁:** T5 评分 ≥75% → 按计划继续;60%~75% → 调整画风预设(偏符号化/绘本风)后重测;<60% → 按 PRD §8 回到产品层讨论"插画+有声书"降级形态,M1 的 T12/T14 需重新设计。

## 文件结构

```
shanhai/
├── pyproject.toml
├── .env.example
├── src/shanhai/
│   ├── __init__.py
│   ├── config.py            # Settings:base_url/api_key/模型名
│   ├── schema.py            # Project/Legend/Script/StoryboardCell 等
│   ├── store.py             # 项目状态持久化(原子写 JSON)
│   ├── styles.py            # 3 种画风预设 prompt 前缀
│   ├── typeset.py           # Pillow 排版:页面文案带、片头片尾卡、AI 水印
│   ├── ffmpeg.py            # FFmpeg 命令构建器 + 执行
│   ├── cli.py               # typer CLI
│   ├── providers/
│   │   ├── __init__.py
│   │   ├── llm.py           # LLMClient(chat + structured)
│   │   ├── image.py         # ImageClient(images_api / chat_api 双模式)
│   │   └── tts.py           # TTSClient
│   └── steps/
│       ├── __init__.py
│       ├── s0_legend.py … s6_compose.py
├── spike/
│   ├── probe_models.py      # 探测代理支持哪种图像端点
│   ├── consistency_test.py  # M0 主脚本
│   └── report.py            # HTML 对比图 + 评分表
├── assets/
│   ├── fonts/               # NotoSansCJKsc-Regular.otf
│   └── bgm/manifest.json
├── docs/decisions/          # 0001-character-consistency.md(T5 产出)
├── projects/                # 运行时产物(gitignore)
└── tests/
```

---

# Milestone 0:角色一致性技术验证

## Task 1: 项目脚手架

**执行代理: Haiku** | 评审: Sonnet

**Files:**
- Create: `pyproject.toml`, `src/shanhai/__init__.py`, `tests/__init__.py`, `.env.example`
- Modify: `.gitignore`
- Create: `assets/fonts/`(下载字体)、`assets/bgm/manifest.json`

**Interfaces:**
- Produces: 可 `uv run pytest` 的包骨架;后续所有任务在此之上工作。

- [ ] **Step 1: 写 pyproject.toml**

```toml
[project]
name = "shanhai"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "httpx>=0.27",
  "pydantic>=2.7",
  "pydantic-settings>=2.3",
  "typer>=0.12",
  "pillow>=10.3",
]

[project.scripts]
shanhai = "shanhai.cli:app"

[dependency-groups]
dev = ["pytest>=8", "ruff>=0.5", "respx>=0.21"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/shanhai"]

[tool.ruff]
line-length = 100
```

- [ ] **Step 2: 建包与空测试**

创建 `src/shanhai/__init__.py`(内容 `__version__ = "0.1.0"`)、`tests/__init__.py`(空)。

- [ ] **Step 3: .env.example**

```bash
SHANHAI_BASE_URL=https://your-proxy.example.com/v1
SHANHAI_API_KEY=sk-xxx
SHANHAI_LLM_MODEL=claude-sonnet-5
SHANHAI_IMAGE_MODEL=gemini-2.5-flash-image
# images_api = /images/generations|edits 形态;chat_api = 多模态 chat 返图形态(先跑 spike/probe_models.py 确定)
SHANHAI_IMAGE_API_MODE=chat_api
SHANHAI_TTS_MODEL=gpt-4o-mini-tts
SHANHAI_TTS_VOICE=alloy
```

- [ ] **Step 4: .gitignore 追加**

追加行:`.env`、`projects/`、`spike/out/`、`__pycache__/`、`.venv/`。

- [ ] **Step 5: 下载 CJK 字体、建 bgm 清单**

```bash
mkdir -p assets/fonts assets/bgm
curl -L -o assets/fonts/NotoSansCJKsc-Regular.otf \
  "https://github.com/notofonts/noto-cjk/raw/main/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf"
echo '{"tracks": []}' > assets/bgm/manifest.json
```

预期:字体文件 >10MB;若下载失败,改用系统字体 `/System/Library/Fonts/STHeiti Light.ttc` 并在 README 注明。

- [ ] **Step 6: 验证 + 提交**

```bash
uv sync && uv run pytest --collect-only -q && uv run python -c "import shanhai; print(shanhai.__version__)"
```

预期:输出 `0.1.0`,pytest 收集 0 个测试不报错。

```bash
git add -A && git commit -m "chore: project scaffold (uv + pyproject + assets)"
```

## Task 2: 配置层 config.py

**执行代理: Haiku** | 评审: Sonnet

**Files:**
- Create: `src/shanhai/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Settings`(pydantic-settings),字段:`base_url: str`、`api_key: str`、`llm_model: str`、`image_model: str`、`image_api_mode: str`("images_api"|"chat_api")、`image_size: str`(默认 "1536x1024")、`tts_model: str`、`tts_voice: str`;可选覆盖 `image_base_url/image_api_key/tts_base_url/tts_api_key`;属性 `image_endpoint`、`tts_endpoint` 返回 `(base_url, api_key)` 元组。环境变量前缀 `SHANHAI_`,读 `.env`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_config.py
from shanhai.config import Settings

def test_defaults_and_fallback(monkeypatch):
    monkeypatch.setenv("SHANHAI_BASE_URL", "https://p.example.com/v1")
    monkeypatch.setenv("SHANHAI_API_KEY", "sk-1")
    s = Settings(_env_file=None)
    assert s.image_api_mode == "chat_api"
    assert s.image_endpoint == ("https://p.example.com/v1", "sk-1")

def test_modality_override(monkeypatch):
    monkeypatch.setenv("SHANHAI_BASE_URL", "https://p.example.com/v1")
    monkeypatch.setenv("SHANHAI_API_KEY", "sk-1")
    monkeypatch.setenv("SHANHAI_IMAGE_BASE_URL", "https://img.example.com/v1")
    s = Settings(_env_file=None)
    assert s.image_endpoint == ("https://img.example.com/v1", "sk-1")
    assert s.tts_endpoint == ("https://p.example.com/v1", "sk-1")
```

- [ ] **Step 2: 运行确认失败**

`uv run pytest tests/test_config.py -v` → 预期 `ModuleNotFoundError: shanhai.config`。

- [ ] **Step 3: 实现**

```python
# src/shanhai/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="SHANHAI_", extra="ignore")

    base_url: str
    api_key: str
    llm_model: str = "claude-sonnet-5"
    image_model: str = "gemini-2.5-flash-image"
    image_api_mode: str = "chat_api"
    image_size: str = "1536x1024"
    tts_model: str = "gpt-4o-mini-tts"
    tts_voice: str = "alloy"
    image_base_url: str | None = None
    image_api_key: str | None = None
    tts_base_url: str | None = None
    tts_api_key: str | None = None

    @property
    def image_endpoint(self) -> tuple[str, str]:
        return (self.image_base_url or self.base_url, self.image_api_key or self.api_key)

    @property
    def tts_endpoint(self) -> tuple[str, str]:
        return (self.tts_base_url or self.base_url, self.tts_api_key or self.api_key)
```

- [ ] **Step 4: 测试通过后提交**

`uv run pytest tests/test_config.py -v` → PASS ×2。

```bash
git add src/shanhai/config.py tests/test_config.py && git commit -m "feat: settings with per-modality endpoint override"
```

## Task 3: 图像 Provider(双模式)+ 端点探测脚本

**执行代理: Opus** | 评审: Opus

这是抽象层最关键的一块:今天接第三方代理,明天换本地 ComfyUI,业务代码只认 `ImageClient.generate()`。

**Files:**
- Create: `src/shanhai/providers/__init__.py`(空)、`src/shanhai/providers/image.py`、`spike/probe_models.py`
- Test: `tests/test_image_provider.py`

**Interfaces:**
- Consumes: `Settings`(Task 2)。
- Produces: `ImageClient(base_url: str, api_key: str, model: str, mode: str = "images_api")`,方法 `generate(prompt: str, size: str = "1536x1024", references: list[Path] | None = None) -> bytes`(返回 PNG 字节);异常 `ImageGenError`。

- [ ] **Step 1: 写失败测试(respx mock 三条路径)**

```python
# tests/test_image_provider.py
import base64
from pathlib import Path
import respx, httpx, pytest
from shanhai.providers.image import ImageClient, ImageGenError

PNG = base64.b64encode(b"fakepng").decode()
BASE = "https://p.example.com/v1"


@respx.mock
def test_generations_b64():
    respx.post(f"{BASE}/images/generations").mock(
        return_value=httpx.Response(200, json={"data": [{"b64_json": PNG}]}))
    c = ImageClient(BASE, "sk", "gpt-image-1", mode="images_api")
    assert c.generate("a cat") == b"fakepng"


@respx.mock
def test_edits_with_reference(tmp_path: Path):
    ref = tmp_path / "ref.png"; ref.write_bytes(b"refpng")
    route = respx.post(f"{BASE}/images/edits").mock(
        return_value=httpx.Response(200, json={"data": [{"b64_json": PNG}]}))
    c = ImageClient(BASE, "sk", "gpt-image-1", mode="images_api")
    assert c.generate("a cat", references=[ref]) == b"fakepng"
    assert b"refpng" in route.calls[0].request.content


@respx.mock
def test_chat_mode_images_field():
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, json={
        "choices": [{"message": {"content": "",
            "images": [{"image_url": {"url": f"data:image/png;base64,{PNG}"}}]}}]}))
    c = ImageClient(BASE, "sk", "nano-banana", mode="chat_api")
    assert c.generate("a cat") == b"fakepng"


@respx.mock
def test_chat_mode_no_image_raises():
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, json={
        "choices": [{"message": {"content": "我无法生成图片"}}]}))
    c = ImageClient(BASE, "sk", "nano-banana", mode="chat_api")
    with pytest.raises(ImageGenError):
        c.generate("a cat")
```

- [ ] **Step 2: 运行确认失败**

`uv run pytest tests/test_image_provider.py -v` → 预期 import 错误。

- [ ] **Step 3: 实现 image.py**

```python
# src/shanhai/providers/image.py
import base64
import re
from pathlib import Path

import httpx


class ImageGenError(Exception):
    pass


class ImageClient:
    """OpenAI 兼容图像客户端,双上游形态。未来本地 ComfyUI 实现同签名 generate() 即可整体替换。"""

    def __init__(self, base_url: str, api_key: str, model: str, mode: str = "images_api"):
        self.model = model
        self.mode = mode
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=300,
        )

    def generate(self, prompt: str, size: str = "1536x1024",
                 references: list[Path] | None = None) -> bytes:
        if self.mode == "chat_api":
            return self._via_chat(prompt, references or [])
        if references:
            return self._via_edits(prompt, references, size)
        return self._via_generations(prompt, size)

    def _via_generations(self, prompt: str, size: str) -> bytes:
        r = self._client.post("/images/generations",
                              json={"model": self.model, "prompt": prompt, "size": size, "n": 1})
        r.raise_for_status()
        return _decode(r.json()["data"][0])

    def _via_edits(self, prompt: str, references: list[Path], size: str) -> bytes:
        files = [("image[]", (p.name, p.read_bytes(), "image/png")) for p in references]
        r = self._client.post("/images/edits",
                              data={"model": self.model, "prompt": prompt, "size": size},
                              files=files)
        r.raise_for_status()
        return _decode(r.json()["data"][0])

    def _via_chat(self, prompt: str, references: list[Path]) -> bytes:
        content: list[dict] = [{"type": "text", "text": prompt}]
        for p in references:
            b64 = base64.b64encode(p.read_bytes()).decode()
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"}})
        r = self._client.post("/chat/completions", json={
            "model": self.model,
            "messages": [{"role": "user", "content": content}],
        })
        r.raise_for_status()
        msg = r.json()["choices"][0]["message"]
        for img in msg.get("images") or []:
            url = img.get("image_url", {}).get("url", "")
            if url.startswith("data:image"):
                return base64.b64decode(url.split(",", 1)[1])
        m = re.search(r"data:image/\w+;base64,([A-Za-z0-9+/=]+)", msg.get("content") or "")
        if m:
            return base64.b64decode(m.group(1))
        raise ImageGenError(f"响应中未找到图像: {str(msg)[:200]}")


def _decode(item: dict) -> bytes:
    if "b64_json" in item:
        return base64.b64decode(item["b64_json"])
    if "url" in item:
        r = httpx.get(item["url"], timeout=120)
        r.raise_for_status()
        return r.content
    raise ImageGenError(f"未知的响应格式: {list(item)}")
```

- [ ] **Step 4: 测试通过**

`uv run pytest tests/test_image_provider.py -v` → PASS ×4。

- [ ] **Step 5: 写探测脚本(真实调用,确定代理支持的形态)**

```python
# spike/probe_models.py
"""探测代理对候选图像模型支持哪种端点。用法:
uv run python spike/probe_models.py gpt-image-1 gemini-2.5-flash-image seedream-4-0
"""
import sys
from pathlib import Path

from shanhai.config import Settings
from shanhai.providers.image import ImageClient, ImageGenError

OUT = Path("spike/out/probe"); OUT.mkdir(parents=True, exist_ok=True)

def main() -> None:
    s = Settings()
    base, key = s.image_endpoint
    for model in sys.argv[1:] or [s.image_model]:
        for mode in ("images_api", "chat_api"):
            try:
                png = ImageClient(base, key, model, mode).generate("一只红色的猫,简笔画")
                (OUT / f"{model}--{mode}.png").write_bytes(png)
                print(f"OK   {model} [{mode}] -> {len(png)} bytes")
            except Exception as e:  # noqa: BLE001 探测脚本要吞掉一切错误继续
                print(f"FAIL {model} [{mode}]: {type(e).__name__}: {str(e)[:120]}")

if __name__ == "__main__":
    main()
```

- [ ] **Step 6: 提交**

```bash
git add src/shanhai/providers tests/test_image_provider.py spike/probe_models.py
git commit -m "feat: image provider (images_api/chat_api) + endpoint probe"
```

## Task 4: 画风预设 + 一致性验证脚本 + HTML 评分报告

**执行代理: Sonnet** | 评审: Opus

**Files:**
- Create: `src/shanhai/styles.py`、`spike/consistency_test.py`、`spike/report.py`
- Test: `tests/test_styles.py`

**Interfaces:**
- Consumes: `ImageClient`(Task 3)、`Settings`(Task 2)。
- Produces: `STYLE_PRESETS: dict[str, str]`(键 `guofeng_ink` / `kids_picture_book` / `modern_illust`);`spike/out/<style>/turnaround_<角色>.png` + `page_XX.png` + `report.html`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_styles.py
from shanhai.styles import STYLE_PRESETS

def test_three_presets():
    assert set(STYLE_PRESETS) == {"guofeng_ink", "kids_picture_book", "modern_illust"}
    assert all(len(v) > 10 for v in STYLE_PRESETS.values())
```

- [ ] **Step 2: 确认失败后实现 styles.py**

```python
# src/shanhai/styles.py
STYLE_PRESETS = {
    "guofeng_ink": "中国水墨画风格,写意笔触,留白构图,淡彩渲染,古典氛围",
    "kids_picture_book": "儿童绘本插画风格,扁平色块,圆润造型,明亮温暖的配色,亲切可爱",
    "modern_illust": "现代扁平插画风格,简洁几何造型,清爽配色,轻微颗粒质感",
}
```

`uv run pytest tests/test_styles.py -v` → PASS。

- [ ] **Step 3: 写一致性验证脚本**

固定用白蛇传的两个角色 × 3 画风 × 每风格 6 页典型场景。三视图 prompt 与页面 prompt 的拼装方式就是将来 S3/S4 的正式方式,spike 验证的就是这条路。

```python
# spike/consistency_test.py
"""M0 角色一致性验证:三视图 -> 以三视图为参考逐页生成,人工评分。
用法: uv run python spike/consistency_test.py [style ...](默认跑全部 3 种画风)
"""
import sys
from pathlib import Path

from shanhai.config import Settings
from shanhai.providers.image import ImageClient
from shanhai.styles import STYLE_PRESETS

CHARACTERS = {
    "白素贞": "年轻女性,白色古装长裙,黑色长发挽髻插一支银簪,眉目温婉,腰间系淡青色丝带",
    "许仙": "年轻男性,青色书生长衫,黑发束冠,面容清秀,手持一把折叠纸伞",
}

SCENES = [
    "西湖断桥上,两人初遇,烟雨朦胧,远景",
    "药铺内,许仙在柜台后抓药,白素贞立于门口,中景",
    "端午节庭院,白素贞面色苍白倚在桌边,近景",
    "金山寺前,白素贞立于波涛之上,神情坚定,全景",
    "雷峰塔下,许仙仰望高塔,黄昏逆光,中景",
    "多年后塔前重逢,两人对望,晨光温暖,中景",
]

TURNAROUND_TMPL = (
    "{style}。角色三视图设定图:同一角色的正面、侧面、背面全身像并排排列,"
    "纯白背景,画面中不要出现任何文字。角色:{feature}"
)
PAGE_TMPL = (
    "{style}。连环画单页画面:{scene}。出场角色:{features}。"
    "严格保持角色与参考图中的形象一致(发型、服饰、面部特征)。画面中不要出现任何文字。"
)


def main() -> None:
    s = Settings()
    base, key = s.image_endpoint
    client = ImageClient(base, key, s.image_model, s.image_api_mode)
    for style_key in sys.argv[1:] or list(STYLE_PRESETS):
        style = STYLE_PRESETS[style_key]
        out = Path("spike/out") / style_key
        out.mkdir(parents=True, exist_ok=True)
        refs: list[Path] = []
        for name, feature in CHARACTERS.items():
            p = out / f"turnaround_{name}.png"
            if not p.exists():
                p.write_bytes(client.generate(
                    TURNAROUND_TMPL.format(style=style, feature=feature), size=s.image_size))
            refs.append(p)
            print(f"[{style_key}] 三视图 {name} 完成")
        features = ";".join(f"{n}({f})" for n, f in CHARACTERS.items())
        for i, scene in enumerate(SCENES, 1):
            p = out / f"page_{i:02d}.png"
            if not p.exists():
                p.write_bytes(client.generate(
                    PAGE_TMPL.format(style=style, scene=scene, features=features),
                    size=s.image_size, references=refs))
            print(f"[{style_key}] 页面 {i}/{len(SCENES)} 完成")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 写 HTML 评分报告生成器**

```python
# spike/report.py
"""汇总 spike/out 下所有图为对比页。评分标准(每页):
同一角色的发型/服饰/面部与三视图一致=1 分,明显漂移=0 分。
用法: uv run python spike/report.py && open spike/out/report.html
"""
import base64
from pathlib import Path

OUT = Path("spike/out")


def img_tag(p: Path) -> str:
    b64 = base64.b64encode(p.read_bytes()).decode()
    return f'<img src="data:image/png;base64,{b64}" style="width:280px;margin:4px">'


def main() -> None:
    rows = []
    for style_dir in sorted(d for d in OUT.iterdir() if d.is_dir() and d.name != "probe"):
        turnarounds = sorted(style_dir.glob("turnaround_*.png"))
        pages = sorted(style_dir.glob("page_*.png"))
        rows.append(f"<h2>{style_dir.name}</h2><h3>三视图(参考)</h3>"
                    + "".join(img_tag(p) for p in turnarounds)
                    + "<h3>页面</h3>"
                    + "".join(f'<figure style="display:inline-block">{img_tag(p)}'
                              f"<figcaption>{p.stem} 一致性:__/2 角色</figcaption></figure>"
                              for p in pages))
    html = ("<meta charset='utf-8'><title>角色一致性评分</title>"
            "<p>每页每个出场角色打 1(一致)或 0(漂移),总分 = 得分/总角色次。</p>"
            + "".join(rows))
    (OUT / "report.html").write_text(html, encoding="utf-8")
    print(f"written: {OUT / 'report.html'}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: 冒烟验证(不发真实请求)**

```bash
uv run python -c "import spike.consistency_test, spike.report" 2>/dev/null \
  || uv run python -c "
import sys; sys.path.insert(0, 'spike')
import consistency_test, report; print('imports ok')"
```

预期:`imports ok`。

- [ ] **Step 6: 提交**

```bash
git add src/shanhai/styles.py tests/test_styles.py spike/
git commit -m "feat: style presets + M0 consistency spike scripts"
```

## Task 5: 跑验证 + 评分 + 门禁决策(人工参与)

**执行代理: Opus(准备材料与初评)+ 人工终评** | 此任务是 M0→M1 的门禁

**Files:**
- Create: `docs/decisions/0001-character-consistency.md`

**Interfaces:**
- Consumes: Task 3/4 的全部脚本;需要用户提供真实 `.env`。
- Produces: 决策记录:选定的图像模型、api mode、最优画风、评分数据、GO/调整/降级 结论。

- [ ] **Step 1: 确认 .env 就绪,跑端点探测**

```bash
uv run python spike/probe_models.py gpt-image-1 gemini-2.5-flash-image
```

预期:至少一个 (model, mode) 组合 OK;把胜出组合写进 `.env`。

- [ ] **Step 2: 跑一致性验证(3 画风 × 2 角色三视图 × 6 页 = 24 次生图)**

```bash
uv run python spike/consistency_test.py && uv run python spike/report.py && open spike/out/report.html
```

- [ ] **Step 3: Opus 初评 + 人工终评**

Opus 子代理读取所有生成图,按 report.py 中的标准逐页初评并给出分风格得分表;用户在 report.html 上人工复核。

- [ ] **Step 4: 写决策记录并提交**

`docs/decisions/0001-character-consistency.md` 必须包含:测试日期、模型与 mode、每画风得分(x/12)、结论(≥75% GO / 60~75% 调画风重测 / <60% 触发 PRD §8 产品降级讨论)、对 T12/T14 的具体指示(用哪个模型、哪个画风做默认)。

```bash
git add docs/decisions/ && git commit -m "docs: M0 character consistency gate decision"
```

---

# Milestone 1:CLI 端到端流水线骨架

## Task 6: 核心数据模型 schema.py

**执行代理: Sonnet** | 评审: Sonnet

**Files:**
- Create: `src/shanhai/schema.py`
- Test: `tests/test_schema.py`

**Interfaces:**
- Produces(后续所有任务依赖的精确类型):

```python
SourceType = Literal["正史", "地方志", "民间传说", "文学作品", "原创演绎"]
Legend(title: str, summary: str, source_type: SourceType, sources: list[str])
Dialogue(character: str, line: str)
Scene(description: str, characters: list[str], narration: str, dialogues: list[Dialogue] = [])
Act(scenes: list[Scene])
CharacterCard(name: str, role: str, personality: str, appearance: str,
              feature_prompt: str = "", turnaround_image: str = "", locked: bool = False)
Script(title: str, theme: str, acts: list[Act], characters: list[CharacterCard])
StoryboardCell(index: int, scene_ref: str, visual_desc: str, characters: list[str],
               caption: str(≤80 字), emotion: str, image: str = "", audio: str = "",
               duration_ms: int = 0, status: Literal["draft","confirmed","failed"] = "draft")
GenerationParams(duration_min: Literal[1,3,5] = 3, audience: Literal["儿童","大众"] = "大众",
                 tone: Literal["温情","奇幻","悬疑"] = "温情")
Project(project_id: str, scenic_spot: str, params: GenerationParams = GenerationParams(),
        status: dict[str, str] = {}, legend_candidates: list[Legend] = [],
        legend: Legend | None = None, script: Script | None = None,
        style_preset: str = "kids_picture_book", storyboard: list[StoryboardCell] = [],
        bgm: str = "", output: dict[str, str] = {})
```

- [ ] **Step 1: 写失败测试**

```python
# tests/test_schema.py
import pytest
from pydantic import ValidationError
from shanhai.schema import Legend, Project, StoryboardCell

def test_project_roundtrip():
    p = Project(project_id="ab12", scenic_spot="雷峰塔")
    p2 = Project.model_validate_json(p.model_dump_json())
    assert p2.scenic_spot == "雷峰塔" and p2.params.duration_min == 3

def test_caption_max_80():
    with pytest.raises(ValidationError):
        StoryboardCell(index=1, scene_ref="1-1", visual_desc="x",
                       characters=[], caption="字" * 81, emotion="宁静")

def test_source_type_enum():
    with pytest.raises(ValidationError):
        Legend(title="t", summary="s", source_type="小道消息", sources=[])
```

- [ ] **Step 2: 确认失败 → 实现**

按 Interfaces 中的精确定义写 `src/shanhai/schema.py`,全部继承 `pydantic.BaseModel`;`caption` 用 `Field(max_length=80)`;可变默认值用 `Field(default_factory=...)`。

- [ ] **Step 3: 测试通过 → 提交**

`uv run pytest tests/test_schema.py -v` → PASS ×3。

```bash
git add src/shanhai/schema.py tests/test_schema.py && git commit -m "feat: core project schema"
```

## Task 7: 状态持久化 store.py

**执行代理: Sonnet** | 评审: Sonnet

**Files:**
- Create: `src/shanhai/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `Project`(Task 6)。
- Produces: `create_project(scenic_spot: str, root: Path = Path("projects")) -> Project`(8 位 hex id);`save(p: Project, root=...) -> None`(原子写:临时文件 + `os.replace`);`load(project_id: str, root=...) -> Project`;`project_dir(project_id: str, root=...) -> Path`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_store.py
from shanhai import store
from shanhai.schema import Legend

def test_create_save_load(tmp_path):
    p = store.create_project("雷峰塔", root=tmp_path)
    p.legend = Legend(title="白蛇传", summary="s", source_type="民间传说", sources=["http://x"])
    store.save(p, root=tmp_path)
    p2 = store.load(p.project_id, root=tmp_path)
    assert p2.legend.title == "白蛇传"
    assert not (store.project_dir(p.project_id, root=tmp_path) / "project.json.tmp").exists()
```

- [ ] **Step 2: 确认失败 → 实现**

```python
# src/shanhai/store.py
import os
import uuid
from pathlib import Path

from shanhai.schema import Project

DEFAULT_ROOT = Path("projects")


def project_dir(project_id: str, root: Path = DEFAULT_ROOT) -> Path:
    return root / project_id


def create_project(scenic_spot: str, root: Path = DEFAULT_ROOT) -> Project:
    p = Project(project_id=uuid.uuid4().hex[:8], scenic_spot=scenic_spot)
    save(p, root=root)
    return p


def save(p: Project, root: Path = DEFAULT_ROOT) -> None:
    d = project_dir(p.project_id, root)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "project.json.tmp"
    tmp.write_text(p.model_dump_json(indent=2), encoding="utf-8")
    os.replace(tmp, d / "project.json")


def load(project_id: str, root: Path = DEFAULT_ROOT) -> Project:
    text = (project_dir(project_id, root) / "project.json").read_text(encoding="utf-8")
    return Project.model_validate_json(text)
```

- [ ] **Step 3: 测试通过 → 提交**

```bash
git add src/shanhai/store.py tests/test_store.py && git commit -m "feat: atomic project state persistence"
```

## Task 8: LLM Provider(chat + 结构化输出)

**执行代理: Sonnet** | 评审: Sonnet

**Files:**
- Create: `src/shanhai/providers/llm.py`
- Test: `tests/test_llm_provider.py`

**Interfaces:**
- Produces: `LLMClient(base_url, api_key, model)`,方法 `chat(system: str, user: str, temperature: float = 0.7) -> str`;`structured(system: str, user: str, schema: type[T], retries: int = 2) -> T`(T 为 BaseModel 子类;prompt 注入 JSON Schema,解析失败带错误重问);异常 `LLMError`。不依赖代理的 `response_format` 支持(经代理不可靠)。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_llm_provider.py
import json
import httpx, respx, pytest
from pydantic import BaseModel
from shanhai.providers.llm import LLMClient, LLMError

BASE = "https://p.example.com/v1"

class Pet(BaseModel):
    name: str
    age: int

def _resp(text: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})

@respx.mock
def test_structured_with_code_fence():
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=_resp('好的:\n```json\n{"name": "咪咪", "age": 3}\n```'))
    pet = LLMClient(BASE, "sk", "m").structured("sys", "user", Pet)
    assert pet.name == "咪咪"

@respx.mock
def test_structured_retries_on_invalid():
    route = respx.post(f"{BASE}/chat/completions")
    route.side_effect = [_resp("不是 JSON"), _resp(json.dumps({"name": "咪咪", "age": 3}))]
    pet = LLMClient(BASE, "sk", "m").structured("sys", "user", Pet)
    assert pet.age == 3 and route.call_count == 2

@respx.mock
def test_structured_exhausts_retries():
    respx.post(f"{BASE}/chat/completions").mock(return_value=_resp("永远不是 JSON"))
    with pytest.raises(LLMError):
        LLMClient(BASE, "sk", "m").structured("sys", "user", Pet, retries=1)
```

- [ ] **Step 2: 确认失败 → 实现**

```python
# src/shanhai/providers/llm.py
import json
import re

import httpx
from pydantic import BaseModel, ValidationError


class LLMError(Exception):
    pass


class LLMClient:
    def __init__(self, base_url: str, api_key: str, model: str):
        self.model = model
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=300,
        )

    def chat(self, system: str, user: str, temperature: float = 0.7) -> str:
        r = self._client.post("/chat/completions", json={
            "model": self.model,
            "temperature": temperature,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
        })
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    def structured[T: BaseModel](self, system: str, user: str,
                                 schema: type[T], retries: int = 2) -> T:
        sys_prompt = (system + "\n\n只输出一个 JSON 对象,不要输出任何其他文字。必须符合此 JSON Schema:\n"
                      + json.dumps(schema.model_json_schema(), ensure_ascii=False))
        prompt = user
        last_err: Exception | None = None
        for _ in range(retries + 1):
            text = self.chat(sys_prompt, prompt, temperature=0.3)
            try:
                return schema.model_validate_json(_extract_json(text))
            except (ValidationError, ValueError) as e:
                last_err = e
                prompt = f"{user}\n\n上一次输出不合法:{e}\n请修正后重新只输出 JSON。"
        raise LLMError(f"结构化输出失败: {last_err}")


def _extract_json(text: str) -> str:
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        return m.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("响应中没有 JSON 对象")
    return text[start:end + 1]
```

- [ ] **Step 3: 测试通过 → 提交**

```bash
git add src/shanhai/providers/llm.py tests/test_llm_provider.py
git commit -m "feat: llm provider with schema-constrained structured output"
```

## Task 9: S0 传说检索

**执行代理: Sonnet** | 评审: Sonnet

**Files:**
- Create: `src/shanhai/steps/__init__.py`(空)、`src/shanhai/steps/s0_legend.py`
- Test: `tests/test_s0.py`

**Interfaces:**
- Consumes: `LLMClient.structured`、`Project`/`Legend`。
- Produces: `run(project: Project, llm: LLMClient) -> Project`(填充 `legend_candidates`,`status["s0"]="done"`);`from_text(project: Project, llm: LLMClient, story_text: str) -> Project`(自备故事:LLM 只做梗概归纳,`source_type="原创演绎"` 由用户文本决定,直接填 `legend` 并跳过候选)。

**骨架已知局限(写入模块 docstring):** 无联网检索,靠 LLM 自身知识 + 强制来源标注;检索 API 接入是 M2 的事。PRD F0 的"90% 知名景区返回可查传说"验收推迟到接入检索后。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_s0.py
import respx, httpx, json
from shanhai.providers.llm import LLMClient
from shanhai.schema import Project
from shanhai.steps import s0_legend

BASE = "https://p.example.com/v1"

CANDS = {"candidates": [{"title": "白蛇传", "summary": "白娘子与许仙…" ,
                          "source_type": "民间传说", "sources": ["《警世通言》"]}]}

@respx.mock
def test_s0_fills_candidates():
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, json={
        "choices": [{"message": {"content": json.dumps(CANDS, ensure_ascii=False)}}]}))
    p = s0_legend.run(Project(project_id="x", scenic_spot="雷峰塔"), LLMClient(BASE, "sk", "m"))
    assert p.legend_candidates[0].title == "白蛇传"
    assert p.status["s0"] == "done"
```

- [ ] **Step 2: 确认失败 → 实现**

```python
# src/shanhai/steps/s0_legend.py
"""S0 传说检索。骨架局限:无联网检索,靠 LLM 知识 + 强制来源标注;M2 接检索 API。"""
from pydantic import BaseModel

from shanhai.providers.llm import LLMClient
from shanhai.schema import Legend, Project

SYSTEM = """你是文旅内容研究员。给定景区名称,列出 2~5 个与之相关的历史传说。
规则:
- 每个传说标注来源类型:正史/地方志/民间传说/文学作品,不得把传说包装成史实
- sources 给出可核查的出处(书名、方志名或链接);无法给出可靠出处的不要列
- 确实没有可靠传说时返回空列表,不要编造"""


class _Candidates(BaseModel):
    candidates: list[Legend]


def run(project: Project, llm: LLMClient) -> Project:
    result = llm.structured(SYSTEM, f"景区名称:{project.scenic_spot}", _Candidates)
    project.legend_candidates = result.candidates
    project.status["s0"] = "done"
    return project


def from_text(project: Project, llm: LLMClient, story_text: str) -> Project:
    summary = llm.chat("把用户提供的故事压缩成 200 字以内的中文梗概,只输出梗概。", story_text)
    project.legend = Legend(title=f"{project.scenic_spot}·自备故事", summary=summary,
                            source_type="原创演绎", sources=["用户自备文本"])
    project.status["s0"] = "done"
    return project
```

- [ ] **Step 3: 测试通过 → 提交**

```bash
git add src/shanhai/steps tests/test_s0.py && git commit -m "feat: S0 legend retrieval step"
```

## Task 10: S1 剧本改编

**执行代理: Sonnet** | 评审: Sonnet

**Files:**
- Create: `src/shanhai/steps/s1_script.py`
- Test: `tests/test_s1.py`

**Interfaces:**
- Consumes: `Project.legend`(不为 None)、`GenerationParams`、`LLMClient.structured`、`Script`。
- Produces: `run(project: Project, llm: LLMClient) -> Project`(填 `script`,`status["s1"]="done"`);`WORD_TARGETS: dict[int, int] = {1: 210, 3: 650, 5: 1100}`(解说总字数目标,按 ~3.5 字/秒)。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_s1.py
import json
import httpx, respx, pytest
from shanhai.providers.llm import LLMClient
from shanhai.schema import Legend, Project
from shanhai.steps import s1_script

BASE = "https://p.example.com/v1"

SCRIPT = {"title": "白蛇传", "theme": "人妖之恋", "acts": [{"scenes": [
    {"description": "断桥初遇", "characters": ["白素贞", "许仙"],
     "narration": "西湖烟雨中……", "dialogues": [{"character": "白素贞", "line": "公子留步。"}]}]}],
    "characters": [{"name": "白素贞", "role": "蛇仙", "personality": "温婉坚韧",
                    "appearance": "白衣女子"}]}

def _project() -> Project:
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.legend = Legend(title="白蛇传", summary="s", source_type="民间传说", sources=["x"])
    return p

@respx.mock
def test_s1_fills_script():
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, json={
        "choices": [{"message": {"content": json.dumps(SCRIPT, ensure_ascii=False)}}]}))
    p = s1_script.run(_project(), LLMClient(BASE, "sk", "m"))
    assert p.script.characters[0].name == "白素贞"
    assert p.status["s1"] == "done"

def test_s1_requires_legend():
    with pytest.raises(ValueError):
        s1_script.run(Project(project_id="x", scenic_spot="雷峰塔"), LLMClient(BASE, "sk", "m"))
```

- [ ] **Step 2: 确认失败 → 实现**

```python
# src/shanhai/steps/s1_script.py
from shanhai.providers.llm import LLMClient
from shanhai.schema import Project, Script

WORD_TARGETS = {1: 210, 3: 650, 5: 1100}

SYSTEM = """你是儿童文学与文旅内容编剧。把给定的景区传说改编为结构化剧本。
规则:
- 保留传说核心情节,不魔改结局
- 受众为"儿童"时,自动规避暴力、恐怖、血腥细节,用温和意象替代
- 旁白承担主要叙事,对白精炼;所有旁白+对白总字数命中目标字数 ±20%
- characters 列出全部出场角色,主要角色不超过 4 个,appearance 用可视觉化的外貌关键词"""


def run(project: Project, llm: LLMClient) -> Project:
    if project.legend is None:
        raise ValueError("先完成 S0 并选定传说")
    words = WORD_TARGETS[project.params.duration_min]
    user = (f"传说:《{project.legend.title}》\n梗概:{project.legend.summary}\n"
            f"目标总字数:{words}\n受众:{project.params.audience}\n基调:{project.params.tone}")
    project.script = llm.structured(SYSTEM, user, Script)
    project.status["s1"] = "done"
    return project
```

- [ ] **Step 3: 测试通过 → 提交**

```bash
git add src/shanhai/steps/s1_script.py tests/test_s1.py && git commit -m "feat: S1 script adaptation step"
```

## Task 11: S2 分镜设计

**执行代理: Sonnet** | 评审: Sonnet

**Files:**
- Create: `src/shanhai/steps/s2_storyboard.py`
- Test: `tests/test_s2.py`

**Interfaces:**
- Consumes: `Project.script`、`StoryboardCell`、`LLMClient.structured`。
- Produces: `run(project: Project, llm: LLMClient) -> Project`(填 `storyboard`,`status["s2"]="done"`);`PAGE_TARGETS: dict[int, tuple[int, int]] = {1: (8, 10), 3: (20, 24), 5: (32, 40)}`;情绪标签约束集合 `EMOTIONS = {"宁静", "欢快", "紧张", "悲伤", "神秘", "恢弘", "温馨"}`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_s2.py
import json
import httpx, respx
from shanhai.providers.llm import LLMClient
from shanhai.schema import Project, Script
from shanhai.steps import s2_storyboard

BASE = "https://p.example.com/v1"

CELLS = {"cells": [
    {"index": i, "scene_ref": "1-1", "visual_desc": f"画面{i}", "characters": ["白素贞"],
     "caption": f"第{i}页的解说词。", "emotion": "宁静"} for i in range(1, 9)]}

@respx.mock
def test_s2_fills_storyboard():
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, json={
        "choices": [{"message": {"content": json.dumps(CELLS, ensure_ascii=False)}}]}))
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.params.duration_min = 1
    p.script = Script(title="t", theme="th", acts=[], characters=[])
    p = s2_storyboard.run(p, LLMClient(BASE, "sk", "m"))
    assert len(p.storyboard) == 8
    assert p.storyboard[0].status == "draft"
    assert p.status["s2"] == "done"
```

- [ ] **Step 2: 确认失败 → 实现**

```python
# src/shanhai/steps/s2_storyboard.py
from pydantic import BaseModel

from shanhai.providers.llm import LLMClient
from shanhai.schema import Project, StoryboardCell

PAGE_TARGETS = {1: (8, 10), 3: (20, 24), 5: (32, 40)}
EMOTIONS = {"宁静", "欢快", "紧张", "悲伤", "神秘", "恢弘", "温馨"}

SYSTEM = """你是连环画分镜师。把剧本切分为一页一格的连环画分镜。
规则:
- caption 是该页的解说文案(旁白或对白),不超过 80 字
- 所有 caption 连起来必须能独立讲通整个故事——听众不看画面也能听懂,这是硬性要求
- visual_desc 描述构图、景别、光线、氛围,供绘图使用,不写文字内容
- emotion 只能从这些标签里选:宁静/欢快/紧张/悲伤/神秘/恢弘/温馨
- index 从 1 开始连续编号"""


class _Cells(BaseModel):
    cells: list[StoryboardCell]


def run(project: Project, llm: LLMClient) -> Project:
    if project.script is None:
        raise ValueError("先完成 S1")
    lo, hi = PAGE_TARGETS[project.params.duration_min]
    user = (f"页数要求:{lo}~{hi} 页。\n剧本 JSON:\n"
            + project.script.model_dump_json(indent=1))
    project.storyboard = llm.structured(SYSTEM, user, _Cells).cells
    project.status["s2"] = "done"
    return project
```

- [ ] **Step 3: 测试通过 → 提交**

```bash
git add src/shanhai/steps/s2_storyboard.py tests/test_s2.py && git commit -m "feat: S2 storyboard step"
```

## Task 12: S3 角色特征卡 + 三视图

**执行代理: Sonnet** | 评审: Opus(涉及一致性链路)

**Files:**
- Create: `src/shanhai/steps/s3_characters.py`
- Test: `tests/test_s3.py`

**Interfaces:**
- Consumes: `Project.script.characters`、`LLMClient.chat`、`ImageClient.generate`、`STYLE_PRESETS`、T5 决策(默认画风/模型)。
- Produces: `run(project: Project, llm: LLMClient, image: ImageClient, workdir: Path, image_size: str) -> Project`:对前 ≤4 个角色,LLM 生成 `feature_prompt`(可直接拼进页面 prompt 的外貌描述片段),图像模型生成三视图存 `workdir/characters/<name>.png`,回填 `turnaround_image`(相对路径)与 `locked=True`;`status["s3"]="done"`。三视图 prompt 模板与 M0 spike 完全一致(TURNAROUND_TMPL 迁移至此,spike 改为引用此模块——单一事实源)。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_s3.py
from pathlib import Path
from unittest.mock import MagicMock
from shanhai.schema import CharacterCard, Project, Script
from shanhai.steps import s3_characters

def test_s3_limits_to_four_and_saves(tmp_path: Path):
    chars = [CharacterCard(name=f"角色{i}", role="r", personality="p", appearance="白衣")
             for i in range(6)]
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.script = Script(title="t", theme="th", acts=[], characters=chars)
    llm = MagicMock(); llm.chat.return_value = "白衣女子,黑色长发,银簪"
    image = MagicMock(); image.generate.return_value = b"png"
    p = s3_characters.run(p, llm, image, tmp_path, "1536x1024")
    assert image.generate.call_count == 4          # 主要角色 ≤4(PRD F3)
    assert p.script.characters[0].locked is True
    assert (tmp_path / "characters" / "角色0.png").exists()
    assert p.script.characters[4].feature_prompt   # 次要角色也有文字特征
    assert p.script.characters[4].turnaround_image == ""
```

- [ ] **Step 2: 确认失败 → 实现**

```python
# src/shanhai/steps/s3_characters.py
from pathlib import Path

from shanhai.providers.image import ImageClient
from shanhai.providers.llm import LLMClient
from shanhai.schema import Project
from shanhai.styles import STYLE_PRESETS

MAX_TURNAROUND = 4

TURNAROUND_TMPL = (
    "{style}。角色三视图设定图:同一角色的正面、侧面、背面全身像并排排列,"
    "纯白背景,画面中不要出现任何文字。角色:{feature}"
)

FEATURE_SYSTEM = ("把角色信息浓缩为一段可直接用于图像生成 prompt 的中文外貌描述片段,"
                  "包含:性别年龄、发型发色、服饰与颜色、标志性道具。只输出这一段描述。")


def run(project: Project, llm: LLMClient, image: ImageClient,
        workdir: Path, image_size: str) -> Project:
    if project.script is None:
        raise ValueError("先完成 S1")
    style = STYLE_PRESETS[project.style_preset]
    char_dir = workdir / "characters"
    char_dir.mkdir(parents=True, exist_ok=True)
    for i, c in enumerate(project.script.characters):
        c.feature_prompt = llm.chat(
            FEATURE_SYSTEM, f"姓名:{c.name}\n身份:{c.role}\n性格:{c.personality}\n外貌:{c.appearance}")
        if i < MAX_TURNAROUND:
            out = char_dir / f"{c.name}.png"
            out.write_bytes(image.generate(
                TURNAROUND_TMPL.format(style=style, feature=c.feature_prompt), size=image_size))
            c.turnaround_image = str(out.relative_to(workdir))
            c.locked = True
    project.status["s3"] = "done"
    return project
```

同时修改 `spike/consistency_test.py`:删除本地 `TURNAROUND_TMPL`,改为 `from shanhai.steps.s3_characters import TURNAROUND_TMPL`。

- [ ] **Step 3: 测试通过(含 spike import 冒烟)→ 提交**

```bash
uv run pytest tests/test_s3.py -v
git add src/shanhai/steps/s3_characters.py tests/test_s3.py spike/consistency_test.py
git commit -m "feat: S3 character cards + turnaround generation"
```

## Task 13: 排版 typeset.py(文案带 / 片头片尾卡 / AI 水印)

**执行代理: Sonnet** | 评审: Sonnet

**Files:**
- Create: `src/shanhai/typeset.py`
- Test: `tests/test_typeset.py`

**Interfaces:**
- Produces(供 S4/S6 使用):
  - `FONT_PATH = Path("assets/fonts/NotoSansCJKsc-Regular.otf")`、`FRAME = (1920, 1080)`
  - `compose_page(art: bytes, caption: str, out: Path) -> None`:画面缩放适配 1920×920 顶部区(黑边填充),底部 160px 半透明黑文案带,白字居中,按字宽换行;右上角固定水印 `AI 生成`(不可关闭,Global Constraints)。
  - `title_card(title: str, subtitle: str, out: Path) -> None`:黑底白字片头。
  - `credits_card(lines: list[str], out: Path) -> None`:片尾,来源标注 + "本片为 AI 生成内容"。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_typeset.py
import io
from pathlib import Path
from PIL import Image
from shanhai import typeset

def _art() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (1536, 1024), "red").save(buf, "PNG")
    return buf.getvalue()

def test_compose_page_frame_size(tmp_path: Path):
    out = tmp_path / "p.png"
    typeset.compose_page(_art(), "西湖烟雨,断桥初遇。", out)
    assert Image.open(out).size == (1920, 1080)

def test_title_and_credits(tmp_path: Path):
    typeset.title_card("雷峰塔", "白蛇传", tmp_path / "t.png")
    typeset.credits_card(["来源:《警世通言》", "本片为 AI 生成内容"], tmp_path / "c.png")
    assert Image.open(tmp_path / "t.png").size == (1920, 1080)
    assert Image.open(tmp_path / "c.png").size == (1920, 1080)
```

- [ ] **Step 2: 确认失败 → 实现**

```python
# src/shanhai/typeset.py
import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_PATH = Path("assets/fonts/NotoSansCJKsc-Regular.otf")
FRAME = (1920, 1080)
CAPTION_H = 160
WATERMARK = "AI 生成"


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_PATH), size)


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    lines, cur = [], ""
    for ch in text:
        if font.getlength(cur + ch) > max_w:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


def compose_page(art: bytes, caption: str, out: Path) -> None:
    frame = Image.new("RGB", FRAME, "black")
    img = Image.open(io.BytesIO(art)).convert("RGB")
    area_h = FRAME[1] - CAPTION_H
    img.thumbnail((FRAME[0], area_h))
    frame.paste(img, ((FRAME[0] - img.width) // 2, (area_h - img.height) // 2))
    draw = ImageDraw.Draw(frame, "RGBA")
    draw.rectangle([0, area_h, FRAME[0], FRAME[1]], fill=(0, 0, 0, 200))
    font = _font(40)
    lines = _wrap(caption, font, FRAME[0] - 240)[:2]
    for i, line in enumerate(lines):
        w = font.getlength(line)
        draw.text(((FRAME[0] - w) / 2, area_h + 24 + i * 56), line, font=font, fill="white")
    wm_font = _font(28)
    draw.text((FRAME[0] - wm_font.getlength(WATERMARK) - 24, 20), WATERMARK,
              font=wm_font, fill=(255, 255, 255, 180))
    frame.save(out)


def _text_card(lines: list[str], sizes: list[int], out: Path) -> None:
    frame = Image.new("RGB", FRAME, "black")
    draw = ImageDraw.Draw(frame)
    total_h = sum(sizes) + 30 * (len(lines) - 1)
    y = (FRAME[1] - total_h) / 2
    for line, size in zip(lines, sizes):
        font = _font(size)
        draw.text(((FRAME[0] - font.getlength(line)) / 2, y), line, font=font, fill="white")
        y += size + 30
    frame.save(out)


def title_card(title: str, subtitle: str, out: Path) -> None:
    _text_card([title, subtitle], [88, 52], out)


def credits_card(lines: list[str], out: Path) -> None:
    _text_card(lines, [36] * len(lines), out)
```

- [ ] **Step 3: 测试通过 → 提交**

```bash
git add src/shanhai/typeset.py tests/test_typeset.py && git commit -m "feat: page typesetting, title/credits cards, AI watermark"
```

## Task 14: S4 连环画页生成

**执行代理: Opus** | 评审: Opus(一致性核心链路)

**Files:**
- Create: `src/shanhai/steps/s4_pages.py`
- Test: `tests/test_s4.py`

**Interfaces:**
- Consumes: `Project.storyboard`、`Project.script.characters`(feature_prompt / turnaround_image)、`ImageClient`、`typeset.compose_page`、`STYLE_PRESETS`。
- Produces: `run(project: Project, image: ImageClient, workdir: Path, image_size: str) -> Project`:逐页拼 prompt(画风前缀 + visual_desc + 出场角色 feature 片段 + 禁文字指令),以出场角色的三视图为 references;失败重试 ≤2 次(共 3 attempt),仍失败则该页 `status="failed"` 继续后面页;成功页经 `compose_page` 排版存 `workdir/pages/page_XX.png`,回填 `cell.image`、`status="confirmed"`;已 confirmed 的页跳过(断点续跑)。页面 prompt 模板同样回供 spike 使用。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_s4.py
import io
from pathlib import Path
from unittest.mock import MagicMock
from PIL import Image
from shanhai.providers.image import ImageGenError
from shanhai.schema import CharacterCard, Project, Script, StoryboardCell
from shanhai.steps import s4_pages

def _png() -> bytes:
    buf = io.BytesIO(); Image.new("RGB", (64, 64), "blue").save(buf, "PNG")
    return buf.getvalue()

def _project(tmp_path: Path) -> Project:
    p = Project(project_id="x", scenic_spot="雷峰塔")
    card = CharacterCard(name="白素贞", role="r", personality="p", appearance="a",
                         feature_prompt="白衣女子", turnaround_image="characters/白素贞.png",
                         locked=True)
    (tmp_path / "characters").mkdir(parents=True)
    (tmp_path / "characters" / "白素贞.png").write_bytes(_png())
    p.script = Script(title="t", theme="th", acts=[], characters=[card])
    p.storyboard = [StoryboardCell(index=1, scene_ref="1-1", visual_desc="断桥",
                                   characters=["白素贞"], caption="西湖初遇。", emotion="宁静")]
    return p

def test_s4_generates_and_composes(tmp_path: Path):
    image = MagicMock(); image.generate.return_value = _png()
    p = s4_pages.run(_project(tmp_path), image, tmp_path, "1536x1024")
    assert p.storyboard[0].status == "confirmed"
    assert (tmp_path / "pages" / "page_01.png").exists()
    refs = image.generate.call_args.kwargs["references"]
    assert refs and refs[0].name == "白素贞.png"      # 三视图作为参考图传入
    prompt = image.generate.call_args.args[0]
    assert "白衣女子" in prompt and "不要出现任何文字" in prompt

def test_s4_retries_then_fails(tmp_path: Path):
    image = MagicMock(); image.generate.side_effect = ImageGenError("boom")
    p = s4_pages.run(_project(tmp_path), image, tmp_path, "1536x1024")
    assert image.generate.call_count == 3            # 1 + 重试 2(PRD F4)
    assert p.storyboard[0].status == "failed"

def test_s4_skips_confirmed(tmp_path: Path):
    proj = _project(tmp_path)
    proj.storyboard[0].status = "confirmed"
    image = MagicMock()
    s4_pages.run(proj, image, tmp_path, "1536x1024")
    image.generate.assert_not_called()               # 断点续跑
```

- [ ] **Step 2: 确认失败 → 实现**

```python
# src/shanhai/steps/s4_pages.py
from pathlib import Path

from shanhai import typeset
from shanhai.providers.image import ImageClient
from shanhai.schema import Project
from shanhai.styles import STYLE_PRESETS

MAX_ATTEMPTS = 3  # 1 次 + 重试 2 次(PRD F4)

PAGE_TMPL = (
    "{style}。连环画单页画面:{scene}。出场角色:{features}。"
    "严格保持角色与参考图中的形象一致(发型、服饰、面部特征)。画面中不要出现任何文字。"
)


def run(project: Project, image: ImageClient, workdir: Path, image_size: str) -> Project:
    if project.script is None or not project.storyboard:
        raise ValueError("先完成 S2/S3")
    style = STYLE_PRESETS[project.style_preset]
    cards = {c.name: c for c in project.script.characters}
    pages_dir = workdir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    for cell in project.storyboard:
        if cell.status == "confirmed":
            continue
        present = [cards[n] for n in cell.characters if n in cards]
        features = ";".join(f"{c.name}({c.feature_prompt})" for c in present) or "无固定角色"
        refs = [workdir / c.turnaround_image for c in present if c.turnaround_image]
        prompt = PAGE_TMPL.format(style=style, scene=cell.visual_desc, features=features)
        out = pages_dir / f"page_{cell.index:02d}.png"
        for attempt in range(MAX_ATTEMPTS):
            try:
                art = image.generate(prompt, size=image_size, references=refs or None)
                typeset.compose_page(art, cell.caption, out)
                cell.image = str(out.relative_to(workdir))
                cell.status = "confirmed"
                break
            except Exception:  # noqa: BLE001 单页失败不拖垮整轮,重试后标 failed
                if attempt == MAX_ATTEMPTS - 1:
                    cell.status = "failed"
    project.status["s4"] = "done" if all(
        c.status == "confirmed" for c in project.storyboard) else "partial"
    return project
```

同时修改 `spike/consistency_test.py`:`from shanhai.steps.s4_pages import PAGE_TMPL`,删除本地副本。

- [ ] **Step 3: 测试通过 → 提交**

```bash
uv run pytest tests/test_s4.py -v
git add src/shanhai/steps/s4_pages.py tests/test_s4.py spike/consistency_test.py
git commit -m "feat: S4 page generation with turnaround references and retry"
```

## Task 15: TTS Provider + S5 配音配乐

**执行代理: Sonnet** | 评审: Sonnet

**Files:**
- Create: `src/shanhai/providers/tts.py`、`src/shanhai/steps/s5_audio.py`
- Test: `tests/test_s5.py`

**Interfaces:**
- Consumes: `Settings.tts_endpoint/tts_model/tts_voice`、`Project.storyboard`、`ffmpeg.probe_duration_ms`(Task 16 提供;本任务先在 s5 内 `from shanhai.ffmpeg import probe_duration_ms`,与 T16 并行开发时按此签名 mock)。
- Produces:
  - `TTSClient(base_url, api_key, model)`,方法 `synthesize(text: str, voice: str, out: Path) -> None`(POST `/audio/speech`,`response_format="mp3"`)。
  - `s5_audio.run(project: Project, tts: TTSClient, voice: str, workdir: Path) -> Project`:逐页合成 `workdir/audio/page_XX.mp3`,`cell.duration_ms = probe_duration_ms(...)`;已有 audio 且文件存在则跳过;BGM:读 `assets/bgm/manifest.json`(结构 `{"tracks": [{"file": "x.mp3", "emotions": ["宁静"], "license": "CC0"}]}`),按分镜情绪众数匹配,命中则 `project.bgm = "assets/bgm/x.mp3"`,清单为空则 `bgm=""` 跳过;`status["s5"]="done"`。
- **骨架已知局限(写入 docstring):** OpenAI 兼容 `/audio/speech` 无 SSML 多音字标注;PRD F5 的多音字词典与读音标注入口等接国内 TTS/本地方案时再做。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_s5.py
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import httpx, respx
from shanhai.providers.tts import TTSClient
from shanhai.schema import Project, StoryboardCell
from shanhai.steps import s5_audio

BASE = "https://p.example.com/v1"

@respx.mock
def test_tts_client(tmp_path: Path):
    respx.post(f"{BASE}/audio/speech").mock(
        return_value=httpx.Response(200, content=b"mp3bytes"))
    TTSClient(BASE, "sk", "tts-1").synthesize("你好", "alloy", tmp_path / "a.mp3")
    assert (tmp_path / "a.mp3").read_bytes() == b"mp3bytes"

def _project() -> Project:
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.storyboard = [StoryboardCell(index=1, scene_ref="1-1", visual_desc="v",
                                   characters=[], caption="西湖初遇。", emotion="宁静")]
    return p

@patch("shanhai.steps.s5_audio.probe_duration_ms", return_value=6800)
def test_s5_fills_duration_and_bgm(mock_probe, tmp_path: Path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"tracks": [
        {"file": "calm.mp3", "emotions": ["宁静"], "license": "CC0"}]}), encoding="utf-8")
    tts = MagicMock()
    p = s5_audio.run(_project(), tts, "alloy", tmp_path, manifest_path=manifest)
    assert p.storyboard[0].duration_ms == 6800
    assert p.bgm.endswith("calm.mp3")
    assert p.status["s5"] == "done"
```

- [ ] **Step 2: 确认失败 → 实现**

```python
# src/shanhai/providers/tts.py
from pathlib import Path

import httpx


class TTSClient:
    def __init__(self, base_url: str, api_key: str, model: str):
        self.model = model
        self._client = httpx.Client(base_url=base_url.rstrip("/"),
                                    headers={"Authorization": f"Bearer {api_key}"}, timeout=300)

    def synthesize(self, text: str, voice: str, out: Path) -> None:
        r = self._client.post("/audio/speech", json={
            "model": self.model, "voice": voice, "input": text, "response_format": "mp3"})
        r.raise_for_status()
        out.write_bytes(r.content)
```

```python
# src/shanhai/steps/s5_audio.py
"""S5 配音配乐。骨架局限:无 SSML 多音字标注(PRD F5),接国内 TTS/本地方案时补。"""
import json
from collections import Counter
from pathlib import Path

from shanhai.ffmpeg import probe_duration_ms
from shanhai.providers.tts import TTSClient
from shanhai.schema import Project

DEFAULT_MANIFEST = Path("assets/bgm/manifest.json")


def run(project: Project, tts: TTSClient, voice: str, workdir: Path,
        manifest_path: Path = DEFAULT_MANIFEST) -> Project:
    audio_dir = workdir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    for cell in project.storyboard:
        out = audio_dir / f"page_{cell.index:02d}.mp3"
        if not (cell.audio and out.exists()):
            tts.synthesize(cell.caption, voice, out)
            cell.audio = str(out.relative_to(workdir))
        cell.duration_ms = probe_duration_ms(out)
    tracks = json.loads(manifest_path.read_text(encoding="utf-8")).get("tracks", [])
    if tracks and project.storyboard:
        mood = Counter(c.emotion for c in project.storyboard).most_common(1)[0][0]
        match = next((t for t in tracks if mood in t.get("emotions", [])), tracks[0])
        project.bgm = str(manifest_path.parent / match["file"])
    project.status["s5"] = "done"
    return project
```

- [ ] **Step 3: 测试通过 → 提交**

注:T16 未完成时 `from shanhai.ffmpeg import probe_duration_ms` 会 ImportError——若并行开发,先在本任务创建只含 `probe_duration_ms` 的最小 `src/shanhai/ffmpeg.py`(见 T16 Step 3 的实现,签名一致),T16 在其上扩展。

```bash
uv run pytest tests/test_s5.py -v
git add src/shanhai/providers/tts.py src/shanhai/steps/s5_audio.py tests/test_s5.py
git commit -m "feat: TTS provider + S5 narration and BGM matching"
```

## Task 16: FFmpeg 封装 + S6 合成输出

**执行代理: Opus** | 评审: Opus

**Files:**
- Create/Modify: `src/shanhai/ffmpeg.py`、创建 `src/shanhai/steps/s6_compose.py`
- Test: `tests/test_ffmpeg.py`、`tests/test_s6.py`

**Interfaces:**
- Consumes: `typeset.title_card/credits_card`、`Project`(storyboard 各页 image/audio/duration_ms、legend.sources、bgm)。
- Produces(命令构建器返回 `list[str]`,便于测试;`sh(cmd: list[str])` 用 subprocess 执行,check=True):
  - `probe_duration_ms(path: Path) -> int`
  - `page_clip_cmd(image: Path, audio: Path | None, duration_ms: int, out: Path) -> list[str]`(图转 25fps 视频,首尾 0.25s 淡入淡出,无 audio 时用 anullsrc)
  - `concat_cmd(clips: list[Path], list_file: Path, out: Path) -> list[str]`(concat demuxer,重编码保证兼容)
  - `finalize_cmd(video: Path, bgm: Path | None, out: Path) -> list[str]`(有 bgm:循环混入 volume=0.18;统一 `loudnorm=I=-16:TP=-1.5:LRA=11`)
  - `s6_compose.run(project: Project, workdir: Path) -> Project`:片头卡(2.5s,景区名+故事名)→ 各页 clip(时长 = duration_ms + 500)→ 片尾卡(3s,来源 + AI 标识)→ concat → finalize → `workdir/output/final.mp4`,回填 `project.output["mp4"]`,`status["s6"]="done"`。跳过 `status=="failed"` 的页并打印警告。

- [ ] **Step 1: 写失败测试(只测命令构建,不跑 ffmpeg)**

```python
# tests/test_ffmpeg.py
from pathlib import Path
from shanhai import ffmpeg

def test_page_clip_cmd_duration_and_fade():
    cmd = " ".join(ffmpeg.page_clip_cmd(Path("p.png"), Path("a.mp3"), 6800, Path("o.mp4")))
    assert "-t 7.3" in cmd            # 6800ms + 500ms 缓冲
    assert "fade=t=in" in cmd and "fade=t=out" in cmd
    assert "1920:1080" in cmd and "yuv420p" in cmd

def test_page_clip_cmd_silent():
    cmd = " ".join(ffmpeg.page_clip_cmd(Path("p.png"), None, 2500, Path("o.mp4")))
    assert "anullsrc" in cmd

def test_finalize_cmd_loudnorm_and_bgm():
    cmd = " ".join(ffmpeg.finalize_cmd(Path("v.mp4"), Path("b.mp3"), Path("o.mp4")))
    assert "loudnorm=I=-16" in cmd and "volume=0.18" in cmd and "amix" in cmd

def test_finalize_cmd_no_bgm():
    cmd = " ".join(ffmpeg.finalize_cmd(Path("v.mp4"), None, Path("o.mp4")))
    assert "loudnorm=I=-16" in cmd and "amix" not in cmd
```

```python
# tests/test_s6.py
from pathlib import Path
from unittest.mock import patch
from shanhai.schema import Legend, Project, StoryboardCell
from shanhai.steps import s6_compose

def test_s6_builds_and_records_output(tmp_path: Path):
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.legend = Legend(title="白蛇传", summary="s", source_type="民间传说", sources=["《警世通言》"])
    (tmp_path / "pages").mkdir(parents=True); (tmp_path / "audio").mkdir()
    (tmp_path / "pages/page_01.png").write_bytes(b"png")
    (tmp_path / "audio/page_01.mp3").write_bytes(b"mp3")
    p.storyboard = [StoryboardCell(index=1, scene_ref="1-1", visual_desc="v", characters=[],
                                   caption="c", emotion="宁静", image="pages/page_01.png",
                                   audio="audio/page_01.mp3", duration_ms=6800,
                                   status="confirmed")]
    with patch("shanhai.steps.s6_compose.ffmpeg.sh") as sh, \
         patch("shanhai.steps.s6_compose.typeset.title_card"), \
         patch("shanhai.steps.s6_compose.typeset.credits_card"):
        p = s6_compose.run(p, tmp_path)
    assert p.output["mp4"].endswith("final.mp4")
    assert p.status["s6"] == "done"
    assert sh.call_count >= 4          # 片头 clip + 页 clip + 片尾 clip + concat + finalize
```

- [ ] **Step 2: 确认失败 → 实现 ffmpeg.py**

```python
# src/shanhai/ffmpeg.py
import subprocess
from pathlib import Path

FPS = 25
FADE = 0.25
BUFFER_MS = 500  # 每页时长 = 解说音频 + 0.5s(PRD F6)


def sh(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, capture_output=True)


def probe_duration_ms(path: Path) -> int:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, text=True).stdout.strip()
    return int(float(out) * 1000)


def page_clip_cmd(image: Path, audio: Path | None, duration_ms: int, out: Path) -> list[str]:
    dur = duration_ms / 1000
    vf = (f"scale=1920:1080:force_original_aspect_ratio=decrease,"
          f"pad=1920:1080:(ow-iw)/2:(oh-ih)/2,"
          f"fade=t=in:st=0:d={FADE},fade=t=out:st={max(dur - FADE, 0):.2f}:d={FADE},"
          f"format=yuv420p")
    cmd = ["ffmpeg", "-y", "-loop", "1", "-i", str(image)]
    if audio:
        cmd += ["-i", str(audio), "-af", "apad"]
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
    cmd += ["-t", f"{dur:g}", "-vf", vf, "-r", str(FPS),
            "-c:v", "libx264", "-c:a", "aac", "-b:a", "192k", str(out)]
    return cmd


def concat_cmd(clips: list[Path], list_file: Path, out: Path) -> list[str]:
    list_file.write_text("".join(f"file '{c.resolve()}'\n" for c in clips), encoding="utf-8")
    return ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
            "-c:v", "libx264", "-c:a", "aac", "-b:a", "192k", str(out)]


def finalize_cmd(video: Path, bgm: Path | None, out: Path) -> list[str]:
    loudnorm = "loudnorm=I=-16:TP=-1.5:LRA=11"
    if bgm:
        fc = (f"[1:a]volume=0.18[bg];[0:a][bg]amix=inputs=2:duration=first[mix];"
              f"[mix]{loudnorm}[aout]")
        return ["ffmpeg", "-y", "-i", str(video), "-stream_loop", "-1", "-i", str(bgm),
                "-filter_complex", fc, "-map", "0:v", "-map", "[aout]",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", str(out)]
    return ["ffmpeg", "-y", "-i", str(video), "-af", loudnorm,
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", str(out)]
```

- [ ] **Step 3: 实现 s6_compose.py**

```python
# src/shanhai/steps/s6_compose.py
from pathlib import Path

from shanhai import ffmpeg, typeset
from shanhai.schema import Project

TITLE_MS = 2500
CREDITS_MS = 3000


def run(project: Project, workdir: Path) -> Project:
    out_dir = workdir / "output"
    clips_dir = out_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    title_png = out_dir / "title.png"
    legend_title = project.legend.title if project.legend else ""
    typeset.title_card(project.scenic_spot, legend_title, title_png)
    sources = project.legend.sources if project.legend else []
    credits_png = out_dir / "credits.png"
    typeset.credits_card([f"传说来源:{s}" for s in sources] + ["本片为 AI 生成内容"], credits_png)

    clips: list[Path] = []
    head = clips_dir / "00_title.mp4"
    ffmpeg.sh(ffmpeg.page_clip_cmd(title_png, None, TITLE_MS, head))
    clips.append(head)
    for cell in project.storyboard:
        if cell.status != "confirmed" or not (cell.image and cell.audio):
            print(f"跳过第 {cell.index} 页(status={cell.status})")
            continue
        clip = clips_dir / f"{cell.index:02d}.mp4"
        ffmpeg.sh(ffmpeg.page_clip_cmd(workdir / cell.image, workdir / cell.audio,
                                       cell.duration_ms + ffmpeg.BUFFER_MS, clip))
        clips.append(clip)
    tail = clips_dir / "99_credits.mp4"
    ffmpeg.sh(ffmpeg.page_clip_cmd(credits_png, None, CREDITS_MS, tail))
    clips.append(tail)

    merged = out_dir / "merged.mp4"
    ffmpeg.sh(ffmpeg.concat_cmd(clips, out_dir / "concat.txt", merged))
    final = out_dir / "final.mp4"
    bgm = Path(project.bgm) if project.bgm else None
    ffmpeg.sh(ffmpeg.finalize_cmd(merged, bgm, final))
    project.output["mp4"] = str(final)
    project.status["s6"] = "done"
    return project
```

- [ ] **Step 4: 测试通过 → 真实 ffmpeg 冒烟**

```bash
uv run pytest tests/test_ffmpeg.py tests/test_s6.py -v
which ffmpeg ffprobe   # 缺则: brew install ffmpeg
uv run python -c "
from pathlib import Path
from shanhai import ffmpeg, typeset
typeset.title_card('测试', '冒烟', Path('/tmp/t.png'))
ffmpeg.sh(ffmpeg.page_clip_cmd(Path('/tmp/t.png'), None, 2000, Path('/tmp/t.mp4')))
print('ms =', ffmpeg.probe_duration_ms(Path('/tmp/t.mp4')))"
```

预期:`ms = 2000`(±100)。

- [ ] **Step 5: 提交**

```bash
git add src/shanhai/ffmpeg.py src/shanhai/steps/s6_compose.py tests/test_ffmpeg.py tests/test_s6.py
git commit -m "feat: ffmpeg pipeline + S6 composition with title/credits and loudnorm"
```

## Task 17: CLI 编排

**执行代理: Sonnet** | 评审: Sonnet

**Files:**
- Create: `src/shanhai/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: 全部 steps、store、config、providers。
- Produces(typer app,入口 `shanhai`):
  - `shanhai new <景区名> [--minutes 1|3|5] [--audience 儿童|大众] [--tone ...] [--style ...] [--story-file PATH]` → 建项目,跑 S0(有 story-file 走 `from_text`),打印候选列表与 project_id
  - `shanhai pick <project_id> <序号>` → 选定传说存入 `legend`
  - `shanhai step <project_id> <s1|s2|s3|s4|s5|s6>` → 单步执行并 save(可重入的分步模式)
  - `shanhai run <景区名> [同 new 的参数]` → 快速模式:S0 自动选第一个候选,S1~S6 一路到底,打印每步耗时与最终 MP4 路径
  - `shanhai status <project_id>` → 打印各步 status 与产物路径

- [ ] **Step 1: 写失败测试(mock 全部 step 模块)**

```python
# tests/test_cli.py
from unittest.mock import MagicMock, patch
from typer.testing import CliRunner
from shanhai.cli import app
from shanhai.schema import Legend, Project

runner = CliRunner()

def _stub_settings():
    return MagicMock(base_url="https://p/v1", api_key="sk", llm_model="m",
                     image_model="im", image_api_mode="chat_api", image_size="1536x1024",
                     tts_model="t", tts_voice="alloy",
                     image_endpoint=("https://p/v1", "sk"), tts_endpoint=("https://p/v1", "sk"))

@patch("shanhai.cli.Settings", _stub_settings)
@patch("shanhai.cli.s0_legend")
@patch("shanhai.cli.store")
def test_new_prints_candidates(store, s0):
    proj = Project(project_id="ab12cd34", scenic_spot="雷峰塔")
    proj.legend_candidates = [Legend(title="白蛇传", summary="s",
                                     source_type="民间传说", sources=["x"])]
    store.create_project.return_value = proj
    s0.run.return_value = proj
    result = runner.invoke(app, ["new", "雷峰塔"])
    assert result.exit_code == 0
    assert "白蛇传" in result.output and "ab12cd34" in result.output

@patch("shanhai.cli.Settings", _stub_settings)
@patch("shanhai.cli.store")
def test_status(store):
    proj = Project(project_id="ab12cd34", scenic_spot="雷峰塔")
    proj.status = {"s0": "done", "s1": "done"}
    store.load.return_value = proj
    result = runner.invoke(app, ["status", "ab12cd34"])
    assert result.exit_code == 0 and "s1" in result.output
```

- [ ] **Step 2: 确认失败 → 实现**

```python
# src/shanhai/cli.py
import time
from pathlib import Path

import typer

from shanhai import store
from shanhai.config import Settings
from shanhai.providers.image import ImageClient
from shanhai.providers.llm import LLMClient
from shanhai.providers.tts import TTSClient
from shanhai.steps import (s0_legend, s1_script, s2_storyboard, s3_characters,
                           s4_pages, s5_audio, s6_compose)

app = typer.Typer(help="山海:景区传说有声连环画生成器(CLI 骨架)")


def _clients(s: Settings) -> tuple[LLMClient, ImageClient, TTSClient]:
    img_base, img_key = s.image_endpoint
    tts_base, tts_key = s.tts_endpoint
    return (LLMClient(s.base_url, s.api_key, s.llm_model),
            ImageClient(img_base, img_key, s.image_model, s.image_api_mode),
            TTSClient(tts_base, tts_key, s.tts_model))


def _apply_params(p, minutes: int, audience: str, tone: str, style: str) -> None:
    p.params.duration_min = minutes
    p.params.audience = audience
    p.params.tone = tone
    p.style_preset = style


@app.command()
def new(scenic_spot: str, minutes: int = 3, audience: str = "大众", tone: str = "温情",
        style: str = "kids_picture_book", story_file: Path | None = None):
    s = Settings()
    llm, _, _ = _clients(s)
    p = store.create_project(scenic_spot)
    _apply_params(p, minutes, audience, tone, style)
    if story_file:
        p = s0_legend.from_text(p, llm, story_file.read_text(encoding="utf-8"))
    else:
        p = s0_legend.run(p, llm)
        for i, c in enumerate(p.legend_candidates, 1):
            typer.echo(f"  [{i}] {c.title}({c.source_type})- {c.summary[:60]}…")
    store.save(p)
    typer.echo(f"project_id: {p.project_id}")
    if not story_file:
        typer.echo(f"下一步: shanhai pick {p.project_id} <序号>")


@app.command()
def pick(project_id: str, index: int):
    p = store.load(project_id)
    p.legend = p.legend_candidates[index - 1]
    store.save(p)
    typer.echo(f"已选定:{p.legend.title}")


@app.command()
def step(project_id: str, name: str):
    s = Settings()
    llm, image, tts = _clients(s)
    p = store.load(project_id)
    workdir = store.project_dir(project_id)
    t0 = time.time()
    if name == "s1":
        p = s1_script.run(p, llm)
    elif name == "s2":
        p = s2_storyboard.run(p, llm)
    elif name == "s3":
        p = s3_characters.run(p, llm, image, workdir, s.image_size)
    elif name == "s4":
        p = s4_pages.run(p, image, workdir, s.image_size)
    elif name == "s5":
        p = s5_audio.run(p, tts, s.tts_voice, workdir)
    elif name == "s6":
        p = s6_compose.run(p, workdir)
    else:
        raise typer.BadParameter(f"未知步骤: {name}")
    store.save(p)
    typer.echo(f"{name} -> {p.status.get(name)}({time.time() - t0:.0f}s)")


@app.command()
def run(scenic_spot: str, minutes: int = 3, audience: str = "大众", tone: str = "温情",
        style: str = "kids_picture_book", story_file: Path | None = None):
    """快速模式:自动选第一个候选传说,一路跑到 MP4。"""
    s = Settings()
    llm, image, tts = _clients(s)
    p = store.create_project(scenic_spot)
    _apply_params(p, minutes, audience, tone, style)
    workdir = store.project_dir(p.project_id)
    total0 = time.time()
    if story_file:
        p = s0_legend.from_text(p, llm, story_file.read_text(encoding="utf-8"))
    else:
        p = s0_legend.run(p, llm)
        if not p.legend_candidates:
            typer.echo("没有检索到可靠传说,请用 --story-file 提供自备故事")
            raise typer.Exit(1)
        p.legend = p.legend_candidates[0]
    store.save(p)
    stages = [("s1", lambda: s1_script.run(p, llm)),
              ("s2", lambda: s2_storyboard.run(p, llm)),
              ("s3", lambda: s3_characters.run(p, llm, image, workdir, s.image_size)),
              ("s4", lambda: s4_pages.run(p, image, workdir, s.image_size)),
              ("s5", lambda: s5_audio.run(p, tts, s.tts_voice, workdir)),
              ("s6", lambda: s6_compose.run(p, workdir))]
    for name, fn in stages:
        t0 = time.time()
        fn()
        store.save(p)
        typer.echo(f"{name} 完成({time.time() - t0:.0f}s)")
    typer.echo(f"总耗时 {(time.time() - total0) / 60:.1f} 分钟")
    typer.echo(f"成片: {p.output.get('mp4')}")


@app.command()
def status(project_id: str):
    p = store.load(project_id)
    typer.echo(f"景区: {p.scenic_spot}  画风: {p.style_preset}")
    for k in ("s0", "s1", "s2", "s3", "s4", "s5", "s6"):
        typer.echo(f"  {k}: {p.status.get(k, 'pending')}")
    if p.output:
        typer.echo(f"  输出: {p.output}")
```

- [ ] **Step 3: 测试通过 + 全量回归 → 提交**

```bash
uv run pytest -v && uv run ruff check .
git add src/shanhai/cli.py tests/test_cli.py && git commit -m "feat: typer CLI (new/pick/step/run/status)"
```

## Task 18: 端到端验收(真实 API,人工确认)

**执行代理: Opus + 人工验收** | M1 出口

**Files:**
- Create: `docs/decisions/0002-m1-e2e.md`

- [ ] **Step 1: 快速模式跑通雷峰塔**

```bash
time uv run shanhai run 雷峰塔 --minutes 1
```

预期:打印每步耗时,产出 `projects/<id>/output/final.mp4`。1 分钟片(8~10 页)总耗时应明显低于 PRD 的 15 分钟线(那是 3 分钟片的要求)。

- [ ] **Step 2: 机检产物**

```bash
ffprobe -v error -show_entries stream=codec_type -of csv=p=0 projects/<id>/output/final.mp4
ffprobe -v error -show_entries format=duration -of csv=p=0 projects/<id>/output/final.mp4
```

预期:`video` + `audio` 两条流;总时长 ≈ 2.5s 片头 + Σ(每页音频+0.5s) + 3s 片尾(±5%)。

- [ ] **Step 3: 断点续跑验证(可重入,PRD §6)**

```bash
uv run shanhai step <id> s4 && uv run shanhai step <id> s6
```

预期:s4 全部跳过(已 confirmed,秒回),s6 重新合成成功。

- [ ] **Step 4: 人工看片 + 记录验收**

人工确认:① 播放正常、音画同步;② 每页水印与片尾 AI 标识在;③ 文案连听能懂;④ 角色跨页一致性主观打分。把结果、真实耗时、每步成本估算写入 `docs/decisions/0002-m1-e2e.md`,遗留问题开列 M2 待办(检索 API、多音字、Web UI、Ken Burns、PDF 导出)。

```bash
git add docs/decisions/0002-m1-e2e.md && git commit -m "docs: M1 e2e acceptance record"
```

---

## Self-Review 记录

- **Spec 覆盖:** S0~S6 各有任务(T9/10/11/12/14/15/16);铁律(程序排版文字)在 T13/T14;AI 标识在 T13(水印)+T16(片尾);重试 ≤2 在 T14;+0.5s 缓冲与 -16 LUFS 在 T16;可重入在 T7/T14/T15/T18;主要角色 ≤4 在 T12。**有意延后(骨架局限,已在对应模块 docstring 标注):** 联网检索(F0)、SSML 多音字(F5)、Ken Burns、PDF/图片序列导出(F6 的 ②③)、成本上限提示(§6)——全部进 M2 待办。
- **类型一致性:** `ImageClient.generate(prompt, size, references)` 在 T3 定义,T4/T12/T14 按同签名调用;`probe_duration_ms` T15/T16 同签名;`TURNAROUND_TMPL`/`PAGE_TMPL` 由 spike 与 steps 共享单一事实源(T12/T14 收编)。
- **占位符扫描:** 无 TBD/TODO;所有代码步骤含完整代码。
