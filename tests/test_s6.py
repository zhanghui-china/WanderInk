from pathlib import Path
from unittest.mock import patch

from shanhai.schema import Legend, Project, StoryboardCell
from shanhai.steps import s6_compose


def test_s6_builds_and_records_output(tmp_path: Path):
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.legend = Legend(title="白蛇传", summary="s", source_type="民间传说", sources=["《警世通言》"])
    (tmp_path / "pages").mkdir(parents=True); (tmp_path / "audio").mkdir()
    (tmp_path / "pages/page_01.png").write_bytes(b"png")
    (tmp_path / "audio/page_01.mp3").write_bytes(b"mp3")
    p.storyboard = [StoryboardCell(index=1, scene_ref="1-1", visual_desc="v", characters=[],
                                   caption="c", emotion="宁静", image="pages/page_01.png",
                                   audio="audio/page_01.mp3", duration_ms=6800,
                                   status="confirmed")]
    with patch("shanhai.steps.s6_compose.ffmpeg.sh") as sh, \
         patch("shanhai.steps.s6_compose.typeset.title_card"), \
         patch("shanhai.steps.s6_compose.typeset.credits_card"):
        p = s6_compose.run(p, tmp_path)
    assert p.output["mp4"].endswith("final.mp4")
    assert p.status["s6"] == "done"
    assert sh.call_count >= 4          # 片头 clip + 页 clip + 片尾 clip + concat + finalize
