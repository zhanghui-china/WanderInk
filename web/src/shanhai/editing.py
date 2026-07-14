"""编辑核心:对已生成项目做增删改排序,精确失效受影响产物,复用既有幂等语义
(s3/s4/s5/s6 按 status/image/audio 决定重生成)。结构变更后 index 与文件名两阶段
重命名对齐,防止 3↔4 互换等场景下产物互相覆盖。所有变更函数末尾清 project.output
(mp4/zip/pdf 一旦内容变即过期)。"""
from pathlib import Path

from shanhai.schema import Project, StoryboardCell

# 产物文件命名与 s4/s5 保持一致:pages/page_{index:02d}.png、audio/page_{index:02d}.mp3
_MEDIA = [("image", "pages", "png"), ("audio", "audio", "mp3")]


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
    for i, cell in enumerate(cells):
        cell.index = i + 1


def update_cell(project: Project, index: int, *, caption: str | None = None,
                visual_desc: str | None = None, emotion: str | None = None,
                characters: list[str] | None = None) -> None:
    """按字段子集改格并精确级联失效:
    - caption:清 audio + duration_ms=0(image/status 不动)
    - visual_desc / characters:status="draft" + 清 image(audio 不动)
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
    if characters is not None:
        cell.characters = characters
        cell.status = "draft"
        cell.image = ""
    if emotion is not None:
        cell.emotion = emotion
    project.output.clear()


def mark_redraw(project: Project, index: int) -> None:
    """标记该页需重绘:置 draft + 清 image(s4 据此重画,文件由 s4 覆盖)。"""
    cell = _cell_at(project, index)
    cell.status = "draft"
    cell.image = ""
    project.output.clear()


def mark_revoice(project: Project, index: int) -> None:
    """标记该页需重配音:清 audio + duration_ms=0(s5 据此重配,文件由 s5 覆盖)。"""
    cell = _cell_at(project, index)
    cell.audio = ""
    cell.duration_ms = 0
    cell.silent = False
    project.output.clear()


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
    project.output.clear()
    return cell


def delete_cell(project: Project, workdir: Path, index: int) -> None:
    """删除该页并删掉其 image/audio 真实文件(missing_ok);其余页 renumber 对齐。"""
    cell = _cell_at(project, index)
    for attr, _subdir, _ext in _MEDIA:
        ref = getattr(cell, attr)
        if ref:
            (workdir / ref).unlink(missing_ok=True)
    project.storyboard.remove(cell)
    renumber(project, workdir)
    project.output.clear()


def reorder_cells(project: Project, workdir: Path, order: list[int]) -> None:
    """按 order(旧 index 的新顺序)重排;order 必须是现有 index 的全排列,否则 ValueError。
    renumber 的两阶段重命名保证互换页产物不互相覆盖。"""
    existing = [c.index for c in project.storyboard]
    if sorted(order) != sorted(existing):
        raise ValueError(f"order 必须是现有 index 的全排列:{order}")
    by_index = {c.index: c for c in project.storyboard}
    project.storyboard = [by_index[o] for o in order]
    renumber(project, workdir)
    project.output.clear()


def mark_character_redraw(project: Project, name: str) -> None:
    """标记角色需重绘:解锁(s3 只重画非 locked 角色,据此重出三视图)。"""
    if project.script is None:
        raise ValueError("项目尚无剧本")
    for c in project.script.characters:
        if c.name == name:
            c.locked = False
            project.output.clear()
            return
    raise ValueError(f"非法角色名:{name}")
