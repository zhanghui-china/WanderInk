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


def test_s3_single_character_failure_does_not_abort_others(tmp_path: Path, capsys):
    # 网络瞬时故障(如 httpx.RemoteProtocolError)在真实部署中重试耗尽后仍可能失败;
    # 单角色三视图失败应退化为纯文字特征,不拖垮其余角色/整条 pipeline(同 S4 单页容错模式)
    chars = [CharacterCard(name=f"角色{i}", role="r", personality="p", appearance="白衣")
             for i in range(3)]
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.script = Script(title="t", theme="th", acts=[], characters=chars)
    llm = MagicMock(); llm.chat.return_value = "白衣女子,黑色长发,银簪"
    image = MagicMock()
    image.generate.side_effect = [b"png", RuntimeError("Server disconnected"), b"png"]
    p = s3_characters.run(p, llm, image, tmp_path, "1536x1024")
    assert image.generate.call_count == 3                     # 三个角色都尝试,未中途中断
    assert p.script.characters[0].turnaround_image != ""      # 第 1 个成功
    assert p.script.characters[1].turnaround_image == ""      # 第 2 个失败,退化为纯文字
    assert p.script.characters[1].locked is False
    assert p.script.characters[2].turnaround_image != ""      # 第 3 个仍正常处理
    assert p.status["s3"] == "partial"
    assert "三视图生成失败" in capsys.readouterr().out


def test_s3_failure_clears_stale_turnaround_and_marks_partial(tmp_path: Path):
    # 关键回归:某主角上一轮留有旧三视图(turnaround_image 非空、未 locked)但本轮生成失败——
    # 旧图必须被清、角色解锁,status 不能因残留旧图冒充成功而误标 done。
    chars = [CharacterCard(name="主角", role="r", personality="p", appearance="白衣",
                           turnaround_image="characters/主角.png")]  # 非 locked,带旧图
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.script = Script(title="t", theme="th", acts=[], characters=chars)
    llm = MagicMock(); llm.chat.return_value = "白衣女子"
    image = MagicMock(); image.generate.side_effect = RuntimeError("boom")
    p = s3_characters.run(p, llm, image, tmp_path, "1536x1024")
    assert p.script.characters[0].turnaround_image == ""     # 旧图被清,不冒充成功
    assert p.script.characters[0].locked is False
    assert p.status["s3"] == "partial"


def test_s3_all_success_within_limit_marks_done(tmp_path: Path):
    # 回归保护:主角全部成功产出并锁定 → done(locked 判定不能把成功项目误标 partial)。
    chars = [CharacterCard(name=f"角色{i}", role="r", personality="p", appearance="白衣")
             for i in range(2)]
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.script = Script(title="t", theme="th", acts=[], characters=chars)
    llm = MagicMock(); llm.chat.return_value = "白衣女子"
    image = MagicMock(); image.generate.return_value = b"png"
    p = s3_characters.run(p, llm, image, tmp_path, "1536x1024")
    assert p.status["s3"] == "done"
    assert all(c.locked for c in p.script.characters)


def test_s3_secondary_characters_do_not_block_done(tmp_path: Path):
    # 回归保护:MAX_TURNAROUND 之外的次要角色本不绘三视图(未 locked、无图),不应把 done 拖成 partial。
    chars = [CharacterCard(name=f"角色{i}", role="r", personality="p", appearance="白衣")
             for i in range(6)]
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.script = Script(title="t", theme="th", acts=[], characters=chars)
    llm = MagicMock(); llm.chat.return_value = "白衣女子"
    image = MagicMock(); image.generate.return_value = b"png"
    p = s3_characters.run(p, llm, image, tmp_path, "1536x1024")
    assert p.status["s3"] == "done"
    assert p.script.characters[4].turnaround_image == ""     # 次要角色无三视图,却不影响 done


def test_feature_system_handles_non_human_characters():
    """FEATURE_SYSTEM 需要求先判断人类/非人类,非人类角色需先点出物种/形体。"""
    system = s3_characters.FEATURE_SYSTEM
    assert "非人类" in system
    assert "物种" in system or "形体" in system
    # 人类分支的原有槽位不能丢,回归保护
    assert "发型发色" in system and "服饰" in system
