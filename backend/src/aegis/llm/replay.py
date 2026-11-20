"""Record/replay store (D-12, T-INV-10).

A recorded response is keyed by (prompt id, prompt version, prompt file hash, model id, hash of
the exact pseudonymised system+user text sent). Only pseudonymised text is ever stored."""

import json
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from aegis.common.hashing import content_hash


class RecordedResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    text: str
    tokens_in: int
    tokens_out: int
    model_id: str


def request_key(
    *, prompt_id: str, prompt_version: str, prompt_hash: str, model_id: str, input_hash: str
) -> str:
    return content_hash(
        {
            "prompt_id": prompt_id,
            "prompt_version": prompt_version,
            "prompt_hash": prompt_hash,
            "model_id": model_id,
            "input_hash": input_hash,
        }
    )


class RecordStore(Protocol):
    def get(self, key: str) -> RecordedResponse | None: ...
    def put(self, rec: RecordedResponse) -> None: ...


class InMemoryRecordStore:
    def __init__(self) -> None:
        self._d: dict[str, RecordedResponse] = {}

    def get(self, key: str) -> RecordedResponse | None:
        return self._d.get(key)

    def put(self, rec: RecordedResponse) -> None:
        self._d[rec.key] = rec

    def __len__(self) -> int:
        return len(self._d)


class JsonlRecordStore:
    """Append-only JSON-lines file; later lines for a key win (a re-record is a new line)."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._d: dict[str, RecordedResponse] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = RecordedResponse.model_validate(json.loads(line))
                    self._d[r.key] = r

    def get(self, key: str) -> RecordedResponse | None:
        return self._d.get(key)

    def put(self, rec: RecordedResponse) -> None:
        self._d[rec.key] = rec
        with self._path.open("a", encoding="utf-8") as f:
            f.write(rec.model_dump_json() + "\n")
