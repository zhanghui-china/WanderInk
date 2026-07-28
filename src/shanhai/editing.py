"""编辑核心:对已生成项目做增删改排序,精确失效受影响产物,复用既有幂等语义
(s3/s4/s5/s6 按 status/image/audio 决定重生成)。结构变更后 index 与文件名两阶段
重命名对齐,防止 3↔4 互换等场景下产物互相覆盖。所有变更函数末尾经 _invalidate_downstream
复位下游环节 status、把 pipeline 打回 partial 并清 project.output(mp4/zip/pdf 一旦内容变即过期)。"""
from pathlib import Path

from shanhai.schema import LocalizedTrack, Project, StoryboardCell

# 产物文件命名与 s4/s5 保持一致:pages/page_{index:02d}.png、audio/page_{index:02d}.mp3
_MEDIA = [("image", "pages", "png"), ("audio", "audio", "mp3")]

# 管线环节顺序(与 api._pipeline / _STEP_NAMES 一致),用于编辑后按环节复位下游 status。
_PIPELINE_STEPS = ("s0", "s1", "s2", "s3", "s4", "s5", "s6")

# 三个计时键描述的是同一次运行(见 api._mark_step_started),失效时必须同进同退。
# 曾经只清前两个、漏了 _finished_at,于是失效后留下"只有结束时刻、没有开始时刻"的孤儿键,
# 前端悬停算不出起止区间、什么都显示不出来。
_STEP_TIMING_SUFFIXES = ("_started_at", "_finished_at", "_elapsed_s")


def clear_step_keys(status: dict[str, str], step: str) -> None:
    """清掉某环节的状态键及其三个计时键。api.py 的下游级联也调它(api 已 import editing,
    反向 import 会成环,故单一真源放这边),保证所有失效路径清的永远是同一组键。"""
    status.pop(step, None)
    for suffix in _STEP_TIMING_SUFFIXES:
        status.pop(f"{step}{suffix}", None)


def _invalidate_downstream(project: Project, from_step: str) -> None:
    """编辑后诚实化联动:from_step(含)起的下游环节产物已过期。
    复位这些环节的 status 键(含三个计时键),把 pipeline 打回 partial,并清 output
    (mp4/zip/pdf 内容一变即失效)。只改传入 project,不落盘(落盘由调用端点负责)。"""
    project.output.clear()
    start = _PIPELINE_STEPS.index(from_step)
    for step in _PIPELINE_STEPS[start:]:
        clear_step_keys(project.status, step)
    project.status["pipeline"] = "partial: 已编辑,待重新生成"


def invalidate_from(project: Project, from_step: str) -> None:
    """`_invalidate_downstream` 的公开入口。本模块里的编辑函数各自在末尾调私有版,
    但「换配音音色」这类改动发生在 api 层(它改的是 params 而不是分镜内容),需要一个
    正经的对外名字,而不是从别的模块伸手去调下划线开头的私有函数。"""
    _invalidate_downstream(project, from_step)


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
        _invalidate_page_image(cell)
    if characters is not None:
        cell.characters = characters
        _invalidate_page_image(cell)   # 出场角色变了,分格构图与旧图都已过期
    if emotion is not None:
        cell.emotion = emotion
    _invalidate_downstream(project, "s4")


def _invalidate_page_image(cell: StoryboardCell) -> None:
    """作废该页的图与**所有描述那张图的元数据**,置回 draft。

    单一真源:清 cell.image 的地方有三处(update_cell 的两个分支 + mark_redraw),
    此前各写各的,新增 image_route/image_lora 时只补了 mark_redraw 一处,于是改画面描述
    会留下一张已被删掉的图的路径标记,界面照着它渲染"LoRA 未生效"——描述一张不存在的图。
    这类"同一个不变量散在多处"的疏漏在本仓库反复发生,故收敛到这里,以后加字段只改一处。
    分格作废是其中一环:改了整页构图或出场角色,旧的分格版式必然过期,回退单图整页重生成。"""
    cell.status = "draft"
    cell.image = ""
    cell.image_gen_ms = 0
    cell.image_route = ""
    cell.image_lora = ""
    cell.panels = []


def mark_redraw(project: Project, index: int) -> None:
    """标记该页需重绘:置 draft + 清 image 及其元数据(s4 据此重画,文件由 s4 覆盖)。"""
    _invalidate_page_image(_cell_at(project, index))
    _invalidate_downstream(project, "s4")


def purge_page_artifacts(workdir: Path) -> int:
    """删掉逐页产物与成片目录,返回删除的文件数。

    只在**分镜被整体换掉**时用(s2_storyboard.run 是 `project.storyboard = result.cells`)——
    那一刻旧的图/音/成片/字幕已确定全部过期,而新分镜页数若比旧的少,多出来的
    page_NN.png / page_NN.mp3 再没有任何东西会覆盖它们,只能永远躺在盘上。
    delete_cell 删单页时会 unlink,同样是"页数减少",两条路径的行为应该一致。

    **不碰 characters/**:角色三视图依赖的是 project.script,而 S2 换的是 storyboard,
    剧本没动、三视图仍然有效(与 api._INVALIDATES 里 s2 不作废 s3 是同一个判断)。
    调用方必须在**该步成功之后**才调它:S2 抛异常时(如 LLM 返回空分镜)旧产物还是
    用户仅有的东西,先删后跑等于一次失败的重生成把成片也赔进去。"""
    n = 0
    for _attr, subdir, ext in _MEDIA:
        for f in (workdir / subdir).glob(f"*.{ext}"):
            f.unlink(missing_ok=True)
            n += 1
    out = workdir / "output"
    if out.exists():
        for f in out.rglob("*"):
            if f.is_file():
                f.unlink(missing_ok=True)
                n += 1
    return n


def _invalidate_track_output(project: Project, lang: str) -> None:
    """该语种成片过期。只清这一语种的产物与状态,不动主语言的 mp4/zip/pdf——
    改英文译文没有理由让中文成片作废。"""
    project.output.pop(f"mp4_{lang}", None)
    project.status.pop(f"s6_{lang}", None)
    # 语种轨的状态键前缀是 track_{lang} 而非环节名,按前缀传进去即可复用同一套清理
    # (旧代码手写键名时漏了 track_{lang}_finished_at,与 _invalidate_downstream 是同一个 bug)。
    clear_step_keys(project.status, f"track_{lang}")


def update_track_caption(project: Project, index: int, lang: str, caption: str) -> None:
    """人工校对某页某语种的译文。文本一变,该页该语种的旧配音就念的是旧稿,一并作废;
    该语种成片同样过期。主语言内容与产物完全不受影响。"""
    cell = _cell_at(project, index)
    track = cell.tracks.setdefault(lang, LocalizedTrack())
    track.caption = caption
    track.audio = ""
    track.duration_ms = 0
    track.silent = False
    project.status.pop(f"s5_{lang}", None)
    _invalidate_track_output(project, lang)


def mark_track_revoice(project: Project, index: int, lang: str) -> None:
    """标记某页某语种需重配音:清该语种音频(译文保留,s5 据此重配)。"""
    cell = _cell_at(project, index)
    track = cell.tracks.setdefault(lang, LocalizedTrack())
    track.audio = ""
    track.duration_ms = 0
    track.silent = False
    project.status.pop(f"s5_{lang}", None)
    _invalidate_track_output(project, lang)


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
    """标记角色需重绘:解锁(s3 只重画非 locked 角色,据此重出三视图)。
    刻意保留 reference_image——它是用户上传的输入而非产物,和 mark_redraw 保留
    visual_desc、mark_revoice 保留 caption 是同一套原则(mark_* 只清产物不清输入);
    真要换掉参考图,前端有显式的移除按钮,不需要靠"重绘"顺带清空。"""
    if project.script is None:
        raise ValueError("项目尚无剧本")
    for c in project.script.characters:
        if c.name == name:
            c.locked = False
            _invalidate_downstream(project, "s3")
            return
    raise ValueError(f"非法角色名:{name}")
