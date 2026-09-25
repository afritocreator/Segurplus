from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _cargar(nombre: str):
    ruta = REPO_ROOT / ".codex" / "hooks" / f"{nombre}.py"
    spec = importlib.util.spec_from_file_location(nombre, ruta)
    assert spec and spec.loader
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


guard = _cargar("guard_facturas")
ruff_hook = _cargar("ruff_after_edit")


def test_guard_bloquea_ruta_real_pero_no_su_readme() -> None:
    real = {"tool_name": "exec_command", "tool_input": {"cmd": "type data/reales/factura.pdf"}}
    permitido = {
        "tool_name": "exec_command",
        "tool_input": {"cmd": "type data/reales/README.md"},
    }

    assert "data/reales" in guard.motivo_bloqueo(real)
    assert guard.motivo_bloqueo(permitido) is None


def test_guard_no_confunde_mencion_documental_con_archivo_objetivo() -> None:
    payload = {
        "tool_name": "apply_patch",
        "tool_input": {
            "patch": "*** Update File: AGENTS.md\n+Nunca leer data/reales/factura.pdf\n"
        },
    }

    assert guard.motivo_bloqueo(payload) is None


def test_ruff_detecta_solo_python_dentro_del_repo() -> None:
    archivo = REPO_ROOT / "temporal_hook_test.py"
    archivo.touch()
    try:
        payload = {
            "tool_name": "apply_patch",
            "tool_input": {
                "patch": "*** Update File: temporal_hook_test.py\n*** Update File: README.md\n"
            },
        }
        assert ruff_hook.archivos_python(payload) == [Path("temporal_hook_test.py")]
    finally:
        archivo.unlink()
