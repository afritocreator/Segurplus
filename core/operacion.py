"""Parámetros operativos del piloto (`data/operacion.yaml`), nunca
hardcodeados en el código -- CLAUDE.md.
"""

from __future__ import annotations

from pathlib import Path

import yaml

RUTA_OPERACION = Path(__file__).resolve().parent.parent / "data" / "operacion.yaml"


def revision_humana_obligatoria() -> bool:
    """Sin cache y sin lectura a nivel de módulo, a propósito -- mismo patrón
    que `core/analisis/homologacion.py::umbral_coincidencia` y
    `core/analisis/alertas.py::_leer_umbrales`: un YAML corrupto no debe
    tumbar el import ni la app Streamlit.

    True: una factura recién procesada queda `requiere_revision` y no
    impacta Evolución/alertas/Excel hasta que alguien la aprueba (ver
    `core/pipeline.py` y `apps/segurplus/paginas/revision.py`). False (el
    default del piloto): queda `aprobada` directo -- la auditoría de quién
    cargó qué se sigue escribiendo igual en los dos casos."""
    datos = yaml.safe_load(RUTA_OPERACION.read_text(encoding="utf-8"))
    if not isinstance(datos, dict) or "revision_humana_obligatoria" not in datos:
        raise ValueError(f"{RUTA_OPERACION} no tiene 'revision_humana_obligatoria'")
    return bool(datos["revision_humana_obligatoria"])
