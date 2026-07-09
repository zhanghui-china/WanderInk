"""资源文件路径须为基于 __file__ 的绝对路径,API 以任意 cwd 启动也不崩。"""
from shanhai import typeset
from shanhai.steps import s5_audio


def test_font_path_absolute_and_exists():
    assert typeset.FONT_PATH.is_absolute()
    assert typeset.FONT_PATH.exists()


def test_bgm_manifest_path_absolute_and_exists():
    assert s5_audio.DEFAULT_MANIFEST.is_absolute()
    assert s5_audio.DEFAULT_MANIFEST.exists()
