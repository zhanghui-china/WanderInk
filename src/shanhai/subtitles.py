"""SRT 软字幕生成。字幕不再烧进画面(见 typeset.overlay_image 的 caption 可选),
改由成片内嵌软字幕轨承载,观众可开关、可中英切换。

时间轴要点:成片是 xfade 交叉溶解拼接的,相邻片段重叠 XFADE_S 秒,所以每个 clip 在
最终时间轴上的起点**不是**前面时长的天真累加——必须走 ffmpeg.xfade_offsets 的同一套
偏移计算,否则字幕会随页数递增而越漂越远。
"""
from pathlib import Path

from shanhai import ffmpeg

Cue = tuple[float, float, str]   # (起, 止, 文本),单位秒

# 单条字幕的字数上限。取广播字幕的通行尺度:中文两行 × 每行 10 字上下,英文两行 ×
# 每行 42 字符(BBC/Netflix 都在这个量级)。一页解说本来就可能上百字,不切就是整屏糊脸。
CUE_MAX_CHARS: dict[str, int] = {"zh": 20, "en": 84}

MIN_CUE_S = 0.8   # 低于这个时长人眼来不及读,画面上只会闪一下

# 断句优先级:先在句末标点断(语义完整),不够再退到句中标点(勉强能读),
# 都不行才按字数硬切(最难看,所以放最后)。
_PUNCT: dict[str, tuple[str, str]] = {
    # 中文正文可能混着半角标点(LLM 出的文本并不总是全角),两套都收
    "zh": ("。！？；….!?;", "，、：,:"),
    "en": (".!?;", ","),
}


def _rules(lang: str) -> tuple[int, tuple[str, str], str, bool]:
    """取该语种的字数上限、断句标点、拼接分隔符,以及"标点后必须跟空白才算断点"。
    未知语种回落到中文档位——宁可切碎一点,也别让没配置过的语种整段糊在屏幕上。
    need_space 只对以空格分词的语种成立(见 _split_at_punct 里小数点/缩写那段)。"""
    key = lang if lang in CUE_MAX_CHARS and lang in _PUNCT else "zh"
    space = key == "en"
    return CUE_MAX_CHARS[key], _PUNCT[key], " " if space else "", space


# 断句后**不能**紧跟在下一段开头的字符:闭合引号/括号/书名号。它们语义上属于前一句,
# 甩到下一条会出现以 ”」》) 开头的病句(审计实测:『峰高千仞。』被切成 …千仞。/ ”后人…)。
_TRAILERS = "”’」』》）〕】>\"')]}"

# 后面跟空格、但**不是**句末的英文缩写。"标点后跟空白才算断点"这条规则能救 3.5 与 U.S.,
# 救不了 "Mr. Zhang"(它后面确实是空格)。没有词典就无法根治,这里只收最常见的几个;
# 漏掉的最坏后果是断句难看一点,不会篡改原文,所以不值得为它引入分词依赖。
_EN_ABBR = ("mr", "mrs", "ms", "dr", "prof", "st", "mt", "no", "vs", "etc", "e.g", "i.e")


def _split_at_punct(text: str, puncts: str, need_space: bool = False) -> list[str]:
    """按标点断句,标点**留在前一段末尾**——搬到下一段开头会变成"、这样开头"的病句。
    连续标点(……、?!)算一组不劈开;闭合引号/括号(_TRAILERS)也一并留在前段。

    need_space(英文用):**只有标点后面跟空白或到结尾才算断点**。英文的 `.` 同时是
    小数点和缩写点,无条件当句末会把 `3.5` 切成 `3.` / `5`、`U.S.` 切成 `U.` / `S.`,
    再被 _merge_short 用空格粘回去就成了 `3. 5` / `U. S.` —— **原文被篡改**。这是审计
    实测到的确定缺陷。断在空白处还有个附带好处:被 strip 掉的正是那个空格,合并时
    用 " " 补回来恰好还原原文。"""
    out: list[str] = []
    start = i = 0
    n = len(text)
    while i < n:
        if text[i] not in puncts:
            i += 1
            continue
        j = i + 1
        while j < n and text[j] in puncts:      # 连续标点算一组
            j += 1
        while j < n and text[j] in _TRAILERS:   # 闭合引号等属于前一句
            j += 1
        if need_space and j < n and not text[j].isspace():
            i = j                                # 小数点(3.5)/连写缩写(U.S.):不是断点
            continue
        if need_space and text[i] == "." \
                and text[start:i].rsplit(" ", 1)[-1].lower() in _EN_ABBR:
            i = j                                # "Mr. Zhang" 这类:点后有空格,但不是句末
            continue
        out.append(text[start:j])
        start = i = j
    if start < n:
        out.append(text[start:])
    return [s for s in (x.strip() for x in out) if s]


def _hard_split(text: str, limit: int) -> list[str]:
    """兜底的定长切分。英文优先在空格处断,否则会把单词从中间劈开。"""
    out = []
    while len(text) > limit:
        cut = text.rfind(" ", 0, limit + 1)
        if cut <= 0:
            cut = limit
        out.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        out.append(text)
    # 定长切分必然在末尾留下余数,而它切出的前段恰好等于 limit,_merge_short 的
    # 「合并后仍 ≤ limit」条件在这条路径上恒不成立(审计实测:「往下走」被劈成
    # 「往」/「下走」)。所以在这里就把最后两段再平分一次。
    if len(out) >= 2 and len(out[-1]) < max(1, limit // 3):
        joined = out[-2] + out[-1] if " " not in out[-2] else out[-2] + " " + out[-1]
        half = len(joined) // 2
        cut = joined.rfind(" ", 0, half + 1)
        cut = cut if cut > 0 else half
        out[-2:] = [joined[:cut].strip(), joined[cut:].strip()]
    return [s for s in out if s]


def _merge_short(segments: list[str], limit: int, join: str) -> list[str]:
    """把过短的碎片并回上一段。标点断句常留下"于是。"这种两三字的尾巴,
    一屏一个词比不切还难读。"""
    floor = max(1, limit // 3)
    out: list[str] = []
    for seg in segments:
        if out and (len(seg) < floor or len(out[-1]) < floor) \
                and len(out[-1]) + len(join) + len(seg) <= limit:
            out[-1] = out[-1] + join + seg
        else:
            out.append(seg)
    # 首段的碎片只能**向后**并:上面的循环 `out and ...` 对第一段恒不成立,于是
    # 「好。」这种开头会独占一条闪一下(审计实测)。这里补上唯一那次向后合并。
    if len(out) > 1 and len(out[0]) < floor and len(out[0]) + len(join) + len(out[1]) <= limit:
        out[:2] = [out[0] + join + out[1]]
    return out


def split_caption(text: str, lang: str) -> list[str]:
    """把一页解说切成若干条字幕文本(顺序即播出顺序)。
    文本本身不超上限时原样返回一条——为切而切只会让画面无谓地跳。"""
    text = (text or "").strip()
    if not text:
        return []
    limit, (primary, secondary), join, need_space = _rules(lang)
    if len(text) <= limit:
        return [text]

    segments = _split_at_punct(text, primary, need_space) or [text]
    segments = [
        s2
        for s in segments
        for s2 in (_split_at_punct(s, secondary, need_space) if len(s) > limit else [s])
    ]
    segments = [
        s2
        for s in segments
        for s2 in (_hard_split(s, limit) if len(s) > limit else [s])
    ]
    return _merge_short(segments, limit, join)


def _durations(weights: list[float], span: float, floor: float) -> list[float]:
    """按权重分配 span,并保证每段不低于 floor。低于 floor 的先钉死在 floor 上,
    剩余时长在其余段之间重新按权重分——一次性 clamp 会让总和溢出页时长。"""
    n = len(weights)
    if floor * n > span:
        return [span / n] * n          # 页太短,宁可都短也绝不能溢出到下一页
    fixed = [False] * n
    share = list(weights)
    while True:
        free = [i for i in range(n) if not fixed[i]]
        if not free:
            break
        rest = span - floor * (n - len(free))
        total = sum(weights[i] for i in free)
        for i in free:
            share[i] = rest * (weights[i] / total if total else 1.0 / len(free))
        below = [i for i in free if share[i] < floor]
        if not below:
            break
        for i in below:
            fixed[i] = True
            share[i] = floor
    return share


def spread(segments: list[str], start: float, span: float) -> list[Cue]:
    """把一页的时间窗 [start, start+span] 按字符数比例分给各段字幕。

    ⚠️ 这是**估算,不是对齐**。TTS 不回传逐词时间戳,按字符数分配隐含"匀速朗读"的
    假设;长短句交替、数字与专名念得慢时,单条会漂零点几秒(页边界仍然准,因为末段
    end 被钉死在 start+span)。真要逐词精确得做强制对齐(DGX 上有 Whisper),不在
    本次范围内——别把这里当成已经对齐过了。
    """
    if not segments:
        return []
    if span <= 0:
        # 异常输入(空页/时长缺失):产出零长 cue,绝不产出 end < start 的非法条目
        return [(start, start, s) for s in segments]

    durations = _durations([float(max(len(s), 1)) for s in segments], span, MIN_CUE_S)
    cues: list[Cue] = []
    t = start
    for i, seg in enumerate(segments):
        end = start + span if i == len(segments) - 1 else t + durations[i]
        cues.append((t, end, seg))     # 首尾相接:下一条的起点就是这条的终点
        t = end
    return cues




def clip_start_times(durations_s: list[float], t: float = ffmpeg.XFADE_S) -> list[float]:
    """每个 clip 在成片时间轴上的起始时刻(秒)。首段从 0 开始,其余取 xfade 过渡起点
    ——xfade_offsets 算的正是"下一段开始淡入"的时刻,与 clip 起点同义。"""
    if not durations_s:
        return []
    return [0.0, *ffmpeg.xfade_offsets(durations_s, t)]


def _ts(seconds: float) -> str:
    """秒 -> SRT 时间戳 HH:MM:SS,mmm(负值夹到 0,避免异常输入产出非法字幕)。"""
    ms = max(0, round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _blocks(cues: list[Cue], sep: str, setting: str = "") -> list[str]:
    """SRT 与 VTT 的唯一实质差别就是时间戳的毫秒分隔符(SRT 用逗号、VTT 用点),
    所以 cue 的筛选与编号逻辑必须共用——两边各写一遍迟早漂移。
    空文本的 cue 直接跳过(某页没有该语种译文时不产出空字幕块),序号按实际写出的
    条目连续编号,不留空洞。
    setting 只有 VTT 能传:SRT 的时间行后面跟任何东西,都会让一部分播放器
    (以及 ffmpeg 的 srt 解析)把整条当成畸形块丢掉,所以默认必须是空。"""
    out: list[str] = []
    tail = f" {setting}" if setting else ""
    for start, end, text in cues:
        text = text.strip()
        if not text:
            continue
        a, b = _ts(start).replace(",", sep), _ts(end).replace(",", sep)
        out.append(f"{len(out) + 1}\n{a} --> {b}{tail}\n{text}\n")
    return out


# 网页播放器的字幕排版。用户明确要求"高度调低一点",所以字号是**往下调**的(0.82em)。
# 第一版实现写成 1.05em、把字号调大了 5%,方向与需求相反,是审计抓出来的。
#
# 位置:**不写 line**,走 WebVTT 默认的 line:auto——浏览器把字幕贴在底部并自带一小段边距,
# 也就是 YouTube/Netflix 那种标准位置。
# ⚠️ 曾经写过 `line:-2`(把字幕往上抬两行),理由写的是"免得压住画面下沿的落款与水印"
# ——**那个理由是错的**:overlay_image 把水印画在 (宽-文字宽-24, y=20),也就是**右上角**;
# 底部那段文字绘制包在 `if caption:` 里,而成片走 overlay_layer("") 空 caption、根本不执行。
# 视频画面的下沿是干净的,当时等于为了躲一个不存在的东西把字幕抬了起来,用户随即反馈"太高"。
# 也别改成 `line:-1`:负数行号遇到多行 cue 时,规范要求渲染器再做一次"溢出就往上挪"的
# 重定位,各浏览器表现不一致;默认 auto 本来就把单行/多行两种情况都处理好了。
#
# ⚠️ 只对网页有效:SRT / mov_text 是纯文本格式,带不了任何样式与位置——下载下来用 VLC 看时,
# 字号与位置一律由播放器自己的设置决定,这里改什么都没用。
_VTT_STYLE = """STYLE
::cue {
  font-size: 0.82em;
  line-height: 1.35;
  background: rgba(0, 0, 0, 0.55);
}
"""
_VTT_CUE_SETTING = "align:center"


def build_srt(cues: list[Cue], out: Path) -> None:
    """写 SRT。给 ffmpeg 的 mov_text 内嵌字幕轨用(不带任何样式,见 _VTT_STYLE 的说明)。"""
    out.write_text("\n".join(_blocks(cues, ",")), encoding="utf-8")


def build_vtt(cues: list[Cue], out: Path) -> None:
    """写 WebVTT。**给网页播放器用**——浏览器的 HTML5 <video> 不解析 MP4 容器内的
    mov_text 字幕轨(Chrome/Firefox/Edge 一律忽略),网页里显示字幕唯一的办法是
    <track kind="subtitles"> 外挂 VTT;而 SRT 也不是浏览器认的格式。
    与 build_srt 共用同一份 cues,时间轴不重算(见 _blocks)。"""
    body = "\n".join(_blocks(cues, ".", _VTT_CUE_SETTING))
    out.write_text(f"WEBVTT\n\n{_VTT_STYLE}\n{body}", encoding="utf-8")
