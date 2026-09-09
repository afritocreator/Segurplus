"""Carga el diccionario de homologación de conceptos desde `data/conceptos/*.yaml`
(nunca hardcodeado en el código, ver CLAUDE.md).

Un archivo por servicio (`telefonia.yaml`, `energia.yaml`, `gas.yaml`, ...)
más `comunes.yaml` para conceptos que aparecen en más de un servicio (un
cargo fijo, por ejemplo). Antes había un único `general.yaml` con todo
junto, y eso hacía que `homologar_concepto` compitiera `consumo_agua`
contra `consumo_gas` en igualdad de condiciones aunque la factura ya
supiera de qué servicio se trataba (ver docs/auditoria-2026-09.md,
hallazgo A-3) -- pasar `servicio` acota los candidatos al archivo de ese
servicio (+ comunes.yaml), en vez de combinar TODOS los YAML de la carpeta.

Sin cache y sin lectura a nivel de módulo, a propósito: un YAML corrupto no
debe tumbar el import ni la app Streamlit (mismo patrón que
`core/analisis/alertas.py` y `core/beneficios/economia_conocimiento.py` de
Consultora)."""

from __future__ import annotations

from pathlib import Path

import yaml

DIR_CONCEPTOS = Path(__file__).resolve().parents[2] / "data" / "conceptos"
NOMBRE_ARCHIVO_COMUNES = "comunes.yaml"


def _combinar(rutas: list[Path]) -> dict[str, list[str]]:
    combinado: dict[str, list[str]] = {}
    for ruta in rutas:
        datos = yaml.safe_load(ruta.read_text(encoding="utf-8"))
        if not datos:
            continue
        if not isinstance(datos, dict):
            raise ValueError(f"{ruta} no tiene la forma esperada (debe ser un mapeo)")
        for concepto, alias in datos.items():
            combinado.setdefault(concepto, [])
            combinado[concepto].extend(alias or [])
    return combinado


def cargar_diccionario(
    servicio: str | None = None, *, directorio: Path | None = None
) -> dict[str, list[str]]:
    """Diccionario de homologación {concepto_normalizado: [alias, ...]}.

    Si `servicio` es `None`, combina TODOS los `*.yaml` de la carpeta (útil
    para herramientas de diagnóstico/admin, donde no se sabe de antemano el
    servicio). Si se pasa un `servicio` (ej. "telefonia"), combina solo
    `comunes.yaml` + `<servicio>.yaml` -- si no existe un YAML para ese
    servicio todavía, se devuelve solo `comunes.yaml` (o vacío si tampoco
    existe ese): un servicio nuevo sin diccionario propio hace que TODOS sus
    conceptos salgan como "concepto nuevo sin clasificar" en vez de competir
    contra los candidatos de otro servicio, que es el comportamiento seguro.
    """
    directorio = directorio or DIR_CONCEPTOS
    if not directorio.exists():
        return {}

    if servicio is None:
        rutas = sorted(directorio.glob("*.yaml"))
    else:
        candidatos = [directorio / NOMBRE_ARCHIVO_COMUNES, directorio / f"{servicio}.yaml"]
        rutas = [r for r in candidatos if r.exists()]

    return _combinar(rutas)
