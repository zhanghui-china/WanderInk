# tests/test_styles.py
from shanhai.styles import STYLE_PRESETS

def test_three_presets():
    assert set(STYLE_PRESETS) == {"guofeng_ink", "kids_picture_book", "modern_illust"}
    assert all(len(v) > 10 for v in STYLE_PRESETS.values())
