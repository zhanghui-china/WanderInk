"""多语种轨(译文 + 该语种配音 + 该语种成片)的核心不变量。"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from shanhai import editing, subtitles
from shanhai.ffmpeg import XFADE_S, mux_subtitles_cmd, xfade_offsets
from shanhai.schema import LocalizedTrack, Project, StoryboardCell
from shanhai.steps import s5_audio, s5t_translate


def _cell(index: int, caption: str = "断桥初遇。") -> StoryboardCell:
    return StoryboardCell(index=index, scene_ref=f"1-{index}", visual_desc="v",
                          characters=[], caption=caption, emotion="宁静")


def _project(n: int = 2) -> Project:
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.storyboard = [_cell(i) for i in range(1, n + 1)]
    return p


# ---------- 数据模型:老项目文件必须照常加载 ----------

def test_old_project_json_without_tracks_still_loads():
    # 兼容性红线:tracks 是这次新加的字段,历史 project.json 里没有它。
    raw = ('{"project_id":"old","scenic_spot":"雷峰塔","storyboard":[{"index":1,'
           '"scene_ref":"1-1","visual_desc":"v","characters":[],"caption":"c",'
           '"emotion":"宁静"}]}')
    p = Project.model_validate_json(raw)
    assert p.storyboard[0].tracks == {}          # 缺省为空字典,不是 None
    assert p.params.voice_en == ""


def test_localized_track_caption_allows_longer_english():
    # 英文同义内容约为中文 2~2.5 倍,主语言那条 80 的上限装不下
    long_en = "A" * 240
    assert LocalizedTrack(caption=long_en).caption == long_en
    with pytest.raises(ValueError):
        LocalizedTrack(caption="A" * 241)


# ---------- 每字节奏按语言取值 ----------

def test_english_pace_does_not_trip_truncation_guard():
    # 中文阈值(150ms/字符)套到英文上会把正常语音全判成截断 → 每页空转 TTS_TRIES。
    en_caption = "She lowered her eyes, and her voice was very soft."
    real_ms = 3400                      # 约 66ms/字符,英文正常语速
    assert real_ms < len(en_caption) * s5_audio._pace("zh")[1]   # 用中文阈值会误判
    assert real_ms >= len(en_caption) * s5_audio._pace("en")[1]  # 用英文阈值则通过


def test_estimate_ms_scales_with_language():
    text = "A" * 140
    # 同样长度的文本,英文按更快的字符速率估时长(否则静音兜底页会拖得离谱)
    assert s5_audio._estimate_ms(text, "en") < s5_audio._estimate_ms(text, "zh")


def test_unknown_language_falls_back_to_main_pace():
    assert s5_audio._pace("de") == s5_audio._pace("zh")


# ---------- track_of:主语言与附加语种的载体切换 ----------

def test_track_of_returns_cell_itself_for_main_language():
    cell = _cell(1)
    assert s5_audio.track_of(cell, "zh") is cell
    assert cell.tracks == {}          # 主语言不该在 tracks 里留痕


def test_track_of_creates_track_for_other_language():
    cell = _cell(1)
    track = s5_audio.track_of(cell, "en")
    assert isinstance(track, LocalizedTrack)
    assert cell.tracks["en"] is track


# ---------- 翻译环节 ----------

def _fake_llm(mapping: dict[int, str]) -> MagicMock:
    llm = MagicMock()
    llm.structured.side_effect = lambda system, user, schema: schema(
        items=[{"index": i, "text": t} for i, t in mapping.items()])
    return llm


def test_translate_fills_tracks():
    p = _project(2)
    p = s5t_translate.run(p, _fake_llm({1: "At Broken Bridge.", 2: "Second page."}), lang="en")
    assert p.storyboard[0].tracks["en"].caption == "At Broken Bridge."
    assert p.status["s5t_en"] == "done"


def test_translate_is_idempotent_skips_already_translated():
    p = _project(2)
    p.storyboard[0].tracks["en"] = LocalizedTrack(caption="Already done.")
    llm = _fake_llm({2: "Second page."})
    p = s5t_translate.run(p, llm, lang="en")
    # 只把没译过的那页送去翻译,已有译文原样保留
    sent = llm.structured.call_args.args[1]
    assert "2." in sent and "1." not in sent
    assert p.storyboard[0].tracks["en"].caption == "Already done."


def test_translate_rejects_unknown_language():
    with pytest.raises(ValueError, match="不支持的语种"):
        s5t_translate.run(_project(1), MagicMock(), lang="de")


def test_translate_truncates_overlong_model_output():
    # 模型不保证守约,超长译文必须先截断,否则撞 LocalizedTrack 的 240 上限直接抛
    p = _project(1)
    p = s5t_translate.run(p, _fake_llm({1: "A" * 400}), lang="en")
    assert len(p.storyboard[0].tracks["en"].caption) == 240


# ---------- 编辑:改译文只作废该语种,不动中文 ----------

def test_editing_track_caption_invalidates_only_that_language():
    p = _project(1)
    p.storyboard[0].audio = "audio/page_01.mp3"
    p.storyboard[0].duration_ms = 5000
    p.storyboard[0].tracks["en"] = LocalizedTrack(
        caption="old", audio="audio/page_01.en.mp3", duration_ms=4000)
    p.output = {"mp4": "output/final.mp4", "mp4_en": "output/final.en.mp4"}

    editing.update_track_caption(p, 1, "en", "new text")

    track = p.storyboard[0].tracks["en"]
    assert track.caption == "new text"
    assert track.audio == "" and track.duration_ms == 0   # 旧配音念的是旧稿,作废
    assert "mp4_en" not in p.output                        # 英文成片过期
    assert p.output["mp4"] == "output/final.mp4"           # 中文成片不受牵连
    assert p.storyboard[0].audio == "audio/page_01.mp3"    # 中文配音不受牵连


def test_mark_track_revoice_keeps_translation():
    p = _project(1)
    p.storyboard[0].tracks["en"] = LocalizedTrack(
        caption="keep me", audio="audio/page_01.en.mp3", duration_ms=4000)
    editing.mark_track_revoice(p, 1, "en")
    assert p.storyboard[0].tracks["en"].caption == "keep me"   # 只清音频,译文留着
    assert p.storyboard[0].tracks["en"].audio == ""


# ---------- 字幕时间轴 ----------

def test_clip_start_times_match_xfade_offsets():
    # 字幕起点必须与 xfade 实际的过渡起点一致:天真累加会随页数递增越漂越远
    durations = [2.5, 7.3, 6.8, 3.0]
    starts = subtitles.clip_start_times(durations)
    assert starts[0] == 0.0
    assert starts[1:] == xfade_offsets(durations, XFADE_S)
    # 每段重叠 XFADE_S,故 clip k 起点比"天真累加"早 k*XFADE_S
    assert starts[2] == pytest.approx(2.5 + 7.3 - 2 * XFADE_S)


def test_build_srt_format_and_skips_empty(tmp_path: Path):
    out = tmp_path / "s.srt"
    subtitles.build_srt([(0.0, 1.5, "first"), (2.0, 3.0, "   "), (4.0, 5.25, "third")], out)
    text = out.read_text(encoding="utf-8")
    assert "00:00:00,000 --> 00:00:01,500" in text
    assert "00:00:04,000 --> 00:00:05,250" in text
    assert "   " not in text.split("\n\n")[0]
    # 空文本被跳过,序号仍连续:只有 1 和 2 两条
    assert text.startswith("1\n") and "\n2\n" in text and "\n3\n" not in text


def test_srt_timestamp_handles_hours():
    assert subtitles._ts(3661.5) == "01:01:01,500"
    assert subtitles._ts(-5) == "00:00:00,000"   # 异常输入夹到 0,不产非法字幕


# ---------- 字幕轨封装 ----------

def test_mux_subtitles_cmd_maps_all_tracks_with_language_tags(tmp_path: Path):
    cmd = mux_subtitles_cmd(tmp_path / "in.mp4",
                            [(tmp_path / "zh.srt", "zho"), (tmp_path / "en.srt", "eng")],
                            tmp_path / "out.mp4")
    assert cmd.count("-map") == 4                     # v + a + 两条字幕
    assert "mov_text" in cmd                          # MP4 容器唯一广泛支持的字幕编码
    assert "-metadata:s:s:0" in cmd and "language=zho" in cmd
    assert "-metadata:s:s:1" in cmd and "language=eng" in cmd
    assert "copy" in cmd                              # 音视频不重编码


# ---------- S5 英文轨落到独立文件、不动中文 ----------

def test_s5_english_track_writes_separate_file_and_keeps_chinese(tmp_path: Path):
    p = _project(1)
    p.storyboard[0].audio = "audio/page_01.mp3"
    p.storyboard[0].duration_ms = 5000
    p.storyboard[0].tracks["en"] = LocalizedTrack(caption="At Broken Bridge.")
    tts = MagicMock()

    with patch("shanhai.steps.s5_audio._synthesize_full", return_value=4200):
        p = s5_audio.run(p, tts, "EN-Female", tmp_path, lang="en")

    track = p.storyboard[0].tracks["en"]
    assert track.audio == "audio/page_01.en.mp3"      # 独立文件名,不覆盖中文那份
    assert track.duration_ms == 4200
    assert p.storyboard[0].audio == "audio/page_01.mp3"   # 中文轨原样
    assert p.storyboard[0].duration_ms == 5000
    assert p.status["s5_en"] == "done"
    assert "s5" not in p.status                        # 不覆盖主语言的状态键


def test_s5_skips_pages_without_translation(tmp_path: Path):
    p = _project(2)
    p.storyboard[0].tracks["en"] = LocalizedTrack(caption="Only this page.")
    tts = MagicMock()
    with patch("shanhai.steps.s5_audio._synthesize_full", return_value=3000) as syn:
        p = s5_audio.run(p, tts, "EN-Female", tmp_path, lang="en")
    assert syn.call_count == 1                          # 没译文的页跳过,不合成空音频
    assert p.status["s5_en"] == "partial"               # 诚实标记未跑全


def test_s5_english_track_does_not_touch_bgm(tmp_path: Path):
    # BGM 与语言无关,主语言那轮已选好;英文轨重跑会白烧 GPU 且可能用曲库覆盖 AI BGM
    p = _project(1)
    p.bgm = "audio/bgm.mp3"
    p.storyboard[0].tracks["en"] = LocalizedTrack(caption="Hello.")
    with patch("shanhai.steps.s5_audio._synthesize_full", return_value=3000), \
         patch("shanhai.steps.s5_audio._resolve_bgm") as resolve:
        p = s5_audio.run(p, MagicMock(), "EN-Female", tmp_path, lang="en")
    resolve.assert_not_called()
    assert p.bgm == "audio/bgm.mp3"


# ---------- 网页字幕:本次反馈「英文配音已生成,尚无英文字幕」的成因与修复 ----------

def test_vtt_shares_cues_with_srt_and_uses_dot_separator(tmp_path: Path):
    """VTT 与 SRT 必须来自同一份 cues,只在时间戳分隔符上不同。
    两边各算一遍时间轴迟早漂移——而时间轴是这里最难查的东西(xfade 重叠)。"""
    cues = [(0.0, 2.5, "第一句"), (2.5, 5.0, ""), (5.0, 7.5, "第三句")]
    srt, vtt = tmp_path / "a.srt", tmp_path / "a.vtt"
    subtitles.build_srt(cues, srt)
    subtitles.build_vtt(cues, vtt)
    s, v = srt.read_text(encoding="utf-8"), vtt.read_text(encoding="utf-8")
    assert v.startswith("WEBVTT\n\n")            # 没有这个头浏览器直接拒收
    assert "00:00:00,000 --> 00:00:02,500" in s  # SRT 用逗号
    assert "00:00:00.000 --> 00:00:02.500" in v  # VTT 用点
    # 空文本 cue 两边都跳过,条目数一致
    assert s.count("-->") == v.count("-->") == 2


def test_vtt_mimetype_is_registered():
    """浏览器只接受 Content-Type 为 text/vtt 的 <track src>,给 octet-stream 会被**静默**
    拒绝——字幕就是不出来、控制台也未必报。StaticFiles 靠 mimetypes 猜,而那取决于运行
    环境的系统 mime 文件。api 模块必须显式注册,不能靠环境的运气。"""
    import mimetypes
    import shanhai.api  # noqa: F401 —— import 即触发 add_type
    assert mimetypes.guess_type("x.vtt")[0] == "text/vtt"


def test_mux_subtitles_marks_only_target_lang_default():
    """没有 default 时播放器一律选第一条轨,英文版就会弹中文字幕——用户观感即
    "没有英文字幕"(本次反馈的成因之一)。非目标轨要**显式**清 0,不写的话
    ffmpeg 会保留源流的 disposition。"""
    cmd = mux_subtitles_cmd(Path("v.mp4"),
                            [(Path("zh.srt"), "zho"), (Path("en.srt"), "eng")],
                            Path("o.mp4"), default_lang="eng")
    assert cmd[cmd.index("-disposition:s:0") + 1] == "0"
    assert cmd[cmd.index("-disposition:s:1") + 1] == "default"


def test_subtitle_langs_puts_current_lang_first():
    """本轮语种要排第一:播放器不认 disposition 时就靠顺序兜底。"""
    from shanhai.steps.s6_compose import _subtitle_langs
    p = _project(1)
    p.storyboard[0].tracks = {"en": LocalizedTrack(caption="English")}
    assert _subtitle_langs(p, "en") == ["en", "zh"]
    assert _subtitle_langs(p, "zh") == ["zh", "en"]
    # 没有任何译文时只剩主语言
    p.storyboard[0].tracks = {}
    assert _subtitle_langs(p, "zh") == ["zh"]
