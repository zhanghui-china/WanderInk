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
    - visual_desc / characters:status="draft" + 清 image + 清 panels(分格页回退单图整页,
      理由见 _invalidate_page_image 的 drop_panels 说明;audio 不动)
    - emotion:不级联(仅影响 s5 的 BGM 情绪匹配)"""
    cell = _cell_at(project, index)
    if caption is not None:
        cell.caption = caption
        cell.audio = ""
        cell.duration_ms = 0
        cell.silent = False
    if visual_desc is not None:
        cell.visual_desc = visual_desc
        _invalidate_page_image(cell, drop_panels=True)
    if characters is not None:
        cell.characters = characters
        # 出场角色变了,旧图与格级构图都已过期(格级 characters 是页级的子集)
        _invalidate_page_image(cell, drop_panels=True)
    if emotion is not None:
        cell.emotion = emotion
    _invalidate_downstream(project, "s4")


def _invalidate_page_image(cell: StoryboardCell, *, drop_panels: bool) -> None:
    """作废该页的图与**所有描述那张图的元数据**,置回 draft。

    单一真源:清 cell.image 的地方有四处(update_cell 的两个分支 + mark_redraw +
    invalidate_pages_of_characters),此前各写各的,新增 image_route/image_lora 时只补了
    mark_redraw 一处,于是改画面描述会留下一张已被删掉的图的路径标记,界面照着它渲染
    "LoRA 未生效"——描述一张不存在的图。这类"同一个不变量散在多处"的疏漏在本仓库反复
    发生,故收敛到这里,以后加字段只改一处。

    `drop_panels` 必须由调用方显式给,因为四个调用点的答案**不一样**:
    - True(改整页 visual_desc / characters):**必须**清。_render_panel_cell 用的是
      panel.visual_desc、根本不读 cell.visual_desc,保留 panels 会让用户改的那句话完全
      不生效;改 characters 同理,格级 panel.characters 是页级的子集,页级改了格级可能
      引用已删除的角色。清掉退回单图整页,那条编辑才有意义。
    - False(重绘 / 角色三视图更新后作废出场页):内容一个字没改,版式不该跟着变。
      而且 panels **只有 S2 会生成、S4 不会**——清掉这一页就永远是单图,除非重跑 S2
      (会冲掉该作品所有人工编辑)。线上「少林寺」29f1f688 就是被这样清掉的:盘上留着
      page_01_panel1/2/3.png 三个格子的图,而 cell.panels 已经空了,9 页里 7 页如此。
    保留 panels 时仍要清掉各格的 image:那些图正在被作废,留着路径就是"描述一张已删的图",
    正是上面那段说的同一类疏漏(S4 会覆写同名文件,但某格重绘失败时状态就不一致了)。"""
    cell.status = "draft"
    cell.image = ""
    cell.image_gen_ms = 0
    cell.image_route = ""
    cell.image_lora = ""
    if drop_panels:
        cell.panels = []
    else:
        for panel in cell.panels:
            panel.image = ""


def mark_redraw(project: Project, index: int) -> None:
    """标记该页需重绘:置 draft + 清 image 及其元数据(s4 据此重画,文件由 s4 覆盖)。"""
    # 重绘不动版式:用户什么内容都没改,分格页重绘后仍该是分格页(见 _invalidate_page_image)
    _invalidate_page_image(_cell_at(project, index), drop_panels=False)
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


def mark_all_revoice(project: Project) -> None:
    """标记**整部作品**需重配音:逐格清主语言与各语种的音轨字段(译文/画面全部保留)。

    换音色走这里而不是只复位下游 status:s5 的续跑复用分支
    (`track.audio and not track.silent and out.exists()` → 直接 return)只看这三个字段,
    旧 mp3 还在盘上就整页跳过、新音色永远轮不到合成——用户看到的就是"换了女声还是男声"。
    只清字段不删文件:s5 会覆盖同名输出,短路条件靠 audio 置空就已经打破了。"""
    langs: set[str] = set()
    for cell in project.storyboard:
        for track in (cell, *cell.tracks.values()):
            track.audio = ""
            track.duration_ms = 0
            track.silent = False
        langs |= cell.tracks.keys()
    for lang in langs:
        project.status.pop(f"s5_{lang}", None)
        _invalidate_track_output(project, lang)
    _invalidate_downstream(project, "s5")   # 只清音轨,s4 图像仍有效,从 s5 起失效即可


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


def turnaround_stamps(project: Project, workdir: Path) -> dict[str, tuple]:
    """每个角色三视图文件的指纹(不存在则为 ())。在 S3 前后各取一次,比对出"这轮真的重画了谁"。

    单独抽出来是因为 api 与 cli 两条入口都要跑这个判据,而本仓库已经反复因为
    "同一个判断写两份"吃亏(见 _invalidate_page_image 的注释、大师 skill 闸门那次)。

    此前的判据是"谁从无图变有图"(missing_turnarounds 的差集),那是错的:用户点重绘、
    换参考图时,mark_character_redraw 与上传端点都**刻意保留** turnaround_image
    (清了卡片立刻变"未生成",空窗难看)。角色前后都"有图",差集恒为空,一页都不作废——
    界面上 s3=done、s4=done、全部 confirmed,新形象却一页都没出现,毫无异常信号。
    补画首张三视图是本判据的特例(() → 有指纹),仍然算数。

    指纹取 (mtime_ns, size) 而非内容哈希:S3 是原地覆写同名文件,mtime 必变;
    读全部三视图算哈希在 12 个角色上是几十 MB 的白工。"""
    stamps: dict[str, tuple] = {}
    for c in (project.script.characters if project.script else []):
        f = workdir / c.turnaround_image if c.turnaround_image else None
        try:
            st = f.stat() if f is not None else None
        except OSError:      # 文件被外部删掉:与"没有图"同义,不是异常路径
            st = None
        stamps[c.name] = (st.st_mtime_ns, st.st_size) if st else ()
    return stamps


def redrawn_characters(before: dict[str, tuple], after: dict[str, tuple]) -> set[str]:
    """两次 turnaround_stamps 之间三视图变过的角色。新增角色(before 里没有)也算。"""
    return {name for name, stamp in after.items() if before.get(name, ()) != stamp}


def invalidate_pages_of_characters(project: Project, names: set[str]) -> list[int]:
    """作废这些角色出场的所有页(置 draft + 清图),返回被作废的页号。

    补画三视图之后必须调它,否则那次补画对**已经画好的页**完全无效:S4 的幂等跳过条件是
    `status == "confirmed" and image and 文件存在`(s4_pages.run),旧页三条全占,于是永远
    停留在没有该角色锚点的版本,而界面上 s3=done / s4=done / 全部 confirmed,一切正常。
    实测 DGX 上的 8f41283a 就卡在这里:补出第一主角三视图后重跑 S4,9 页里只重画了当时
    恰好不是 confirmed 的 2 页,其余 7 页至今仍是无锚点的旧图。

    复用 _invalidate_page_image 而不是自己清字段:清图这件事的不变量只应有一处真源。"""
    if not names:
        return []
    hit = [c.index for c in project.storyboard if names & set(c.characters)]
    for cell in project.storyboard:
        if names & set(cell.characters):
            _invalidate_page_image(cell, drop_panels=False)   # 换的是角色锚点,不是版式
    if hit:
        _invalidate_downstream(project, "s4")
    return hit


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
