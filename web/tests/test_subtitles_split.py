"""解说切分与时间分配。一页解说可能上百字,一次性糊在屏幕上既读不完也挡画面,
所以要切成若干条按口播时间推进——这里守住"切得开、不越界、不重叠"三条。"""
from pathlib import Path

import pytest

from shanhai import subtitles
from shanhai.subtitles import CUE_MAX_CHARS, MIN_CUE_S, split_caption, spread

ZH_120 = "武当山位于湖北省西北部,是道教圣地。相传真武大帝在此修炼得道,历代帝王多有敕建。" \
         "明成祖朱棣曾征调三十万军民,历时十二年营建宫观,金顶立于天柱峰之巅,云海翻涌时" \
         "宛如仙境;时至今日,香火仍旺,登山朝拜者络绎不绝,山门之外常年排着长队等候。"

EN_300 = (
    "Wudang Mountain rises in the northwest of Hubei Province and has long been "
    "regarded as a sacred home of Taoism. Legend holds that the Emperor of the North "
    "attained the Way here. In the Ming dynasty three hundred thousand workers "
    "labored for twelve years to raise its temples, and the golden summit still "
    "stands above a restless sea of cloud."
)


# ---------- split_caption ----------

def test_zh_long_caption_split_within_limit():
    assert len(ZH_120) > 100
    parts = split_caption(ZH_120, "zh")
    assert len(parts) > 1
    assert all(len(p) <= CUE_MAX_CHARS["zh"] for p in parts), parts


def test_en_long_caption_split_within_limit():
    assert len(EN_300) > 280
    parts = split_caption(EN_300, "en")
    assert len(parts) > 1
    assert all(len(p) <= CUE_MAX_CHARS["en"] for p in parts), parts


def test_split_prefers_sentence_end_and_keeps_punctuation():
    """在句号处断,而不是数着字硬切;标点留在前一段末尾,否则下一段会以"。"开头。"""
    text = "第一句话说的是山。第二句话说的是水。第三句话说的是人。"
    parts = split_caption(text, "zh")
    assert parts == ["第一句话说的是山。", "第二句话说的是水。", "第三句话说的是人。"]


def test_english_split_does_not_break_words():
    parts = split_caption(EN_300, "en")
    assert " ".join(parts).split() == EN_300.split()


def test_short_caption_is_returned_as_one():
    assert split_caption("山高水长。", "zh") == ["山高水长。"]
    assert split_caption("A short line.", "en") == ["A short line."]


def test_blank_returns_empty():
    assert split_caption("", "zh") == []
    assert split_caption("   \n ", "en") == []


def test_unknown_lang_falls_back_to_zh_limit():
    parts = split_caption(ZH_120, "ja")
    assert all(len(p) <= CUE_MAX_CHARS["zh"] for p in parts)


def test_no_tiny_tail_fragments():
    """标点断句容易留下两三个字的尾巴,一屏一个词比不切还难读,必须并回上一段。"""
    text = "这是一段足够长的解说词用来触发切分逻辑。好。于是继续往下讲述这段故事的后半段。"
    parts = split_caption(text, "zh")
    assert "好。" not in parts, parts


# ---------- spread ----------

def test_spread_covers_window_exactly():
    parts = split_caption(ZH_120, "zh")
    cues = spread(parts, 12.0, 9.5)
    assert cues[0][0] == pytest.approx(12.0)
    assert cues[-1][1] == pytest.approx(12.0 + 9.5)   # 浮点累加误差必须收干净
    for (_, end, _), (nxt, _, _) in zip(cues, cues[1:]):
        assert nxt == pytest.approx(end)              # 首尾相接,不重叠也不留缝
    assert all(a < b for a, b, _ in cues)


def test_spread_is_proportional_to_length():
    cues = spread(["一" * 30, "一" * 10], 0.0, 8.0)
    assert cues[0][1] - cues[0][0] == pytest.approx(6.0)
    assert cues[1][1] - cues[1][0] == pytest.approx(2.0)


def test_spread_respects_min_cue():
    """短段按比例只能分到零点几秒,会闪一下就没——抬到 MIN_CUE_S,长段让出时间。"""
    cues = spread(["一" * 60, "好"], 0.0, 6.0)
    assert cues[1][1] - cues[1][0] == pytest.approx(MIN_CUE_S)
    assert cues[0][1] - cues[0][0] == pytest.approx(6.0 - MIN_CUE_S)


def test_spread_degrades_to_equal_split_when_window_too_short():
    """MIN_CUE_S * 段数 > span 时退化成等分:宁可每条都短,也绝不能溢到下一页。"""
    cues = spread(["甲", "乙", "丙", "丁"], 5.0, 2.0)
    assert cues[-1][1] == pytest.approx(7.0)
    for a, b, _ in cues:
        assert b - a == pytest.approx(0.5)


def test_spread_edge_cases():
    assert spread([], 3.0, 5.0) == []
    for a, b, _ in spread(["甲", "乙"], 4.0, 0.0):   # 时长缺失时也不能产出 end < start
        assert b >= a


# ---------- VTT 样式 / cue setting ----------

def test_vtt_has_style_block_and_cue_setting(tmp_path: Path):
    out = tmp_path / "a.vtt"
    subtitles.build_vtt([(0.0, 2.0, "山")], out)
    text = out.read_text(encoding="utf-8")
    assert text.startswith("WEBVTT\n\n")
    assert "STYLE" in text and "::cue" in text
    assert "00:00:00.000 --> 00:00:02.000 line:-2" in text


def test_srt_has_no_style_and_no_cue_setting(tmp_path: Path):
    """SRT 的时间行后面跟任何东西都可能让播放器把整块丢掉——这条最容易写岔。"""
    out = tmp_path / "a.srt"
    subtitles.build_srt([(0.0, 2.0, "山")], out)
    text = out.read_text(encoding="utf-8")
    assert "STYLE" not in text and "::cue" not in text
    time_line = [ln for ln in text.splitlines() if "-->" in ln]
    assert time_line == ["00:00:00,000 --> 00:00:02,000"]


# ---------- 对抗审计实测到的四个切分缺陷(每条都附了触发输入) ----------

def test_english_decimals_and_abbreviations_survive_intact():
    """英文的 `.` 同时是小数点和缩写点。无条件当句末会把 3.5 切成 3./5、U.S. 切成 U./S.,
    再被 _merge_short 用空格粘回去就成了 "3. 5" / "U. S." —— **原文被篡改**。
    判据是"拼回去必须与原文逐词相同",而不是"看起来没断错"。"""
    t = ("The golden summit rises 3.5 kilometers above the valley floor, and Mr. Zhang, "
         "who arrived from the U.S. in 1998, still recalls the bell that echoed there.")
    parts = subtitles.split_caption(t, "en")
    assert " ".join(parts).split() == t.split()
    assert "3.5" in " ".join(parts) and "U.S." in " ".join(parts)
    assert all(len(p) <= subtitles.CUE_MAX_CHARS["en"] for p in parts)


def test_chinese_closing_quote_stays_with_previous_cue():
    """判据只看"下一个字符是不是标点"时,」”》 会被甩到下一条开头,
    正是 _split_at_punct 自己 docstring 里说要避免的那种病句形态。"""
    t = "《武当志》载:“峰高千仞。”后人多有附会,说法不一,真伪早已难辨了,至今众说纷纭。"
    parts = subtitles.split_caption(t, "zh")
    assert not any(p[0] in "”’」』》)】" for p in parts), parts
    assert "".join(parts) == t


def test_hard_split_does_not_leave_two_char_tail():
    """定长切分的前段恰好等于 limit,余段再短也满足不了 _merge_short 的
    「合并后仍 ≤ limit」,于是硬切路径上永远留着两三字的尾巴(实测「往下走」→「往」/「下走」)。"""
    t = "前半句。这段足够长以便触发切分逻辑的解说词继续一直往下走。"
    parts = subtitles.split_caption(t, "zh")
    assert len(parts[-1]) >= 3, parts
    assert "".join(parts) == t


def test_vtt_font_size_is_reduced_not_enlarged():
    """用户的原话是"高度太高,调低一点"。第一版实现写成 font-size: 1.05em —— 把字号
    调**大**了 5%,方向与需求相反。这条钉住方向,免得后人又调回去。"""
    import re
    m = re.search(r"font-size:\s*([\d.]+)em", subtitles._VTT_STYLE)
    assert m and float(m.group(1)) < 1.0, subtitles._VTT_STYLE
