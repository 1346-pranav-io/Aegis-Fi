"""Minimal versioned model artefacts: a file plus a manifest with its SHA-256 (STANDARDS 9).

Interim file-based registry for Phase 2. The Postgres `ml.model_artefact` table (DATA_SCHEMA)
is not built yet; MLflow holds run metrics. Loading anything without a matching manifest fails.
"""

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from aegis.common.errors import IntegrityError
from aegis.common.hashing import file_sha256


class ArtefactManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    version: str
    kind: str  # CLASSIFIER | CALIBRATOR | RULE_PACK
    file: str  # file name next to the manifest
    sha256: str
    feature_set_version: str | None = None
    features: list[str] = []
    split_hash: str | None = None
    dataset_manifest_hash: str | None = None
    code_hash: str | None = None
    seed: int | None = None
    params: dict[str, Any] = {}
    metrics: dict[str, Any] = {}
    mlflow_run_id: str | None = None


def write_manifest(model_path: Path, **meta: Any) -> Path:
    manifest = ArtefactManifest(file=model_path.name, sha256=file_sha256(str(model_path)), **meta)
    out = model_path.with_suffix(model_path.suffix + ".manifest.json")
    out.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return out


def verify_manifest(manifest_path: Path) -> tuple[ArtefactManifest, Path]:
    """Return (manifest, model file path) only if the file's current hash matches the manifest."""
    if not manifest_path.exists():
        raise IntegrityError(f"no manifest at {manifest_path}: unregistered artefacts are refused")
    manifest = ArtefactManifest.model_validate(json.loads(manifest_path.read_text("utf-8")))
    if Path(manifest.file).name != manifest.file:  # no path separators: stay in the manifest dir
        raise IntegrityError(f"manifest file name {manifest.file!r} is not a plain file name")
    model_path = manifest_path.parent / manifest.file
    if not model_path.exists() or file_sha256(str(model_path)) != manifest.sha256:
        raise IntegrityError(f"artefact {manifest.file} is missing or does not match its manifest")
    return manifest, model_path
