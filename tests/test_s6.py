from pathlib import Path
from unittest.mock import patch

from shanhai.schema import Legend, Project, StoryboardCell
from shanhai.steps import s6_compose
from shanhai.steps.s6_compose import _credits_lines


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


def test_s6_kenburns_xfade_pipeline(tmp_path: Path):
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
        s6_compose.run(p, tmp_path)
    cmds = [" ".join(call.args[0]) for call in sh.call_args_list]
    assert any("zoompan" in c for c in cmds)                      # 每页 Ken Burns 推拉
    xf = next(c for c in cmds if "xfade=transition=fade" in c)    # 页间交叉溶解链
    assert "acrossfade" in xf                                     # narration 不交叠
    assert "fade=t=in" in xf and "fade=t=out" in xf               # 全片首尾黑场开合,不硬切
    assert "00_title.mp4" in xf and "01.mp4" in xf and "99_credits.mp4" in xf  # 片头+页+片尾同链


def test_s6_skips_missing_and_unconfirmed_cells(tmp_path: Path):
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.legend = Legend(title="白蛇传", summary="s", source_type="民间传说", sources=["《警世通言》"])
    p.storyboard = [
        # confirmed 但产物文件不存在 -> A5 应跳过而非喂给 ffmpeg 崩溃
        StoryboardCell(index=1, scene_ref="1-1", visual_desc="v", characters=[], caption="c",
                       emotion="宁静", image="pages/page_01.png", audio="audio/page_01.mp3",
                       duration_ms=6800, status="confirmed"),
        # 未确认页 -> 跳过
        StoryboardCell(index=2, scene_ref="1-2", visual_desc="v", characters=[], caption="c",
                       emotion="宁静", status="draft"),
    ]
    with patch("shanhai.steps.s6_compose.ffmpeg.sh") as sh, \
         patch("shanhai.steps.s6_compose.typeset.title_card"), \
         patch("shanhai.steps.s6_compose.typeset.credits_card"):
        p = s6_compose.run(p, tmp_path)
    assert sh.call_count == 4           # 仅 片头 + 片尾 + concat + finalize,无正文页 clip
    assert p.status["s6"] == "done"


def test_credits_original_not_labeled_as_legend():
    legend = Legend(title="t", summary="s", source_type="原创演绎", sources=["用户自备文本"])
    lines = _credits_lines(legend)
    assert any("原创演绎" in ln for ln in lines)          # PRD F0②:显式标注
    assert not any("传说来源" in ln for ln in lines)      # 不得包装成真传说
    assert "本片为 AI 生成内容" in lines


def test_credits_empty_sources_falls_back_to_type():
    lines = _credits_lines(Legend(title="t", summary="s", source_type="民间传说", sources=[]))
    assert any("民间传说" in ln for ln in lines)          # PRD §9.4:空 sources 也须有来源标注
    assert "本片为 AI 生成内容" in lines


def test_credits_none_legend_defensive():
    lines = _credits_lines(None)
    assert lines and "本片为 AI 生成内容" in lines         # legend 缺失也不崩且有兜底标注
