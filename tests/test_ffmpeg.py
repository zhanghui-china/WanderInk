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
    cmd = " ".join(ffmpeg.page_clip_cmd(Path("p.png"), Path("ov.png"), Path("a.mp3"),
                                        6800, Path("o.mp4")))
    assert "-t 7.3" in cmd            # 6800ms + 500ms 缓冲
    assert "zoompan" in cmd           # Ken Burns 缓慢推拉取代淡入淡出
    assert "overlay=0:0" in cmd       # 静态字幕/水印层叠加在 zoompan 之后
    assert "-i ov.png" in cmd         # overlay 作为独立输入(不随 zoompan 推拉)
    assert cmd.index("zoompan") < cmd.index("overlay=0:0")  # 先推拉底图再叠静态层
    assert "fade=t=" not in cmd       # 不再淡入淡出到黑(改由 xfade 溶接)
    assert "s=1920x1080" in cmd and "yuv420p" in cmd
    assert "-ar 44100" in cmd and "-ac 2" in cmd  # 与静音分支采样率/声道对齐
    assert "-preset ultrafast" in cmd  # 中间 clip 后续会被 xfade 整片重编码,此处快编码省时省一代画质损失


def test_page_clip_cmd_zoom_direction_alternates():
    zin = " ".join(ffmpeg.page_clip_cmd(Path("p.png"), Path("ov.png"), Path("a.mp3"),
                                        3000, Path("o.mp4"), zoom_in=True))
    zout = " ".join(ffmpeg.page_clip_cmd(Path("p.png"), Path("ov.png"), Path("a.mp3"),
                                         3000, Path("o.mp4"), zoom_in=False))
    assert "min(1+" in zin            # 推近:zoom 1 → 1.08
    assert "max(1.08" in zout         # 拉远:zoom 1.08 → 1
    assert zin != zout                # 奇偶页方向不同


def test_page_clip_cmd_silent():
    cmd = " ".join(ffmpeg.page_clip_cmd(Path("p.png"), Path("ov.png"), None,
                                        2500, Path("o.mp4")))
    assert "anullsrc" in cmd
    assert "zoompan" in cmd
    assert "overlay=0:0" in cmd
    assert "-t 2.5" in cmd            # 静帧片头/片尾无缓冲(项目既有约定)


def test_concat_audio_cmd():
    cmd = " ".join(ffmpeg.concat_audio_cmd(
        [Path("a.mp3"), Path("b.mp3")], Path("list.txt"), Path("page.mp3")))
    assert "-f concat" in cmd and "-safe 0" in cmd and "list.txt" in cmd
    assert "libmp3lame" in cmd and "-ar 44100" in cmd and "-ac 2" in cmd and "page.mp3" in cmd


def test_trim_silence_cmd():
    cmd = " ".join(ffmpeg.trim_silence_cmd(Path("r.mp3"), Path("p.mp3")))
    assert "silenceremove" in cmd and cmd.count("areverse") == 2   # 两端剪
    assert "detection=peak" in cmd and "-45dB" in cmd
    assert "apad=pad_dur=0.18" in cmd
    assert "libmp3lame" in cmd and "-ar 44100" in cmd and "-ac 2" in cmd
    assert "-i r.mp3" in cmd and "p.mp3" in cmd
    assert "apad=pad_dur=0.12" in " ".join(ffmpeg.trim_silence_cmd(
        Path("r.mp3"), Path("p.mp3"), pad_s=0.12))


def test_still_clip_cmd_no_zoompan_no_overlay():
    cmd = " ".join(ffmpeg.still_clip_cmd(Path("t.png"), None, 2500, Path("o.mp4")))
    assert "zoompan" not in cmd       # 片头/片尾卡静止,烘焙文字不漂移
    assert "overlay=" not in cmd
    assert "-t 2.5" in cmd            # 静帧无缓冲
    assert "s=1920x1080" not in cmd and "scale=1920:1080" in cmd
    assert "yuv420p" in cmd and "-ar 44100" in cmd and "-ac 2" in cmd
    assert "-preset ultrafast" in cmd  # 中间 clip 快编码(同 page_clip_cmd)


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
    assert "ultrafast" not in cmd                    # 最终成片编码保持默认 preset,画质不降


def test_xfade_concat_cmd_opens_and_closes_on_black():
    # 全片首帧从黑淡入、末帧淡出到黑;xfade 只做页间过渡,不含全局开合
    clips = [Path("t.mp4"), Path("p1.mp4"), Path("c.mp4")]
    cmd = " ".join(ffmpeg.xfade_concat_cmd(clips, [2.5, 7.3, 3.0], Path("m.mp4")))
    assert cmd.count("fade=t=in") == 1               # 仅首段从黑淡入
    assert "fade=t=in:st=0" in cmd
    assert cmd.count("fade=t=out") == 1              # 仅末段淡出到黑
    assert "fade=t=out:st=2.5" in cmd                # 末段 3.0s − 0.5s 起淡出


def test_finalize_cmd_loudnorm_applies_to_voice_not_the_mix():
    """滤镜链的三条硬要求(每一条都对应一个已被用户报过的毛病):

    1. loudnorm 只作用于人声支路 `[0:a]`,**不能**作用在 `[mix]` 上——单遍 loudnorm 是
       动态的,挂在混音后会在人声间隙抬高整体增益、把配乐顶上来,即"背景音有时候比人声大";
    2. 配乐增益来自传入的实测值,不再是写死的 volume=0.18;
    3. amix 必须显式 normalize=0,否则默认会把每路除以 2(-6dB),而补回来的那个
       loudnorm 已经不在混音后了。"""
    cmd = " ".join(ffmpeg.finalize_cmd(Path("v.mp4"), Path("b.mp3"), Path("o.mp4"), -24.6))
    assert "[0:a]loudnorm=I=-16" in cmd
    assert "[mix]loudnorm" not in cmd
    assert "volume=-24.6dB" in cmd and "volume=0.18" not in cmd
    assert "sidechaincompress" in cmd
    assert "amix=inputs=2:duration=first:normalize=0" in cmd
    assert "alimiter" in cmd


def test_bgm_gain_is_computed_from_measured_loudness():
    """不管 ACE-Step 出的曲子多响,混完都恒定落在"比人声低 18 dB"(即 -34 LUFS)。
    线上实测的两个极端值:黄鹤楼 -9.4、华山 -21.4,跨度 12 dB。"""
    assert ffmpeg.bgm_gain_db(-9.4) == pytest.approx(-24.6)
    assert ffmpeg.bgm_gain_db(-21.4) == pytest.approx(-12.6)
    for lufs in (-9.4, -21.4, -11.5):
        assert lufs + ffmpeg.bgm_gain_db(lufs) == pytest.approx(
            ffmpeg.VOICE_TARGET_LUFS - ffmpeg.BGM_BELOW_VOICE_DB)


def test_measure_lufs_falls_back_loud_not_quiet(tmp_path: Path):
    """测量失败必须往"素材很响"的方向兜底:猜响 → 衰减更多 → 配乐偏轻,顶多不明显;
    猜轻 → 衰减不够 → 盖住解说,正是这次要修的毛病。方向锁,别写反。"""
    got = ffmpeg.measure_lufs(tmp_path / "does-not-exist.mp3")
    assert got == ffmpeg._LUFS_FALLBACK
    assert ffmpeg.bgm_gain_db(got) < -20, "兜底值必须导致大幅衰减"


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


# ---------- 超时:卡死的 ffmpeg 会永久占住线程池的槽,必须有上限 ----------

def test_sh_passes_a_timeout_by_default():
    """核心断言:调用真的带了 timeout。没有它,一次卡死就是永久阻塞。"""
    with patch("shanhai.ffmpeg.subprocess.run") as run:
        ffmpeg.sh(["ffmpeg", "-i", "x", "o.mp4"])
    assert run.call_args.kwargs["timeout"] == ffmpeg.FFMPEG_TIMEOUT_S


def test_sh_explicit_timeout_wins():
    with patch("shanhai.ffmpeg.subprocess.run") as run:
        ffmpeg.sh(["ffmpeg", "-i", "x", "o.mp4"], timeout=7)
    assert run.call_args.kwargs["timeout"] == 7


def test_sh_wraps_timeout_as_runtime_error():
    """必须包成 RuntimeError:uploads.to_voice_sample_wav 只 `except RuntimeError` 来转 400,
    裸 TimeoutExpired 漏出去会退化成 500;而且它的消息会把整条 ffmpeg 命令行(含
    filter_complex 巨串)经 _save_error 写进 project.json 并显示给用户。"""
    err = subprocess.TimeoutExpired(["ffmpeg"], 1800, stderr=b"frame= 120 fps=0.0")
    with patch("shanhai.ffmpeg.subprocess.run", side_effect=err), \
         pytest.raises(RuntimeError, match="超时") as ei:
        ffmpeg.sh(["ffmpeg", "-i", "x", "o.mp4"])
    assert "1800" in str(ei.value)              # 带上预算,好区分"卡住"与"真慢"
    assert "frame= 120" in str(ei.value)        # 带上卡住那一刻的进度行


def test_sh_timeout_message_survives_missing_stderr():
    """TimeoutExpired.stderr 在 POSIX 下有值,但不能假定——拼装要兜住 None。"""
    err = subprocess.TimeoutExpired(["ffmpeg"], 5, stderr=None)
    with patch("shanhai.ffmpeg.subprocess.run", side_effect=err), \
         pytest.raises(RuntimeError, match="超时"):
        ffmpeg.sh(["ffmpeg", "-i", "x", "o.mp4"])


def test_probe_duration_ms_wraps_timeout():
    err = subprocess.TimeoutExpired(["ffprobe"], 60, stderr="")
    with patch("shanhai.ffmpeg.subprocess.run", side_effect=err), \
         pytest.raises(RuntimeError, match="超时"):
        ffmpeg.probe_duration_ms(Path("hang.mp3"))


def test_sh_really_kills_the_child_on_timeout():
    """不 mock 的端到端:sh 收的就是一条命令,用 sleep 能真跑通「超时 → 强杀 → 抛错」。

    子进程真的被杀掉是这次修复的关键——否则超时只是 Python 侧不再等,机器上那个 ffmpeg
    仍在烧 CPU。subprocess.run 在超时后会 kill() 再 communicate(),这条用例验证的就是它。"""
    import time
    t0 = time.monotonic()
    with pytest.raises(RuntimeError, match="超时"):
        ffmpeg.sh(["sleep", "30"], timeout=1)
    elapsed = time.monotonic() - t0
    assert elapsed < 10, f"没有在超时后立刻返回(耗时 {elapsed:.1f}s),子进程可能没被杀掉"
