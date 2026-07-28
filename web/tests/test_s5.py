import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx, pytest, respx
from shanhai.providers.music import MusicClient
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
    tts.synthesize.side_effect = \
        lambda text, voice, out, **kw: Path(out).write_bytes(b"\xff\xf3mp3")
    return tts


def _sh_creates_out(cmd):
    # 让 mock 的 ffmpeg.sh 产出命令末参指定的文件(trim/concat/silent 的输出),供后续 replace/probe
    Path(cmd[-1]).write_bytes(b"\xff\xf3mp3")


@patch("shanhai.ffmpeg.sh", side_effect=_sh_creates_out)
@patch("shanhai.steps.s5_audio.probe_duration_ms", return_value=6800)
def test_s5_fills_duration_and_bgm(mock_probe, mock_sh, tmp_path: Path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"tracks": [
        {"file": "calm.mp3", "emotions": ["宁静"], "license": "CC0"}]}), encoding="utf-8")
    tts = _writing_tts()
    p = s5_audio.run(_project(), tts, "alloy", tmp_path, manifest_path=manifest)
    assert p.storyboard[0].duration_ms == 6800
    assert p.bgm.endswith("calm.mp3")
    assert p.status["s5"] == "done"


@patch("shanhai.ffmpeg.sh", side_effect=_sh_creates_out)
@patch("shanhai.steps.s5_audio.probe_duration_ms", return_value=6800)
def test_s5_bgm_matches_dominant_emotion(mock_probe, mock_sh, tmp_path: Path):
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


@patch("shanhai.steps.s5_audio.probe_duration_ms", return_value=1200)
def test_s5_existing_audio_raised_to_min_ms(mock_probe, tmp_path: Path):
    # M6:续跑复用既有真人音轨也要套 MIN_MS 下限,否则短音轨在成片里一闪而过。
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"tracks": []}), encoding="utf-8")
    p = _project()
    (tmp_path / "audio").mkdir(parents=True)
    (tmp_path / "audio" / "page_01.mp3").write_bytes(b"mp3")
    p.storyboard[0].audio = "audio/page_01.mp3"
    s5_audio.run(p, MagicMock(), "alloy", tmp_path, manifest_path=manifest)
    assert p.storyboard[0].duration_ms == 2500       # 1200 < MIN_MS(2500)→ 抬到下限


@patch("shanhai.ffmpeg.sh", side_effect=_sh_creates_out)
@patch("shanhai.steps.s5_audio.probe_duration_ms", return_value=6800)
def test_s5_survives_manifest_track_missing_file(mock_probe, mock_sh, tmp_path: Path):
    # H4:manifest 是合法 JSON 但 track 缺 file 字段,不该抛 KeyError 拖垮已完成的合成。
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"tracks": [{"emotions": ["宁静"]}]}), encoding="utf-8")
    p = s5_audio.run(_project(), _writing_tts(), "alloy", tmp_path, manifest_path=manifest)
    assert p.bgm == ""                               # 选曲失败降级为无配乐
    assert p.storyboard[0].audio.endswith("page_01.mp3")   # 配音仍完成
    assert p.status["s5"] == "done"


@patch("shanhai.ffmpeg.sh", side_effect=_sh_creates_out)
@patch("shanhai.steps.s5_audio.probe_duration_ms", return_value=6800)
def test_s5_resynthesizes_when_file_missing(mock_probe, mock_sh, tmp_path: Path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"tracks": [
        {"file": "calm.mp3", "emotions": ["宁静"], "license": "CC0"}]}), encoding="utf-8")
    p = _project()                                   # caption="西湖初遇。"
    p.storyboard[0].audio = "audio/page_01.mp3"      # 引用的文件并不存在
    tts = _writing_tts()
    s5_audio.run(p, tts, "alloy", tmp_path, manifest_path=manifest)
    tts.synthesize.assert_called_once()              # 产物丢失则重新合成
    assert tts.synthesize.call_args.args[0] == "西湖初遇。"


@patch("shanhai.ffmpeg.sh", side_effect=_sh_creates_out)
@patch("shanhai.steps.s5_audio.probe_duration_ms", return_value=6800)
def test_s5_single_shot_preferred(mock_probe, mock_sh, tmp_path: Path):
    # P1:CosyVoice2 类稳定模型整段单发一次(不分句)。probe 6800 ≥ 28字×150 floor → 采用单发,
    # 仅一次合成、一次修剪、不拼接(避免弱模型时代逐句合成的多次调用与句间硬拼)。
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"tracks": []}), encoding="utf-8")
    p = Project(project_id="x", scenic_spot="雷峰塔")
    cap = "黄昏的西湖边，雷峰塔映入水中，像藏着一封千年未拆的旧信。"
    p.storyboard = [StoryboardCell(index=1, scene_ref="1-1", visual_desc="v", characters=[],
                                   caption=cap, emotion="宁静")]
    tts = _writing_tts()
    p = s5_audio.run(p, tts, "alloy", tmp_path, manifest_path=manifest)
    tts.synthesize.assert_called_once()              # 整段一次,不逐句
    assert tts.synthesize.call_args.args[0] == cap
    cmds = [" ".join(c.args[0]) for c in mock_sh.call_args_list]
    assert sum("silenceremove" in c for c in cmds) == 1     # 只整段修剪一次
    assert not any("-f concat" in c for c in cmds)          # 单发无需拼接
    assert p.storyboard[0].duration_ms == 6800


@patch("shanhai.ffmpeg.sh", side_effect=_sh_creates_out)
@patch("shanhai.steps.s5_audio.probe_duration_ms", return_value=6800)
def test_s5_uses_project_params_voice_and_speed(mock_probe, mock_sh, tmp_path: Path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"tracks": []}), encoding="utf-8")
    p = _project()
    p.params.voice = "shimmer"
    p.params.speed = 1.5
    tts = _writing_tts()
    s5_audio.run(p, tts, "alloy", tmp_path, manifest_path=manifest)
    assert tts.synthesize.call_args.args[1] == "shimmer"   # project.params.voice 覆盖传入 voice
    assert tts.synthesize.call_args.kwargs["speed"] == 1.5


@patch("shanhai.ffmpeg.sh", side_effect=_sh_creates_out)
@patch("shanhai.steps.s5_audio.probe_duration_ms", return_value=6800)
def test_s5_ai_bgm_parallel_with_tts(mock_probe, mock_sh, tmp_path: Path):
    # PERF:AI BGM 生成与逐页 TTS 并行。music.generate 成功 → project.bgm 取 AI 产物 bgm.mp3
    #(而非曲库),同时 TTS 照常完成、status done——并行不改 BGM 赋值与降级语义。
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"tracks": [
        {"file": "calm.mp3", "emotions": ["宁静"], "license": "CC0"}]}), encoding="utf-8")
    music = MagicMock()  # generate 成功(默认不抛),写出 bgm.mp3 由 _generate_ai_bgm 内部路径决定
    p = s5_audio.run(_project(), _writing_tts(), "alloy", tmp_path,
                     music=music, manifest_path=manifest)
    music.generate.assert_called_once()
    assert p.bgm.endswith("bgm.mp3")                 # AI 路径产物,而非曲库 calm.mp3
    assert p.storyboard[0].audio.endswith("page_01.mp3")   # TTS 仍完成
    assert p.status["s5"] == "done"


@patch("shanhai.ffmpeg.sh", side_effect=_sh_creates_out)
@patch("shanhai.steps.s5_audio.probe_duration_ms", return_value=6800)
def test_s5_ai_bgm_failure_degrades_to_manifest(mock_probe, mock_sh, tmp_path: Path):
    # 并行下 AI BGM 在独立线程里失败仍走三级降级(AI→静态曲库),不炸 TTS 或整个 S5。
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"tracks": [
        {"file": "calm.mp3", "emotions": ["宁静"], "license": "CC0"}]}), encoding="utf-8")
    music = MagicMock(); music.generate.side_effect = RuntimeError("shim 未部署")
    p = s5_audio.run(_project(), _writing_tts(), "alloy", tmp_path,
                     music=music, manifest_path=manifest)
    assert p.bgm.endswith("calm.mp3")                # 降级到静态曲库
    assert p.storyboard[0].audio.endswith("page_01.mp3")   # 配音不受 BGM 失败影响
    assert p.status["s5"] == "done"


def test_s5_split_clauses_unit():
    assert s5_audio._split_clauses("黄昏的西湖边，雷峰塔映入水中，像藏着一封千年未拆的旧信。") == \
        ["黄昏的西湖边，", "雷峰塔映入水中，", "像藏着一封千年未拆的旧信。"]  # 分隔符留在句末
    assert s5_audio._split_clauses("一二三四五六七八九十") == ["一二三四五六七八九十"]  # 无标点→单句
    assert s5_audio._split_clauses("好，走开。") == ["好，走开。"]              # 短碎片"好，"并入后句
    assert s5_audio._split_clauses("") == []                                # 空串→空


@patch("shanhai.ffmpeg.sh", side_effect=_sh_creates_out)
@patch("shanhai.steps.s5_audio.probe_duration_ms")
def test_s5_falls_back_to_chunked_when_truncated(mock_probe, mock_sh, tmp_path: Path):
    # P1 兜底:弱模型整段单发疑似截断(300ms << 28字×150=4200 floor)→ 退化逐句合成;
    # 且逐句路径里首句再截断(500<1050)时重合成取最长(1200)——保留旧防截断能力做兜底。
    # probe 序列:①单发300 ②③句1两试500/1200 ④句2=5000 ⑤句3=5000 ⑥拼接总时长8000
    mock_probe.side_effect = [300, 500, 1200, 5000, 5000, 8000]
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"tracks": []}), encoding="utf-8")
    p = Project(project_id="x", scenic_spot="雷峰塔")
    cap = "黄昏的西湖边，雷峰塔映入水中，像藏着一封千年未拆的旧信。"
    p.storyboard = [StoryboardCell(index=1, scene_ref="1-1", visual_desc="v", characters=[],
                                   caption=cap, emotion="宁静")]
    tts = _writing_tts()
    p = s5_audio.run(p, tts, "alloy", tmp_path, manifest_path=manifest)
    said = [c.args[0] for c in tts.synthesize.call_args_list]
    assert said[0] == cap                            # 首次整段单发(截断)
    assert said[1] == said[2] == "黄昏的西湖边，"      # 退化后首句因截断重合成
    assert said[3:] == ["雷峰塔映入水中，", "像藏着一封千年未拆的旧信。"]
    cmds = [" ".join(c.args[0]) for c in mock_sh.call_args_list]
    assert any("-f concat" in c for c in cmds)       # 退化路径拼接
    assert p.storyboard[0].duration_ms == 8000


@patch("shanhai.ffmpeg.sh", side_effect=_sh_creates_out)
@patch("shanhai.steps.s5_audio.probe_duration_ms", return_value=3000)
def test_s5_high_speed_not_judged_truncated(mock_probe, mock_sh, tmp_path: Path):
    # 批7a:floor 随 speed 缩放。28字、probe=3000ms。speed=2.0 → floor=round(28×150/2)=2100,
    # 3000≥2100 → 采用整段单发,不退化逐句(高语速正常语音不再被误判截断)。
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"tracks": []}), encoding="utf-8")
    p = Project(project_id="x", scenic_spot="雷峰塔")
    cap = "黄昏的西湖边，雷峰塔映入水中，像藏着一封千年未拆的旧信。"
    p.storyboard = [StoryboardCell(index=1, scene_ref="1-1", visual_desc="v", characters=[],
                                   caption=cap, emotion="宁静")]
    p.params.speed = 2.0
    tts = _writing_tts()
    p = s5_audio.run(p, tts, "alloy", tmp_path, manifest_path=manifest)
    tts.synthesize.assert_called_once()                     # 仅整段一次,未退化逐句
    cmds = [" ".join(c.args[0]) for c in mock_sh.call_args_list]
    assert not any("-f concat" in c for c in cmds)          # 无拼接
    assert p.storyboard[0].duration_ms == 3000              # 正常语音,不误判


@patch("shanhai.ffmpeg.sh", side_effect=_sh_creates_out)
@patch("shanhai.steps.s5_audio.probe_duration_ms", return_value=3000)
def test_s5_speed_one_still_chunks_same_audio(mock_probe, mock_sh, tmp_path: Path):
    # 对照(回归保护):同样 28字/probe=3000,speed=1.0 时 floor=4200,3000<4200 → 仍退化逐句。
    # 证明区别纯由 speed 缩放带来,speed=1.0 行为与改动前完全一致。
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"tracks": []}), encoding="utf-8")
    p = Project(project_id="x", scenic_spot="雷峰塔")
    cap = "黄昏的西湖边，雷峰塔映入水中，像藏着一封千年未拆的旧信。"
    p.storyboard = [StoryboardCell(index=1, scene_ref="1-1", visual_desc="v", characters=[],
                                   caption=cap, emotion="宁静")]  # speed 默认 1.0
    tts = _writing_tts()
    p = s5_audio.run(p, tts, "alloy", tmp_path, manifest_path=manifest)
    assert tts.synthesize.call_count == 4                   # 整段1 + 逐句3(退化路径)
    cmds = [" ".join(c.args[0]) for c in mock_sh.call_args_list]
    assert any("-f concat" in c for c in cmds)              # 退化拼接


@patch("shanhai.ffmpeg.sh", side_effect=_sh_creates_out)
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
    def _synth(text, voice, out, **kw):                     # 坏页抛错,好页写文件
        calls["n"] += 1
        if calls["n"] == 1:
            raise TTSError("boom")
        Path(out).write_bytes(b"\xff\xf3mp3")
    tts = MagicMock(); tts.synthesize.side_effect = _synth
    p = s5_audio.run(p, tts, "alloy", tmp_path, manifest_path=manifest)
    assert p.storyboard[0].audio.endswith("page_01.mp3")   # 坏页走静音兜底,仍有音轨
    assert p.storyboard[0].duration_ms == 2500             # max(2500, 3字/4)
    assert p.storyboard[0].silent is True                  # 坏页标记静音兜底
    assert mock_sh.called                                  # 调用了 ffmpeg 生成静音
    assert p.storyboard[1].audio.endswith("page_02.mp3")   # 好页正常合成
    assert p.storyboard[1].silent is False                 # 好页真人解说,非静音
    assert p.status["s5"] == "partial"                     # 含静音页 → 不诚实地报 done


@patch("shanhai.ffmpeg.sh", side_effect=_sh_creates_out)
@patch("shanhai.steps.s5_audio.probe_duration_ms", return_value=6800)
def test_s5_all_silent_is_partial(mock_probe, mock_sh, tmp_path: Path):
    # 全页 TTS 失败 → 全部静音兜底:仍有音轨但不算真人解说,状态须诚实为 partial
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"tracks": []}), encoding="utf-8")
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.storyboard = [
        StoryboardCell(index=1, scene_ref="1-1", visual_desc="v", characters=[],
                       caption="页一。", emotion="宁静"),
        StoryboardCell(index=2, scene_ref="1-2", visual_desc="v", characters=[],
                       caption="页二。", emotion="宁静"),
    ]
    tts = MagicMock(); tts.synthesize.side_effect = TTSError("boom")
    p = s5_audio.run(p, tts, "alloy", tmp_path, manifest_path=manifest)
    assert p.status["s5"] == "partial"                     # 全静音不算 done
    assert all(c.silent is True for c in p.storyboard)     # 每页均标记静音兜底
    assert all(c.audio for c in p.storyboard)              # 仍有静音音轨,成片完整


@patch("shanhai.ffmpeg.sh", side_effect=_sh_creates_out)
@patch("shanhai.steps.s5_audio.probe_duration_ms", return_value=6800)
def test_s5_resynthesizes_silent_fallback_on_rerun(mock_probe, mock_sh, tmp_path: Path):
    # C4:上轮静音兜底的页(audio 有值但 silent=True)重跑时不被跳过,应再次尝试真人合成
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"tracks": []}), encoding="utf-8")
    p = _project()                                         # caption="西湖初遇。"
    (tmp_path / "audio").mkdir(parents=True)
    (tmp_path / "audio" / "page_01.mp3").write_bytes(b"silent")
    p.storyboard[0].audio = "audio/page_01.mp3"
    p.storyboard[0].silent = True                          # 上轮静音兜底,文件已存在
    tts = _writing_tts()
    p = s5_audio.run(p, tts, "alloy", tmp_path, manifest_path=manifest)
    tts.synthesize.assert_called()                         # TTS 恢复,静音页重合成而非跳过
    assert p.storyboard[0].silent is False                 # 补回真人解说
    assert p.status["s5"] == "done"


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


@patch("shanhai.ffmpeg.sh", side_effect=_sh_creates_out)
@patch("shanhai.steps.s5_audio.probe_duration_ms", return_value=6800)
def test_s5_survives_missing_manifest(mock_probe, mock_sh, tmp_path: Path):
    # H4:manifest 缺失不抛,合成照常完成,仅跳过配乐(project.bgm 为空)
    p = _project()
    tts = _writing_tts()
    p = s5_audio.run(p, tts, "alloy", tmp_path, manifest_path=tmp_path / "nope.json")
    assert p.bgm == ""                                     # 无 manifest → 无配乐
    assert p.storyboard[0].audio.endswith("page_01.mp3")   # 各页仍有音轨,合成完整
    assert p.storyboard[0].silent is False
    assert p.status["s5"] == "done"


@patch("shanhai.ffmpeg.sh", side_effect=_sh_creates_out)
@patch("shanhai.steps.s5_audio.probe_duration_ms", return_value=6800)
def test_s5_survives_corrupt_manifest(mock_probe, mock_sh, tmp_path: Path):
    # H4:manifest 损坏(非法 JSON)不抛,合成照常完成
    bad = tmp_path / "manifest.json"
    bad.write_text("{not valid json", encoding="utf-8")
    p = _project()
    tts = _writing_tts()
    p = s5_audio.run(p, tts, "alloy", tmp_path, manifest_path=bad)
    assert p.bgm == ""                                     # 损坏 → 跳过配乐
    assert p.storyboard[0].audio.endswith("page_01.mp3")   # 合成不受影响
    assert p.status["s5"] == "done"


@patch("shanhai.ffmpeg.sh", side_effect=_sh_creates_out)
@patch("shanhai.steps.s5_audio.probe_duration_ms", return_value=2000)
def test_s5_raises_short_audio_to_min_ms(mock_probe, mock_sh, tmp_path: Path):
    # M6:真实合成 2000ms(≥ floor 1900,无重试)但 < MIN_MS(2500)→ 抬到 MIN_MS,避免页面一闪而过
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"tracks": []}), encoding="utf-8")
    p = _project()                                         # caption="西湖初遇。"(floor=5×380=1900)
    tts = _writing_tts()
    p = s5_audio.run(p, tts, "alloy", tmp_path, manifest_path=manifest)
    assert p.storyboard[0].duration_ms == s5_audio.MIN_MS  # 抬到最短显示时长
    assert p.storyboard[0].silent is False                 # 仍是真人解说,只是偏短


@patch("shanhai.ffmpeg.sh", side_effect=_sh_creates_out)
@patch("shanhai.steps.s5_audio.probe_duration_ms", return_value=6800)
def test_s5_parallel_pages_no_crosstalk(mock_probe, mock_sh, tmp_path: Path):
    # PERF1:多页并行,各自写各自音轨、各自时长,无串扰;状态诚实为 done
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"tracks": []}), encoding="utf-8")
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.storyboard = [
        StoryboardCell(index=i, scene_ref=f"1-{i}", visual_desc="v", characters=[],
                       caption=f"第{i}页解说词。", emotion="宁静")
        for i in range(1, 6)
    ]
    tts = _writing_tts()
    p = s5_audio.run(p, tts, "alloy", tmp_path, manifest_path=manifest)
    for i, c in enumerate(p.storyboard, start=1):
        assert c.audio.endswith(f"page_{i:02d}.mp3")       # 每页写各自文件,无串扰
        assert c.duration_ms == 6800                       # 各页时长独立正确
        assert c.silent is False
    assert p.status["s5"] == "done"


@patch("shanhai.ffmpeg.sh", side_effect=_sh_creates_out)
@patch("shanhai.steps.s5_audio.probe_duration_ms", return_value=6800)
def test_s5_cancel_check_stops_early(mock_probe, mock_sh, tmp_path: Path):
    # cancel_check 恒真 → 首个 future 一完成就停,S5_CONCURRENCY 之外排队的页不再启动
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"tracks": []}), encoding="utf-8")
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.storyboard = [
        StoryboardCell(index=i, scene_ref=f"1-{i}", visual_desc="v", characters=[],
                       caption=f"第{i}页解说词。", emotion="宁静")
        for i in range(1, 15)
    ]
    import time
    tts = MagicMock()
    def _slow_synth(text, voice, out, **kw):                # 加点耗时,确保排队页赶在完成前被取消
        time.sleep(0.05)
        Path(out).write_bytes(b"\xff\xf3mp3")
    tts.synthesize.side_effect = _slow_synth
    p = s5_audio.run(p, tts, "alloy", tmp_path, music=_writing_music(),
                     manifest_path=manifest, cancel_check=lambda: True)
    assert not all(c.audio and not c.silent for c in p.storyboard)  # 未全部配完就停了
    assert p.status["s5"] == "partial"


# ---------- AI BGM 三级降级(AI 生成 → 静态曲库 → 无 BGM) ----------

def _writing_music() -> MagicMock:
    music = MagicMock()
    music.generate.side_effect = \
        lambda prompt, duration_s, out, **kw: Path(out).write_bytes(b"\xff\xf3mp3")
    return music


@patch("shanhai.ffmpeg.sh", side_effect=_sh_creates_out)
@patch("shanhai.steps.s5_audio.probe_duration_ms", return_value=6800)
def test_s5_uses_ai_bgm_when_music_client_succeeds(mock_probe, mock_sh, tmp_path: Path):
    manifest = tmp_path / "manifest.json"
    # manifest 也配了曲目,证明 AI 生成优先、不会去查 manifest
    manifest.write_text(json.dumps({"tracks": [
        {"file": "calm.mp3", "emotions": ["宁静"], "license": "CC0"}]}), encoding="utf-8")
    p = s5_audio.run(_project(), _writing_tts(), "alloy", tmp_path,
                     music=_writing_music(), manifest_path=manifest)
    assert p.bgm == str(tmp_path / "audio" / "bgm.mp3")
    assert Path(p.bgm).read_bytes() == b"\xff\xf3mp3"


@patch("shanhai.ffmpeg.sh", side_effect=_sh_creates_out)
@patch("shanhai.steps.s5_audio.probe_duration_ms", return_value=6800)
def test_s5_falls_back_to_manifest_when_ai_bgm_fails(mock_probe, mock_sh, tmp_path: Path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"tracks": [
        {"file": "calm.mp3", "emotions": ["宁静"], "license": "CC0"}]}), encoding="utf-8")
    music = MagicMock()
    music.generate.side_effect = RuntimeError("shim 未部署")
    p = s5_audio.run(_project(), _writing_tts(), "alloy", tmp_path,
                     music=music, manifest_path=manifest)
    assert p.bgm.endswith("calm.mp3")                   # 降级到静态曲库


@patch("shanhai.ffmpeg.sh", side_effect=_sh_creates_out)
@patch("shanhai.steps.s5_audio.probe_duration_ms", return_value=6800)
def test_s5_no_bgm_when_ai_fails_and_manifest_empty(mock_probe, mock_sh, tmp_path: Path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"tracks": []}), encoding="utf-8")
    music = MagicMock()
    music.generate.side_effect = RuntimeError("shim 未部署")
    p = s5_audio.run(_project(), _writing_tts(), "alloy", tmp_path,
                     music=music, manifest_path=manifest)
    assert p.bgm == ""


@patch("shanhai.ffmpeg.sh", side_effect=_sh_creates_out)
@patch("shanhai.steps.s5_audio.probe_duration_ms", return_value=6800)
def test_s5_no_music_client_falls_back_to_manifest_unchanged(mock_probe, mock_sh, tmp_path: Path):
    # music 未传(默认 None)——行为须与改动前完全一致(回归保护)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"tracks": [
        {"file": "calm.mp3", "emotions": ["宁静"], "license": "CC0"}]}), encoding="utf-8")
    p = s5_audio.run(_project(), _writing_tts(), "alloy", tmp_path, manifest_path=manifest)
    assert p.bgm.endswith("calm.mp3")


def test_build_music_prompt_includes_tone_and_style_tags():
    p = _project()
    p.params.tone = "奇幻"
    p.style_preset = "guofeng_ink"
    prompt = s5_audio._build_music_prompt(p)
    assert "Mystical" in prompt
    assert "Guzheng" in prompt
    assert "Instrumental" in prompt


def test_target_music_duration_caps_at_max():
    p = _project()
    p.params.duration_min = 5
    assert s5_audio._target_music_duration_s(p) == 180.0    # 300s 封顶到 180.0
    p.params.duration_min = 1
    assert s5_audio._target_music_duration_s(p) == 60.0      # 未封顶


def test_lang_pace_thresholds_below_measured_speech_rate():
    """截断下限必须显著低于该语种的**实测**最快语速,否则正常语音会被判成截断,
    空转 TTS_TRIES 逐句退化(中文 380→150 那个坑的成因)。

    实测基准(DGX,写死在这里当回归基线,改常量时必须连同这里一起复核):
    - zh CosyVoice2/Qwen3-TTS:最快 221 ms/字符(镇国塔 20 页统计)
    - en Qwen3-TTS EN-Female:最快 54.1 ms/字符(2026-07-27 实测 5 段)
    """
    measured_min = {"zh": 221, "en": 54.1}
    for lang, fastest in measured_min.items():
        _, floor = s5_audio._pace(lang)
        assert floor < fastest, f"{lang} 下限 {floor} 不低于实测最快 {fastest},正常语音会被误判截断"
        assert floor / fastest < 0.8, f"{lang} 下限 {floor} 距实测最快 {fastest} 余量不足两成"


def test_lang_pace_falls_back_to_default_for_unknown_lang():
    assert s5_audio._pace("ja") == s5_audio.LANG_PACE[s5_audio.DEFAULT_LANG]


@patch("shanhai.ffmpeg.sh", side_effect=_sh_creates_out)
@patch("shanhai.steps.s5_audio.probe_duration_ms", return_value=6800)
def test_s5_bgm_switch_off_skips_generation(mock_probe, mock_sh, tmp_path: Path):
    """没勾配乐:一次 ACE-Step 都不该发——单曲最长 180s 且与生图抢同一块 GPU。
    曲库里明明有曲子也不选,免得"关了配乐却还是有音乐"。"""
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"tracks": [
        {"file": "calm.mp3", "emotions": ["宁静"]}]}), encoding="utf-8")
    p = _project()
    p.params.bgm = False
    music = _writing_music()
    p = s5_audio.run(p, _writing_tts(), "alloy", tmp_path, music=music, manifest_path=manifest)
    assert music.generate.call_count == 0
    assert p.bgm == ""
    assert p.status["bgm"] == "skipped"


@patch("shanhai.ffmpeg.sh", side_effect=_sh_creates_out)
@patch("shanhai.steps.s5_audio.probe_duration_ms", return_value=6800)
def test_s5_bgm_status_records_which_path_won(mock_probe, mock_sh, tmp_path: Path):
    """status['bgm'] 必须如实反映走了哪条路。改造前这里什么都不写,于是 music-shim 的
    模板路径写错攒了 33 个无配乐的作品才被用户发现——这条测试就是守着那个教训。"""
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"tracks": [
        {"file": "calm.mp3", "emotions": ["宁静"]}]}), encoding="utf-8")

    p = s5_audio.run(_project(), _writing_tts(), "alloy", tmp_path,
                     music=_writing_music(), manifest_path=manifest)
    assert p.status["bgm"] == "ai"

    failing = MagicMock()
    failing.generate.side_effect = RuntimeError("shim 500")
    p = s5_audio.run(_project(), _writing_tts(), "alloy", tmp_path,
                     music=failing, manifest_path=manifest)
    assert p.status["bgm"] == "manifest"

    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"tracks": []}), encoding="utf-8")
    p = s5_audio.run(_project(), _writing_tts(), "alloy", tmp_path,
                     music=failing, manifest_path=empty)
    assert p.bgm == ""
    # 用户勾了配乐却没拿到 = failed,不是"正常无配乐"。原代码把这条当正常路径、
    # 连日志都不打,正是问题攒了 33 个作品才暴露的原因。
    assert p.status["bgm"] == "failed"


def test_default_manifest_is_not_empty():
    """兜底曲库必须真的有曲子。

    这条补的是既有测试的盲区:所有 BGM 测试都用 mock 或 tmp_path 造的 manifest,
    **没有一条跑真实的 DEFAULT_MANIFEST**——所以"生产上曲库是空的、三级降级实际只有
    两级且都落空"这件事,测试根本测不出来,只能靠用户报障。
    """
    data = json.loads(s5_audio.DEFAULT_MANIFEST.read_text(encoding="utf-8"))
    tracks = data.get("tracks", [])
    assert tracks, "assets/bgm/manifest.json 是空的,兜底曲库形同虚设"
    covered = {e for t in tracks for e in t.get("emotions", [])}
    for t in tracks:
        assert (s5_audio.DEFAULT_MANIFEST.parent / t["file"]).exists(), f"曲库缺文件 {t['file']}"
    # 分镜可能出现的全部情绪都要有曲子兜底,否则 _select_manifest_bgm 会退化成"永远选第一首"
    assert {"宁静", "温情", "惊变", "悲壮", "险境", "烟雨", "苍凉"} <= covered
