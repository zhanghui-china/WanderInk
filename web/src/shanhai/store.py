import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from shanhai.schema import Project

DEFAULT_ROOT = Path("projects")

# 用户录制的音色样本目录。刻意**不**放进某个 project 目录:录音入口同时存在于「新建作品
# 表单」(那一刻还没有 project_id、没有目录)和「作品详情页」,放用户级才能让两个入口共用
# 同一个上传端点与同一份存储,只在上传完成后分叉成"建作品"或"改 params"。
# 但它放在 projects/ **内部**——为的是复用现成的 /files 静态挂载,用户才能回听自己录的样本;
# 下划线前缀 + 真实 project_id 恒为 uuid4 hex[:8],不会撞名。
# 已知欠账:删作品不会连带清掉样本(20 秒 16k 单声道约 640KB,量级可忽略)。
VOICE_SAMPLE_DIRNAME = "_voice_samples"


def voice_sample_dir(root: Path = DEFAULT_ROOT) -> Path:
    return root / VOICE_SAMPLE_DIRNAME

# project_id 形状校验:仅允许字母数字下划线短横线,堵住路径遍历(../、/、空白等)。
_PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def project_dir(project_id: str, root: Path = DEFAULT_ROOT) -> Path:
    """project_id 落盘路径的唯一入口:load/save/create 均经此函数,故此处校验一劳永逸。"""
    if not _PROJECT_ID_RE.fullmatch(project_id):
        raise ValueError(f"非法 project_id: {project_id!r}")
    return root / project_id


def create_project(scenic_spot: str, root: Path = DEFAULT_ROOT) -> Project:
    p = Project(
        project_id=uuid.uuid4().hex[:8],
        scenic_spot=scenic_spot,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    save(p, root=root)
    return p


def atomic_write_text(path: Path, text: str) -> None:
    """原子写文本文件:先写唯一临时名(pid+线程 id+uuid)再 os.replace 发布。
    多线程并发写同一路径时各写各的临时文件互不覆盖;固定 .tmp 名会被并发写者互相截断
    (torn write)或在另一线程 replace 后触发 FileNotFoundError。读者永远只见完整旧/新文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f"{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def save(p: Project, root: Path = DEFAULT_ROOT) -> None:
    atomic_write_text(project_dir(p.project_id, root) / "project.json", p.model_dump_json(indent=2))


def load(project_id: str, root: Path = DEFAULT_ROOT) -> Project:
    text = (project_dir(project_id, root) / "project.json").read_text(encoding="utf-8")
    return Project.model_validate_json(text)
