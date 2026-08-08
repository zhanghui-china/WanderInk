"""skill 正文组装。

核心是「按需读哪几份」这个决定:Claude Skills 靠模型用文件工具自己选,我们没有文件工具,
改由作品参数直接算。选错=白烧上下文或缺关键方法论,而两者都不会报错,所以要锁住。
"""
import pytest

from shanhai import skills
from shanhai.schema import Project


def _p(minutes: int = 1, tone: str = "温情") -> Project:
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.params.duration_min = minutes
    p.params.tone = tone
    return p


def test_s1_picks_format_by_duration():
    """四份 format 一次只用得上一份。1/3 分钟是概念超短片,5 分钟是叙事短片;
    feature/series 是长片与剧集,我们的作品永远用不到——拼进去纯属白占上下文。"""
    assert skills._parts_for("s1", _p(1))[-1] == "references/format-ultrashort.md"
    assert skills._parts_for("s1", _p(3))[-1] == "references/format-ultrashort.md"
    assert skills._parts_for("s1", _p(5))[-1] == "references/format-short.md"
    for parts in (skills._parts_for("s1", _p(m)) for m in (1, 3, 5)):
        assert not any("feature" in x or "series" in x for x in parts)


def test_s2_picks_genre_by_tone():
    assert skills._parts_for("s2", _p(tone="奇幻"))[-1] == "references/genre-B-genre.md"
    assert skills._parts_for("s2", _p(tone="悬疑"))[-1] == "references/genre-B-genre.md"
    assert skills._parts_for("s2", _p(tone="温情"))[-1] == "references/genre-A-mood.md"


def test_s2_always_includes_the_must_read_set():
    """SKILL.md 的「参考资料索引」把 core-methodology 标为"每次创作都读";
    shot-design 与 storyboard-format 是第四五步(分镜拆解与出表)所必需——而我们单轮直出,
    等于一次要跑完这几步,所以三份都得在。"""
    parts = skills._parts_for("s2", _p())
    for must in ("SKILL.md", "references/core-methodology.md",
                 "references/shot-design.md", "references/storyboard-format.md"):
        assert must in parts


def test_missing_text_degrades_to_empty_not_partial(monkeypatch):
    """缺任何一份必读文件 → 整体返回空串(调用方据此降级为普通生成),
    **不拼一半**:半份 skill 产出什么无人知晓,而"看起来在用大师、其实是残缺版"
    比"明确降级"更难排查。"""
    real = skills._read

    def one_missing(skill: str, rel: str) -> str:
        return "" if rel.endswith("core-methodology.md") else (real(skill, rel) or "x")

    monkeypatch.setattr(skills, "_read", one_missing)
    assert skills.build_skill_prompt("s1", _p()) == ""


@pytest.mark.skipif(not skills.available(), reason="assets/skills/ 未取回(scripts/fetch-skills.py)")
def test_real_assets_assemble_and_carry_attribution():
    """有正文时:拼得出来、带署名、且量级与实测 hermes 加载的 ~20k token 同级。
    上界是防呆——整包塞进去(编剧 13.6 万字节)会显著超过 hermes 的用量,那说明选择逻辑失效了。"""
    text = skills.build_skill_prompt("s1", _p())
    assert "@山音" in text                      # MIT 要求保留署名
    assert 10_000 < len(text) < 60_000
    # 两个环节拼出来的必须不同(各读各的 skill),相同说明串了
    assert skills.build_skill_prompt("s2", _p()) != text
