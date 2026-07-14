import pytest
from pydantic import ValidationError

from shanhai.schema import GenerationParams, Legend, Panel, Project, StoryboardCell


def test_project_roundtrip():
    p = Project(project_id="ab12", scenic_spot="雷峰塔")
    p2 = Project.model_validate_json(p.model_dump_json())
    assert p2.scenic_spot == "雷峰塔" and p2.params.duration_min == 3


def test_caption_max_80():
    with pytest.raises(ValidationError):
        StoryboardCell(index=1, scene_ref="1-1", visual_desc="x",
                       characters=[], caption="字" * 81, emotion="宁静")


def test_source_type_enum():
    with pytest.raises(ValidationError):
        Legend(title="t", summary="s", source_type="小道消息", sources=[])


def test_generation_params_multi_panel_default_false():
    p = Project(project_id="ab12", scenic_spot="雷峰塔")
    assert p.params.multi_panel is False


def test_storyboard_cell_panels_default_empty():
    c = StoryboardCell(index=1, scene_ref="1-1", visual_desc="x",
                       characters=[], caption="c", emotion="宁静")
    assert c.panels == []


def test_storyboard_cell_panels_roundtrip():
    c = StoryboardCell(index=1, scene_ref="1-1", visual_desc="x", characters=[],
                       caption="c", emotion="宁静",
                       panels=[Panel(visual_desc="v1", shot_type="closeup", characters=["白娘子"])])
    c2 = StoryboardCell.model_validate_json(c.model_dump_json())
    assert len(c2.panels) == 1
    assert c2.panels[0].shot_type == "closeup"
    assert c2.panels[0].characters == ["白娘子"]
