#!/usr/bin/env python3
"""Prueba la extracción con Gemini de punta a punta contra UNA factura real,
sin levantar el tablero ni tocar la base de datos -- equivalente de
`scripts/test-invoice-extraction.ts` de Kleric-.

Uso:
    export GEMINI_API_KEY=...          # la misma que está cargada en Streamlit Cloud
    python scripts/probar_extraccion.py ruta/a/factura.pdf

Qué hace, en orden, mostrando cada paso:
1. Llama a Gemini (vía `core.extraccion.gemini.extraer_con_gemini`, la MISMA
   función que usa `core/pipeline.py` -- ver el porqué más abajo) y muestra
   el JSON CRUDO que devolvió, antes de convertirlo a `FacturaExtraida`.
2. Campos clave para revisar a mano.
3. Lee el total impreso en el PDF con la regex de `core/ingesta/pdf_texto.py`
   (la doble lectura).
4. Corre la validación aritmética y muestra el resultado de cada control.

Llama a `extraer_con_gemini` en vez de reimplementar la llamada a la API acá
a propósito (docs/auditoria-2026-09-piloto.md, hallazgo B-3): antes este
script armaba su propio `cliente.models.generate_content(...)`, duplicando
la lógica de `core/extraccion/gemini.py` -- cuando esa función empezó a
mandarle también el texto extraído del PDF, este script se quedó atrás,
mostrando un resultado que ya no coincidía con el que da la app real.
Llamando a la función real, este script SIEMPRE prueba exactamente lo mismo
que carga.py -- no puede volver a desalinearse.

Este script NUNCA escribe en `data/reales/facturas.duckdb` ni en ningún otro
lado -- es de solo lectura/diagnóstico, para no mezclar una prueba con datos
reales de verdad.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Permite correr el script sin `pip install -e .` (mismo patrón que streamlit_app.py).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.extraccion.gemini import MODELO, ExtraccionError, extraer_con_gemini  # noqa: E402
from core.extraccion.validacion import validar_factura  # noqa: E402
from core.ingesta.pdf_texto import extraer_texto, total_impreso  # noqa: E402


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Uso: {sys.argv[0]} ruta/a/factura.pdf", file=sys.stderr)
        return 1

    ruta = Path(sys.argv[1])
    if not ruta.exists():
        print(f"No existe: {ruta}", file=sys.stderr)
        return 1

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Falta GEMINI_API_KEY en el entorno (export GEMINI_API_KEY=...).", file=sys.stderr)
        return 1

    documento = extraer_texto(ruta)

    print(f"=== 1. Llamando a Gemini ({MODELO}) con {ruta.name} ===\n")
    try:
        factura = extraer_con_gemini(
            ruta.read_bytes(), api_key=api_key, texto_extraido=documento.texto
        )
    except ExtraccionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print(
            "\nSi el error menciona un parámetro desconocido o un formato inesperado, "
            "la forma de la API de google-genai cambió -- revisar la documentación "
            "vigente (ver docs/decisiones/ADR-001-lectura-de-facturas.md).",
            file=sys.stderr,
        )
        return 1

    print("--- JSON crudo devuelto por el modelo ---")
    print(factura.respuesta_extraida)
    print()

    datos = json.loads(factura.respuesta_extraida)  # ya se sabe que es JSON válido, si llegó acá

    print("=== 2. Campos clave para revisar a mano ===\n")
    for campo in (
        "emisor",
        "cuit",
        "servicio",
        "periodo_desde",
        "periodo_hasta",
        "fecha_emision",
        "subtotal",
        "total",
    ):
        valor = datos.get(campo)
        marca = "  <-- null" if valor is None else ""
        print(f"  {campo:20s} = {valor!r}{marca}")
    print(f"  conceptos: {len(datos.get('conceptos', []))} línea(s)")
    print(f"  impuestos: {len(datos.get('impuestos', []))} línea(s)")
    print(f"  recargos:  {len(datos.get('recargos', []))} línea(s)")
    print()

    print("=== 3. Doble lectura del total (regex sobre el texto del PDF) ===\n")
    total_leido = total_impreso(documento.texto)
    print(f"  Total según el modelo:      {factura.total!r}")
    print(f"  Total leído con la regex:   {total_leido!r}")
    if factura.total is not None and total_leido is not None:
        diferencia = abs(factura.total - total_leido)
        print(f"  Diferencia:                 {diferencia:.2f}")
    print()

    print("=== 4. Validación aritmética ===\n")
    resultado = validar_factura(factura, total_impreso=total_leido)
    print(f"  suma_conceptos:    {resultado.suma_conceptos:.2f}")
    print(f"  suma_impuestos:    {resultado.suma_impuestos:.2f}")
    print(f"  suma_recargos:     {resultado.suma_recargos:.2f}")
    print(f"  todas_las_lineas_ok: {resultado.todas_las_lineas_ok}")
    print(f"  subtotal_presente:   {resultado.subtotal_presente}")
    print(f"  subtotal_ok:         {resultado.subtotal_ok}")
    print(f"  total_presente:      {resultado.total_presente}")
    print(f"  total_ok:            {resultado.total_ok}")
    print(f"  total_impreso_ok:    {resultado.total_impreso_ok}")
    print(f"  FACTURA_VALIDA:      {resultado.factura_valida}")
    if not resultado.factura_valida:
        print("\n  Motivos:")
        for motivo in resultado.motivos_de_falla():
            print(f"    - {motivo}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
