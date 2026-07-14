# 日式分格漫画排版(可选模式) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 S2(分镜)→S4(生图)加一个可选开关 `params.multi_panel`,打开后每页分镜可以拆成 1~4 个由 LLM 按剧情节奏决定格数的漫画格,格子独立生图后用纯 PIL 排版合成,支持一个"insert"特写格异形裁剪叠加在其它格子上面。

**Architecture:** 每格分镜(`Panel`)独立调用 `image.generate()`(复用现有 S3 角色三视图参考一致性机制),生成结果交给新模块 `paneling.compose_manga_page()` 按版式模板拼成一整页位图,拼版结果原样交给现有的 `typeset.compose_page()` 做统一满幅裁切——S5(配音)/S6(合成)/ffmpeg 完全不用改,下游看到的仍然是"一页一张图"。开关关闭(默认)时所有代码走原有单图路径,字节级行为不变。

**Tech Stack:** Python 3.12 / Pydantic / Pillow(PIL)/ pytest + respx(HTTP mock)/ FastAPI / React + TypeScript(Vite)

## Global Constraints

- 每页最多 4 格(`MAX_PANELS_PER_PAGE = 4`),LLM 提示词里声明 + S2 落盘前做防御性裁剪。
- 每页最多一个 `shot_type == "insert"` 格。
- 开关默认 `False`,不影响任何存量项目和现有单图路径的字节级行为。
- 分格排版是排版结构,和现有画风(国风水墨/儿童绘本/现代插画)是独立维度,不新增 `style_preset`。
- 拼版画布尺寸复用 `typeset.FRAME`(1920×1080),避免拼版结果被下游 cover-crop 意外裁掉内容。
- 幂等续跑沿用现状的**整页粒度**判断(`cell.status == "confirmed"` 且页面文件存在则跳过),不引入按格子的细粒度断点续跑。
- 所有新代码遵循仓库现有风格:中文注释、respx mock 测试网络调用、Pillow 纯函数可离线单测。

---

## Task 1: Schema — Panel 模型 + StoryboardCell.panels + GenerationParams.multi_panel

**Files:**
- Modify: `src/shanhai/schema.py`
- Test: `tests/test_schema.py`

**Interfaces:**
- Produces: `shanhai.schema.Panel`(字段 `visual_desc: str`、`shot_type: Literal["wide","medium","closeup","insert"] = "medium"`、`characters: list[str] = []`、`image: str = ""`);`StoryboardCell.panels: list[Panel]`(默认空列表);`GenerationParams.multi_panel: bool`(默认 `False`)。这三个是 Task 2~5 都要用到的类型,字段名和默认值必须与此处完全一致。

- [ ] **Step 1: 写失败测试**

在 `tests/test_schema.py` 顶部的 import 里加上 `Panel` 和 `GenerationParams`(原文件只 `from shanhai.schema import Legend, Project, StoryboardCell`,改成:
```python
from shanhai.schema import GenerationParams, Legend, Panel, Project, StoryboardCell
```
在文件末尾追加:
```python
def test_generation_params_multi_panel_default_false():
    p = Project(project_id="ab12", scenic_spot="雷峰塔")
    assert p.params.multi_panel is False


def test_storyboard_cell_panels_default_empty():
    c = StoryboardCell(index=1, scene_ref="1-1", visual_desc="x",
                       characters=[], caption="c", emotion="宁静")
    assert c.panels == []


def test_storyboard_cell_panels_roundtrip():
    c = StoryboardCell(index=1, scene_ref="1-1", visual_desc="x", characters=[],
                       caption="c", emotion="宁静",
                       panels=[Panel(visual_desc="v1", shot_type="closeup", characters=["白娘子"])])
    c2 = StoryboardCell.model_validate_json(c.model_dump_json())
    assert len(c2.panels) == 1
    assert c2.panels[0].shot_type == "closeup"
    assert c2.panels[0].characters == ["白娘子"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/nativeas/Work/shanhai && uv run pytest tests/test_schema.py -v`
Expected: FAIL,报 `ImportError: cannot import name 'Panel' from 'shanhai.schema'`(或 `GenerationParams` 缺 `multi_panel` 字段导致 `AttributeError`)。

- [ ] **Step 3: 实现**

打开 `src/shanhai/schema.py`,在 `class StoryboardCell(BaseModel):` 定义**之前**插入新的 `Panel` 模型:
```python
class Panel(BaseModel):
    """分格漫画的单个格子(仅 params.multi_panel=True 时使用)。"""
    visual_desc: str
    shot_type: Literal["wide", "medium", "closeup", "insert"] = "medium"
    characters: list[str] = Field(default_factory=list)
    image: str = ""  # S4 填入,该格自己的生成图相对路径
```

在 `StoryboardCell` 类内,`status: Literal["draft", "confirmed", "failed"] = "draft"` 这一行**之后**新增一行:
```python
    panels: list[Panel] = Field(default_factory=list)  # 空 = 单图模式(现状不变)
```

在 `GenerationParams` 类内,`speed: float = 1.0` 这一行**之后**新增一行:
```python
    multi_panel: bool = False
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /Users/nativeas/Work/shanhai && uv run pytest tests/test_schema.py -v`
Expected: PASS(全部测试,含新增 3 条)

- [ ] **Step 5: Commit**

```bash
cd /Users/nativeas/Work/shanhai
git add src/shanhai/schema.py tests/test_schema.py
git commit -m "feat(schema): 新增 Panel 模型 + StoryboardCell.panels + GenerationParams.multi_panel"
```

---

## Task 2: paneling.py —— 排版合成模块

**Files:**
- Create: `src/shanhai/paneling.py`
- Test: `tests/test_paneling.py`

**Interfaces:**
- Consumes: `shanhai.schema.Panel`(Task 1);`shanhai.typeset.FRAME`(现有常量,`(1920, 1080)`)。
- Produces: `paneling.compose_manga_page(panel_imgs: list[bytes], panels: list[Panel]) -> bytes`——Task 4(S4)直接调用这个函数,两个参数**等长、按格子顺序一一对应**(调用方已按失败跳过对齐好)。空列表时 `raise ValueError`。返回值是一张 PNG 编码的完整页面图片(PIL `Image.save(..., "PNG")` 后的 bytes),尺寸恒为 `shanhai.typeset.FRAME`。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_paneling.py`:
```python
import io

import pytest
from PIL import Image

from shanhai.paneling import compose_manga_page
from shanhai.schema import Panel
from shanhai.typeset import FRAME


def _solid(color: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (200, 200), color).save(buf, "PNG")
    return buf.getvalue()


def test_compose_manga_page_empty_raises():
    with pytest.raises(ValueError, match="没有可用"):
        compose_manga_page([], [])


def test_compose_manga_page_single_panel_fills_frame():
    img = compose_manga_page([_solid((255, 0, 0))], [Panel(visual_desc="v", shot_type="wide")])
    out = Image.open(io.BytesIO(img))
    assert out.size == FRAME
    r, g, b = out.getpixel((FRAME[0] // 2, FRAME[1] // 2))
    assert r > 200 and g < 50 and b < 50  # 唯一一格铺满全页,中心点应是红色


def test_compose_manga_page_four_panels_land_in_quadrants():
    colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]
    imgs = [_solid(c) for c in colors]
    panels = [Panel(visual_desc=f"v{i}", shot_type="medium") for i in range(4)]
    out = Image.open(io.BytesIO(compose_manga_page(imgs, panels)))
    assert out.size == FRAME
    w, h = FRAME
    points = [(w // 4, h // 4), (3 * w // 4, h // 4), (w // 4, 3 * h // 4), (3 * w // 4, 3 * h // 4)]
    for (x, y), expect in zip(points, colors):
        assert out.getpixel((x, y)) == expect  # 纯色格子缩放/裁切后中心点应仍是原色


def test_compose_manga_page_insert_overlays_host():
    host = _solid((10, 10, 10))
    insert = _solid((255, 255, 255))
    panels = [Panel(visual_desc="host", shot_type="wide"),
              Panel(visual_desc="closeup", shot_type="insert")]
    out = Image.open(io.BytesIO(compose_manga_page([host, insert], panels)))
    w, h = FRAME
    assert out.getpixel((40, 40)) == (10, 10, 10)          # 宿主格左上角未被叠加覆盖
    near = out.getpixel((w - 150, h - 150))
    assert near[0] > 200 and near[1] > 200 and near[2] > 200  # 宿主格右下角能采到叠加的白色特写


def test_compose_manga_page_lone_insert_falls_back_to_full_page():
    # 唯一一格标了 insert 时没有宿主格可叠加,应退化为普通铺满整页,不报错
    img = compose_manga_page([_solid((0, 200, 0))], [Panel(visual_desc="v", shot_type="insert")])
    out = Image.open(io.BytesIO(img))
    assert out.size == FRAME
    assert out.getpixel((FRAME[0] // 2, FRAME[1] // 2))[1] > 150
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/nativeas/Work/shanhai && uv run pytest tests/test_paneling.py -v`
Expected: FAIL,报 `ModuleNotFoundError: No module named 'shanhai.paneling'`

- [ ] **Step 3: 实现**

创建 `src/shanhai/paneling.py`:
```python
"""日式分格漫画排版:纯 PIL 合成,无网络依赖,可完全离线单测。
把 N 张独立生成的格子图按预定义版式拼成一整页;shot_type=="insert" 的格子
做圆角裁剪叠加在其它格子上面(漫画常见的嵌入式特写手法)。"""
import io

from PIL import Image, ImageDraw

from shanhai.schema import Panel
from shanhai.typeset import FRAME

GUTTER = 12  # 格间装订线宽度(像素)
BORDER = (20, 16, 12)  # 画布底色 = 装订线颜色(深墨色)
INSET_SCALE = 0.55  # 特写叠加格相对宿主格的边长比例
INSET_MARGIN = 24  # 特写叠加格距宿主格边缘的留白(像素)
INSET_RADIUS = 24  # 特写叠加格圆角半径(像素)
OUTLINE_COLOR = (245, 240, 228)  # 特写叠加格描边颜色(米宣纸色)
OUTLINE_WIDTH = 6  # 特写叠加格描边宽度(像素)

# 每种"常规格数"(不含 insert 格)对应的归一化矩形列表 (x0, y0, x1, y1),0~1 比例坐标。
LAYOUTS: dict[int, list[tuple[float, float, float, float]]] = {
    1: [(0.0, 0.0, 1.0, 1.0)],
    2: [(0.0, 0.0, 1.0, 0.5), (0.0, 0.5, 1.0, 1.0)],
    3: [(0.0, 0.0, 1.0, 0.55), (0.0, 0.55, 0.5, 1.0), (0.5, 0.55, 1.0, 1.0)],
    4: [(0.0, 0.0, 0.5, 0.5), (0.5, 0.0, 1.0, 0.5), (0.0, 0.5, 0.5, 1.0), (0.5, 0.5, 1.0, 1.0)],
}


def _rect_px(rect: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = rect
    w, h = FRAME
    return (round(x0 * w), round(y0 * h), round(x1 * w), round(y1 * h))


def _cover(img: Image.Image, w: int, h: int) -> Image.Image:
    """缩放并裁剪填满 (w, h),cover-fit,居中裁切。"""
    scale = max(w / img.width, h / img.height)
    resized = img.resize((max(round(img.width * scale), w), max(round(img.height * scale), h)))
    left = (resized.width - w) // 2
    top = (resized.height - h) // 2
    return resized.crop((left, top, left + w, top + h))


def _rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    return mask


def compose_manga_page(panel_imgs: list[bytes], panels: list[Panel]) -> bytes:
    """panel_imgs 与 panels 等长、按格子顺序一一对应(调用方已按生成失败跳过对齐好两个列表)。
    返回一张 PNG 编码的整页图片,尺寸恒为 FRAME。"""
    n = len(panel_imgs)
    if n == 0:
        raise ValueError("没有可用的格子图片")

    insert_idx = next((i for i, p in enumerate(panels) if p.shot_type == "insert"), None)
    if insert_idx is not None and n == 1:
        insert_idx = None  # 唯一一格标了 insert 也没有宿主格可叠加,退化为普通整页

    regular = [(img, p) for i, (img, p) in enumerate(zip(panel_imgs, panels)) if i != insert_idx]
    layout = LAYOUTS[len(regular)]

    canvas = Image.new("RGB", FRAME, BORDER)
    for (data, _), rect in zip(regular, layout):
        x0, y0, x1, y1 = _rect_px(rect)
        w, h = x1 - x0 - GUTTER, y1 - y0 - GUTTER
        img = Image.open(io.BytesIO(data)).convert("RGB")
        canvas.paste(_cover(img, w, h), (x0 + GUTTER // 2, y0 + GUTTER // 2))

    if insert_idx is not None:
        hx0, hy0, hx1, hy1 = _rect_px(layout[0])  # 宿主格固定选常规格里版式模板的第一格
        iw = round((hx1 - hx0) * INSET_SCALE)
        ih = round((hy1 - hy0) * INSET_SCALE)
        img = Image.open(io.BytesIO(panel_imgs[insert_idx])).convert("RGB")
        inset = _cover(img, iw, ih)
        mask = _rounded_mask((iw, ih), INSET_RADIUS)
        pos = (hx1 - iw - INSET_MARGIN, hy1 - ih - INSET_MARGIN)
        outline = Image.new("RGB", (iw + OUTLINE_WIDTH * 2, ih + OUTLINE_WIDTH * 2), OUTLINE_COLOR)
        canvas.paste(outline, (pos[0] - OUTLINE_WIDTH, pos[1] - OUTLINE_WIDTH))
        canvas.paste(inset, pos, mask)

    buf = io.BytesIO()
    canvas.save(buf, "PNG")
    return buf.getvalue()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /Users/nativeas/Work/shanhai && uv run pytest tests/test_paneling.py -v`
Expected: PASS(全部 5 条)

- [ ] **Step 5: Commit**

```bash
cd /Users/nativeas/Work/shanhai
git add src/shanhai/paneling.py tests/test_paneling.py
git commit -m "feat(paneling): 新增分格漫画排版合成模块"
```

---

## Task 3: S2(分镜)—— 分格提示词 + 硬上限裁剪

**Files:**
- Modify: `src/shanhai/steps/s2_storyboard.py`
- Test: `tests/test_s2.py`

**Interfaces:**
- Consumes: `project.params.multi_panel`(Task 1)。
- Produces: 无新增公开函数;`s2_storyboard.MAX_PANELS_PER_PAGE = 4` 常量供 Task 2/4 引用文档说明用(不强制被其它模块 import,只是防御性裁剪本地使用)。

- [ ] **Step 1: 写失败测试**

在 `tests/test_s2.py` 顶部 import 区,`from shanhai.steps import s2_storyboard` 保持不变(已存在),追加一行:
```python
from shanhai.schema import Panel
```
在文件末尾追加:
```python
@respx.mock
def test_s2_single_page_mode_omits_panel_rules_in_system_prompt():
    cells = {"cells": [
        {"index": 1, "scene_ref": "1-1", "visual_desc": "断桥", "characters": [],
         "caption": "初遇。", "emotion": "宁静"}]}
    route = respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, json={
        "choices": [{"message": {"content": json.dumps(cells, ensure_ascii=False)}}]}))
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.params.duration_min = 1
    p.script = Script(title="t", theme="th", acts=[], characters=[])
    s2_storyboard.run(p, LLMClient(BASE, "sk", "m"))
    sent = json.loads(route.calls[0].request.content)
    assert "分格" not in sent["messages"][0]["content"]


@respx.mock
def test_s2_multi_panel_includes_panel_rules_in_system_prompt():
    cells = {"cells": [
        {"index": 1, "scene_ref": "1-1", "visual_desc": "断桥", "characters": [],
         "caption": "初遇。", "emotion": "宁静", "panels": []}]}
    route = respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, json={
        "choices": [{"message": {"content": json.dumps(cells, ensure_ascii=False)}}]}))
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.params.duration_min = 1
    p.params.multi_panel = True
    p.script = Script(title="t", theme="th", acts=[], characters=[])
    s2_storyboard.run(p, LLMClient(BASE, "sk", "m"))
    sent = json.loads(route.calls[0].request.content)
    system_msg = sent["messages"][0]["content"]
    assert "分格" in system_msg and "insert" in system_msg


@respx.mock
def test_s2_multi_panel_clamps_panels_to_hard_cap():
    cells = {"cells": [
        {"index": 1, "scene_ref": "1-1", "visual_desc": "断桥", "characters": ["白素贞"],
         "caption": "初遇。", "emotion": "宁静",
         "panels": [{"visual_desc": f"格{i}", "shot_type": "medium", "characters": ["白素贞"]}
                    for i in range(6)]}]}  # LLM 违规给了 6 格,应被裁到 4
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, json={
        "choices": [{"message": {"content": json.dumps(cells, ensure_ascii=False)}}]}))
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.params.duration_min = 1
    p.params.multi_panel = True
    p.script = Script(title="t", theme="th", acts=[], characters=[])
    p = s2_storyboard.run(p, LLMClient(BASE, "sk", "m"))
    assert len(p.storyboard[0].panels) == s2_storyboard.MAX_PANELS_PER_PAGE
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/nativeas/Work/shanhai && uv run pytest tests/test_s2.py -v`
Expected: FAIL —— 第一条断言 `"分格" not in ...` 本来就会通过(因为现状就没有),但第二条 `test_s2_multi_panel_includes_panel_rules...` 会失败(`AssertionError`,因为 `SYSTEM` 目前不含分格规则、也不含"insert"),第三条会因为 `MAX_PANELS_PER_PAGE` 不存在报 `AttributeError`。

- [ ] **Step 3: 实现**

打开 `src/shanhai/steps/s2_storyboard.py`,在 `PAGE_TARGETS`/`EMOTIONS` 常量下方新增:
```python
MAX_PANELS_PER_PAGE = 4  # 每页分格上限,防成本失控

PANEL_RULES = """
分格模式:每页可以拆成 1~4 个格子(panels 字段),按剧情节奏决定格数——
平静场景可以只给 1 格(等价于铺满整页),高潮或转折场景给 3~4 格,不必每页都用满格数。
每个格子填写:
- visual_desc:该格自己的构图/景别/氛围(不是整页笼统描述)
- shot_type:wide(远景)/medium(中景)/closeup(特写)/insert(嵌入式特写叠加格,
  漫画里常见的裁成异形叠在其它格子上面的手法)
- characters:该格实际出现的角色,可以是页面角色的子集
每页最多一个 insert 格,只在情绪转折或关键台词处使用,不是每页都要有。
panels 数量最多 4 个,超出会被截断,请自行控制在这个范围内。"""
```

修改 `run()` 函数,把:
```python
def run(project: Project, llm: LLMClient) -> Project:
    if project.script is None:
        raise ValueError("先完成 S1")
    lo, hi = PAGE_TARGETS[project.params.duration_min]
    user = (f"页数要求:{lo}~{hi} 页。\n剧本 JSON:\n"
            + project.script.model_dump_json(indent=1))
    project.storyboard = llm.structured(SYSTEM, user, _Cells).cells
```
改成:
```python
def run(project: Project, llm: LLMClient) -> Project:
    if project.script is None:
        raise ValueError("先完成 S1")
    lo, hi = PAGE_TARGETS[project.params.duration_min]
    system = SYSTEM + (PANEL_RULES if project.params.multi_panel else "")
    user = (f"页数要求:{lo}~{hi} 页。\n剧本 JSON:\n"
            + project.script.model_dump_json(indent=1))
    project.storyboard = llm.structured(system, user, _Cells).cells
```

在下方现有的两个 `for cell in project.storyboard:` 循环(index 重排 + 剔除旁白角色)之后,再加一个防御性裁剪循环:
```python
    for cell in project.storyboard:  # 防御:LLM 可能无视上限,强制裁到 MAX_PANELS_PER_PAGE
        cell.panels = cell.panels[:MAX_PANELS_PER_PAGE]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /Users/nativeas/Work/shanhai && uv run pytest tests/test_s2.py -v`
Expected: PASS(全部测试,含新增 3 条)

- [ ] **Step 5: Commit**

```bash
cd /Users/nativeas/Work/shanhai
git add src/shanhai/steps/s2_storyboard.py tests/test_s2.py
git commit -m "feat(s2): multi_panel 开关追加分格提示词 + 硬上限裁剪"
```

---

## Task 4: S4(生图)—— 逐格生成 + 排版合成

**Files:**
- Modify: `src/shanhai/steps/s4_pages.py`
- Test: `tests/test_s4.py`

**Interfaces:**
- Consumes: `shanhai.paneling.compose_manga_page(panel_imgs, panels)`(Task 2);`cell.panels: list[Panel]`(Task 1);现有 `ImageClient.generate(prompt, size, references) -> bytes`、`_downscaled_ref(src, cache_dir) -> Path`、`typeset.compose_page(art, out) -> None`(均已存在,不改签名)。
- Produces: 无新增公开函数;`cell.panels` 非空时,`_render_cell` 内部分支到新的私有函数 `_render_panel_cell`,行为对外透明——外部只需知道 `run()` 的输入输出契约不变(`project.storyboard[i].image`/`.status` 语义不变)。

- [ ] **Step 1: 写失败测试**

在 `tests/test_s4.py` 顶部 import 区,`from shanhai.schema import CharacterCard, Project, Script, StoryboardCell` 改成:
```python
from shanhai.schema import CharacterCard, Panel, Project, Script, StoryboardCell
```
在 `_project(tmp_path)` 函数**之后**新增一个辅助函数:
```python
def _multi_panel_project(tmp_path: Path, n_panels: int = 2) -> Project:
    p = _project(tmp_path)
    p.storyboard[0].panels = [
        Panel(visual_desc=f"格{i}", shot_type="medium", characters=["白素贞"])
        for i in range(1, n_panels + 1)
    ]
    return p
```
在文件末尾追加:
```python
def test_s4_multi_panel_generates_one_call_per_panel_and_composes(tmp_path: Path):
    image = MagicMock(); image.generate.return_value = _png()
    p = s4_pages.run(_multi_panel_project(tmp_path, 3), image, tmp_path, "1536x1024")
    assert image.generate.call_count == 3
    assert p.storyboard[0].status == "confirmed"
    assert (tmp_path / "pages" / "page_01.png").exists()
    assert (tmp_path / "pages" / "page_01_panel1.png").exists()
    assert (tmp_path / "pages" / "page_01_panel3.png").exists()


def test_s4_multi_panel_partial_failure_still_composes(tmp_path: Path):
    # 3 格,第 2 格全部 3 次尝试都失败,第 1/3 格各一次成功——整页仍应 confirmed,
    # 排版按实际拿到的 2 格算(不拿占位图硬凑)。
    image = MagicMock()
    calls = {"n": 0}

    def side_effect(*a, **kw):
        calls["n"] += 1
        if calls["n"] in (2, 3, 4):
            raise ImageGenError("boom")
        return _png()

    image.generate.side_effect = side_effect
    p = s4_pages.run(_multi_panel_project(tmp_path, 3), image, tmp_path, "1536x1024")
    assert p.storyboard[0].status == "confirmed"
    assert image.generate.call_count == 5  # 1(格1成功) + 3(格2三次失败) + 1(格3成功)
    assert (tmp_path / "pages" / "page_01.png").exists()
    assert not (tmp_path / "pages" / "page_01_panel2.png").exists()


def test_s4_multi_panel_all_fail_marks_cell_failed(tmp_path: Path):
    image = MagicMock(); image.generate.side_effect = ImageGenError("boom")
    p = s4_pages.run(_multi_panel_project(tmp_path, 2), image, tmp_path, "1536x1024")
    assert p.storyboard[0].status == "failed"
    assert not (tmp_path / "pages" / "page_01.png").exists()


def test_s4_multi_panel_prompt_includes_shot_hint(tmp_path: Path):
    p = _multi_panel_project(tmp_path, 1)
    p.storyboard[0].panels[0].shot_type = "closeup"
    image = MagicMock(); image.generate.return_value = _png()
    s4_pages.run(p, image, tmp_path, "1536x1024")
    prompt = image.generate.call_args.args[0]
    assert "特写" in prompt


def test_s4_single_page_mode_unaffected(tmp_path: Path):
    # 回归:panels 为空时必须走原有单图路径,字节级行为不变
    image = MagicMock(); image.generate.return_value = _png()
    p = s4_pages.run(_project(tmp_path), image, tmp_path, "1536x1024")
    assert image.generate.call_count == 1
    assert p.storyboard[0].status == "confirmed"
    assert not (tmp_path / "pages" / "page_01_panel1.png").exists()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/nativeas/Work/shanhai && uv run pytest tests/test_s4.py -v`
Expected: FAIL —— 新增的 4 条多格测试会失败(目前 `cell.panels` 被完全忽略,`image.generate` 只会按现有单图路径被调用 1 次而非 2~3 次);`test_s4_single_page_mode_unaffected` 应该已经能过(现状本就如此),留作显式回归锚点。

- [ ] **Step 3: 实现**

打开 `src/shanhai/steps/s4_pages.py`,顶部 import 区新增:
```python
from shanhai import paneling
```
（放在现有 `from shanhai import typeset` 下面一行)

再把现有的 `from shanhai.schema import Project, StoryboardCell` 改成:
```python
from shanhai.schema import Panel, Project, StoryboardCell
```

在 `PAGE_TMPL` 常量**之后**新增:
```python
PANEL_TMPL = (
    "{style}。漫画格画面:{scene}。出场角色:{features}。{shot}。"
    "严格保持角色与参考图中的形象一致(外观特征、色彩、服饰或体表覆盖物)。"
    "画面中不要出现任何文字。"
)

SHOT_HINTS = {
    "wide": "远景构图,交代场景全貌",
    "medium": "中景构图,人物与环境兼顾",
    "closeup": "特写镜头,聚焦面部表情与细节",
    "insert": "特写镜头,聚焦面部表情与细节",
}
```

在 `_render_cell` 函数**之前**新增两个私有函数:
```python
def _panel_prompt(panel: Panel, style: str, cards: dict) -> tuple[str, list]:
    present = [cards[n] for n in panel.characters if n in cards]
    features = ";".join(f"{c.name}({c.feature_prompt})" for c in present) or "无固定角色"
    shot = SHOT_HINTS.get(panel.shot_type, SHOT_HINTS["medium"])
    prompt = PANEL_TMPL.format(style=style, scene=panel.visual_desc, features=features, shot=shot)
    return prompt, present


def _render_panel_cell(cell: StoryboardCell, style: str, cards: dict, image: ImageClient,
                       image_size: str, workdir: Path, pages_dir: Path, ref_cache: Path) -> None:
    imgs: list[bytes] = []
    kept_panels = []
    for i, panel in enumerate(cell.panels, 1):
        prompt, present = _panel_prompt(panel, style, cards)
        for attempt in range(MAX_ATTEMPTS):
            try:
                refs = [_downscaled_ref(workdir / c.turnaround_image, ref_cache)
                        for c in present if c.turnaround_image]
                art = image.generate(prompt, size=image_size, references=refs or None)
                out = pages_dir / f"page_{cell.index:02d}_panel{i}.png"
                out.write_bytes(art)
                panel.image = str(out.relative_to(workdir))
                imgs.append(art)
                kept_panels.append(panel)
                break
            except Exception:  # noqa: BLE001 单格失败不拖垮整页,重试后放弃该格
                if attempt == MAX_ATTEMPTS - 1:
                    pass
    if not imgs:
        cell.status = "failed"
        return
    composed = paneling.compose_manga_page(imgs, kept_panels)
    out = pages_dir / f"page_{cell.index:02d}.png"
    typeset.compose_page(composed, out)
    cell.image = str(out.relative_to(workdir))
    cell.status = "confirmed"
```

修改 `_render_cell` 函数,在函数体**最开头**、`present = [cards[n] for n in cell.characters if n in cards]` 这一行**之前**插入 3 行分支,其余原有代码原样保留。修改后的完整函数(与改动前相比,只多了开头的 `if cell.panels: ... return` 这 3 行,后面的 `for attempt in range(MAX_ATTEMPTS):` 整段一字不改):
```python
def _render_cell(cell: StoryboardCell, style: str, cards: dict, image: ImageClient,
                 image_size: str, workdir: Path, pages_dir: Path, ref_cache: Path) -> None:
    if cell.panels:
        _render_panel_cell(cell, style, cards, image, image_size, workdir, pages_dir, ref_cache)
        return
    present = [cards[n] for n in cell.characters if n in cards]
    features = ";".join(f"{c.name}({c.feature_prompt})" for c in present) or "无固定角色"
    prompt = PAGE_TMPL.format(style=style, scene=cell.visual_desc, features=features)
    out = pages_dir / f"page_{cell.index:02d}.png"
    for attempt in range(MAX_ATTEMPTS):
        try:
            refs = [_downscaled_ref(workdir / c.turnaround_image, ref_cache)
                    for c in present if c.turnaround_image]
            art = image.generate(prompt, size=image_size, references=refs or None)
            typeset.compose_page(art, out)
            cell.image = str(out.relative_to(workdir))
            cell.status = "confirmed"
            return
        except Exception:  # noqa: BLE001 单页失败不拖垮整轮,重试后标 failed
            if attempt == MAX_ATTEMPTS - 1:
                cell.status = "failed"
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /Users/nativeas/Work/shanhai && uv run pytest tests/test_s4.py -v`
Expected: PASS(全部测试,含新增 5 条 —— 4 条多格 + 1 条单图回归锚点)

- [ ] **Step 5: 运行全量回归**

Run: `cd /Users/nativeas/Work/shanhai && uv run pytest -q`
Expected: 全部通过,无既有测试被破坏(单图路径逻辑未改动,只是在函数最前面加了一个提前 return 分支)。

- [ ] **Step 6: Commit**

```bash
cd /Users/nativeas/Work/shanhai
git add src/shanhai/steps/s4_pages.py tests/test_s4.py
git commit -m "feat(s4): 分格模式下逐格生图 + 调用 paneling 排版合成"
```

---

## Task 5: API 层 —— NewProject.multi_panel 透传

**Files:**
- Modify: `src/shanhai/api.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `Project.params.multi_panel`(Task 1)。
- Produces: `POST /api/projects` 请求体新增可选字段 `multi_panel: bool`(默认 `False`),写入 `Project.params.multi_panel`,前端(Task 6)据此传参。

- [ ] **Step 1: 写失败测试**

在 `tests/test_api.py` 里找到已有的 `test_create_project_sets_owner_to_current_user`(在"归属 / 队列 / 取消(Phase 2)"分区下),在它**之后**追加:
```python
@patch("shanhai.api._pipeline")
@patch("shanhai.api.Settings")
@patch("shanhai.api.store.save")
@patch("shanhai.api.store.create_project")
def test_create_project_passes_multi_panel(mock_create, _save, _settings, _pipe):
    p = Project(project_id="mpid01", scenic_spot="花果山")
    mock_create.return_value = p
    r = client.post("/api/projects",
                    json={"scenic_spot": "花果山", "minutes": 1, "multi_panel": True})
    assert r.status_code == 200
    api._JOBS["mpid01"].result(timeout=2)
    assert p.params.multi_panel is True


@patch("shanhai.api._pipeline")
@patch("shanhai.api.Settings")
@patch("shanhai.api.store.save")
@patch("shanhai.api.store.create_project")
def test_create_project_multi_panel_defaults_false(mock_create, _save, _settings, _pipe):
    p = Project(project_id="mpid02", scenic_spot="花果山")
    mock_create.return_value = p
    r = client.post("/api/projects", json={"scenic_spot": "花果山", "minutes": 1})
    assert r.status_code == 200
    api._JOBS["mpid02"].result(timeout=2)
    assert p.params.multi_panel is False
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/nativeas/Work/shanhai && uv run pytest tests/test_api.py -k multi_panel -v`
Expected: FAIL `test_create_project_passes_multi_panel` —— `assert p.params.multi_panel is True` 失败(实际是 `False`,因为请求体里的 `multi_panel` 目前被 `NewProject` 忽略)。第二条 `defaults_false` 会直接通过(现状默认就是 False),留作显式回归锚点。

- [ ] **Step 3: 实现**

打开 `src/shanhai/api.py`,找到 `class NewProject(BaseModel):`,在 `speed: float = 1.0` 这一行**之后**新增:
```python
    multi_panel: bool = False
```

找到 `create_project` 函数内部这一段(在 `with _JOBS_LOCK:` 块里):
```python
        p.params.voice = body.voice
        p.params.speed = body.speed
```
改成:
```python
        p.params.voice = body.voice
        p.params.speed = body.speed
        p.params.multi_panel = body.multi_panel
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /Users/nativeas/Work/shanhai && uv run pytest tests/test_api.py -k multi_panel -v`
Expected: PASS(2 条)

- [ ] **Step 5: 运行全量回归**

Run: `cd /Users/nativeas/Work/shanhai && uv run pytest -q`
Expected: 全部通过。

- [ ] **Step 6: Commit**

```bash
cd /Users/nativeas/Work/shanhai
git add src/shanhai/api.py tests/test_api.py
git commit -m "feat(api): NewProject 新增 multi_panel 字段透传"
```

---

## Task 6: 前端 —— 新建表单开关

**Files:**
- Modify: `web/src/types.ts`
- Modify: `web/src/api.ts`(仅确认现状,预计无需改动)
- Modify: `web/src/components/NewProjectForm.tsx`

**Interfaces:**
- Consumes: 后端 `POST /api/projects` 接受 `multi_panel: boolean`(Task 5)。
- Produces: 无新增导出——`NewProjectInput` 类型新增可选字段供 `api.create()` 透传(该函数已是 `(body: NewProjectInput) => fetch(...)` 的纯透传实现,`web/src/api.ts` 本身不需要改动)。

前端目前没有配置任何测试框架(`web/` 下无 vitest/jest 配置),此任务用 `tsc`/`build` + 人工浏览器核查代替自动化测试,和仓库现状一致。

- [ ] **Step 1: 类型定义**

打开 `web/src/types.ts`,找到 `NewProjectInput` 接口:
```typescript
export interface NewProjectInput {
  scenic_spot: string
  minutes: number
  audience: string
  tone: string
  style: string
  story?: string | null
  voice?: string
  speed?: number
}
```
改成:
```typescript
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
}
```

- [ ] **Step 2: 表单状态与提交**

打开 `web/src/components/NewProjectForm.tsx`,找到 state 声明区(`const [speed, setSpeed] = useState(1.0)` 这一行),在它**之后**新增:
```typescript
  const [multiPanel, setMultiPanel] = useState(false)
```

找到 `submit` 函数里的 `api.create({...})` 调用:
```typescript
      const { project_id } = await api.create({
        scenic_spot: spot.trim(),
        minutes,
        audience,
        tone,
        style,
        story: story.trim() || null,
        voice,
        speed,
      })
```
改成:
```typescript
      const { project_id } = await api.create({
        scenic_spot: spot.trim(),
        minutes,
        audience,
        tone,
        style,
        story: story.trim() || null,
        voice,
        speed,
        multi_panel: multiPanel,
      })
```

- [ ] **Step 3: 复选框 UI**

在 `<div className="grid grid-cols-2 gap-3">...</div>` 这个网格块的**闭合标签之后**(紧接在网格 `</div>` 后面、"自备故事"那个 `<div>` 之前)新增:
```tsx
      <div className="flex items-center gap-2">
        <input
          id="multi-panel"
          type="checkbox"
          checked={multiPanel}
          onChange={(e) => setMultiPanel(e.target.checked)}
          className="h-4 w-4 rounded border-line accent-cinnabar"
        />
        <label htmlFor="multi-panel" className="text-xs text-ink-soft">
          启用分格排版(日式分镜)
        </label>
      </div>
```

- [ ] **Step 4: 类型检查**

Run: `cd /Users/nativeas/Work/shanhai/web && npx tsc --noEmit`
Expected: 无输出(通过)

- [ ] **Step 5: 构建**

Run: `cd /Users/nativeas/Work/shanhai/web && npm run build`
Expected: `✓ built in ...`,无报错

- [ ] **Step 6: 浏览器人工核查**

用 Browser 工具起一个隔离验证实例(参照仓库 `.claude/launch.json` 里已有的 `shanhai-web-verify` 配置,端口 8099,不影响正在跑的其它实例),登录后打开"新建作品"表单,确认复选框"启用分格排版(日式分镜)"正常显示、可勾选;勾选后提交一次新建请求(用现有的 respx/mock 后端测试已覆盖了字段透传正确性,这里只核查 UI 交互本身没有渲染/交互 bug)。

- [ ] **Step 7: Commit**

```bash
cd /Users/nativeas/Work/shanhai
git add web/src/types.ts web/src/components/NewProjectForm.tsx
git commit -m "feat(web): 新建作品表单加分格排版开关"
```

---

## Task 7: 端到端真实验证(不写自动化测试,人工核查)

**Files:** 无代码改动,仅验证。

- [ ] **Step 1: 全量回归**

Run: `cd /Users/nativeas/Work/shanhai && uv run pytest -q`
Expected: 全部通过。

Run: `cd /Users/nativeas/Work/shanhai && uv run ruff check src/ tests/`
Expected: `All checks passed!`(若 `tests/test_s5.py` 报既有的、与本次改动无关的 F401,忽略即可,那是 pre-existing 问题)。

- [ ] **Step 2: 真实端到端生成**

在本机(或已确认空闲的 DGX)勾选"启用分格排版",完整生成一部 1 分钟档的短篇作品。人工检查:
- 至少一页的成图确实是多格排版(不是铺满整页的单张插画)
- 至少能在某一页看到 insert 特写格叠加的效果(圆角裁剪 + 描边,叠在别的格子上面)
- 最终合成的视频/音频不受影响(S5 配音、S6 合成正常完成,`pipeline` 状态到 `done`)

- [ ] **Step 3: 回归对照**

同一批参数、不勾选开关,重新生成一部对照作品,人工确认效果和改动前完全一致(每页仍是单张铺满整页的插画)。

---

## Self-Review 记录

**Spec 覆盖检查**:spec 的"开关与数据模型"→Task 1/5/6;"S2 改动"→Task 3;"S4 改动"→Task 4;"新模块 paneling.py"→Task 2;"成本护栏(硬上限 4 格)"→Task 3 的 `MAX_PANELS_PER_PAGE` 裁剪;"前端"→Task 6;"测试"→每个 Task 内嵌 TDD 步骤 + Task 7 端到端人工核查。spec 里提到的"幂等续跑沿用整页粒度"这条约束在 Task 4 里通过**不修改**现有 `run()` 里的 `pending` 判断逻辑天然满足,不需要单独任务。

**占位符扫描**:全文无 TBD/TODO,所有测试都是完整可运行代码,所有实现步骤都给出确切代码块。

**类型一致性检查**:`Panel`(Task 1 定义)在 Task 2(`compose_manga_page`)、Task 3(S2 填充)、Task 4(S4 消费)里字段名/类型全程一致(`visual_desc`/`shot_type`/`characters`/`image`);`GenerationParams.multi_panel`(Task 1)在 Task 3(`project.params.multi_panel`)、Task 5(`body.multi_panel`→`p.params.multi_panel`)、Task 6(前端 `multi_panel` 请求体字段名)三处命名完全一致;`paneling.compose_manga_page(panel_imgs, panels)` 的函数名与参数顺序在 Task 2 定义、Task 4 调用处一致。

---

Plan complete and saved to `docs/superpowers/plans/2026-07-14-manga-panel-layout.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
