from shanhai.safety import find_sensitive


def test_find_sensitive_no_hits_on_clean_text():
    assert find_sensitive("白娘子与许仙的传说,发生在西湖断桥") == []


def test_find_sensitive_detects_political_figure():
    assert "周恩来" in find_sensitive("1946年周恩来在南京梅园新村与国民党谈判")


def test_find_sensitive_detects_event_keyword():
    assert "文化大革命" in find_sensitive("这段历史发生在文化大革命期间")


def test_find_sensitive_empty_text():
    assert find_sensitive("") == []


def test_find_sensitive_dedupes_and_preserves_order():
    hits = find_sensitive("毛泽东与周恩来、毛泽东三人在延安")
    assert hits == ["毛泽东", "周恩来"]


# ---------- 误伤收敛:高歧义短独立词 ----------

def test_find_sensitive_no_false_hit_on_tai_du_substring():
    # "天台独特"含"台独"子串,但属无害组合,不应命中
    assert "台独" not in find_sensitive("站在天台独特的云海之上,风景绝美")


def test_find_sensitive_no_false_hit_on_stage_solo():
    # "舞台独白"含"台独"子串,亦无害
    assert find_sensitive("演员在舞台独白,道尽半生沧桑") == []


def test_find_sensitive_detects_real_separatism():
    # 真敏感表述仍拦:后缀"分子"/前缀"搞"
    assert "台独" in find_sensitive("台独分子妄图分裂国家")
    assert "藏独" in find_sensitive("境外势力搞藏独")
    assert "港独" in find_sensitive("香港独立是绝不容许的分裂图谋")


def test_find_sensitive_detects_bare_separatism_without_enumerated_pattern():
    # 复审重点:大量无歧义分裂表述过去从硬兜底漏过,裸匹配须一律命中
    for phrase in [
        "鼓吹台独",
        "主张台独",
        "台独思想根深蒂固",
        "坚持台独立场",
        "谋求台独",
        "实现台独的图谋",
        "台独万岁",
        "为台独张目",
    ]:
        assert "台独" in find_sensitive(phrase), phrase


def test_find_sensitive_more_benign_separatist_substrings():
    # 更多无害上/下词组合仍不误伤
    assert find_sensitive("戏台独角戏演得出神入化") == []
    assert find_sensitive("他在阳台独坐了一整夜") == []
    assert "疆独" not in find_sensitive("新疆独特的风光令人神往")
    assert "港独" not in find_sensitive("这是香港独家发行的纪念版")
    assert "藏独" not in find_sensitive("这份私人收藏独一无二")


# ---------- 误伤收敛:高歧义两字领导人名 ----------

def test_find_sensitive_no_false_hit_on_common_name():
    # 常见民用人名"李强"无党政头衔上下文,不应命中
    assert find_sensitive("村长李强带着乡亲修完了这条水渠") == []


def test_find_sensitive_short_figure_needs_title_context():
    # 带党政头衔上下文才判命中
    assert "李强" in find_sensitive("据报道,李强总理出席了会议")
    assert "朱德" in find_sensitive("朱德元帅是人民军队的缔造者之一")
