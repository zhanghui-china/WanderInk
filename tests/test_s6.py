import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from shanhai.schema import Legend, LocalizedTrack, Project, StoryboardCell
from shanhai.steps import s6_compose
from shanhai.steps.s6_compose import _credits_lines


def _multi_page_project(tmp_path: Path, n: int) -> Project:
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.legend = Legend(title="白蛇传", summary="s", source_type="民间传说", sources=["《警世通言》"])
    (tmp_path / "pages").mkdir(parents=True); (tmp_path / "audio").mkdir()
    cells = []
    for i in range(1, n + 1):
        (tmp_path / f"pages/page_{i:02d}.png").write_bytes(b"png")
        (tmp_path / f"audio/page_{i:02d}.mp3").write_bytes(b"mp3")
        cells.append(StoryboardCell(index=i, scene_ref=f"1-{i}", visual_desc="v", characters=[],
                                    caption=f"第{i}页", emotion="宁静",
                                    image=f"pages/page_{i:02d}.png",
                                    audio=f"audio/page_{i:02d}.mp3",
                                    duration_ms=6800, status="confirmed"))
    p.storyboard = cells
    return p


def _concat_inputs(cmd: list[str]) -> list[str]:
    return [cmd[i + 1] for i in range(len(cmd)) if cmd[i] == "-i"]


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
         patch("shanhai.steps.s6_compose.typeset.credits_card"), \
         patch("shanhai.steps.s6_compose.typeset.overlay_layer") as ov:
        p = s6_compose.run(p, tmp_path)
    assert p.output["mp4"].endswith("final.mp4")
    assert p.status["s6"] == "done"
    assert sh.call_count >= 4          # 片头 clip + 页 clip + 片尾 clip + concat + finalize
    assert ov.call_count == 1          # 全片共用一张水印 overlay(字幕已改走软字幕轨)


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
         patch("shanhai.steps.s6_compose.typeset.credits_card"), \
         patch("shanhai.steps.s6_compose.typeset.overlay_layer"):
        s6_compose.run(p, tmp_path)
    cmds = [" ".join(call.args[0]) for call in sh.call_args_list]
    page = next(c for c in cmds if "clips/01.mp4" in c and "xfade=transition" not in c)
    assert "zoompan" in page and "overlay=0:0" in page           # 页:Ken Burns 底图 + 静态字幕层
    title = next(c for c in cmds if "00_title.mp4" in c and "xfade=transition" not in c)
    credits = next(c for c in cmds if "99_credits.mp4" in c and "xfade=transition" not in c)
    assert "zoompan" not in title and "zoompan" not in credits   # 片头/片尾静止,文字不漂移
    xf = next(c for c in cmds if "xfade=transition=fade" in c)    # 页间交叉溶解链
    assert "acrossfade" in xf                                     # narration 不交叠
    assert "fade=t=in" in xf and "fade=t=out" in xf               # 全片首尾黑场开合,不硬切
    assert "00_title.mp4" in xf and "01.mp4" in xf and "99_credits.mp4" in xf  # 片头+页+片尾同链


def test_s6_skips_missing_and_unconfirmed_cells(tmp_path: Path):
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.legend = Legend(title="白蛇传", summary="s", source_type="民间传说", sources=["《警世通言》"])
    (tmp_path / "pages").mkdir(parents=True); (tmp_path / "audio").mkdir()
    (tmp_path / "pages/page_01.png").write_bytes(b"png")
    (tmp_path / "audio/page_01.mp3").write_bytes(b"mp3")
    p.storyboard = [
        # confirmed 且产物文件齐备 -> 入选,产出正文页 clip
        StoryboardCell(index=1, scene_ref="1-1", visual_desc="v", characters=[], caption="c",
                       emotion="宁静", image="pages/page_01.png", audio="audio/page_01.mp3",
                       duration_ms=6800, status="confirmed"),
        # confirmed 但产物文件不存在 -> A5 应跳过而非喂给 ffmpeg 崩溃
        StoryboardCell(index=2, scene_ref="1-2", visual_desc="v", characters=[], caption="c",
                       emotion="宁静", image="pages/page_02.png", audio="audio/page_02.mp3",
                       duration_ms=6800, status="confirmed"),
        # 未确认页 -> 跳过
        StoryboardCell(index=3, scene_ref="1-3", visual_desc="v", characters=[], caption="c",
                       emotion="宁静", status="draft"),
    ]
    with patch("shanhai.steps.s6_compose.ffmpeg.sh") as sh, \
         patch("shanhai.steps.s6_compose.typeset.title_card"), \
         patch("shanhai.steps.s6_compose.typeset.credits_card"), \
         patch("shanhai.steps.s6_compose.typeset.overlay_layer") as ov:
        p = s6_compose.run(p, tmp_path)
    # 片头 + 1 正文页 + 片尾 + concat + finalize,跳过另两页。
    # 没有封字幕轨那一趟:默认烧中文硬字幕,中文软轨被排除,此片再无其它语种可封。
    assert sh.call_count == 5
    assert ov.call_count == 1           # 仅入选页生成 overlay(另两页被跳过,不出图层)
    assert p.status["s6"] == "partial"  # 有页被跳过,不能诚实地标 done(见 2026-07-16 反馈)


def test_s6_refuses_empty_when_no_content_cells(tmp_path: Path):
    # 0 个 confirmed+image+audio 页 -> 拒绝产出仅片头/片尾的空片,raise 让管线记 error
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.legend = Legend(title="白蛇传", summary="s", source_type="民间传说", sources=["《警世通言》"])
    p.storyboard = [
        # confirmed 但产物文件不存在 -> 跳过
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
        with pytest.raises(ValueError, match="拒绝产出空片"):
            s6_compose.run(p, tmp_path)
    assert sh.call_count == 0            # 空片提前拒绝,不浪费任何 ffmpeg 合成
    assert "s6" not in p.status          # 未标记 done


def test_s6_parallel_encode_preserves_page_order(tmp_path: Path):
    # PERF2:并行编码下,完成顺序可能与页序相反,但 clips/durations 必须按索引回填、严格保持页序
    n = 5
    p = _multi_page_project(tmp_path, n)

    def _sh(cmd):
        # 第 1 页人为拖慢,制造"后提交先完成"的乱序,验证结果仍按页序回填而非 as_completed 完成序
        if "clips/01.mp4" in " ".join(cmd):
            time.sleep(0.05)

    with patch("shanhai.steps.s6_compose.ffmpeg.sh", side_effect=_sh) as sh, \
         patch("shanhai.steps.s6_compose.typeset.title_card"), \
         patch("shanhai.steps.s6_compose.typeset.credits_card"), \
         patch("shanhai.steps.s6_compose.typeset.overlay_layer") as ov:
        s6_compose.run(p, tmp_path)

    # 默认烧中文硬字幕 → 每页各一张 overlay(关掉开关才回到全片共用一张,另有专门用例)
    assert ov.call_count == n
    page_calls = [c.args[0] for c in sh.call_args_list if "zoompan" in " ".join(c.args[0])]
    assert len(page_calls) == n                      # 每页各编码一次

    concat_cmd = next(c.args[0] for c in sh.call_args_list
                      if "xfade=transition=fade" in " ".join(c.args[0]))
    names = [Path(i).name for i in _concat_inputs(concat_cmd)]
    expected = ["00_title.mp4"] + [f"{i:02d}.mp4" for i in range(1, n + 1)] + ["99_credits.mp4"]
    assert names == expected                          # xfade 拼接顺序与页序严格一致


def test_s6_page_clips_encode_concurrently(tmp_path: Path):
    # PERF2:验证确有并行——多页编码时间窗口重叠,而非逐页串行
    n = 4
    p = _multi_page_project(tmp_path, n)
    active = 0
    max_active = 0
    lock = threading.Lock()

    def _sh(cmd):
        nonlocal active, max_active
        if "zoompan" not in " ".join(cmd):            # 仅正文页 clip 编码模拟耗时
            return
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with lock:
            active -= 1

    with patch("shanhai.steps.s6_compose.ffmpeg.sh", side_effect=_sh), \
         patch("shanhai.steps.s6_compose.typeset.title_card"), \
         patch("shanhai.steps.s6_compose.typeset.credits_card"), \
         patch("shanhai.steps.s6_compose.typeset.overlay_layer"):
        s6_compose.run(p, tmp_path)

    assert max_active > 1                             # 确有多页同时编码
    assert max_active <= s6_compose.S6_CONCURRENCY     # 不超并发上限


def test_s6_page_encode_exception_propagates(tmp_path: Path):
    # 某页编码异常须向上抛,与既有语义一致:S6 编码失败即 pipeline error,不静默吞掉
    p = _multi_page_project(tmp_path, 3)

    def _sh(cmd):
        if "clips/02.mp4" in " ".join(cmd):
            raise RuntimeError("ffmpeg 崩了")

    with patch("shanhai.steps.s6_compose.ffmpeg.sh", side_effect=_sh), \
         patch("shanhai.steps.s6_compose.typeset.title_card"), \
         patch("shanhai.steps.s6_compose.typeset.credits_card"), \
         patch("shanhai.steps.s6_compose.typeset.overlay_layer"):
        with pytest.raises(RuntimeError, match="ffmpeg 崩了"):
            s6_compose.run(p, tmp_path)
    assert "s6" not in p.status


# ---- 硬字幕烧录(burn_subtitles) ----

def _burn_run(p: Project, tmp_path: Path, lang: str = "zh"):
    """跑一轮 S6,返回 (overlay_layer 的 mock, 产出目录)。ffmpeg 全程打桩。"""
    with patch("shanhai.steps.s6_compose.ffmpeg.sh"), \
         patch("shanhai.steps.s6_compose.typeset.title_card"), \
         patch("shanhai.steps.s6_compose.typeset.credits_card"), \
         patch("shanhai.steps.s6_compose.typeset.overlay_layer") as ov:
        s6_compose.run(p, tmp_path, lang=lang)
    return ov, tmp_path / "output"


def test_burn_subtitles_renders_each_page_caption(tmp_path: Path):
    """开烧录:逐页各出一张 overlay,且烧的是该页自己的解说词。"""
    p = _multi_page_project(tmp_path, 3)
    ov, _ = _burn_run(p, tmp_path)
    captions = [c.args[0] for c in ov.call_args_list]
    assert captions == ["第1页", "第2页", "第3页"]      # 逐页各自的文案,不是共用空串
    outs = [c.args[1] for c in ov.call_args_list]
    assert len({str(o) for o in outs}) == 3              # 三张互不覆盖


def test_burn_subtitles_off_shares_one_watermark(tmp_path: Path):
    """关烧录:回到全片共用一张空 caption 水印层,与烧录功能上线前逐字一致。"""
    p = _multi_page_project(tmp_path, 3)
    p.params.burn_subtitles = False
    ov, _ = _burn_run(p, tmp_path)
    assert ov.call_count == 1
    assert ov.call_args.args[0] == ""                    # 空 caption = 只留水印


def test_burn_subtitles_skips_chinese_soft_track(tmp_path: Path):
    """烧了中文硬字幕就不该再出中文软轨/VTT,否则网页播放器上是双份字幕。"""
    p = _multi_page_project(tmp_path, 2)
    _, out_dir = _burn_run(p, tmp_path)
    assert not (out_dir / "final.zh.srt").exists()
    assert not (out_dir / "final.zh.vtt").exists()


def test_no_burn_keeps_chinese_soft_track(tmp_path: Path):
    p = _multi_page_project(tmp_path, 2)
    p.params.burn_subtitles = False
    _, out_dir = _burn_run(p, tmp_path)
    assert (out_dir / "final.zh.srt").exists()
    assert (out_dir / "final.zh.vtt").exists()


def test_burn_subtitles_removes_stale_soft_subtitles(tmp_path: Path):
    """本次上线前完成的作品盘上留着 final.zh.vtt,而 _serialize 是按文件在不在下发的。
    重跑 S6 烧了硬字幕却不清掉它,网页上就是画面烧死的字 + VTT 各一份——正是烧录
    要避免的双份。写不出来还不够,得把陈留的删掉。"""
    p = _multi_page_project(tmp_path, 2)
    out_dir = tmp_path / "output"
    out_dir.mkdir(parents=True)
    (out_dir / "final.zh.vtt").write_text("WEBVTT\n", encoding="utf-8")
    (out_dir / "final.zh.srt").write_text("1\n", encoding="utf-8")
    _burn_run(p, tmp_path)
    assert not (out_dir / "final.zh.vtt").exists()
    assert not (out_dir / "final.zh.srt").exists()


def test_burn_subtitles_does_not_touch_english_film(tmp_path: Path):
    """英文片本期不烧(typeset 逐字符换行会把单词拦腰断开),仍走软字幕。"""
    p = _multi_page_project(tmp_path, 2)
    for cell in p.storyboard:
        cell.tracks["en"] = LocalizedTrack(caption=f"page {cell.index}",
                                           audio=cell.audio, duration_ms=6800)
    ov, out_dir = _burn_run(p, tmp_path, lang="en")
    assert ov.call_count == 1                            # 共用水印,画面不烧字
    assert ov.call_args.args[0] == ""
    assert (out_dir / "final.en.en.srt").exists()        # 英文软字幕照旧产出


def test_burn_subtitles_defaults_on_for_legacy_projects():
    """老 project.json 没有这个键,反序列化取默认 True——重跑 S6 就能拿到带字幕的版本。"""
    p = Project.model_validate({"project_id": "old", "scenic_spot": "雷峰塔"})
    assert p.params.burn_subtitles is True


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
