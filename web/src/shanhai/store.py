import os
import re
import threading
import uuid
from pathlib import Path

from shanhai.schema import Project

DEFAULT_ROOT = Path("projects")

# project_id 形状校验:仅允许字母数字下划线短横线,堵住路径遍历(../、/、空白等)。
_PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def project_dir(project_id: str, root: Path = DEFAULT_ROOT) -> Path:
    """project_id 落盘路径的唯一入口:load/save/create 均经此函数,故此处校验一劳永逸。"""
    if not _PROJECT_ID_RE.fullmatch(project_id):
        raise ValueError(f"非法 project_id: {project_id!r}")
    return root / project_id


def create_project(scenic_spot: str, root: Path = DEFAULT_ROOT) -> Project:
    p = Project(project_id=uuid.uuid4().hex[:8], scenic_spot=scenic_spot)
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
