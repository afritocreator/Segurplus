#!/usr/bin/env python3
"""Re-homologa las filas de `conceptos` YA GUARDADAS en la base, con el
diccionario ACTUAL de `data/conceptos/*.yaml` -- sin llamar a Gemini ni
re-extraer nada. Ver `core/rehomologacion.py` para el porqué.

Uso:
    python scripts/rehomologar.py                       # dry-run, base real
    python scripts/rehomologar.py --servicio telefonia   # acotado a un servicio
    python scripts/rehomologar.py --base /tmp/otra.duckdb
    python scripts/rehomologar.py --aplicar              # escribe de verdad

Por defecto es DRY-RUN: muestra qué cambiaría, no escribe nada. Solo con
`--aplicar` hace el UPDATE en la base. Nunca llama a Gemini ni consume la
cuota de `MAX_LLAMADAS_POR_HORA` (core/extraccion/gemini.py) -- este
script no importa ese módulo ni nada que salga a la red.

Flujo típico de calibración: cargar facturas -> mirar la pantalla "Sin
clasificar" del tablero, ordenada por importe -> agregar el alias que
falta a `data/conceptos/<servicio>.yaml` -> correr este script en dry-run
para ver el efecto -> si las "regresion" (ver más abajo) no preocupan,
correr de nuevo con --aplicar.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

# Permite correr el script sin `pip install -e .` (mismo patrón que
# scripts/probar_extraccion.py y streamlit_app.py).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.almacenamiento import RUTA_BASE, conectar  # noqa: E402
from core.analisis.diccionario import cargar_diccionario  # noqa: E402
from core.analisis.homologacion import umbral_coincidencia  # noqa: E402
from core.rehomologacion import (  # noqa: E402
    aplicar_cambios,
    leer_filas_a_rehomologar,
    recalcular,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--servicio", default=None, help="Acotar a un solo servicio.")
    parser.add_argument(
        "--base",
        type=Path,
        default=None,
        help="Ruta a la base DuckDB (default: data/reales/facturas.duckdb).",
    )
    parser.add_argument(
        "--aplicar",
        action="store_true",
        help="Escribe los cambios en la base. Sin esto, solo muestra el diff (dry-run).",
    )
    args = parser.parse_args()

    ruta = args.base or RUTA_BASE
    con = conectar(ruta)
    try:
        filas = leer_filas_a_rehomologar(con, servicio=args.servicio)
        if not filas:
            print("No hay conceptos para rehomologar (¿base vacía o servicio inexistente?).")
            return 0

        servicios = {f.servicio for f in filas}
        if None in servicios:
            print("No se re-homologa: hay filas sin servicio asignado.", file=sys.stderr)
            return 2
        diccionarios = {s: cargar_diccionario(s) for s in servicios}
        inseguros = sorted(s for s, d in diccionarios.items() if not d)
        if inseguros:
            print(
                f"No se re-homologa: diccionario vacío para {', '.join(inseguros)}.",
                file=sys.stderr,
            )
            return 2

        cambios = recalcular(filas, diccionarios, umbral=umbral_coincidencia())
        conteo = Counter(c.tipo for c in cambios)
        print(f"Filas evaluadas: {len(cambios)}")
        print(f"  nuevo:      {conteo['nuevo']}")
        print(f"  regresion:  {conteo['regresion']}")
        print(f"  cambio:     {conteo['cambio']}")
        print(f"  sin_cambio: {conteo['sin_cambio']}")
        print()

        regresiones = [c for c in cambios if c.tipo == "regresion"]
        if regresiones:
            print("=== REGRESIONES -- ¿un alias nuevo le robó el match a este concepto? ===")
            for c in regresiones:
                print(
                    f"  {c.descripcion!r}: {c.concepto_antes} "
                    f"({f'{c.score_antes:.3f}' if c.score_antes is not None else 'sin medición'}) "
                    f"-> sin clasificar ({c.score_despues:.3f})"
                )
            print()

        cambiados = [c for c in cambios if c.tipo in ("nuevo", "cambio")]
        if cambiados:
            print("=== NUEVOS / CAMBIOS ===")
            for c in cambiados:
                antes = c.concepto_antes or "(sin clasificar)"
                print(
                    f"  {c.descripcion!r}: {antes} -> {c.concepto_despues} ({c.score_despues:.3f})"
                )
            print()

        if not args.aplicar:
            print(
                "Dry-run: no se escribió nada. Correr con --aplicar para persistir estos cambios."
            )
            return 0

        tocadas = aplicar_cambios(con, cambios)
        print(f"Aplicado: {tocadas} fila(s) actualizada(s).")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
