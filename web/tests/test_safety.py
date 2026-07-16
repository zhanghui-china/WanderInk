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
