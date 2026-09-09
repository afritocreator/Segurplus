"""Carga el diccionario de homologación de conceptos desde `data/conceptos/*.yaml`
(nunca hardcodeado en el código, ver CLAUDE.md). Todos los archivos de esa
carpeta se combinan en un único mapeo -- así cada servicio puede tener su
propio YAML sin pisar a los demás.

Sin cache y sin lectura a nivel de módulo, a propósito: un YAML corrupto no
debe tumbar el import ni la app Streamlit (mismo patrón que
`core/analisis/alertas.py` y `core/beneficios/economia_conocimiento.py` de
Consultora)."""

from __future__ import annotations

from pathlib import Path

import yaml

DIR_CONCEPTOS = Path(__file__).resolve().parents[2] / "data" / "conceptos"


def cargar_diccionario(*, directorio: Path | None = None) -> dict[str, list[str]]:
    """Combina todos los `*.yaml` de `data/conceptos/` en un único
    {concepto_normalizado: [alias, ...]}."""
    directorio = directorio or DIR_CONCEPTOS
    combinado: dict[str, list[str]] = {}
    if not directorio.exists():
        return combinado

    for ruta in sorted(directorio.glob("*.yaml")):
        datos = yaml.safe_load(ruta.read_text(encoding="utf-8"))
        if not datos:
            continue
        if not isinstance(datos, dict):
            raise ValueError(f"{ruta} no tiene la forma esperada (debe ser un mapeo)")
        for concepto, alias in datos.items():
            combinado.setdefault(concepto, [])
            combinado[concepto].extend(alias or [])
    return combinado
