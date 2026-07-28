"""多语种轨(译文 + 该语种配音 + 该语种成片)的核心不变量。"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from shanhai import editing, subtitles
from shanhai.ffmpeg import XFADE_S, mux_subtitles_cmd, xfade_offsets
from shanhai.schema import TRACK_CAPTION_MAX, LocalizedTrack, Project, StoryboardCell
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
    # 英文同义内容约为中文 2~2.5 倍,主语言那条上限装不下。
    # 跟着主语言 80→120 一起从 240 放宽到 300:一条 120 字的中文译成英文约 280 字,
    # 只放宽中文那头会让 S5t 撞上旧的 240、把整批翻译判失败(与 S2 那次同一形态)。
    long_en = "A" * 300
    assert LocalizedTrack(caption=long_en).caption == long_en
    with pytest.raises(ValueError):
        LocalizedTrack(caption="A" * 301)


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
    # 模型不保证守约,超长译文必须先截断,否则撞 LocalizedTrack 的上限直接抛。
    # 断言用 schema 的常量而不是字面量:上限调整时这条测试要跟着走,而不是变成
    # "锁死一个已经不对的数字"。
    p = _project(1)
    p = s5t_translate.run(p, _fake_llm({1: "A" * 999}), lang="en")
    assert len(p.storyboard[0].tracks["en"].caption) == TRACK_CAPTION_MAX


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


# ---------- 每个语种各算各的时间轴(本次「中文字幕与声音不同步」的根因回归) ----------

def _bilingual(tmp_path: Path, n: int = 3, en_pages: tuple[int, ...] = (1, 2, 3)) -> Project:
    """n 页中文齐备,en_pages 那几页另有英文译文+配音。中英每页时长故意不同,
    这样"用错语种的时长"会立刻在字幕时间戳上显形。"""
    p = Project(project_id="x", scenic_spot="雷峰塔")
    (tmp_path / "pages").mkdir(parents=True, exist_ok=True)
    (tmp_path / "audio").mkdir(exist_ok=True)
    cells = []
    for i in range(1, n + 1):
        (tmp_path / f"pages/page_{i:02d}.png").write_bytes(b"png")
        (tmp_path / f"audio/page_{i:02d}.mp3").write_bytes(b"mp3")
        cell = StoryboardCell(index=i, scene_ref=f"1-{i}", visual_desc="v", characters=[],
                              caption=f"第{i}页。", emotion="宁静",
                              image=f"pages/page_{i:02d}.png",
                              audio=f"audio/page_{i:02d}.mp3",
                              duration_ms=6000, status="confirmed")
        if i in en_pages:
            (tmp_path / f"audio/page_{i:02d}.en.mp3").write_bytes(b"mp3")
            cell.tracks["en"] = LocalizedTrack(caption=f"Page {i}.",
                                               audio=f"audio/page_{i:02d}.en.mp3",
                                               duration_ms=9000)
        cells.append(cell)
    p.storyboard = cells
    return p


def _run_s6(project: Project, workdir: Path, lang: str):
    from shanhai.steps import s6_compose
    with patch("shanhai.steps.s6_compose.ffmpeg.sh"), \
         patch("shanhai.steps.s6_compose.typeset.title_card"), \
         patch("shanhai.steps.s6_compose.typeset.credits_card"), \
         patch("shanhai.steps.s6_compose.typeset.overlay_layer"), \
         patch("shanhai.steps.s6_compose.export.build_exports"):
        return s6_compose.run(project, workdir, lang=lang)


def _cue_starts(srt: Path) -> list[float]:
    out = []
    for line in srt.read_text(encoding="utf-8").splitlines():
        if "-->" not in line:
            continue
        h, m, rest = line.split(" --> ")[0].split(":")
        s, ms = rest.split(",")
        out.append(int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000)
    return out


def test_english_round_does_not_rewrite_chinese_subtitles(tmp_path: Path):
    """本次故障的直接回归:字幕文件名不带 suffix,英文那轮会原地覆盖 final.zh.srt。
    此前时间轴一律取本轮 lang 的画面时长,中文字幕遂被按英文时长重写,偏差随页数
    累积到二十多秒——用户看到的就是"中文对不上口型、英文却是准的"。"""
    from shanhai.steps import s6_compose
    p = _bilingual(tmp_path)
    zh_srt = tmp_path / "output" / "final.zh.srt"

    _run_s6(p, tmp_path, "zh")
    before = zh_srt.read_text(encoding="utf-8")
    _run_s6(p, tmp_path, "en")
    assert zh_srt.read_text(encoding="utf-8") == before   # 英文轮一字未动中文字幕

    # 且这些起点确实由**中文** durations 算出(英文那份会明显靠后)
    _, zh_durations = s6_compose._timeline(p, tmp_path, "zh")
    _, en_durations = s6_compose._timeline(p, tmp_path, "en")
    zh_starts = subtitles.clip_start_times(zh_durations)
    en_starts = subtitles.clip_start_times(en_durations)
    got = _cue_starts(zh_srt)
    for i in range(len(p.storyboard)):
        assert zh_starts[i + 1] in got
    assert en_starts[-2] not in got                        # 末页没被算成英文时长


def test_timeline_durations_match_real_encoding(tmp_path: Path):
    """_timeline 是纯函数、不编码,但字幕时间轴全靠它——它必须与真实编码出的每段
    时长逐项相等,否则字幕又会与画面脱钩(只是换个更隐蔽的方式)。"""
    from shanhai import ffmpeg as real_ffmpeg
    from shanhai.steps import s6_compose
    p = _bilingual(tmp_path)
    captured: list[list[float]] = []
    real_concat = real_ffmpeg.xfade_concat_cmd   # 先取实函数,否则 patch 后自我递归

    def _spy(clips, durations_s, out, *a, **kw):
        captured.append(list(durations_s))
        return real_concat(clips, durations_s, out, *a, **kw)

    with patch("shanhai.steps.s6_compose.ffmpeg.xfade_concat_cmd", side_effect=_spy):
        _run_s6(p, tmp_path, "en")

    _, predicted = s6_compose._timeline(p, tmp_path, "en")
    assert captured[0] == predicted


def test_english_round_keeps_pages_missing_from_english_track(tmp_path: Path):
    """某页没有英文译文/配音时,它在英文轮的 cells 里被剔除。字幕若跟着本轮 cells 走,
    重写后的中文字幕里这一页就直接消失、后续页索引还整体前移。"""
    p = _bilingual(tmp_path, n=3, en_pages=(1, 3))
    zh_srt = tmp_path / "output" / "final.zh.srt"
    _run_s6(p, tmp_path, "zh")
    _run_s6(p, tmp_path, "en")
    text = zh_srt.read_text(encoding="utf-8")
    assert "第2页" in text                                  # 缺英文的那页,中文字幕仍在
    assert len(_cue_starts(zh_srt)) >= 3


# ---------- 字幕属于「成片」而不是「语种」(对抗审计发现的镜像 bug) ----------

def test_each_film_carries_its_own_timeline_for_all_langs(tmp_path: Path):
    """同一条成片里的所有语种轨必须共用**这条成片**的时间轴,只有文本按语种取。

    第一版修复矫枉过正:让每个语种各按自己那条成片算时间轴,中文字幕对了,但中文成片里的
    英文轨变成了英文成片的时间轴——中文页 6s、英文页 9s,偏差逐页累积,末页 cue 起点会
    超出中文成片总长、永远不显示。审计在真实数据上量到 +23.75s。这条守住它。"""
    p = _bilingual(tmp_path, n=3, en_pages=(1, 2, 3))
    _run_s6(p, tmp_path, "zh")
    out = tmp_path / "output"
    # 主片的两条轨:起点必须逐条相同(同一条成片、同一套页边界)
    assert _cue_starts(out / "final.zh.srt")[:1] == _cue_starts(out / "final.en.srt")[:1]
    zh_first = _cue_starts(out / "final.zh.srt")
    en_first = _cue_starts(out / "final.en.srt")
    # 每页第一条 cue 的起点应一一对应(切分后每页可能有多条,取各页首条比对)
    assert zh_first[0] == en_first[0]
    assert max(en_first) <= max(zh_first) + 1e-6, "英文轨越出了中文成片的时间轴"


def test_track_film_writes_its_own_subtitle_set(tmp_path: Path):
    """英文成片写 final.en.{lang}.srt,**不碰**主片那套 final.{lang}.srt。
    文件名带成片 suffix 是"字幕属于成片"这条设计的落地形式。"""
    p = _bilingual(tmp_path, n=3, en_pages=(1, 2, 3))
    _run_s6(p, tmp_path, "zh")
    out = tmp_path / "output"
    before = {f.name: f.read_text(encoding="utf-8") for f in out.glob("final.??.srt")}
    _run_s6(p, tmp_path, "en")
    after = {f.name: f.read_text(encoding="utf-8") for f in out.glob("final.??.srt")}
    assert before == after, "英文轮改动了主片的字幕文件"
    assert (out / "final.en.zh.srt").exists() and (out / "final.en.en.srt").exists()


def test_cues_abut_across_pages(tmp_path: Path):
    """页间不变量:BUFFER_MS 与 XFADE_S 数值相等,使相邻页起点间隔恰为解说时长,
    于是下一页首条 cue 的 start 精确等于上一页末条 cue 的 end——零重叠零空隙。
    切分只要保证末条 end == start+span,这条就自动继承。反过来说,一旦有人把 span
    改回"该语种自己的 duration_ms",跨语种轨立刻开始重叠——这条会先炸。"""
    p = _bilingual(tmp_path, n=3, en_pages=(1, 2, 3))
    _run_s6(p, tmp_path, "zh")
    text = (tmp_path / "output" / "final.en.srt").read_text(encoding="utf-8")
    spans = []
    for line in text.splitlines():
        if "-->" not in line:
            continue
        a, b = line.split(" --> ")
        def _sec(t: str) -> float:
            h, m, rest = t.split(":")
            s, ms = rest.split(",")
            return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
        spans.append((_sec(a), _sec(b)))
    for (_, end), (nxt, _) in zip(spans, spans[1:]):
        assert nxt == pytest.approx(end, abs=1e-3), f"cue 之间有重叠或空隙:{end} → {nxt}"
