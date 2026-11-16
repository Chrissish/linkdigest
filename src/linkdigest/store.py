"""SQLite 状态库 + 文件系统产物布局。

data/
  db.sqlite3
  jobs/
    INDEX.md                     全部任务的可读索引（标题 / 平台 / 源链接）
    <日期-时间>_<平台>_<标题>_<job_id>/    目录名可读，末尾保留 job_id 用于反查
      manifest.json   这一条内容的身份证（标题 / 平台 / 源链接 / 模式）
      source.json     采集元数据
      audio.wav       16k mono
      transcript.json TranscriptBundle（契约）
      report.json     结构化报告
      report.md       渲染后报告
      stages.jsonl    每阶段耗时与结果
      handoff/        无模型时的外挂交接包（BRIEF.md + answer.json 待填）
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import settings

_LOCK = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id           TEXT PRIMARY KEY,
    input        TEXT NOT NULL,
    input_kind   TEXT NOT NULL DEFAULT 'url',
    status       TEXT NOT NULL DEFAULT 'queued',
    stage        TEXT NOT NULL DEFAULT 'queued',
    progress     REAL NOT NULL DEFAULT 0,
    title        TEXT,
    platform     TEXT,
    duration_sec REAL,
    chars        INTEGER,
    llm_model    TEXT,
    degraded     INTEGER NOT NULL DEFAULT 0,
    error        TEXT,
    cost_usd     REAL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC);
"""

STAGES = [
    ("queued", "排队"),
    ("ingest", "解析链接"),
    ("download", "取流"),
    ("extract", "抽取音轨"),
    ("asr", "语音转写"),
    ("normalize", "校验归一"),
    ("bundle", "落盘契约"),
    ("understand", "分层理解"),
    ("report", "生成报告"),
    ("handoff", "导出交接"),
    ("done", "完成"),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(slots=True)
class Job:
    id: str
    input: str
    input_kind: str
    status: str
    stage: str
    progress: float
    title: str | None
    platform: str | None
    duration_sec: float | None
    chars: int | None
    llm_model: str | None
    degraded: bool
    error: str | None
    cost_usd: float | None
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Job":
        data = dict(row)
        data["degraded"] = bool(data.get("degraded"))
        return cls(**data)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "dir": job_dir(self.id).name,
            "input": self.input,
            "input_kind": self.input_kind,
            "status": self.status,
            "stage": self.stage,
            "stage_label": dict(STAGES).get(self.stage, self.stage),
            "progress": round(self.progress, 3),
            "title": self.title,
            "platform": self.platform,
            "duration_sec": self.duration_sec,
            "chars": self.chars,
            "llm_model": self.llm_model,
            "degraded": self.degraded,
            "error": self.error,
            "cost_usd": self.cost_usd,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class Store:
    def __init__(self, db_path: Path | None = None) -> None:
        settings.ensure_dirs()
        self.db_path = db_path or settings.db_path
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def create_job(self, input_value: str, input_kind: str = "url") -> Job:
        job_id = uuid.uuid4().hex[:12]
        now = _now()
        with _LOCK, self._conn() as conn:
            conn.execute(
                "INSERT INTO jobs (id, input, input_kind, status, stage, progress, created_at, updated_at)"
                " VALUES (?, ?, ?, 'queued', 'queued', 0, ?, ?)",
                (job_id, input_value, input_kind, now, now),
            )
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        job_dir(job_id).mkdir(parents=True, exist_ok=True)
        return Job.from_row(row)

    def update(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = _now()
        keys = ", ".join(f"{k} = ?" for k in fields)
        with _LOCK, self._conn() as conn:
            conn.execute(f"UPDATE jobs SET {keys} WHERE id = ?", (*fields.values(), job_id))

    def get(self, job_id: str) -> Job | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return Job.from_row(row) if row else None

    def list(self, limit: int = 30) -> list[Job]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [Job.from_row(r) for r in rows]

    def delete(self, job_id: str) -> None:
        """从状态库里移除记录（产物目录由调用方处理）。"""
        with _LOCK, self._conn() as conn:
            conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))

    def resolve(self, token: str) -> Job | None:
        """允许用 job_id 前缀、目录名片段或标题片段定位任务。"""
        token = (token or "").strip().strip("`'\"")
        for bad in "*?[]":
            token = token.replace(bad, "")
        if not token:
            return None
        job = self.get(token)
        if job is not None:
            return job
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE id LIKE ? OR input LIKE ? ORDER BY created_at DESC LIMIT 1",
                (token + "%", f"%{token}%"),
            ).fetchone()
        if row is not None:
            return Job.from_row(row)
        if settings.jobs_dir.exists():
            for path in sorted(settings.jobs_dir.glob(f"*{token}*"), reverse=True):
                if path.is_dir():
                    found = self.get(path.name.rsplit("_", 1)[-1])
                    if found is not None:
                        return found
        return None

    def finalize_dir(self, job_id: str) -> Path:
        """任务收尾：把目录改成「时间_平台_标题_短id」，写身份证，刷新总索引。

        幂等：重复调用只会得到同一个目标名；改名失败时退回旧目录，不影响产物可读性。
        """
        job = self.get(job_id)
        if job is None:
            return settings.jobs_dir / job_id
        current = job_dir(job_id)
        target = settings.jobs_dir / job_dir_name(job)
        if current.resolve() != target.resolve():
            if target.exists():
                target = target.with_name(f"{target.name}-{uuid.uuid4().hex[:4]}")
            try:
                if current.exists():
                    current.rename(target)
                else:
                    target.mkdir(parents=True, exist_ok=True)
            except OSError as exc:  # 磁盘/权限问题不该让任务失败
                append_stage(job_id, {"stage": "finalize", "error": f"改名失败：{exc}"})
                target = current
                target.mkdir(parents=True, exist_ok=True)
        write_json(
            target / "manifest.json",
            {
                "id": job.id,
                "dir": target.name,
                "input": job.input,
                "title": job.title,
                "platform": job.platform,
                "url": (read_json(target / "source.json").get("source", {}).get("canonical_url")
                        if (target / "source.json").exists() else job.input),
                "status": job.status,
                "chars": job.chars,
                "duration_sec": job.duration_sec,
                "llm_model": job.llm_model,
                "degraded": job.degraded,
                "created_at": job.created_at,
                "artifacts": sorted(p.name for p in target.iterdir() if p.is_file()),
            },
        )
        refresh_index()
        return target


_SAFE_CHARS = re.compile(r"[^0-9A-Za-z\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]+")


def slugify(text: str | None, limit: int = 40) -> str:
    """压成目录名片段：保留中英日文字与数字，其余折叠成单个连字符。"""
    value = _SAFE_CHARS.sub("-", (text or "").strip()).strip("-")
    value = re.sub(r"-{2,}", "-", value)
    if len(value) > limit:
        value = value[:limit].rstrip("-")
    return value or "untitled"


def _stamp(created_at: str | None) -> str:
    try:
        moment = datetime.fromisoformat(created_at) if created_at else datetime.now(timezone.utc)
    except ValueError:
        moment = datetime.now(timezone.utc)
    return moment.astimezone().strftime("%Y%m%d-%H%M")


def _source_hint(value: str) -> str:
    """没抓到标题时，从输入里挤出一个能认出来的名字（域名 + 视频 id / 文件名）。"""
    if not value:
        return ""
    if "://" in value:
        from urllib.parse import parse_qs, urlsplit

        # 老记录里存过被 shell 转义过的链接（watch\?v\=xxx），先还原再解析
        value = value.replace("\\?", "?").replace("\\=", "=").replace("\\/", "/")
        parts = urlsplit(value)
        host = (parts.hostname or "").replace("www.", "").split(".")[0]
        tail = parts.path.rstrip("/").rsplit("/", 1)[-1]
        query = parse_qs(parts.query)
        for key in ("v", "id", "bvid", "aid"):
            if query.get(key):
                tail = query[key][0]
                break
        return slugify(f"{host}-{tail}" if tail and tail != host else host, 40)
    return slugify(Path(value).stem, 40)


def job_dir_name(job: Job) -> str:
    """<本地时间>_<平台>_<标题>_<job_id>。job_id 放末尾，既能反查也不会撞名。"""
    title = slugify(job.title, 48) if job.title else (_source_hint(job.input) or "untitled")
    platform = slugify(job.platform, 12) if job.platform else "unknown"
    return "_".join([_stamp(job.created_at), platform, title, job.id])


def job_dir(job_id: str) -> Path:
    """按 job_id 找产物目录：先看裸 id（老任务），再按 *_<job_id> 反查。"""
    direct = settings.jobs_dir / job_id
    if direct.exists():
        return direct
    if settings.jobs_dir.exists():
        matches = sorted(settings.jobs_dir.glob(f"*_{job_id}"))
        if matches:
            return matches[0]
    return direct


def job_path(job_id: str, name: str) -> Path:
    return job_dir(job_id) / name


def refresh_index(limit: int = 300) -> Path:
    """data/jobs/INDEX.md：不用开数据库、不用记 id 也能一眼找到某条内容。"""
    path = settings.jobs_dir / "INDEX.md"
    jobs = Store().list(limit)
    lines = [
        "# 任务索引",
        "",
        f"共 {len(jobs)} 条（按创建时间倒序）。目录名 = 时间_平台_标题_job_id，`_job_id` 可用于所有命令行操作。",
        "",
        "| 目录 | 标题 | 平台 | 状态 | 字数 | 标签 | 源 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for job in jobs:
        state = "失败" if job.status == "failed" else ("待外部理解" if job.degraded else ("完成" if job.status == "done" else job.stage))
        title = (job.title or job.input).replace("|", "／")
        if len(title) > 46:
            title = title[:46] + "…"
        source = job.input.replace("|", "／")
        if len(source) > 60:
            source = source[:57] + "…"
        tags = ""
        report_file = job_dir(job.id) / "report.json"
        if report_file.exists():
            try:
                tags = " ".join(
                    f"#{str(t).replace(' ', '-')}"
                    for t in (read_json(report_file).get("tags") or [])[:5]
                )
            except (ValueError, OSError):
                tags = ""
        lines.append(
            f"| `{job_dir(job.id).name}` | {title} | {job.platform or '-'} | {state} "
            f"| {job.chars or '-'} | {tags or '-'} | {source} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def append_stage(job_id: str, record: dict[str, Any]) -> None:
    record = {"ts": _now(), **record}
    path = job_path(job_id, "stages.jsonl")
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_stages(job_id: str) -> list[dict[str, Any]]:
    path = job_path(job_id, "stages.jsonl")
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
