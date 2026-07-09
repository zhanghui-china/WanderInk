from pathlib import Path
from unittest.mock import MagicMock
from shanhai.schema import CharacterCard, Project, Script
from shanhai.steps import s3_characters

def test_s3_limits_to_four_and_saves(tmp_path: Path):
    chars = [CharacterCard(name=f"角色{i}", role="r", personality="p", appearance="白衣")
             for i in range(6)]
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.script = Script(title="t", theme="th", acts=[], characters=chars)
    llm = MagicMock(); llm.chat.return_value = "白衣女子,黑色长发,银簪"
    image = MagicMock(); image.generate.return_value = b"png"
    p = s3_characters.run(p, llm, image, tmp_path, "1536x1024")
    assert image.generate.call_count == 4          # 主要角色 ≤4(PRD F3)
    assert p.script.characters[0].locked is True
    assert (tmp_path / "characters" / "角色0.png").exists()
    assert p.script.characters[4].feature_prompt   # 次要角色也有文字特征
    assert p.script.characters[4].turnaround_image == ""


def test_s3_skips_locked_with_existing_turnaround(tmp_path: Path):
    chars = [CharacterCard(name=f"角色{i}", role="r", personality="p", appearance="白衣")
             for i in range(3)]
    chars[0].locked = True
    chars[0].turnaround_image = "characters/角色0.png"
    chars[0].feature_prompt = "已定稿特征"
    (tmp_path / "characters").mkdir(parents=True)
    (tmp_path / "characters" / "角色0.png").write_bytes(b"png")
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.script = Script(title="t", theme="th", acts=[], characters=chars)
    llm = MagicMock(); llm.chat.return_value = "新特征"
    image = MagicMock(); image.generate.return_value = b"png"
    p = s3_characters.run(p, llm, image, tmp_path, "1536x1024")
    assert image.generate.call_count == 2                     # 仅两个未锁定角色重绘
    assert p.script.characters[0].feature_prompt == "已定稿特征"  # 锁定角色未被覆盖
