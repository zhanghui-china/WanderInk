import os
import threading
import uuid
from pathlib import Path

from shanhai.schema import Project

DEFAULT_ROOT = Path("projects")


def project_dir(project_id: str, root: Path = DEFAULT_ROOT) -> Path:
    return root / project_id


def create_project(scenic_spot: str, root: Path = DEFAULT_ROOT) -> Project:
    p = Project(project_id=uuid.uuid4().hex[:8], scenic_spot=scenic_spot)
    save(p, root=root)
    return p


def save(p: Project, root: Path = DEFAULT_ROOT) -> None:
    d = project_dir(p.project_id, root)
    d.mkdir(parents=True, exist_ok=True)
    # 唯一临时名(pid+线程 id+uuid):多线程并发写同一项目时各写各的临时文件互不覆盖,
    # 再各自原子 os.replace 发布。固定 .tmp 名会被并发写者互相截断(torn write)或
    # 在另一线程 replace 后触发 FileNotFoundError。原子发布语义不变。
    tmp = d / f"project.json.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
    tmp.write_text(p.model_dump_json(indent=2), encoding="utf-8")
    os.replace(tmp, d / "project.json")


def load(project_id: str, root: Path = DEFAULT_ROOT) -> Project:
    text = (project_dir(project_id, root) / "project.json").read_text(encoding="utf-8")
    return Project.model_validate_json(text)
