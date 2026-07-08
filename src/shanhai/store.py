import os
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
    tmp = d / "project.json.tmp"
    tmp.write_text(p.model_dump_json(indent=2), encoding="utf-8")
    os.replace(tmp, d / "project.json")


def load(project_id: str, root: Path = DEFAULT_ROOT) -> Project:
    text = (project_dir(project_id, root) / "project.json").read_text(encoding="utf-8")
    return Project.model_validate_json(text)
