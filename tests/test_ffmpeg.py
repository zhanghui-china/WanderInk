from pathlib import Path

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
