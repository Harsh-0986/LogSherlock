"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SAMPLES_DIR = REPO_ROOT / "samples"
FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def settings() -> Settings:
    """Test settings with small, predictable limits."""
    return Settings(
        app_env="test",
        max_upload_mb=1,
        max_log_lines=20_000,
        max_evidence_chars=16_000,
        max_groups_for_model=8,
        redaction_enabled=True,
    )


@pytest.fixture
def sample_node_log() -> str:
    """The bundled synthetic Node.js API log."""
    return (SAMPLES_DIR / "sample-node-api.log").read_text(encoding="utf-8")


@pytest.fixture
def sample_python_log() -> str:
    """The bundled synthetic Python worker log."""
    return (SAMPLES_DIR / "sample-python-worker.log").read_text(encoding="utf-8")
