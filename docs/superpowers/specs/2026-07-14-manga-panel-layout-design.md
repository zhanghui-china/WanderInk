# Spec:日式分格漫画排版(可选模式)

日期 2026-07-14,状态"已与用户确认设计,待写实现计划"

## 背景

目前 S4(漫画页生成)对每一页分镜(`StoryboardCell`)只生成**一张铺满整页的插画**,`typeset.compose_page()` 做的是单图 cover-crop,没有任何分格排版逻辑。S2(分镜)的提示词也明确写死"把剧本切分为一页一格的连环画分镜"(`s2_storyboard.py:SYSTEM`)——即"一页一格"是当前架构的既定设计,不是缺失。

用户想要的效果是日式漫画那种**一页多格**的分镜:有远景/中景/特写等不同景别的格子搭配,关键情绪节拍处会有一个**嵌入式特写格**(裁成异形、带边框)叠加在其他格子上面——这是漫画里常见的"insert 格"手法。

## 全局约束(与用户确认过的取舍)

- **新增开关,不替换默认行为**:现有的"一页一图"效果原样保留为默认,新效果是可选模式,不影响任何存量项目。
- **和现有画风(国风水墨/儿童绘本/现代插画)是独立维度**:分格排版是排版结构,画风是绘图美术风格,两者可以任意组合,不新增第四个 `style_preset`。
- **格数由 LLM 按剧情节奏决定**,不是每页固定格数:平静场景 1~2 格,高潮/转折场景 3~4 格,更贴近真实漫画节奏。硬上限 4 格,防成本失控。
- **特写叠加格(insert)由 LLM 选择性决定**,不是每页都有,只在情绪转折/关键台词处出现,每页最多一个。
- 生成耗时/成本会明显上升(一页从 1 次生图变成最多 4 次),这是用户已知且接受的取舍,靠"默认关闭 + 硬上限"兜底,不做进一步的成本优化(如缓存/批量生图)。

## 技术选型:为什么是"每格独立生图 + 程序化排版合成"

评估过三条路:

1. **(采用)每格独立生图,程序化合成**——每一格分镜各自调用一次 `image.generate()`(复用 S3 已有的角色三视图参考机制保持人物一致性),生成后用新的纯 PIL 排版模块把 N 张格子图拼成一整页(选版式模板 + 描边 + insert 格异形裁剪叠加)。
2. **一次性让图像模型画出整页分格漫画**——单个 prompt 描述"4 格漫画页,格1:xxx,格2:xxx",模型一次出整页图。
3. **用图像后端的 inpaint/局部编辑能力画进预设画布模板**。

选 1,原因:
- 方案 2/3 都要求图像后端本身理解结构化分格指令或支持区域编辑。shanhai 现有的多个后端(云端 gpt-image-2/StepFun、本地 ComfyUI shim)没有一个是专门训练做分格漫画排版的,格数、边框、文字溢出都不可控;方案 3 还额外依赖 wuzi 那边 ComfyUI 工作流的能力,不是 shanhai 能控制或跨后端稳定复现的东西。
- 方案 1 排版完全由代码控制,100% 确定;完全复用现有的角色一致性机制(S3 三视图参考图)和现有的单元素失败隔离模式(`s4_pages.py` 已有的 per-cell try/except、`s3_characters.py` 单角色失败退化不拖垮整体的先例),风险最低、和现有代码风格最贴合。

## 设计

### 开关与数据模型

`GenerationParams` 新增 `multi_panel: bool = False`,和 `duration_min`/`audience`/`tone` 同级,创建项目时随 `NewProject` 请求体一并传入(`api.py` 的 `create_project`/`NewProject` 模型)。

`StoryboardCell`(`schema.py`)新增:
```python
class Panel(BaseModel):
    visual_desc: str
    shot_type: Literal["wide", "medium", "closeup", "insert"] = "medium"
    characters: list[str] = []
    image: str = ""  # S4 填入,该格自己的生成图相对路径


class StoryboardCell(BaseModel):
    ...
    panels: list[Panel] = Field(default_factory=list)  # 空 = 单图模式(现状不变)
```
`panels` 为空是关闭开关时的常态,S4/typeset 走原有分支,字节级行为不变。`insert` 是特写叠加格的 shot_type,每页最多一个(S2 生成时约束,S4/paneling 侧也做兜底截断)。

### S2(分镜)改动

`multi_panel` 打开时,`SYSTEM` 提示词追加分格规则段:
- 每页 1~4 格(上限 4),按剧情节奏决定格数——平静场景可以只有 1 格(等价退化为现在的单图页),高潮/转折场景 3~4 格
- 每格给出 `visual_desc`(该格的构图/景别/氛围)、`shot_type`(wide/medium/closeup/insert)、`characters`(该格实际出现的角色,可以是页面角色的子集)
- 每页最多一个 `insert` 格,只在情绪转折/关键台词处使用,不强制每页都有
- 页级的 `caption`/`emotion`/`characters` 语义不变(旁白解说、页面主要角色集合),`panels` 是页面内部的视觉分解,不影响解说文案连贯性这条硬约束

复用同一个 `_Cells` 结构化输出 schema(`StoryboardCell` 已经带上 `panels` 字段),不用另开一套 pydantic 模型或另一次 LLM 调用。`multi_panel` 关闭时 `SYSTEM` 不追加这段,LLM 自然不会填 `panels`(和现状完全一致)。

### S4(生图)改动

`cell.panels` 为空:走现有代码路径,一字不改。

`cell.panels` 非空:
1. 逐格调用 `image.generate()`,prompt 由该格的 `visual_desc` + 该格 `characters` 的 `feature_prompt`(复用现有 `PAGE_TMPL` 的角色描述拼装方式)+ 按 `shot_type` 追加的取景提示语拼装(如 `closeup` → "特写镜头,聚焦面部表情与细节"),参考图仍传该格 `characters` 对应的三视图(复用 `_downscaled_ref` 现有逻辑)。生成结果落盘为 `pages/page_{index:02d}_panel{n}.png`(`Panel.image` 存相对路径),与页级图片同目录、按序号区分,方便人工核查排版问题。
2. 单格失败沿用现有 `MAX_ATTEMPTS` 重试;重试后仍失败的格子从排版里跳过(不拿占位图硬凑,版式模板按实际拿到的格数重新选择,类似"单角色三视图失败退化为纯文字特征"的既有降级哲学——单格失败不拖垮整页)。若一页里**所有**格子都失败,该页整体按现有 `_render_cell` 的失败语义处理:`cell.status = "failed"`,不产出该页图片(与今天单图模式失败时的行为一致)。
3. 全部格子处理完后,调用新模块 `paneling.compose_manga_page(panel_bytes, panels_meta) -> bytes` 做排版合成。
4. 合成结果按现有方式传给 `typeset.compose_page()` 做统一的满幅 cover-crop 并落盘为 `pages/page_{index:02d}.png`(页级路径不变)——**S5/S6/ffmpeg 完全不用改**,下游拿到的仍然是"一页一张图",`cell.image` 字段语义不变。
5. 幂等续跑:沿用现状的**整页粒度**判断(`cell.status == "confirmed" and cell.image 文件存在` 则跳过,见 `s4_pages.py` 的 `pending` 计算),不引入按格子的细粒度断点续跑——多格模式下重跑一页会重新生成该页全部格子,不做单格级别的缓存复用,保持和现有整页重试模型一致,避免引入新的状态机复杂度。

### 新模块:`src/shanhai/paneling.py`

纯函数、无网络依赖,可完全离线单测:
- `LAYOUTS: dict[int, ...]`:按实际拿到的格子数(1~4)预定义归一化的格子矩形位置(1 格 = 铺满,等同现状;2 格横切或竖切;3 格一大两小;4 格宫格)。
- `compose_manga_page(panel_imgs: list[bytes], panels: list[Panel]) -> bytes`:按 `LAYOUTS[len(panel_imgs)]` 把每格图片 cover-crop 贴入对应矩形,格间画分隔线/装订线;若某格 `shot_type == "insert"`,把该格图片做异形裁剪(圆角矩形或切角,统一走一个简单裁剪函数,不做画风差异化——留作后续可选打磨项)+ 描边,叠加在版面里最大的格子上,靠近角落。

### 前端

- `NewProjectForm.tsx`:新增复选框"启用分格排版(日式分镜)",绑定新的 `multi_panel` 字段,一并传入 `POST /api/projects`。
- `api.ts`/`types.ts`:`NewProject`(前端建项目参数类型)加 `multi_panel?: boolean`。
- 项目详情页(`ProjectDetail.tsx`)不需要改动——`page.image` 依旧是单张图片 URL,分格是生成阶段的内部实现细节,不需要在展示层暴露。

### 成本护栏

多格模式下单页生图次数最多到 4 次(现状的 4 倍),叠加本地 ComfyUI 已知会被共用机 GPU 争抢拖慢(本次会话早前实测)。护栏:默认关闭 + 硬上限 4 格。不在这次范围内做进一步优化(如格子间生图去重、批量请求),后续如果多格模式使用频繁再评估。

### 测试

- `tests/test_paneling.py`(新建):构造几张小尺寸假图,验证 `compose_manga_page` 在 1/2/3/4 格各情形下输出尺寸正确、格子位置符合版式模板、insert 格确实被裁剪叠加且未被主流程覆盖。纯离线单测,不涉及网络。
- `tests/test_s2.py`(如无则新建,若已有 S2 测试则追加):`multi_panel=True` 时 `SYSTEM` 提示词包含分格规则关键词(参照 `tests/test_s1.py` 的静态关键词断言先例);`multi_panel=False` 时不包含。
- `tests/test_s4.py`:扩展 respx mock 场景,覆盖 `cell.panels` 非空时逐格调用生图 + 调用 `paneling.compose_manga_page` 的路径;单格失败时验证不拖垮整页、版式按实际格数回退。

## 验证

1. `uv run pytest -q` 全量回归,新增测试通过、不影响现有单图路径的既有测试。
2. 手动在本机跑一次真实端到端:勾选"启用分格排版",完整生成一部短篇(1 分钟档),人工检查产出的页面图片确实有多格排版、能看到至少一次 insert 特写叠加效果,且解说音频/视频合成不受影响(验证 S5/S6 未被波及)。
3. 对照不勾选开关的正常项目,确认单图模式行为和改动前完全一致(回归检查)。

## 待实现文件清单

- `src/shanhai/schema.py` —— 新增 `Panel` 模型,`StoryboardCell.panels` 字段,`GenerationParams.multi_panel` 字段
- `src/shanhai/api.py` —— `NewProject` 请求体新增 `multi_panel`,`create_project` 透传
- `src/shanhai/steps/s2_storyboard.py` —— `SYSTEM` 提示词按开关追加分格规则段
- `src/shanhai/steps/s4_pages.py` —— 逐格生图分支 + 调用 `paneling.compose_manga_page`
- `src/shanhai/paneling.py`(新建)—— 版式模板 + 排版合成 + insert 格异形裁剪叠加
- `web/src/components/NewProjectForm.tsx`、`web/src/api.ts`、`web/src/types.ts` —— 新建表单开关 + 类型透传
- `tests/test_paneling.py`(新建)、`tests/test_s2.py`(新建或追加)、`tests/test_s4.py`(追加)
