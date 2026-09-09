"""Skipea automáticamente los tests marcados `red_real` si no hay
GEMINI_API_KEY configurada (ni sentido en CI, que a propósito no la tiene
-- ver .github/workflows/ci.yml)."""

import os

import pytest


def pytest_collection_modifyitems(config, items):
    if os.environ.get("GEMINI_API_KEY"):
        return
    skip_red_real = pytest.mark.skip(reason="requiere GEMINI_API_KEY (no configurada)")
    for item in items:
        if "red_real" in item.keywords:
            item.add_marker(skip_red_real)
