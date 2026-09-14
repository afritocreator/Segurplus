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


def max_llamadas_gemini_por_hora() -> int:
    """Tope de llamadas a Gemini por hora (docs/auditoria-2026-09-piloto.md,
    hallazgo B-4) -- antes vivía hardcodeado como
    `core.extraccion.gemini.MAX_LLAMADAS_POR_HORA`, un parámetro operativo
    que CLAUDE.md pide que nunca esté en el código. Se hace cumplir con
    `core.almacenamiento.llamadas_ultima_hora`, que cuenta llamadas reales
    (tabla `intentos_gemini`), no un proxy."""
    datos = yaml.safe_load(RUTA_OPERACION.read_text(encoding="utf-8"))
    if not isinstance(datos, dict) or "max_llamadas_gemini_por_hora" not in datos:
        raise ValueError(f"{RUTA_OPERACION} no tiene 'max_llamadas_gemini_por_hora'")
    return int(datos["max_llamadas_gemini_por_hora"])
