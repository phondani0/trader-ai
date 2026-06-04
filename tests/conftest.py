from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def disable_observability_export_by_default(monkeypatch):
    monkeypatch.setenv("OBSERVABILITY_ENABLED", "false")
