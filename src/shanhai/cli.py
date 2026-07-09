import time
from pathlib import Path

import typer

from shanhai import store
from shanhai.config import Settings
from shanhai.providers.image import ImageClient
from shanhai.providers.llm import LLMClient
from shanhai.providers.tts import TTSClient
from shanhai.steps import (s0_legend, s1_script, s2_storyboard, s3_characters,
                           s4_pages, s5_audio, s6_compose)
from shanhai.styles import STYLE_PRESETS

app = typer.Typer(help="山海:景区传说有声连环画生成器(CLI 骨架)")

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


def _clients(s: Settings) -> tuple[LLMClient, ImageClient, TTSClient]:
    img_base, img_key = s.image_endpoint
    tts_base, tts_key = s.tts_endpoint
    return (LLMClient(s.base_url, s.api_key, s.llm_model),
            ImageClient(img_base, img_key, s.image_model, s.image_api_mode),
            TTSClient(tts_base, tts_key, s.tts_model))


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
    s = Settings()
    llm, _, _ = _clients(s)
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
    s = Settings()
    llm, image, tts = _clients(s)
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
        p = s4_pages.run(p, image, workdir, s.image_size)
    elif name == "s5":
        p = s5_audio.run(p, tts, s.tts_voice, workdir)
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
    s = Settings()
    llm, image, tts = _clients(s)
    story = _read_story(story_file)
    p = store.create_project(scenic_spot)
    _apply_params(p, minutes, audience, tone, style)
    workdir = store.project_dir(p.project_id)
    total0 = time.time()
    if story is not None:
        p = s0_legend.from_text(p, llm, story)
    else:
        p = s0_legend.run(p, llm)
        if not p.legend_candidates:
            typer.echo("没有检索到可靠传说,请用 --story-file 提供自备故事")
            raise typer.Exit(1)
        p.legend = p.legend_candidates[0]
    store.save(p)
    stages = [("s1", lambda: s1_script.run(p, llm)),
              ("s2", lambda: s2_storyboard.run(p, llm)),
              ("s3", lambda: s3_characters.run(p, llm, image, workdir, s.image_size)),
              ("s4", lambda: s4_pages.run(p, image, workdir, s.image_size)),
              ("s5", lambda: s5_audio.run(p, tts, s.tts_voice, workdir)),
              ("s6", lambda: s6_compose.run(p, workdir))]
    for name, fn in stages:
        t0 = time.time()
        fn()
        store.save(p)
        st = p.status.get(name, "missing")
        mark = "完成" if st == "done" else f"⚠️ {st}"
        typer.echo(f"{name} {mark}({time.time() - t0:.0f}s)")
    typer.echo(f"总耗时 {(time.time() - total0) / 60:.1f} 分钟")
    if not any(c.status == "confirmed" for c in p.storyboard):
        typer.echo("⚠️ 未生成任何正文页,成片仅含片头片尾,判定失败")
        raise typer.Exit(1)
    typer.echo(f"成片: {p.output.get('mp4')}")


@app.command()
def status(project_id: str):
    p = store.load(project_id)
    typer.echo(f"景区: {p.scenic_spot}  画风: {p.style_preset}")
    for k in ("s0", "s1", "s2", "s3", "s4", "s5", "s6"):
        typer.echo(f"  {k}: {p.status.get(k, 'pending')}")
    if p.output:
        typer.echo(f"  输出: {p.output}")
