"""内容安全兜底:通用敏感人物/话题关键词过滤。

本应用定位是"景区民间传说"有声连环画,不适合涉及真实近现代政治人物/事件的
戏剧化演绎。S0/S1 的 system prompt 已经"软引导"LLM 主动避开这类话题,这里
是关键词匹配的"硬兜底"——防止 LLM 不听话时仍把敏感内容漏过去。

这是轻量级关键词匹配,不是穷尽式审查;按需增删 SENSITIVE_TERMS 即可,
不需要改调用方代码。
"""

# 精确匹配词:三字及以上领导人全名(辨识度足够,裸子串误伤低)+ 明确的敏感事件词。
SENSITIVE_TERMS: list[str] = [
    # 近现代及当代最高党政领导人(不分是否在世)——真实政治人物不适合戏剧化演绎
    "毛泽东", "周恩来", "刘少奇", "邓小平", "李先念", "杨尚昆",
    "江泽民", "胡锦涛", "温家宝", "习近平", "李克强",
    "蒋介石", "蒋经国", "李登辉", "陈水扁", "马英九", "蔡英文", "赖清德",
    # 高度敏感的政治事件/话题
    "文化大革命", "六四", "天安门事件", "法轮功",
    "反右运动", "大跃进",
]

# 高歧义两字领导人名:朱德/陈云/李鹏/李强 本身也是极常见的民用姓名(村民"李强"、
# 邻居"陈云"),裸匹配误伤严重。故不裸匹配,要求邻接党政头衔上下文才算命中——
# 三字全名(周恩来/毛泽东等)辨识度够,仍走上面的精确匹配。
AMBIGUOUS_SHORT_FIGURES: list[str] = ["朱德", "陈云", "李鹏", "李强"]
FIGURE_TITLES: list[str] = ["总理", "主席", "总书记", "书记", "委员长", "元帅", "同志"]

# 高歧义短独立诉求词:台独/藏独/疆独/港独。方向必须"默认从严"——漏拦(真·分裂表述
# 穿过硬兜底)远比误伤(无害子串被拦)危险,故对这四个词做裸子串命中,再用"无害组合
# 白名单"反向排除,而不是枚举敏感模式才命中(枚举法天然漏,如"鼓吹台独/坚持台独立场"
# 都会穿过)。仅当某次出现确属已知无害组合才排除:前一字与地名字组成无害上词
# (天台/舞台/戏台/阳台/讲台;香港/海港;边疆/新疆;收藏/珍藏…),且"独"后一字组成无害
# 下词(独特/独白/独家/独奏/独坐…)。"独立"(下一字"立")本身即分裂诉求,绝不计入无害
# 下词。地名全称+独立(台湾独立/西藏独立/新疆独立/香港独立)另行精确命中,回报短词本身,
# 保持返回契约不变。
SEPARATIST_TERMS: list[str] = ["台独", "藏独", "疆独", "港独"]
# 全称 -> 回报的短词
SEPARATIST_FULL: dict[str, str] = {
    "台湾独立": "台独", "西藏独立": "藏独", "新疆独立": "疆独", "香港独立": "港独",
}
# 各地名字可组成无害上词的"前一字"(用于识别 天台/舞台/香港/新疆 等无害语境)
_SEP_BENIGN_PREV: dict[str, set[str]] = {
    "台": set("天舞戏阳讲楼亭柜窗月后前站擂吧灯钓"),
    "藏": set("收珍宝储埋私躲暗冷矿窖馆"),
    "疆": set("边新沙"),
    "港": set("香海军渔商货机河盐"),
}
# "独"后可组成无害下词的"后一字"(独特/独白/独家…);刻意不含"立",独立即分裂诉求
_SEP_BENIGN_NEXT: set[str] = set("特白角奏坐唱舞家自行处身生门到占揽木轮幕秀资享具苗善断一")


def _is_benign_separatist_occurrence(text: str, idx: int, term: str) -> bool:
    """判断 text[idx:idx+2] 处的短独立词是否落在已知无害组合中。
    需同时满足:前一字与地名字组成无害上词,且"独"后一字组成无害下词。"""
    region = term[0]
    prev_char = text[idx - 1] if idx > 0 else None
    next_char = text[idx + 2] if idx + 2 < len(text) else None
    return (
        prev_char is not None
        and prev_char in _SEP_BENIGN_PREV[region]
        and next_char is not None
        and next_char in _SEP_BENIGN_NEXT
    )


def _find_separatist(text: str) -> list[str]:
    """裸子串命中四个短独立词,反向排除无害组合;并精确命中地名全称+独立。"""
    hits: list[str] = []
    for full, term in SEPARATIST_FULL.items():
        if full in text:
            hits.append(term)
    for term in SEPARATIST_TERMS:
        start = 0
        while (idx := text.find(term, start)) != -1:
            if not _is_benign_separatist_occurrence(text, idx, term):
                hits.append(term)
                break  # 该词已命中,无需再看后续出现
            start = idx + 1
    return hits


def find_sensitive(text: str) -> list[str]:
    """返回 text 中命中的敏感词(去重、保出现顺序);无命中或空文本返回空列表。
    对外签名与返回不变(命中词列表);内部对高歧义两字名/短独立词加上下文约束以收敛误伤。"""
    if not text:
        return []
    hits: list[str] = [t for t in SENSITIVE_TERMS if t in text]
    # 两字领导人名:仅当紧跟党政头衔(如"李强总理""朱德元帅")才判命中
    for name in AMBIGUOUS_SHORT_FIGURES:
        if any(name + title in text for title in FIGURE_TITLES):
            hits.append(name)
    # 短独立诉求词:裸匹配 + 无害组合白名单反向排除(回报短词)
    hits.extend(_find_separatist(text))
    return list(dict.fromkeys(hits))
