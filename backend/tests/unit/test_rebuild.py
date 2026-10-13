"""Rebuilding a downloadable .pbip from a stored artifact snapshot (used by revert)."""

from app.services import generation_service
from app.services.generation_service import rebuild_pbip


def test_rebuild_pbip_from_snapshot(tmp_path, monkeypatch, sample_model, valid_report):
    monkeypatch.setattr(generation_service.settings, "generated_dir", tmp_path)
    snapshot = {
        "semantic_model": sample_model.model_dump(),
        "report": valid_report.model_dump(),
    }

    zip_path, validation = rebuild_pbip(snapshot, "session-xyz", "MyReport")

    assert zip_path.exists()
    assert zip_path.suffix == ".zip"
    assert validation["report"]["valid"] is True
