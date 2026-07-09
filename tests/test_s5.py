import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx, pytest, respx
from shanhai.providers.tts import TTSClient, TTSError
from shanhai.schema import Project, StoryboardCell
from shanhai.steps import s5_audio

BASE = "https://p.example.com/v1"


@respx.mock
def test_tts_client(tmp_path: Path):
    respx.post(f"{BASE}/audio/speech").mock(return_value=httpx.Response(
        200, headers={"content-type": "audio/mpeg"}, content=b"mp3bytes"))
    TTSClient(BASE, "sk", "tts-1").synthesize("你好", "alloy", tmp_path / "a.mp3")
    assert (tmp_path / "a.mp3").read_bytes() == b"mp3bytes"


@respx.mock
def test_tts_rejects_non_audio(tmp_path: Path):
    # 代理配额耗尽时常返回 200 + JSON 错误体
    respx.post(f"{BASE}/audio/speech").mock(return_value=httpx.Response(
        200, headers={"content-type": "application/json"}, content=b'{"error":"quota"}'))
    out = tmp_path / "a.mp3"
    with pytest.raises(TTSError):
        TTSClient(BASE, "sk", "tts-1").synthesize("你好", "alloy", out)
    assert not out.exists()                          # 非音频不落盘


@respx.mock
def test_tts_accepts_octet_stream(tmp_path: Path):
    # 部分代理用 application/octet-stream 返回合法音频,不应误拒
    respx.post(f"{BASE}/audio/speech").mock(return_value=httpx.Response(
        200, headers={"content-type": "application/octet-stream"}, content=b"\xff\xf3mp3"))
    TTSClient(BASE, "sk", "tts-1").synthesize("你好", "alloy", tmp_path / "a.mp3")
    assert (tmp_path / "a.mp3").read_bytes() == b"\xff\xf3mp3"


def _project() -> Project:
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.storyboard = [StoryboardCell(index=1, scene_ref="1-1", visual_desc="v",
                                   characters=[], caption="西湖初遇。", emotion="宁静")]
    return p


def _writing_tts() -> MagicMock:
    # synthesize 真写文件到 out(第3参),配合 _synthesize_full 的 tmp.replace(out)
    tts = MagicMock()
    tts.synthesize.side_effect = lambda text, voice, out: Path(out).write_bytes(b"\xff\xf3mp3")
    return tts


@patch("shanhai.steps.s5_audio.probe_duration_ms", return_value=6800)
def test_s5_fills_duration_and_bgm(mock_probe, tmp_path: Path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"tracks": [
        {"file": "calm.mp3", "emotions": ["宁静"], "license": "CC0"}]}), encoding="utf-8")
    tts = _writing_tts()
    p = s5_audio.run(_project(), tts, "alloy", tmp_path, manifest_path=manifest)
    assert p.storyboard[0].duration_ms == 6800
    assert p.bgm.endswith("calm.mp3")
    assert p.status["s5"] == "done"


@patch("shanhai.steps.s5_audio.probe_duration_ms", return_value=6800)
def test_s5_bgm_matches_dominant_emotion(mock_probe, tmp_path: Path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"tracks": [
        {"file": "calm.mp3", "emotions": ["宁静"], "license": "CC0"},
        {"file": "tense.mp3", "emotions": ["激烈"], "license": "CC0"}]}), encoding="utf-8")
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.storyboard = [
        StoryboardCell(index=1, scene_ref="1-1", visual_desc="v", characters=[],
                       caption="水漫金山。", emotion="激烈"),
        StoryboardCell(index=2, scene_ref="1-2", visual_desc="v", characters=[],
                       caption="法海来袭。", emotion="激烈"),
        StoryboardCell(index=3, scene_ref="1-3", visual_desc="v", characters=[],
                       caption="断桥重逢。", emotion="宁静"),
    ]
    p = s5_audio.run(p, _writing_tts(), "alloy", tmp_path, manifest_path=manifest)
    assert p.bgm.endswith("tense.mp3")               # 主导情绪选中非首条 track


@patch("shanhai.steps.s5_audio.probe_duration_ms", return_value=6800)
def test_s5_skips_existing_audio(mock_probe, tmp_path: Path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"tracks": [
        {"file": "calm.mp3", "emotions": ["宁静"], "license": "CC0"}]}), encoding="utf-8")
    p = _project()
    (tmp_path / "audio").mkdir(parents=True)
    (tmp_path / "audio" / "page_01.mp3").write_bytes(b"mp3")
    p.storyboard[0].audio = "audio/page_01.mp3"
    tts = MagicMock()
    s5_audio.run(p, tts, "alloy", tmp_path, manifest_path=manifest)
    tts.synthesize.assert_not_called()               # 已配音且文件在则跳过合成


@patch("shanhai.steps.s5_audio.probe_duration_ms", return_value=6800)
def test_s5_resynthesizes_when_file_missing(mock_probe, tmp_path: Path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"tracks": [
        {"file": "calm.mp3", "emotions": ["宁静"], "license": "CC0"}]}), encoding="utf-8")
    p = _project()                                   # caption="西湖初遇。"
    p.storyboard[0].audio = "audio/page_01.mp3"      # 引用的文件并不存在
    tts = _writing_tts()
    s5_audio.run(p, tts, "alloy", tmp_path, manifest_path=manifest)
    tts.synthesize.assert_called_once()              # 产物丢失则重新合成
    assert tts.synthesize.call_args.args[0] == "西湖初遇。"


@patch("shanhai.steps.s5_audio.probe_duration_ms")
def test_s5_retries_truncated_tts_keeps_longest(mock_probe, tmp_path: Path):
    # 小模型 TTS 偶发截断:首次 2000ms(< 10字×380=3800 floor,疑似截断)→ 重合成 5000ms,取长
    mock_probe.side_effect = [2000, 5000]
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"tracks": []}), encoding="utf-8")
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.storyboard = [StoryboardCell(index=1, scene_ref="1-1", visual_desc="v", characters=[],
                                   caption="一二三四五六七八九十", emotion="宁静")]
    tts = _writing_tts()
    p = s5_audio.run(p, tts, "alloy", tmp_path, manifest_path=manifest)
    assert tts.synthesize.call_count == 2            # 无标点走单句路径,截断后重合成
    assert p.storyboard[0].duration_ms == 5000       # 保留更长(完整)那次


def test_s5_split_clauses_unit():
    assert s5_audio._split_clauses("黄昏的西湖边，雷峰塔映入水中，像藏着一封千年未拆的旧信。") == \
        ["黄昏的西湖边，", "雷峰塔映入水中，", "像藏着一封千年未拆的旧信。"]  # 分隔符留在句末
    assert s5_audio._split_clauses("一二三四五六七八九十") == ["一二三四五六七八九十"]  # 无标点→单句
    assert s5_audio._split_clauses("好，走开。") == ["好，走开。"]              # 短碎片"好，"并入后句
    assert s5_audio._split_clauses("") == []                                # 空串→空


@patch("shanhai.ffmpeg.sh")
@patch("shanhai.steps.s5_audio.probe_duration_ms", return_value=5000)
def test_s5_splits_caption_into_clauses_and_concats(mock_probe, mock_sh, tmp_path: Path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"tracks": []}), encoding="utf-8")
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.storyboard = [StoryboardCell(index=1, scene_ref="1-1", visual_desc="v", characters=[],
                                   caption="黄昏的西湖边，雷峰塔映入水中，像藏着一封千年未拆的旧信。",
                                   emotion="宁静")]
    tts = _writing_tts()
    p = s5_audio.run(p, tts, "alloy", tmp_path, manifest_path=manifest)
    assert tts.synthesize.call_count == 3            # 每句各合成一次
    said = [c.args[0] for c in tts.synthesize.call_args_list]
    assert said == ["黄昏的西湖边，", "雷峰塔映入水中，", "像藏着一封千年未拆的旧信。"]
    concat_cmd = " ".join(mock_sh.call_args.args[0])  # 最后一次 sh 是拼接
    assert "-f concat" in concat_cmd                  # 分句音频拼成整页
    assert p.storyboard[0].duration_ms == 5000        # 拼接后真实总时长


@patch("shanhai.ffmpeg.sh")
@patch("shanhai.steps.s5_audio.probe_duration_ms", return_value=6800)
def test_s5_silent_fallback_on_tts_failure(mock_probe, mock_sh, tmp_path: Path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"tracks": []}), encoding="utf-8")
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.storyboard = [
        StoryboardCell(index=1, scene_ref="1-1", visual_desc="v", characters=[],
                       caption="坏页。", emotion="宁静"),
        StoryboardCell(index=2, scene_ref="1-2", visual_desc="v", characters=[],
                       caption="好页。", emotion="宁静"),
    ]
    calls = {"n": 0}
    def _synth(text, voice, out):                          # 坏页抛错,好页写文件
        calls["n"] += 1
        if calls["n"] == 1:
            raise TTSError("boom")
        Path(out).write_bytes(b"\xff\xf3mp3")
    tts = MagicMock(); tts.synthesize.side_effect = _synth
    p = s5_audio.run(p, tts, "alloy", tmp_path, manifest_path=manifest)
    assert p.storyboard[0].audio.endswith("page_01.mp3")   # 坏页走静音兜底,仍有音轨
    assert p.storyboard[0].duration_ms == 2500             # max(2500, 3字/4)
    assert mock_sh.called                                  # 调用了 ffmpeg 生成静音
    assert p.storyboard[1].audio.endswith("page_02.mp3")   # 好页正常合成
    assert p.status["s5"] == "done"                        # 全页有音频(含兜底)


@patch("shanhai.ffmpeg.sh", side_effect=RuntimeError("no ffmpeg"))
@patch("shanhai.steps.s5_audio.probe_duration_ms", return_value=6800)
def test_s5_partial_when_fallback_also_fails(mock_probe, mock_sh, tmp_path: Path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"tracks": []}), encoding="utf-8")
    p = _project()                                    # caption="西湖初遇。"
    tts = MagicMock(); tts.synthesize.side_effect = TTSError("boom")
    p = s5_audio.run(p, tts, "alloy", tmp_path, manifest_path=manifest)
    assert p.storyboard[0].audio == "" and p.storyboard[0].duration_ms == 0
    assert p.status["s5"] == "partial"               # TTS + 兜底均失败 -> partial
