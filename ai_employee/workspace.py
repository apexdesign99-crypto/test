"""社員のワークスペース(記憶と成果物の置き場)。

ディレクトリ構成:

    <office>/<employee_id>/
        profile.json      職務定義書
        notes.jsonl       業務メモ(追記のみ)
        tasks.json        タスク一覧(可変)
        files/            成果物ファイル(社員が読み書きできる唯一の領域)
        sessions/         日付ごとの会話ログ
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from .config import office_root
from .profile import EmployeeProfile

TASK_STATUSES = ("open", "done", "cancelled")


def now() -> datetime:
    """タイムゾーン付きの現在時刻(ローカル)。"""
    return datetime.now(timezone.utc).astimezone()


def _short_id() -> str:
    return uuid.uuid4().hex[:8]


class WorkspaceError(RuntimeError):
    """ワークスペース操作の失敗。"""


class Workspace:
    """1 名分の永続データを扱う。"""

    def __init__(self, employee_id: str, root: Path | None = None) -> None:
        self.employee_id = employee_id
        self.root = (root or office_root()) / employee_id

    # ------------------------------------------------------------ パス

    @property
    def profile_path(self) -> Path:
        return self.root / "profile.json"

    @property
    def notes_path(self) -> Path:
        return self.root / "notes.jsonl"

    @property
    def tasks_path(self) -> Path:
        return self.root / "tasks.json"

    @property
    def files_dir(self) -> Path:
        return self.root / "files"

    @property
    def sessions_dir(self) -> Path:
        return self.root / "sessions"

    def exists(self) -> bool:
        return self.profile_path.is_file()

    def ensure(self) -> None:
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    # -------------------------------------------------------- プロフィール

    def save_profile(self, profile: EmployeeProfile) -> None:
        self.ensure()
        profile.save(self.profile_path)

    def load_profile(self) -> EmployeeProfile:
        if not self.exists():
            raise WorkspaceError(
                f"社員 '{self.employee_id}' は在籍していません。"
                f"先に `hire` で採用してください。"
            )
        return EmployeeProfile.load(self.profile_path)

    # ------------------------------------------------------------ 業務メモ

    def add_note(
        self,
        title: str,
        body: str,
        tags: Iterable[str] | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        note = {
            "id": _short_id(),
            "created_at": now().isoformat(timespec="seconds"),
            "title": title.strip(),
            "body": body.strip(),
            "tags": sorted({t.strip() for t in (tags or []) if t.strip()}),
            "project_id": (project_id or "").strip() or None,
        }
        self.root.mkdir(parents=True, exist_ok=True)
        with self.notes_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(note, ensure_ascii=False) + "\n")
        return note

    def iter_notes(self) -> Iterator[dict[str, Any]]:
        if not self.notes_path.is_file():
            return
        with self.notes_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def search_notes(
        self,
        query: str | None = None,
        tag: str | None = None,
        since: str | None = None,
        limit: int = 10,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """新しい順にメモを検索する。query は題名・本文の部分一致(大小無視)。"""
        needle = (query or "").strip().lower()
        hits: list[tuple[int, dict[str, Any]]] = []
        for index, note in enumerate(self.iter_notes()):
            if needle and needle not in (note["title"] + "\n" + note["body"]).lower():
                continue
            if tag and tag not in note.get("tags", []):
                continue
            if since and note["created_at"] < since:
                continue
            if project_id and note.get("project_id") != project_id:
                continue
            hits.append((index, note))
        # 同一秒に記録されたメモが前後しないよう、記録順を副キーにする。
        hits.sort(key=lambda pair: (pair[1]["created_at"], pair[0]), reverse=True)
        return [note for _, note in hits[: max(1, limit)]]

    # -------------------------------------------------------------- タスク

    def _read_tasks(self) -> list[dict[str, Any]]:
        if not self.tasks_path.is_file():
            return []
        return json.loads(self.tasks_path.read_text(encoding="utf-8"))

    def _write_tasks(self, tasks: list[dict[str, Any]]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.tasks_path.write_text(
            json.dumps(tasks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def add_task(
        self, title: str, detail: str = "", due: str | None = None
    ) -> dict[str, Any]:
        task = {
            "id": _short_id(),
            "title": title.strip(),
            "detail": detail.strip(),
            "due": (due or "").strip() or None,
            "status": "open",
            "created_at": now().isoformat(timespec="seconds"),
            "closed_at": None,
            "result": None,
        }
        tasks = self._read_tasks()
        tasks.append(task)
        self._write_tasks(tasks)
        return task

    def list_tasks(self, status: str | None = "open") -> list[dict[str, Any]]:
        if status not in (None, "all", *TASK_STATUSES):
            raise WorkspaceError(f"不正なステータスです: {status}")
        tasks = self._read_tasks()
        if status in (None, "all"):
            return tasks
        return [t for t in tasks if t["status"] == status]

    def close_task(
        self, task_id: str, result: str = "", status: str = "done"
    ) -> dict[str, Any]:
        if status not in ("done", "cancelled"):
            raise WorkspaceError(f"完了時のステータスが不正です: {status}")
        tasks = self._read_tasks()
        for task in tasks:
            if task["id"] == task_id:
                if task["status"] != "open":
                    raise WorkspaceError(
                        f"タスク {task_id} は既に {task['status']} です"
                    )
                task["status"] = status
                task["result"] = result.strip() or None
                task["closed_at"] = now().isoformat(timespec="seconds")
                self._write_tasks(tasks)
                return task
        raise WorkspaceError(f"タスクが見つかりません: {task_id}")

    # ---------------------------------------------------------- 成果物ファイル

    def resolve(self, relative_path: str) -> Path:
        """files/ 配下に閉じ込めたうえで絶対パスへ解決する。"""
        candidate = (self.files_dir / relative_path).expanduser()
        files_dir = self.files_dir.resolve()
        try:
            resolved = candidate.resolve()
        except OSError as exc:  # pragma: no cover - OS 依存
            raise WorkspaceError(f"パスを解決できません: {relative_path}") from exc
        if resolved != files_dir and files_dir not in resolved.parents:
            raise WorkspaceError(
                "ワークスペース外へのアクセスは許可されていません: " f"{relative_path}"
            )
        return resolved

    def write_file(self, relative_path: str, content: str) -> Path:
        path = self.resolve(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def read_file(self, relative_path: str) -> str:
        path = self.resolve(relative_path)
        if not path.is_file():
            raise WorkspaceError(f"ファイルが存在しません: {relative_path}")
        return path.read_text(encoding="utf-8")

    def list_files(self, subdir: str = "") -> list[str]:
        base = self.resolve(subdir) if subdir else self.files_dir
        if not base.is_dir():
            return []
        files = [p for p in base.rglob("*") if p.is_file()]
        return sorted(str(p.relative_to(self.files_dir)) for p in files)

    # ---------------------------------------------------------- 会話ログ

    def session_path(self, name: str | None = None) -> Path:
        name = name or now().strftime("%Y-%m-%d")
        return self.sessions_dir / f"{name}.json"

    def load_session(self, name: str | None = None) -> list[dict[str, Any]]:
        path = self.session_path(name)
        if not path.is_file():
            return []
        return json.loads(path.read_text(encoding="utf-8"))

    def save_session(
        self, messages: list[dict[str, Any]], name: str | None = None
    ) -> None:
        self.ensure()
        self.session_path(name).write_text(
            json.dumps(messages, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )


def roster(root: Path | None = None) -> list[EmployeeProfile]:
    """在籍中の社員プロフィールを一覧で返す。"""
    base = root or office_root()
    if not base.is_dir():
        return []
    people: list[EmployeeProfile] = []
    for entry in sorted(base.iterdir()):
        profile_path = entry / "profile.json"
        if profile_path.is_file():
            people.append(EmployeeProfile.load(profile_path))
    return people
