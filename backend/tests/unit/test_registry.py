from pathlib import Path

import pytest

from aegis.common.errors import IntegrityError
from aegis.ml.registry import verify_manifest, write_manifest


def test_manifest_roundtrip_and_tamper_detection(tmp_path: Path) -> None:
    model = tmp_path / "m.json"
    model.write_text("model-bytes", encoding="utf-8")
    mf = write_manifest(model, name="b1", version="1", kind="CLASSIFIER", seed=7)
    manifest, path = verify_manifest(mf)
    assert manifest.seed == 7 and path == model
    model.write_text("tampered", encoding="utf-8")
    with pytest.raises(IntegrityError):
        verify_manifest(mf)


def test_unregistered_artefact_is_refused(tmp_path: Path) -> None:
    with pytest.raises(IntegrityError):
        verify_manifest(tmp_path / "nothing.manifest.json")


def test_manifest_cannot_point_outside_its_directory(tmp_path: Path) -> None:
    outside = tmp_path / "outside.bin"
    outside.write_text("x", encoding="utf-8")
    inner = tmp_path / "inner"
    inner.mkdir()
    mf = write_manifest(outside, name="n", version="1", kind="CLASSIFIER")
    moved = inner / mf.name
    moved.write_text(
        mf.read_text(encoding="utf-8").replace("outside.bin", "../outside.bin"), "utf-8"
    )
    with pytest.raises(IntegrityError):
        verify_manifest(moved)
