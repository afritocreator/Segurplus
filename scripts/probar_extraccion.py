#!/usr/bin/env python3
"""Prueba la extracción con Gemini de punta a punta contra UNA factura real,
sin levantar el tablero ni tocar la base de datos -- equivalente de
`scripts/test-invoice-extraction.ts` de Kleric-.

Uso:
    export GEMINI_API_KEY=...          # la misma que está cargada en Streamlit Cloud
    python scripts/probar_extraccion.py ruta/a/factura.pdf

Qué hace, en orden, mostrando cada paso:
1. Llama a Gemini y muestra el JSON CRUDO que devolvió (antes de convertirlo a
   FacturaExtraida) -- así se ve exactamente qué manda el modelo, incluso si
   `factura_desde_json` fallara al interpretarlo.
2. Convierte ese JSON al esquema canónico.
3. Lee el total impreso en el PDF con la regex de `core/ingesta/pdf_texto.py`
   (la doble lectura).
4. Corre la validación aritmética y muestra el resultado de cada control.

Este script NUNCA escribe en `data/reales/facturas.duckdb` ni en ningún otro
lado -- es de solo lectura/diagnóstico, para no mezclar una prueba con datos
reales de verdad (ver Bloque 9 del plan de correcciones, que sí carga).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Permite correr el script sin `pip install -e .` (mismo patrón que streamlit_app.py).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.extraccion.esquema import esquema_json_para_modelo  # noqa: E402
from core.extraccion.gemini import MODELO, PROMPT_EXTRACCION, factura_desde_json  # noqa: E402
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

    print(f"=== 1. Llamando a Gemini ({MODELO}) con {ruta.name} ===\n")

    from google import genai  # import diferido, igual que en core/extraccion/gemini.py

    cliente = genai.Client(api_key=api_key)
    pdf_bytes = ruta.read_bytes()
    try:
        respuesta = cliente.models.generate_content(
            model=MODELO,
            contents=[
                PROMPT_EXTRACCION,
                {"inline_data": {"data": pdf_bytes, "mime_type": "application/pdf"}},
            ],
            config={
                "response_mime_type": "application/json",
                "response_json_schema": esquema_json_para_modelo(),
            },
        )
    except Exception as exc:  # noqa: BLE001 -- se quiere ver el error tal cual, no envuelto
        print(f"ERROR llamando a Gemini: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(
            "\nSi el error menciona un parámetro desconocido o un formato inesperado, "
            "la forma de la API de google-genai cambió -- revisar la documentación "
            "vigente (ver docs/decisiones/ADR-001-lectura-de-facturas.md).",
            file=sys.stderr,
        )
        return 1

    texto = respuesta.text
    print("--- JSON crudo devuelto por el modelo ---")
    print(texto)
    print()

    if not texto:
        print("ERROR: Gemini no devolvió texto en la respuesta.", file=sys.stderr)
        return 1

    try:
        datos = json.loads(texto)
    except json.JSONDecodeError as exc:
        print(f"ERROR: la respuesta no es JSON válido: {exc}", file=sys.stderr)
        return 1

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

    print("=== 3. Convirtiendo al esquema canónico (factura_desde_json) ===\n")
    try:
        factura = factura_desde_json(datos)
    except Exception as exc:  # noqa: BLE001 -- se quiere ver el error tal cual
        print(f"ERROR convirtiendo el JSON: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print("  OK\n")

    print("=== 4. Doble lectura del total (regex sobre el texto del PDF) ===\n")
    documento = extraer_texto(ruta)
    total_leido = total_impreso(documento.texto)
    print(f"  Total según el modelo:      {factura.total!r}")
    print(f"  Total leído con la regex:   {total_leido!r}")
    if factura.total is not None and total_leido is not None:
        diferencia = abs(factura.total - total_leido)
        print(f"  Diferencia:                 {diferencia:.2f}")
    print()

    print("=== 5. Validación aritmética ===\n")
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
