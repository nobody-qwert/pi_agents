"""Explicit live contract check for the configured LM Studio endpoint."""

from __future__ import annotations

import os

import pytest

from orchestrator.model_gateway import LmStudioGateway
from orchestrator.settings import load_settings


def test_configured_lm_studio_model_is_ready() -> None:
    """Reach the real endpoint only when an operator explicitly enables it."""
    if os.environ.get("RUN_LM_STUDIO_INTEGRATION") != "1":
        pytest.skip("set RUN_LM_STUDIO_INTEGRATION=1 to check LM Studio")

    readiness = LmStudioGateway(load_settings()).readiness()

    assert readiness.status == "ready", (
        "configured LM Studio endpoint is not ready: "
        f"{readiness.status} for model {readiness.configured_model_id}"
    )
