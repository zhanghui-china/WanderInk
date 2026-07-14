import time
from pathlib import Path

import typer

from shanhai import auth, store
from shanhai.config import Settings
from shanhai.runtime_config import STAGE_CLIENTS, AppConfig, resolve_settings
from shanhai.providers.image import ImageClient
from shanhai.providers.llm import LLMClient
from shanhai.providers.music import MusicClient
from shanhai.providers.tts import TTSClient
from shanhai.steps import (s0_legend, s1_script, s2_storyboard, s3_characters,
                           s4_pages, s5_audio, s6_compose)
from shanhai.styles import STYLE_PRESETS

app = typer.Typer(help="WanderInk:景区传说有声连环画生成器(CLI 骨架)")

_MINUTES = (1, 3, 5)
_AUDIENCES = ("儿童", "大众")
_TONES = ("温情", "奇幻", "悬疑")


def _validate_params(minutes: int, audience: str, tone: str, style: str) -> None:
    """建/存项目前校验枚举参数,非法值快速失败,避免写入永久不可加载的 project.json。"""
    if minutes not in _MINUTES:
        raise typer.BadParameter(f"--minutes 须为 {'/'.join(map(str, _MINUTES))},收到 {minutes}")
    if audience not in _AUDIENCES:
        raise typer.BadParameter(f"--audience 须为 {'/'.join(_AUDIENCES)},收到 {audience}")
    if tone not in _TONES:
        raise typer.BadParameter(f"--tone 须为 {'/'.join(_TONES)},收到 {tone}")
    if style not in STYLE_PRESETS:
        raise typer.BadParameter(f"--style 须为 {'/'.join(STYLE_PRESETS)},收到 {style}")


def _clients(s: Settings) -> tuple[LLMClient, ImageClient, TTSClient, MusicClient]:
    llm_base, llm_key = s.llm_endpoint
    img_base, img_key = s.image_endpoint
    tts_base, tts_key = s.tts_endpoint
    music_base, music_key = s.music_endpoint
    if s.llm_provider == "ollama":
        from shanhai.providers.llm_ollama import OllamaLLMClient
        llm = OllamaLLMClient(llm_base, llm_key, s.llm_model, timeout=s.llm_timeout)
    else:
        llm = LLMClient(llm_base, llm_key, s.llm_model, timeout=s.llm_timeout)
    return (llm,
            ImageClient(img_base, img_key, s.image_model, s.image_api_mode),
            TTSClient(tts_base, tts_key, s.tts_model),
            MusicClient(music_base, music_key, s.music_model))


def resolve_stage_clients(
    cfg: AppConfig | None = None,
) -> tuple[dict[str, Settings], dict[str, tuple[LLMClient, ImageClient, TTSClient, MusicClient]]]:
    """为每个用到 client 的环节(STAGE_CLIENTS 键,S0–S5)解析生效 Settings 与 (llm,image,tts)。
    api._pipeline 与 cli.run 共用,避免两处硬编码环节列表与解析样板(环节列表以 STAGE_CLIENTS 为单一真源)。"""
    settings = {st: resolve_settings(st, cfg) for st in STAGE_CLIENTS}
    clients = {st: _clients(settings[st]) for st in settings}
    return settings, clients


def _apply_params(p, minutes: int, audience: str, tone: str, style: str) -> None:
    p.params.duration_min = minutes
    p.params.audience = audience
    p.params.tone = tone
    p.style_preset = style


def _read_story(story_file: Path | None) -> str | None:
    if story_file is None:
        return None
    try:
        return story_file.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise typer.BadParameter(f"故事文件需为 UTF-8 编码: {story_file}") from e


_STORY_FILE = typer.Option(None, exists=True, dir_okay=False, readable=True)


@app.command()
def new(scenic_spot: str, minutes: int = 3, audience: str = "大众", tone: str = "温情",
        style: str = "guofeng_ink", story_file: Path | None = _STORY_FILE):
    _validate_params(minutes, audience, tone, style)
    llm, _, _, _ = _clients(resolve_settings("s0"))
    story = _read_story(story_file)
    p = store.create_project(scenic_spot)
    _apply_params(p, minutes, audience, tone, style)
    if story is not None:
        p = s0_legend.from_text(p, llm, story)
    else:
        p = s0_legend.run(p, llm)
        for i, c in enumerate(p.legend_candidates, 1):
            typer.echo(f"  [{i}] {c.title}({c.source_type})- {c.summary[:60]}…")
    store.save(p)
    typer.echo(f"project_id: {p.project_id}")
    if story is None:
        typer.echo(f"下一步: shanhai pick {p.project_id} <序号>")


@app.command()
def pick(project_id: str, index: int):
    p = store.load(project_id)
    n = len(p.legend_candidates)
    if not 1 <= index <= n:
        raise typer.BadParameter(f"序号 {index} 越界:当前有 {n} 个候选,请传入 1..{n}")
    p.legend = p.legend_candidates[index - 1]
    store.save(p)
    typer.echo(f"已选定:{p.legend.title}")


@app.command()
def step(project_id: str, name: str):
    s = resolve_settings(name)
    llm, image, tts, music = _clients(s)
    p = store.load(project_id)
    workdir = store.project_dir(project_id)
    t0 = time.time()
    if name == "s1":
        p = s1_script.run(p, llm)
    elif name == "s2":
        p = s2_storyboard.run(p, llm)
    elif name == "s3":
        p = s3_characters.run(p, llm, image, workdir, s.image_size)
    elif name == "s4":
        p = s4_pages.run(p, image, workdir, s.image_size, strict=s.strict_consistency)
    elif name == "s5":
        p = s5_audio.run(p, tts, s.tts_voice, workdir, music)
    elif name == "s6":
        p = s6_compose.run(p, workdir)
    else:
        raise typer.BadParameter(f"未知步骤: {name}")
    store.save(p)
    typer.echo(f"{name} -> {p.status.get(name)}({time.time() - t0:.0f}s)")


@app.command()
def run(scenic_spot: str, minutes: int = 3, audience: str = "大众", tone: str = "温情",
        style: str = "guofeng_ink", story_file: Path | None = _STORY_FILE):
    """快速模式:自动选第一个候选传说,一路跑到 MP4。"""
    _validate_params(minutes, audience, tone, style)
    # 每环节各自解析生效 Settings 与 client(与 api._pipeline 同构:不同环节可用不同端点/模型)。
    settings, clients = resolve_stage_clients()
    story = _read_story(story_file)
    p = store.create_project(scenic_spot)
    _apply_params(p, minutes, audience, tone, style)
    workdir = store.project_dir(p.project_id)
    total0 = time.time()
    if story is not None:
        p = s0_legend.from_text(p, clients["s0"][0], story)
    else:
        p = s0_legend.run(p, clients["s0"][0])
        if not p.legend_candidates:
            typer.echo("没有检索到可靠传说,请用 --story-file 提供自备故事")
            raise typer.Exit(1)
        p.legend = p.legend_candidates[0]
    store.save(p)
    stages = [("s1", lambda: s1_script.run(p, clients["s1"][0])),
              ("s2", lambda: s2_storyboard.run(p, clients["s2"][0])),
              ("s3", lambda: s3_characters.run(p, clients["s3"][0], clients["s3"][1], workdir,
                                               settings["s3"].image_size)),
              ("s4", lambda: s4_pages.run(p, clients["s4"][1], workdir, settings["s4"].image_size,
                                          strict=settings["s4"].strict_consistency)),
              ("s5", lambda: s5_audio.run(p, clients["s5"][2], settings["s5"].tts_voice, workdir,
                                         clients["s5"][3])),
              ("s6", lambda: s6_compose.run(p, workdir))]
    for name, fn in stages:
        t0 = time.time()
        fn()
        store.save(p)
        st = p.status.get(name, "missing")
        mark = "完成" if st == "done" else f"⚠️ {st}"
        typer.echo(f"{name} {mark}({time.time() - t0:.0f}s)")
    typer.echo(f"总耗时 {(time.time() - total0) / 60:.1f} 分钟")
    if not any(c.status == "confirmed" and c.image and c.audio for c in p.storyboard):
        typer.echo("⚠️ 未生成任何完整正文页(缺画面或配音),成片仅含片头片尾,判定失败")
        raise typer.Exit(1)
    typer.echo(f"成片: {p.output.get('mp4')}")


@app.command()
def adduser():
    """交互式建账号:输入用户名+密码,bcrypt 哈希后落盘 users.json(仅供管理员用,不做批量导入)。"""
    username = typer.prompt("用户名")
    password = typer.prompt("密码", hide_input=True)
    try:
        auth.add_user(username, password)
    except ValueError as e:
        typer.echo(f"⚠️ {e}")
        raise typer.Exit(1) from e
    typer.echo(f"已写入 users.json: {username}")


@app.command()
def status(project_id: str):
    p = store.load(project_id)
    typer.echo(f"景区: {p.scenic_spot}  画风: {p.style_preset}")
    for k in ("s0", "s1", "s2", "s3", "s4", "s5", "s6"):
        typer.echo(f"  {k}: {p.status.get(k, 'pending')}")
    if p.output:
        typer.echo(f"  输出: {p.output}")
