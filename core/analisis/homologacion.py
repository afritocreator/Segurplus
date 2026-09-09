"""Homologación de conceptos: mapea la descripción literal de una línea de
factura (que cada proveedor escribe distinto — "ABONO LINEA MOVIL", "Abono
Plan Control", "Cargo fijo móvil") a un concepto normalizado ("abono_movil"),
para poder comparar el mismo concepto entre facturas y entre períodos.

Port de `lib/invoice/match.ts` de Kleric- (similitud de bigramas, coeficiente
de Dice) — ahí se usa para emparejar una línea de factura contra un catálogo
de productos; acá contra un diccionario de conceptos normalizados por
servicio, cargado desde `data/conceptos/*.yaml` (nunca hardcodeado, ver
CLAUDE.md).
"""

from __future__ import annotations

import re
import unicodedata

UMBRAL_COINCIDENCIA = 0.45  # mismo umbral calibrado en match.ts


def normalizar(texto: str) -> str:
    texto = texto.lower()
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")  # saca acentos
    texto = re.sub(r"[^a-z0-9\s]", " ", texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto


def _bigramas(texto: str) -> set[str]:
    limpio = normalizar(texto)
    return {limpio[i : i + 2] for i in range(len(limpio) - 1)}


def similitud(a: str, b: str) -> float:
    """Coeficiente de Dice entre 0 (nada en común) y 1 (idéntico)."""
    bigramas_a = _bigramas(a)
    bigramas_b = _bigramas(b)
    if not bigramas_a or not bigramas_b:
        return 0.0
    interseccion = len(bigramas_a & bigramas_b)
    return (2 * interseccion) / (len(bigramas_a) + len(bigramas_b))


def homologar_concepto(
    descripcion: str, diccionario: dict[str, list[str]]
) -> tuple[str | None, float]:
    """Busca el concepto normalizado más parecido a `descripcion` dentro de
    `diccionario` ({concepto_normalizado: [alias, alias, ...]}).

    Devuelve `(None, score)` si el mejor score queda por debajo del umbral —
    eso NO se descarta silenciosamente: en `core/analisis/alertas.py` se
    convierte en la alerta "concepto nuevo sin clasificar", que suele ser
    justo el cargo que se coló.
    """
    mejor_concepto = None
    mejor_score = 0.0
    for concepto, alias in diccionario.items():
        candidatos = [concepto, *alias]
        score = max(similitud(descripcion, c) for c in candidatos)
        if score > mejor_score:
            mejor_score = score
            mejor_concepto = concepto

    if mejor_score < UMBRAL_COINCIDENCIA:
        return None, mejor_score
    return mejor_concepto, mejor_score
