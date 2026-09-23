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
    """Tope de llamadas a Gemini por hora (docs/auditoria-2026-09-facturas-
    reales.md, hallazgo B-4) -- antes vivía hardcodeado como
    `core.extraccion.gemini.MAX_LLAMADAS_POR_HORA`, un parámetro operativo
    que CLAUDE.md pide que nunca esté en el código. Se hace cumplir con
    `core.almacenamiento.llamadas_ultima_hora`, que cuenta llamadas reales
    (tabla `intentos_gemini`), no un proxy."""
    datos = yaml.safe_load(RUTA_OPERACION.read_text(encoding="utf-8"))
    if not isinstance(datos, dict) or "max_llamadas_gemini_por_hora" not in datos:
        raise ValueError(f"{RUTA_OPERACION} no tiene 'max_llamadas_gemini_por_hora'")
    return int(datos["max_llamadas_gemini_por_hora"])


def dias_retencion_intentos_gemini() -> int:
    """Cuántos días se conserva una fila de `intentos_gemini` antes de
    purgarse (docs/auditoria-2026-09-facturas-reales.md, hallazgo C-9): la
    tabla crece una fila por cada llamada REAL a Gemini y nunca se
    purgaba, en una base gratuita con límite de espacio. Se aplica en
    `core.almacenamiento.registrar_intento_gemini` -- un DELETE por cada
    llamada real no se nota al lado de la llamada misma."""
    datos = yaml.safe_load(RUTA_OPERACION.read_text(encoding="utf-8"))
    if not isinstance(datos, dict) or "dias_retencion_intentos_gemini" not in datos:
        raise ValueError(f"{RUTA_OPERACION} no tiene 'dias_retencion_intentos_gemini'")
    return int(datos["dias_retencion_intentos_gemini"])


def intentos_gemini_por_llamada() -> int:
    """Cuántas veces reintentar una llamada a Gemini que falló por un error
    transitorio antes de rendirse (docs/auditoria-2026-09-web.md, E-6) --
    ver `core.extraccion.gemini.es_error_transitorio` y
    `core.pipeline.procesar_pdf`."""
    datos = yaml.safe_load(RUTA_OPERACION.read_text(encoding="utf-8"))
    if not isinstance(datos, dict) or "intentos_gemini_por_llamada" not in datos:
        raise ValueError(f"{RUTA_OPERACION} no tiene 'intentos_gemini_por_llamada'")
    return int(datos["intentos_gemini_por_llamada"])


def espera_reintento_gemini_segundos() -> float:
    """Espera base, en segundos, antes de cada reintento a Gemini -- ver
    `core.pipeline.procesar_pdf` (docs/auditoria-2026-09-web.md, E-6)."""
    datos = yaml.safe_load(RUTA_OPERACION.read_text(encoding="utf-8"))
    if not isinstance(datos, dict) or "espera_reintento_gemini_segundos" not in datos:
        raise ValueError(f"{RUTA_OPERACION} no tiene 'espera_reintento_gemini_segundos'")
    return float(datos["espera_reintento_gemini_segundos"])


def zona_horaria() -> str:
    """Nombre de zona horaria IANA del equipo que usa el tablero (docs/
    auditoria-2026-09-facturas-reales.md, hallazgo C-8) -- para mostrar "a
    qué hora reintentar" (`core.pipeline.procesar_pdf`) en la hora de pared
    del usuario, no en la del servidor. Streamlit Community Cloud corre en
    UTC; el equipo está en Tandil, Buenos Aires (UTC-3) -- sin esto, el
    mensaje decía una hora 3 horas adelantada respecto del reloj real."""
    datos = yaml.safe_load(RUTA_OPERACION.read_text(encoding="utf-8"))
    if not isinstance(datos, dict) or "zona_horaria" not in datos:
        raise ValueError(f"{RUTA_OPERACION} no tiene 'zona_horaria'")
    return str(datos["zona_horaria"])
