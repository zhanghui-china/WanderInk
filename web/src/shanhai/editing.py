"""编辑核心:对已生成项目做增删改排序,精确失效受影响产物,复用既有幂等语义
(s3/s4/s5/s6 按 status/image/audio 决定重生成)。结构变更后 index 与文件名两阶段
重命名对齐,防止 3↔4 互换等场景下产物互相覆盖。所有变更函数末尾经 _invalidate_downstream
复位下游环节 status、把 pipeline 打回 partial 并清 project.output(mp4/zip/pdf 一旦内容变即过期)。"""
from pathlib import Path

from shanhai.schema import Project, StoryboardCell

# 产物文件命名与 s4/s5 保持一致:pages/page_{index:02d}.png、audio/page_{index:02d}.mp3
_MEDIA = [("image", "pages", "png"), ("audio", "audio", "mp3")]

# 管线环节顺序(与 api._pipeline / _STEP_NAMES 一致),用于编辑后按环节复位下游 status。
_PIPELINE_STEPS = ("s0", "s1", "s2", "s3", "s4", "s5", "s6")


def _invalidate_downstream(project: Project, from_step: str) -> None:
    """编辑后诚实化联动:from_step(含)起的下游环节产物已过期。
    复位这些环节的 status 键(含 _started_at/_elapsed_s),把 pipeline 打回 partial,并清 output
    (mp4/zip/pdf 内容一变即失效)。只改传入 project,不落盘(落盘由调用端点负责)。"""
    project.output.clear()
    start = _PIPELINE_STEPS.index(from_step)
    for step in _PIPELINE_STEPS[start:]:
        for key in (step, f"{step}_started_at", f"{step}_elapsed_s"):
            project.status.pop(key, None)
    project.status["pipeline"] = "partial: 已编辑,待重新生成"


def _cell_at(project: Project, index: int) -> StoryboardCell:
    for cell in project.storyboard:
        if cell.index == index:
            return cell
    raise ValueError(f"非法 index:{index}")


def renumber(project: Project, workdir: Path) -> None:
    """按 storyboard 当前列表顺序把 index 重排为 1..n,并把 image/audio 产物文件
    改名对齐到新 index。两阶段重命名(先全部移到唯一临时名,再落到最终名),避免
    新旧文件名交叉时互相覆盖。引用的文件不存在则跳过该文件、仍更新 index 与引用字符串。"""
    cells = project.storyboard
    for attr, subdir, ext in _MEDIA:
        media_dir = workdir / subdir
        temps: dict[int, Path] = {}
        # 第一遍:所有仍存在的被引用文件 → 唯一临时名(按列表位置命名,天然唯一)
        for i, cell in enumerate(cells):
            ref = getattr(cell, attr)
            if not ref:
                continue
            src = workdir / ref
            if not src.exists():
                continue
            tmp = media_dir / f".renumber.{i}.tmp"
            src.replace(tmp)
            temps[id(cell)] = tmp
        # 第二遍:临时名 → page_{new:02d},并同步引用字符串
        for i, cell in enumerate(cells):
            ref = getattr(cell, attr)
            if not ref:
                continue
            new_index = i + 1
            tmp = temps.get(id(cell))
            if tmp is not None:
                media_dir.mkdir(parents=True, exist_ok=True)
                tmp.replace(media_dir / f"page_{new_index:02d}.{ext}")
            setattr(cell, attr, f"{subdir}/page_{new_index:02d}.{ext}")
    # 分格页每格自己的图(pages/page_{index:02d}_panel{i}.png,i 从 1 起,见 s4_pages._render_panel_cell)
    # 同样两阶段改名对齐新 index,单图页(panels 为空)天然跳过、行为不变。
    pages_dir = workdir / "pages"
    panel_temps: dict[tuple[int, int], Path] = {}
    for ci, cell in enumerate(cells):
        for pi, panel in enumerate(cell.panels, 1):
            if not panel.image:
                continue
            src = workdir / panel.image
            if not src.exists():
                continue
            tmp = pages_dir / f".renumber.panel.{ci}.{pi}.tmp"   # (列表位置, 格号)天然唯一
            src.replace(tmp)
            panel_temps[(ci, pi)] = tmp
    for ci, cell in enumerate(cells):
        new_index = ci + 1
        for pi, panel in enumerate(cell.panels, 1):
            if not panel.image:
                continue
            tmp = panel_temps.get((ci, pi))
            if tmp is not None:
                pages_dir.mkdir(parents=True, exist_ok=True)
                tmp.replace(pages_dir / f"page_{new_index:02d}_panel{pi}.png")
            panel.image = f"pages/page_{new_index:02d}_panel{pi}.png"
    for i, cell in enumerate(cells):
        cell.index = i + 1


def update_cell(project: Project, index: int, *, caption: str | None = None,
                visual_desc: str | None = None, emotion: str | None = None,
                characters: list[str] | None = None) -> None:
    """按字段子集改格并精确级联失效:
    - caption:清 audio + duration_ms=0(image/status 不动)
    - visual_desc / characters:status="draft" + 清 image + 清 panels(分格页回退单图整页)(audio 不动)
    - emotion:不级联(仅影响 s5 的 BGM 情绪匹配)"""
    cell = _cell_at(project, index)
    if caption is not None:
        cell.caption = caption
        cell.audio = ""
        cell.duration_ms = 0
        cell.silent = False
    if visual_desc is not None:
        cell.visual_desc = visual_desc
        cell.status = "draft"
        cell.image = ""
        cell.panels = []   # 分格页作废分格:改整页构图后回退成单图整页重生成(s4 单图分支)
    if characters is not None:
        cell.characters = characters
        cell.status = "draft"
        cell.image = ""
        cell.panels = []   # 同上:出场角色变了,分格构图已过期,回退单图整页重生成
    if emotion is not None:
        cell.emotion = emotion
    _invalidate_downstream(project, "s4")


def mark_redraw(project: Project, index: int) -> None:
    """标记该页需重绘:置 draft + 清 image(s4 据此重画,文件由 s4 覆盖)。"""
    cell = _cell_at(project, index)
    cell.status = "draft"
    cell.image = ""
    _invalidate_downstream(project, "s4")


def mark_revoice(project: Project, index: int) -> None:
    """标记该页需重配音:清 audio + duration_ms=0(s5 据此重配,文件由 s5 覆盖)。"""
    cell = _cell_at(project, index)
    cell.audio = ""
    cell.duration_ms = 0
    cell.silent = False
    _invalidate_downstream(project, "s5")   # 只清了音轨,s4 图像仍有效,从 s5 起失效即可


def insert_cell(project: Project, workdir: Path, after_index: int, *, caption: str,
                visual_desc: str, emotion: str = "宁静",
                characters: list[str] | None = None) -> StoryboardCell:
    """在 after_index 之后插入新格(after_index=0 插最前);新格 status=draft、
    image/audio 为空。插入后 renumber 重排 index 并对齐既有产物文件名。"""
    n = len(project.storyboard)
    if after_index < 0 or after_index > n:
        raise ValueError(f"非法 after_index:{after_index}")
    cell = StoryboardCell(index=after_index + 1, scene_ref="", visual_desc=visual_desc,
                          characters=characters or [], caption=caption, emotion=emotion,
                          status="draft")
    project.storyboard.insert(after_index, cell)
    renumber(project, workdir)
    _invalidate_downstream(project, "s4")
    return cell


def delete_cell(project: Project, workdir: Path, index: int) -> None:
    """删除该页并删掉其 image/audio 真实文件(missing_ok);分格页再删掉每格自己的图。
    其余页 renumber 对齐。"""
    cell = _cell_at(project, index)
    for attr, _subdir, _ext in _MEDIA:
        ref = getattr(cell, attr)
        if ref:
            (workdir / ref).unlink(missing_ok=True)
    for panel in cell.panels:   # 分格页每格自己的图也要删,单图页(panels 为空)天然跳过
        if panel.image:
            (workdir / panel.image).unlink(missing_ok=True)
    project.storyboard.remove(cell)
    renumber(project, workdir)
    _invalidate_downstream(project, "s4")


def reorder_cells(project: Project, workdir: Path, order: list[int]) -> None:
    """按 order(旧 index 的新顺序)重排;order 必须是现有 index 的全排列,否则 ValueError。
    renumber 的两阶段重命名保证互换页产物不互相覆盖。"""
    existing = [c.index for c in project.storyboard]
    if sorted(order) != sorted(existing):
        raise ValueError(f"order 必须是现有 index 的全排列:{order}")
    by_index = {c.index: c for c in project.storyboard}
    project.storyboard = [by_index[o] for o in order]
    renumber(project, workdir)
    _invalidate_downstream(project, "s4")


def mark_character_redraw(project: Project, name: str) -> None:
    """标记角色需重绘:解锁(s3 只重画非 locked 角色,据此重出三视图)。"""
    if project.script is None:
        raise ValueError("项目尚无剧本")
    for c in project.script.characters:
        if c.name == name:
            c.locked = False
            _invalidate_downstream(project, "s3")
            return
    raise ValueError(f"非法角色名:{name}")
