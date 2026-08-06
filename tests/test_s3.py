import time
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
    # concurrency=1:位置式 side_effect(第 2 次失败)依赖调用顺序,串行才能确定命中第 2 个角色;
    # 并行时哪个角色拿到失败是非确定的(该场景的"单角色失败不拖垮其余"由下方 test_s3_parallel 覆盖)
    p = s3_characters.run(p, llm, image, tmp_path, "1536x1024", concurrency=1)
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


def test_s3_parallel_one_failure_does_not_abort_others(tmp_path: Path):
    # 并行(concurrency>1)下单角色失败仍不拖垮其余:哪个角色失败非确定,故按名字定向让「角色1」失败,
    # 断言与顺序无关——其余角色照常产出、失败判定仍为 partial、总调用次数不变。
    chars = [CharacterCard(name=f"角色{i}", role="r", personality="p", appearance=f"衣{i}")
             for i in range(3)]
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.script = Script(title="t", theme="th", acts=[], characters=chars)
    llm = MagicMock()
    llm.chat.side_effect = lambda _sys, user: f"特征-{user}"  # 特征含各自外貌,可定向
    image = MagicMock()

    def gen(prompt, **kw):
        if "衣1" in prompt:
            raise RuntimeError("Server disconnected")
        return b"png"
    image.generate.side_effect = gen
    p = s3_characters.run(p, llm, image, tmp_path, "1536x1024", concurrency=3)
    assert image.generate.call_count == 3                     # 三个角色都尝试
    assert p.script.characters[0].turnaround_image != ""      # 角色0 成功
    assert p.script.characters[1].turnaround_image == ""      # 角色1 失败,退化纯文字
    assert p.script.characters[1].locked is False
    assert p.script.characters[2].turnaround_image != ""      # 角色2 成功
    assert p.status["s3"] == "partial"


def test_s3_parallel_all_success_marks_done(tmp_path: Path):
    # 并行下全部成功仍逐角色产出、锁定并判定 done(与串行等价)。
    chars = [CharacterCard(name=f"角色{i}", role="r", personality="p", appearance="白衣")
             for i in range(3)]
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.script = Script(title="t", theme="th", acts=[], characters=chars)
    llm = MagicMock(); llm.chat.return_value = "白衣女子"
    image = MagicMock(); image.generate.return_value = b"png"
    p = s3_characters.run(p, llm, image, tmp_path, "1536x1024", concurrency=3)
    assert image.generate.call_count == 3
    assert p.status["s3"] == "done"
    assert all(c.locked for c in p.script.characters)
    for i in range(3):
        assert (tmp_path / "characters" / f"角色{i}.png").exists()


def test_s3_cancel_check_stops_early(tmp_path: Path):
    # cancel_check 每次都返回 True:第 1 个角色处理完成后即触发取消,
    # 尚未排上的角色被 pending.cancel() 拦掉,不应全部锁定,status 应为 partial。
    chars = [CharacterCard(name=f"角色{i}", role="r", personality="p", appearance="白衣")
             for i in range(3)]
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.script = Script(title="t", theme="th", acts=[], characters=chars)
    # ⚠️ 每个角色的活儿必须**慢到**主线程来得及在 as_completed 里看到第一个完成:
    # mock 瞬时返回时,concurrency=1 的 worker 线程可能在主线程走到取消判断之前就把 3 个
    # 角色全跑完,pending.cancel() 无从拦截 → 全部 locked → assert not True 挂掉。
    # 2026-07-28 在 DGX(机器更快)上真的撞到过一次,造成一次假警报的部署中断。
    llm = MagicMock()
    llm.chat.side_effect = lambda *a, **k: (time.sleep(0.05), "白衣女子,黑色长发,银簪")[1]
    image = MagicMock(); image.generate.return_value = b"png"
    p = s3_characters.run(p, llm, image, tmp_path, "1536x1024", concurrency=1,
                           cancel_check=lambda: True)
    assert not all(c.locked for c in p.script.characters)
    assert p.status["s3"] == "partial"


# ---------- 参考图驱动的三视图编辑(0→1 张参考图新增能力) ----------

def test_turnaround_tmpl_wording_unchanged():
    # 老模板是 DGX 实测过的资产,不能被后人顺手改坏措辞。
    assert s3_characters.TURNAROUND_TMPL == (
        "{style}。角色三视图设定图:同一角色的正面、侧面、背面全身像并排排列,"
        "纯白背景,画面中不要出现任何文字。角色:{feature}"
    )


def test_s3_with_reference_uses_edit_template_and_passes_references(tmp_path: Path):
    ref_dir = tmp_path / "characters" / "refs"
    ref_dir.mkdir(parents=True)
    ref_path = ref_dir / "ref_0.png"
    ref_path.write_bytes(b"ref")
    chars = [CharacterCard(name="角色0", role="r", personality="p", appearance="白衣",
                           reference_image="characters/refs/ref_0.png")]
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.script = Script(title="t", theme="th", acts=[], characters=chars)
    llm = MagicMock(); llm.chat.return_value = "白衣女子"
    image = MagicMock(); image.generate.return_value = b"png"
    s3_characters.run(p, llm, image, tmp_path, "1536x1024")
    args, kwargs = image.generate.call_args
    assert kwargs["references"] == [ref_path]
    assert "以参考图中的角色为准" in args[0]        # 走 TURNAROUND_REF_TMPL
    assert "{style}" not in args[0]                # ref 模板不含 style 占位


def test_s3_without_reference_uses_old_template_and_no_references(tmp_path: Path):
    chars = [CharacterCard(name="角色0", role="r", personality="p", appearance="白衣")]
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.script = Script(title="t", theme="th", acts=[], characters=chars)
    llm = MagicMock(); llm.chat.return_value = "白衣女子"
    image = MagicMock(); image.generate.return_value = b"png"
    s3_characters.run(p, llm, image, tmp_path, "1536x1024")
    args, kwargs = image.generate.call_args
    assert "references" not in kwargs
    assert "角色三视图设定图" in args[0]            # 走老的 TURNAROUND_TMPL


def test_fifth_character_with_reference_breaks_max_turnaround(tmp_path: Path):
    # MAX_TURNAROUND=4,第 5 个角色(index=4)默认不画,但传了参考图应突破限制。
    chars = [CharacterCard(name=f"角色{i}", role="r", personality="p", appearance="白衣")
             for i in range(5)]
    ref_dir = tmp_path / "characters" / "refs"
    ref_dir.mkdir(parents=True)
    (ref_dir / "ref_4.png").write_bytes(b"ref")
    chars[4].reference_image = "characters/refs/ref_4.png"
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.script = Script(title="t", theme="th", acts=[], characters=chars)
    llm = MagicMock(); llm.chat.return_value = "白衣女子"
    image = MagicMock(); image.generate.return_value = b"png"
    s3_characters.run(p, llm, image, tmp_path, "1536x1024")
    assert image.generate.call_count == 5
    assert p.script.characters[4].turnaround_image != ""
    assert p.status["s3"] == "done"


def test_fifth_character_without_reference_not_drawn(tmp_path: Path):
    chars = [CharacterCard(name=f"角色{i}", role="r", personality="p", appearance="白衣")
             for i in range(5)]
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.script = Script(title="t", theme="th", acts=[], characters=chars)
    llm = MagicMock(); llm.chat.return_value = "白衣女子"
    image = MagicMock(); image.generate.return_value = b"png"
    s3_characters.run(p, llm, image, tmp_path, "1536x1024")
    assert image.generate.call_count == 4
    assert p.script.characters[4].turnaround_image == ""
    assert p.status["s3"] == "done"                # 次要角色不参与 done 判定


def test_reference_edit_failure_falls_back_to_text_to_image(tmp_path: Path):
    ref_dir = tmp_path / "characters" / "refs"
    ref_dir.mkdir(parents=True)
    (ref_dir / "ref_0.png").write_bytes(b"ref")
    chars = [CharacterCard(name="角色0", role="r", personality="p", appearance="白衣",
                          reference_image="characters/refs/ref_0.png")]
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.script = Script(title="t", theme="th", acts=[], characters=chars)
    llm = MagicMock(); llm.chat.return_value = "白衣女子"
    image = MagicMock()
    image.generate.side_effect = [RuntimeError("编辑失败"), b"png"]
    s3_characters.run(p, llm, image, tmp_path, "1536x1024")
    assert image.generate.call_count == 2
    assert "references" not in image.generate.call_args_list[1].kwargs   # 第二次是纯文生图重试
    assert p.script.characters[0].locked is True
    assert p.script.characters[0].turnaround_image != ""
    assert p.status["s3"] == "done"


def test_reference_and_fallback_both_fail_keeps_reference_image(tmp_path: Path, capsys):
    ref_dir = tmp_path / "characters" / "refs"
    ref_dir.mkdir(parents=True)
    (ref_dir / "ref_0.png").write_bytes(b"ref")
    chars = [CharacterCard(name="角色0", role="r", personality="p", appearance="白衣",
                          reference_image="characters/refs/ref_0.png")]
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.script = Script(title="t", theme="th", acts=[], characters=chars)
    llm = MagicMock(); llm.chat.return_value = "白衣女子"
    image = MagicMock(); image.generate.side_effect = RuntimeError("boom")
    s3_characters.run(p, llm, image, tmp_path, "1536x1024")
    assert image.generate.call_count == 2                     # 编辑 1 次 + 文生图兜底 1 次,均失败
    assert p.script.characters[0].turnaround_image == ""
    assert p.script.characters[0].locked is False
    assert p.script.characters[0].reference_image == "characters/refs/ref_0.png"  # 用户上传不因失败被丢
    assert "两条路径均失败" in capsys.readouterr().out
    assert p.status["s3"] == "partial"


def test_fifth_character_with_reference_failure_marks_partial(tmp_path: Path):
    # 报告点名的最容易漏判的一行:有参考图的次要角色(突破 MAX_TURNAROUND)生成失败,
    # status["s3"] 必须是 partial,不能因为它本是"次要角色"就被 _draw_flags 豁免判定。
    chars = [CharacterCard(name=f"角色{i}", role="r", personality="p", appearance=f"衣{i}")
             for i in range(5)]
    ref_dir = tmp_path / "characters" / "refs"
    ref_dir.mkdir(parents=True)
    (ref_dir / "ref_4.png").write_bytes(b"ref")
    chars[4].reference_image = "characters/refs/ref_4.png"
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.script = Script(title="t", theme="th", acts=[], characters=chars)
    llm = MagicMock()
    llm.chat.side_effect = lambda _sys, user: f"特征-{user}"   # 特征含各自外貌,可定向失败

    def gen(prompt, **kw):
        if "衣4" in prompt:
            raise RuntimeError("boom")
        return b"png"
    image = MagicMock(); image.generate.side_effect = gen
    s3_characters.run(p, llm, image, tmp_path, "1536x1024", concurrency=1)
    assert p.script.characters[4].turnaround_image == ""
    assert all(c.turnaround_image for c in chars[:4])          # 其余角色不受影响
    assert p.status["s3"] == "partial"


def test_max_turnaround_total_caps_candidates(tmp_path: Path):
    # 4 个默认主角 + 10 个带参考图角色,候选总数 14 超过 MAX_TURNAROUND_TOTAL=12,
    # 超额的排在最后的 2 个角色不画。
    n_ref = 10
    chars = [CharacterCard(name=f"角色{i}", role="r", personality="p", appearance="白衣")
             for i in range(4 + n_ref)]
    ref_dir = tmp_path / "characters" / "refs"
    ref_dir.mkdir(parents=True)
    for i in range(4, 4 + n_ref):
        (ref_dir / f"ref_{i}.png").write_bytes(b"ref")
        chars[i].reference_image = f"characters/refs/ref_{i}.png"
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.script = Script(title="t", theme="th", acts=[], characters=chars)
    llm = MagicMock(); llm.chat.return_value = "白衣女子"
    image = MagicMock(); image.generate.return_value = b"png"
    s3_characters.run(p, llm, image, tmp_path, "1536x1024")
    assert image.generate.call_count == s3_characters.MAX_TURNAROUND_TOTAL
    assert chars[-1].turnaround_image == ""
    assert chars[-2].turnaround_image == ""
    assert p.status["s3"] == "done"


def test_feature_system_handles_non_human_characters():
    """FEATURE_SYSTEM 需要求先判断人类/非人类,非人类角色需先点出物种/形体。"""
    system = s3_characters.FEATURE_SYSTEM
    assert "非人类" in system
    assert "物种" in system or "形体" in system
    # 人类分支的原有槽位不能丢,回归保护
    assert "发型发色" in system and "服饰" in system
    # 物种判断只能依据身份与外貌,不许从名字推断(「小虎」不是老虎)
    assert "角色名" in system


def test_s3_feature_prompt_input_excludes_character_name(tmp_path: Path):
    """喂给 LLM 的角色信息不得带姓名:「小虎」这种名字会让 LLM 把人判成幼虎,
    对外貌描述零信息量,只有污染。"""
    chars = [CharacterCard(name="小虎", role="放牛娃", personality="莽撞",
                           appearance="十岁男孩,粗布短衫")]
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.script = Script(title="t", theme="th", acts=[], characters=chars)
    llm = MagicMock(); llm.chat.return_value = "十岁男孩,粗布短衫"
    image = MagicMock(); image.generate.return_value = b"png"
    s3_characters.run(p, llm, image, tmp_path, "1536x1024")

    _system, user = llm.chat.call_args[0]
    assert "姓名" not in user
    assert "小虎" not in user
    assert "放牛娃" in user and "粗布短衫" in user   # 身份/外貌等有效信息仍在


def test_s3_budget_not_consumed_by_already_locked_characters(tmp_path: Path):
    """已定稿的角色不该占掉 MAX_TURNAROUND_TOTAL 的名额。

    对抗审计发现的静默故障:前 4 个主角上一轮已 locked、本轮会被幂等跳过、一次生图请求
    都不发,但旧版 _draw_flags 照样给它们扣名额。于是"前 4 个已完成 + 用户给第 5~16 个
    角色都传了参考图"这个场景里,后面几个传了图的角色**永远画不出来**,而 status 仍是
    done、界面上没有任何异常,重跑多少次都一样(_draw_flags 是纯函数,不可自愈)。
    """
    total = s3_characters.MAX_TURNAROUND_TOTAL
    chars = [CharacterCard(name=f"角色{i}", role="r", personality="p", appearance="白衣")
             for i in range(total + 4)]
    (tmp_path / "characters").mkdir(parents=True)
    (tmp_path / "characters" / "refs").mkdir()
    # 前 4 个主角已定稿(有图、有文件、locked)
    for i in range(4):
        (tmp_path / "characters" / f"角色{i}.png").write_bytes(b"png")
        chars[i].turnaround_image = f"characters/角色{i}.png"
        chars[i].locked = True
    # 其余全部传了参考图 → 都是候选,且都还没画
    for i in range(4, len(chars)):
        rel = f"characters/refs/ref_{i}.png"
        (tmp_path / rel).write_bytes(b"png")
        chars[i].reference_image = rel

    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.script = Script(title="t", theme="th", acts=[], characters=chars)
    llm = MagicMock(); llm.chat.return_value = "白衣女子"
    image = MagicMock(); image.generate.return_value = b"png"
    p = s3_characters.run(p, llm, image, tmp_path, "1536x1024")

    # 名额全部留给真正需要生图的角色,而不是被那 4 个已定稿的白占
    assert image.generate.call_count == total
    for i in range(4, 4 + total):
        assert p.script.characters[i].locked is True, f"角色{i} 传了参考图却没画出来"


def test_turnaround_progress_denominator_matches_draw_flags(tmp_path: Path):
    """S3 进度的分母必须是「本轮真的会画的角色数」,不是角色总数。

    只有前 MAX_TURNAROUND 个主角、以及传了参考图的角色才会画,还有 MAX_TURNAROUND_TOTAL
    硬顶。拿总数当分母会永远停在 4/8 那样卡住不动;而在 api 层另算一遍判据必然与
    _draw_flags 漂移——这条同时守着"同源"这件事。
    """
    chars = [CharacterCard(name=f"角色{i}", role="r", personality="p", appearance="白衣")
             for i in range(8)]
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.script = Script(title="t", theme="th", acts=[], characters=chars)

    # 8 个角色但只有前 4 个是候选 → 分母 4,不是 8
    assert s3_characters.turnaround_progress(p, tmp_path) == (0, 4)

    # 前两个已出图 → 分子 2
    for i in range(2):
        chars[i].turnaround_image = f"characters/角色{i}.png"
    assert s3_characters.turnaround_progress(p, tmp_path) == (2, 4)

    # 第 6 个传了参考图 → 它也进候选,分母变 5
    (tmp_path / "characters" / "refs").mkdir(parents=True)
    rel = "characters/refs/ref_x.png"
    (tmp_path / rel).write_bytes(b"png")
    chars[5].reference_image = rel
    assert s3_characters.turnaround_progress(p, tmp_path) == (2, 5)


def test_turnaround_progress_without_script():
    """S1 之前 script 为 None,不能炸——前端在任何阶段都会读这个字段。"""
    p = Project(project_id="x", scenic_spot="雷峰塔")
    assert s3_characters.turnaround_progress(p, Path("/nonexistent")) == (0, 0)


def _framed_png_bytes() -> bytes:
    """模拟三视图那种"纯白背景 + 左右留白 + 内侧暗线"的图——正是边框判据的误判形态。
    与 tests/test_image_provider.py 的 _framed_png 同构,这里只需要能触发判据即可。"""
    from PIL import Image, ImageDraw
    import io
    im = Image.new("RGB", (1920, 1080), (250, 249, 246))
    d = ImageDraw.Draw(im)
    d.rectangle((52, 0, 58, im.height), fill=(20, 20, 20))                    # 左侧暗线
    d.rectangle((im.width - 58, 0, im.width - 52, im.height), fill=(20, 20, 20))  # 右侧暗线
    d.ellipse((400, 300, 1500, 800), fill=(120, 140, 160))                    # 框内有内容
    buf = io.BytesIO(); im.save(buf, format="PNG")
    return buf.getvalue()


def test_s3_is_not_killed_by_the_page_frame_check(tmp_path: Path):
    """边框判据是 S4 页面的合格性标准,不该套在 S3 的三视图上。

    三视图是"纯白背景 + 三个全身像并排",左右留白极易被判成竖框线。线上真的因此误杀过
    两次,其中一次是用户**传了参考图**、参考图编辑路径被判失败、回退到完全不用参考图的
    文生图——用户看到"三视图已生成"却发现不像自己传的图。
    这条用例守的就是:provider 层放行带框的图,S3 照常定稿。"""
    p = Project(project_id="x", scenic_spot="可可托海")
    p.script = Script(title="t", theme="th", acts=[],
                      characters=[CharacterCard(name="牧羊少年", role="r",
                                                personality="p", appearance="粗布羊毛衣")])
    llm = MagicMock(); llm.chat.return_value = "十岁少年,粗布羊毛衣"
    image = MagicMock(); image.generate.return_value = _framed_png_bytes()
    p = s3_characters.run(p, llm, image, tmp_path, "1536x1024")
    c = p.script.characters[0]
    assert c.turnaround_image == "characters/牧羊少年.png"
    assert c.locked is True
    assert (tmp_path / "characters" / "牧羊少年.png").exists()


# ---------- 三视图生成耗时(照 S4 页耗时那套做法)----------

def _one_char_project(**kw) -> Project:
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.script = Script(title="t", theme="th", acts=[],
                      characters=[CharacterCard(name="角色0", role="r", personality="p",
                                                appearance="白衣", **kw)])
    return p


def test_s3_records_turnaround_gen_ms_on_success(tmp_path: Path):
    p = _one_char_project()
    llm = MagicMock(); llm.chat.return_value = "白衣女子"
    image = MagicMock()
    image.generate.side_effect = lambda *a, **k: (time.sleep(0.02), b"png")[1]
    p = s3_characters.run(p, llm, image, tmp_path, "1536x1024")
    assert p.script.characters[0].turnaround_gen_ms >= 20


def test_s3_turnaround_gen_ms_zero_when_generation_fails(tmp_path: Path):
    """失败时跟着 turnaround_image 一起清零——留着上一次成功的读数就是条假信息。"""
    p = _one_char_project(turnaround_image="characters/角色0.png", turnaround_gen_ms=9999)
    llm = MagicMock(); llm.chat.return_value = "白衣女子"
    image = MagicMock(); image.generate.side_effect = RuntimeError("生图失败")
    p = s3_characters.run(p, llm, image, tmp_path, "1536x1024")
    c = p.script.characters[0]
    assert c.turnaround_image == "" and c.turnaround_gen_ms == 0


def test_s3_turnaround_gen_ms_includes_the_failed_reference_attempt(tmp_path: Path):
    """参考图编辑失败后回退文生图,耗时**包含**失败那次——那两次是两条不同路径各一次,
    用户实打实等了两遍。只记后一次会出现"显示生成 3s、实际等了 40s"。"""
    ref_dir = tmp_path / "characters" / "refs"
    ref_dir.mkdir(parents=True)
    (ref_dir / "ref_0.png").write_bytes(b"ref")
    p = _one_char_project(reference_image="characters/refs/ref_0.png")
    llm = MagicMock(); llm.chat.return_value = "白衣女子"

    def _slow_then_ok(*a, **k):
        time.sleep(0.02)
        if "references" in k:
            raise RuntimeError("编辑失败")
        return b"png"

    image = MagicMock(); image.generate.side_effect = _slow_then_ok
    p = s3_characters.run(p, llm, image, tmp_path, "1536x1024")
    assert image.generate.call_count == 2
    assert p.script.characters[0].turnaround_gen_ms >= 40   # 两次都算进去了


def test_s3_turnaround_gen_ms_overwritten_not_accumulated(tmp_path: Path):
    """重跑覆盖不累计,与 StoryboardCell.image_gen_ms 同语义。"""
    llm = MagicMock(); llm.chat.return_value = "白衣女子"

    p = _one_char_project()
    slow = MagicMock(); slow.generate.side_effect = lambda *a, **k: (time.sleep(0.05), b"png")[1]
    p = s3_characters.run(p, llm, slow, tmp_path, "1536x1024")
    first = p.script.characters[0].turnaround_gen_ms

    p.script.characters[0].locked = False          # 模拟重绘(mark_character_redraw 只解锁)
    fast = MagicMock(); fast.generate.side_effect = lambda *a, **k: (time.sleep(0.005), b"png")[1]
    p = s3_characters.run(p, llm, fast, tmp_path, "1536x1024")
    second = p.script.characters[0].turnaround_gen_ms
    assert 0 < second < first                      # 覆盖成新值,不是相加


def test_s3_locked_character_keeps_its_recorded_time(tmp_path: Path):
    """已定稿角色被 _already_done 跳过,耗时不该被清掉或改写。"""
    (tmp_path / "characters").mkdir()
    (tmp_path / "characters" / "角色0.png").write_bytes(b"png")
    p = _one_char_project(turnaround_image="characters/角色0.png",
                          turnaround_gen_ms=1234, locked=True)
    llm = MagicMock(); image = MagicMock()
    p = s3_characters.run(p, llm, image, tmp_path, "1536x1024")
    assert image.generate.call_count == 0
    assert p.script.characters[0].turnaround_gen_ms == 1234
