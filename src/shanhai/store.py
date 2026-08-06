import json
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
# 它放在 projects/ **内部**只是为了省一个顶层目录;下划线前缀 + 真实 project_id 恒为
# uuid4 hex[:8],不会撞名。
# (原注释说"为的是复用 /files 静态挂载让用户回听"——这条已不成立:2026-08-06 起整个
#  _voice_samples/ 前缀被 _ArtifactStatic 拦掉了,回听只走带鉴权的 API 端点。)
# 已知欠账:删作品不会连带清掉样本(20 秒 16k 单声道约 640KB,量级可忽略)。
VOICE_SAMPLE_DIRNAME = "_voice_samples"

# 用户保存的 BGM 库。与音色样本同构、同理由:选配乐的入口同样横跨「新建作品表单」
# (还没有 project_id)与「作品详情页」,只能放用户级。
# 与音色的差别是这里要存**结构化条目**(归属/名称/来源/时长),不是一张扁平映射表,
# 因为库要按用户过滤、要在界面上列出来,而音色那份索引只回答"句柄对应哪个文件"。
BGM_DIRNAME = "_bgm"


# ⚠️ 以下几个函数的 root 一律写成 `None` 再在函数体里取 DEFAULT_ROOT,**不能**写成
# `root: Path = DEFAULT_ROOT`——默认值在函数定义时就求值绑死了,测试里
# `monkeypatch.setattr(store, "DEFAULT_ROOT", tmp_path)` 改的是模块属性,对已绑死的默认值
# 完全无效。那个写法曾让 4 条 api 测试自以为隔离、实际全在往真实 projects/ 里写,
# 在 DGX 上跑一次 pytest 就往线上作品目录塞了一个假作品 s2fail。
def voice_sample_dir(root: Path | None = None) -> Path:
    return (root or DEFAULT_ROOT) / VOICE_SAMPLE_DIRNAME


# 音色句柄 → 本地样本文件名 的映射。**必须显式存**:句柄由上游 TTS 生成
# (clone:shanhai_voice_<hex>.wav),本地文件名是我们自己的随机盐(vs_<token>.wav),
# 两者之间没有任何可推导的关系。此前这份对应只出现在上传那一次 HTTP 响应里、随即被前端丢掉,
# 于是"这个作品用的音色对应盘上哪个 wav"永远回答不了——用户想回听自己录的音色都做不到。
# 存在服务端而不是存进各个 project:线上两部「可可托海」共用同一个句柄,存进项目会重复;
# 而且让客户端回传文件名等于让它指定服务端读哪个文件,又得多一层路径校验。
_VOICE_INDEX_NAME = "index.json"
_VOICE_INDEX_LOCK = threading.Lock()


def _voice_index_path(root: Path | None = None) -> Path:
    return voice_sample_dir(root) / _VOICE_INDEX_NAME


def _read_voice_index(root: Path | None = None) -> dict:
    try:
        data = json.loads(_voice_index_path(root).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return {}      # 升级前的老部署没有这个文件;写坏了也只当"查不到"
    return data if isinstance(data, dict) else {}


def remember_voice_sample(voice: str, filename: str, root: Path | None = None) -> None:
    """记下某个音色句柄对应哪份本地录音。读-改-写全程持锁,免得并发上传丢条目。"""
    with _VOICE_INDEX_LOCK:
        index = _read_voice_index(root)
        index[voice] = filename
        atomic_write_text(_voice_index_path(root), json.dumps(index, ensure_ascii=False, indent=1))


def voice_sample_for(voice: str, root: Path | None = None) -> str | None:
    """该音色句柄对应的本地样本文件名;查不到或不可信则 None。

    只认 basename:索引万一被写进 `../../x` 这种值,也不能让播放端点顺着它读出音色目录之外
    的东西(这份文件将来可能由一次性补数脚本写入,不能假定内容一定干净)。"""
    name = _read_voice_index(root).get(voice)
    if not isinstance(name, str) or not name or name != Path(name).name:
        return None
    return name


# ---------- 用户 BGM 库 ----------

_BGM_INDEX_NAME = "index.json"
_BGM_INDEX_LOCK = threading.Lock()


def bgm_dir(root: Path | None = None) -> Path:
    return (root or DEFAULT_ROOT) / BGM_DIRNAME


def _bgm_index_path(root: Path | None = None) -> Path:
    return bgm_dir(root) / _BGM_INDEX_NAME


def load_bgm_items(root: Path | None = None) -> list[dict]:
    """全部库条目。文件缺失/写坏一律当空库——BGM 是非关键增强,不该因为索引坏了就 500。"""
    try:
        data = json.loads(_bgm_index_path(root).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return []
    items = data.get("items") if isinstance(data, dict) else None
    return [it for it in items if isinstance(it, dict)] if isinstance(items, list) else []


def update_bgm_items(mutate, root: Path | None = None) -> None:
    """锁内原子地 读→变换→写。所有写操作都走这里,不要各自读了再写——
    那正是丢更新的来源(auth.py 踩过同一个坑,只因当时唯一写路径是人工 CLI 才没暴露)。
    mutate 就地改传入的 list;它抛异常则不落盘。"""
    with _BGM_INDEX_LOCK:
        items = load_bgm_items(root)
        mutate(items)
        atomic_write_text(_bgm_index_path(root),
                          json.dumps({"items": items}, ensure_ascii=False, indent=1))


def bgm_item(item_id: str, root: Path | None = None) -> dict | None:
    """按 id 取条目;顺带做文件名可信性校验。

    只认 basename:索引里的 file 万一被写成 `../../x`,也不能让试听端点顺着它读出
    BGM 目录之外的东西(与 voice_sample_for 同一道防线)。"""
    for it in load_bgm_items(root):
        if it.get("id") != item_id:
            continue
        name = it.get("file")
        if not isinstance(name, str) or not name or name != Path(name).name:
            return None
        return it
    return None

# project_id 形状校验:仅允许字母数字下划线短横线,堵住路径遍历(../、/、空白等)。
# 首字符另外排除下划线:那是共享目录的保留命名空间(VOICE_SAMPLE_DIRNAME = "_voice_samples"),
# 放行的话 DELETE /api/projects/_voice_samples 会把全站音色样本 rmtree 掉——delete_project
# 只判 is_dir 就删,不要求目录里有 project.json。共享目录有自己的入口 voice_sample_dir(),
# 不经过 project_dir,故拒掉不影响任何合法用途。
_PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9-][A-Za-z0-9_-]*$")


def project_dir(project_id: str, root: Path | None = None) -> Path:
    """project_id 落盘路径的唯一入口:load/save/create 均经此函数,故此处校验一劳永逸。"""
    if not _PROJECT_ID_RE.fullmatch(project_id):
        raise ValueError(f"非法 project_id: {project_id!r}")
    return (root or DEFAULT_ROOT) / project_id


def create_project(scenic_spot: str, root: Path | None = None) -> Project:
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


def save(p: Project, root: Path | None = None) -> None:
    atomic_write_text(project_dir(p.project_id, root) / "project.json", p.model_dump_json(indent=2))


def load(project_id: str, root: Path | None = None) -> Project:
    text = (project_dir(project_id, root) / "project.json").read_text(encoding="utf-8")
    return Project.model_validate_json(text)
