import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from shanhai import ffmpeg


def test_silent_audio_cmd():
    cmd = " ".join(ffmpeg.silent_audio_cmd(6500, Path("s.mp3")))
    assert "anullsrc=r=44100:cl=stereo" in cmd
    assert "-t 6.5" in cmd and "libmp3lame" in cmd and "s.mp3" in cmd


def test_page_clip_cmd_duration_and_kenburns():
    cmd = " ".join(ffmpeg.page_clip_cmd(Path("p.png"), Path("a.mp3"), 6800, Path("o.mp4")))
    assert "-t 7.3" in cmd            # 6800ms + 500ms 缓冲
    assert "zoompan" in cmd           # Ken Burns 缓慢推拉取代淡入淡出
    assert "fade=t=" not in cmd       # 不再淡入淡出到黑(改由 xfade 溶接)
    assert "s=1920x1080" in cmd and "yuv420p" in cmd
    assert "-ar 44100" in cmd and "-ac 2" in cmd  # 与静音分支采样率/声道对齐


def test_page_clip_cmd_zoom_direction_alternates():
    zin = " ".join(ffmpeg.page_clip_cmd(Path("p.png"), Path("a.mp3"), 3000, Path("o.mp4"),
                                        zoom_in=True))
    zout = " ".join(ffmpeg.page_clip_cmd(Path("p.png"), Path("a.mp3"), 3000, Path("o.mp4"),
                                         zoom_in=False))
    assert "min(1+" in zin            # 推近:zoom 1 → 1.08
    assert "max(1.08" in zout         # 拉远:zoom 1.08 → 1
    assert zin != zout                # 奇偶页方向不同


def test_page_clip_cmd_silent():
    cmd = " ".join(ffmpeg.page_clip_cmd(Path("p.png"), None, 2500, Path("o.mp4")))
    assert "anullsrc" in cmd
    assert "zoompan" in cmd
    assert "-t 2.5" in cmd            # 静帧片头/片尾无缓冲(项目既有约定)


def test_clip_duration_s_buffer_only_with_audio():
    assert ffmpeg.clip_duration_s(6800, has_audio=True) == 7.3   # 解说 + 0.5s 缓冲
    assert ffmpeg.clip_duration_s(2500, has_audio=False) == 2.5  # 静帧无缓冲


def test_xfade_offsets_accumulate():
    # offset_k = Σ_{i≤k} d_i − (k+1)·T ;n 段 clip → n-1 段过渡
    offs = ffmpeg.xfade_offsets([2.5, 2.0, 1.7, 3.0], 0.5)
    assert len(offs) == 3
    assert offs == pytest.approx([2.0, 3.5, 4.7])


def test_xfade_offsets_single_clip_no_transition():
    assert ffmpeg.xfade_offsets([2.5], 0.5) == []


def test_xfade_concat_cmd_chain():
    clips = [Path("t.mp4"), Path("p1.mp4"), Path("c.mp4")]
    cmd = " ".join(ffmpeg.xfade_concat_cmd(clips, [2.5, 2.0, 3.0], Path("m.mp4")))
    assert cmd.count("xfade=transition=fade") == 2   # 3 clip → 2 段溶解
    assert "acrossfade" in cmd                       # 音频交叉淡接不交叠
    assert "offset=2.000" in cmd                     # 2.5 − 0.5
    assert "offset=3.500" in cmd                     # 2.5+2.0 − 2·0.5
    assert "[vout]" in cmd and "[aout]" in cmd
    assert "-i t.mp4" in cmd and "-i c.mp4" in cmd   # 片头/片尾纳入同一溶解链


def test_xfade_concat_cmd_opens_and_closes_on_black():
    # 全片首帧从黑淡入、末帧淡出到黑;xfade 只做页间过渡,不含全局开合
    clips = [Path("t.mp4"), Path("p1.mp4"), Path("c.mp4")]
    cmd = " ".join(ffmpeg.xfade_concat_cmd(clips, [2.5, 7.3, 3.0], Path("m.mp4")))
    assert cmd.count("fade=t=in") == 1               # 仅首段从黑淡入
    assert "fade=t=in:st=0" in cmd
    assert cmd.count("fade=t=out") == 1              # 仅末段淡出到黑
    assert "fade=t=out:st=2.5" in cmd                # 末段 3.0s − 0.5s 起淡出


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
