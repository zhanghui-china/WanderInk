import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from shanhai import ffmpeg


def test_page_clip_cmd_duration_and_fade():
    cmd = " ".join(ffmpeg.page_clip_cmd(Path("p.png"), Path("a.mp3"), 6800, Path("o.mp4")))
    assert "-t 7.3" in cmd            # 6800ms + 500ms 缓冲
    assert "fade=t=in" in cmd and "fade=t=out" in cmd
    assert "1920:1080" in cmd and "yuv420p" in cmd
    assert "-ar 44100" in cmd and "-ac 2" in cmd  # 与静音分支采样率/声道对齐


def test_page_clip_cmd_silent():
    cmd = " ".join(ffmpeg.page_clip_cmd(Path("p.png"), None, 2500, Path("o.mp4")))
    assert "anullsrc" in cmd


def test_finalize_cmd_loudnorm_and_bgm():
    cmd = " ".join(ffmpeg.finalize_cmd(Path("v.mp4"), Path("b.mp3"), Path("o.mp4")))
    assert "loudnorm=I=-16" in cmd and "volume=0.18" in cmd and "amix" in cmd


def test_finalize_cmd_no_bgm():
    cmd = " ".join(ffmpeg.finalize_cmd(Path("v.mp4"), None, Path("o.mp4")))
    assert "loudnorm=I=-16" in cmd and "amix" not in cmd


def test_sh_surfaces_stderr_on_failure():
    err = subprocess.CalledProcessError(1, ["ffmpeg"], stderr=b"No such file or directory")
    with patch("shanhai.ffmpeg.subprocess.run", side_effect=err), \
         pytest.raises(RuntimeError, match="No such file or directory"):
        ffmpeg.sh(["ffmpeg", "-i", "x", "o.mp4"])


def test_probe_duration_ms_rejects_na():
    with patch("shanhai.ffmpeg.subprocess.run", return_value=SimpleNamespace(stdout="N/A\n")), \
         pytest.raises(ValueError, match="无法解析时长"):
        ffmpeg.probe_duration_ms(Path("bad.mp3"))
