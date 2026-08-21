"""원본 스냅샷 저장소.

사후 검증을 위해 **원천이 준 응답을 그대로 보존한다.** 가공 결과만 남기면
"그때 그 판단이 어떤 데이터에 근거했는가"에 답할 수 없다. 재난 대응에서 이
질문은 감사·책임 문제로 직결된다.

내용 주소 방식(SHA-256)을 쓴다. 같은 응답을 여러 번 받아도 한 번만 저장되고,
Webhook 재전송이나 폴링 중복이 파일을 늘리지 않는다. 이 멱등성이 곧
`Provenance.snapshot_id`의 안정성을 보장한다.

라이선스 제약이 있는 원본이 저장될 수 있으므로 스냅샷 디렉터리는 `.gitignore`
대상이다. 저장소에 커밋되지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

from .config import Settings, get_settings

MANIFEST_NAME = "manifest.json"


@dataclass(frozen=True, slots=True)
class SnapshotRef:
    """저장된 스냅샷 하나에 대한 참조."""

    snapshot_id: str
    dataset_id: str
    stored_at: datetime
    byte_size: int
    content_type: str
    endpoint: str | None
    path: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "dataset_id": self.dataset_id,
            "stored_at": self.stored_at.isoformat(),
            "byte_size": self.byte_size,
            "content_type": self.content_type,
            "endpoint": self.endpoint,
        }


class SnapshotStore:
    """원본 응답을 내용 주소로 보존한다."""

    def __init__(self, root: Path) -> None:
        self._root = root

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> Self:
        resolved = settings or get_settings()
        return cls(resolved.snapshot_dir)

    @property
    def root(self) -> Path:
        return self._root

    def _dataset_dir(self, dataset_id: str) -> Path:
        safe = "".join(ch for ch in dataset_id if ch.isalnum() or ch in "-_") or "unknown"
        return self._root / safe

    @staticmethod
    def digest(body: bytes) -> str:
        return hashlib.sha256(body).hexdigest()

    def put(
        self,
        *,
        dataset_id: str,
        body: bytes,
        content_type: str = "application/json",
        endpoint: str | None = None,
        request_params: dict[str, Any] | None = None,
        stored_at: datetime | None = None,
    ) -> SnapshotRef:
        """원본을 저장하고 참조를 반환한다.

        같은 내용이면 기존 스냅샷을 그대로 재사용한다(멱등).
        `request_params`에는 인증키가 들어가지 않는다 — 호출자가 제거해서 넘긴다.
        """
        snapshot_id = self.digest(body)
        directory = self._dataset_dir(dataset_id)
        directory.mkdir(parents=True, exist_ok=True)

        suffix = _suffix_for(content_type)
        blob = directory / f"{snapshot_id}{suffix}"
        timestamp = stored_at or datetime.now(UTC)

        if not blob.exists():
            _atomic_write(blob, body)
            meta = {
                "snapshot_id": snapshot_id,
                "dataset_id": dataset_id,
                "stored_at": timestamp.isoformat(),
                "byte_size": len(body),
                "content_type": content_type,
                "endpoint": endpoint,
                "request_params": _redact(request_params or {}),
            }
            _atomic_write(
                blob.with_suffix(blob.suffix + ".meta.json"),
                json.dumps(meta, ensure_ascii=False, indent=1).encode("utf-8"),
            )
            self._append_manifest(dataset_id, meta)
        else:
            existing = self._read_meta(blob)
            if existing:
                timestamp = _parse_time(existing.get("stored_at")) or timestamp
                content_type = str(existing.get("content_type") or content_type)
                endpoint = existing.get("endpoint") or endpoint

        return SnapshotRef(
            snapshot_id=snapshot_id,
            dataset_id=dataset_id,
            stored_at=timestamp,
            byte_size=len(body),
            content_type=content_type,
            endpoint=endpoint,
            path=blob,
        )

    def get(self, dataset_id: str, snapshot_id: str) -> bytes | None:
        """스냅샷을 읽는다. 내용이 해시와 다르면 None.

        검증하지 않으면 부분 기록된 파일이 '마지막 정상자료'로 제시된다.
        """
        directory = self._dataset_dir(dataset_id)
        if not directory.is_dir():
            return None
        for candidate in directory.glob(f"{snapshot_id}*"):
            if candidate.name.endswith(".meta.json"):
                continue
            try:
                body = candidate.read_bytes()
            except OSError:
                return None
            if self.digest(body) != snapshot_id:
                return None
            return body
        return None

    def latest(self, dataset_id: str) -> SnapshotRef | None:
        """가장 최근에 저장된 스냅샷. 원천 장애 시 마지막 정상자료로 쓴다."""
        entries = self.history(dataset_id)
        return entries[-1] if entries else None

    def history(self, dataset_id: str) -> tuple[SnapshotRef, ...]:
        """저장 순서대로 스냅샷 목록을 반환한다."""
        manifest = self._dataset_dir(dataset_id) / MANIFEST_NAME
        if not manifest.is_file():
            return ()
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return ()
        records = payload.get("snapshots") if isinstance(payload, dict) else None
        if not isinstance(records, list):
            return ()

        refs: list[SnapshotRef] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            snapshot_id = str(record.get("snapshot_id") or "")
            stored_at = _parse_time(record.get("stored_at"))
            if not snapshot_id or stored_at is None:
                continue
            content_type = str(record.get("content_type") or "application/json")
            blob = self._dataset_dir(dataset_id) / f"{snapshot_id}{_suffix_for(content_type)}"
            refs.append(
                SnapshotRef(
                    snapshot_id=snapshot_id,
                    dataset_id=dataset_id,
                    stored_at=stored_at,
                    byte_size=int(record.get("byte_size") or 0),
                    content_type=content_type,
                    endpoint=record.get("endpoint"),
                    path=blob,
                )
            )
        refs.sort(key=lambda ref: ref.stored_at)
        return tuple(refs)

    def datasets(self) -> tuple[str, ...]:
        if not self._root.is_dir():
            return ()
        return tuple(
            sorted(child.name for child in self._root.iterdir() if child.is_dir())
        )

    def _append_manifest(self, dataset_id: str, meta: dict[str, Any]) -> None:
        manifest = self._dataset_dir(dataset_id) / MANIFEST_NAME
        payload: dict[str, Any] = {"dataset_id": dataset_id, "snapshots": []}
        if manifest.is_file():
            try:
                existing = json.loads(manifest.read_text(encoding="utf-8"))
                if isinstance(existing, dict) and isinstance(existing.get("snapshots"), list):
                    payload = existing
            except (json.JSONDecodeError, OSError):
                pass

        entry = {
            key: meta[key]
            for key in ("snapshot_id", "stored_at", "byte_size", "content_type", "endpoint")
            if key in meta
        }
        snapshots: list[Any] = payload["snapshots"]
        if not any(
            isinstance(item, dict) and item.get("snapshot_id") == entry["snapshot_id"]
            for item in snapshots
        ):
            snapshots.append(entry)
        _atomic_write(
            manifest, json.dumps(payload, ensure_ascii=False, indent=1).encode("utf-8")
        )

    @staticmethod
    def _read_meta(blob: Path) -> dict[str, Any] | None:
        meta_path = blob.with_suffix(blob.suffix + ".meta.json")
        if not meta_path.is_file():
            return None
        try:
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        return payload if isinstance(payload, dict) else None


def _atomic_write(path: Path, body: bytes) -> None:
    """임시 파일에 쓴 뒤 교체한다.

    직접 쓰다가 중단되면 부분 기록된 파일이 남고, 내용 주소 방식에서는
    `exists()`가 True라서 복구되지 않는다. 그 파일이 나중에 '마지막 정상자료'로
    제시되면 잘못된 데이터가 판단 근거가 된다.
    """
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


_SUFFIXES: dict[str, str] = {
    "application/json": ".json",
    "application/xml": ".xml",
    "text/xml": ".xml",
    "text/csv": ".csv",
    "text/plain": ".txt",
    "application/zip": ".zip",
}

#: 요청 파라미터에서 절대 보존하지 않는 키. 스냅샷에 인증키가 남으면 안 된다.
_SECRET_KEYS = frozenset(
    {
        "servicekey",
        "authkey",
        "apikey",
        "api_key",
        "key",
        "secret",
        "token",
        "consumer_secret",
        "confmkey",
    }
)


def _suffix_for(content_type: str) -> str:
    base = content_type.split(";")[0].strip().lower()
    return _SUFFIXES.get(base, ".bin")


def _redact(params: dict[str, Any]) -> dict[str, Any]:
    return {
        key: ("<redacted>" if key.lower() in _SECRET_KEYS else value)
        for key, value in params.items()
    }


def _parse_time(raw: Any) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
