import pytest
from pydantic import ValidationError

from shanhai.schema import Legend, Project, StoryboardCell


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
