"""Orquesta el pipeline completo para UN PDF: ingesta -> extracción con
Gemini -> validación aritmética -> homologación -> guardado (en `facturas`
si es válida, en `cuarentena` si no). Es la única función que la app
Streamlit (o un script de línea de comandos, a futuro) necesita llamar por
factura -- así la UI queda como cáscara fina (CLAUDE.md) y este módulo es
testeable sin Streamlit.

No hace nada nuevo: compone funciones ya escritas y probadas en
`core/ingesta/`, `core/extraccion/` y `core/analisis/`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb

from core.almacenamiento import factura_ya_procesada, guardar_en_cuarentena, guardar_factura
from core.analisis.diccionario import cargar_diccionario
from core.analisis.homologacion import homologar_concepto
from core.extraccion.gemini import ExtraccionError, extraer_con_gemini
from core.extraccion.validacion import validar_factura
from core.ingesta.pdf_texto import PdfSinTextoError, extraer_texto, total_impreso


@dataclass
class ResultadoPipeline:
    ruta: Path
    hash_pdf: str
    estado: str  # "ya_procesada" | "cuarentena" | "guardada" | "error_extraccion"
    detalle: str = ""


def procesar_pdf(
    ruta: Path,
    con: duckdb.DuckDBPyConnection,
    *,
    api_key: str | None = None,
    diccionario: dict[str, list[str]] | None = None,
) -> ResultadoPipeline:
    """Procesa un único PDF de punta a punta. No lanza excepciones para
    errores esperables del pipeline (PDF sin texto, extracción fallida,
    factura que no valida) -- esos casos se reportan en `ResultadoPipeline`,
    no cortan el procesamiento de los demás PDFs de un lote."""
    diccionario = diccionario if diccionario is not None else cargar_diccionario()

    try:
        documento = extraer_texto(ruta)
    except PdfSinTextoError as exc:
        return ResultadoPipeline(ruta, hash_pdf="", estado="error_extraccion", detalle=str(exc))

    if factura_ya_procesada(con, documento.hash_sha256):
        return ResultadoPipeline(ruta, documento.hash_sha256, estado="ya_procesada")

    try:
        factura = extraer_con_gemini(ruta.read_bytes(), api_key=api_key)
    except ExtraccionError as exc:
        return ResultadoPipeline(
            ruta, documento.hash_sha256, estado="error_extraccion", detalle=str(exc)
        )

    factura.hash_pdf = documento.hash_sha256
    factura.ruta_pdf = str(ruta)

    resultado = validar_factura(factura, total_impreso=total_impreso(documento.texto))

    if not resultado.factura_valida:
        guardar_en_cuarentena(
            con, hash_pdf=factura.hash_pdf, ruta_pdf=factura.ruta_pdf, resultado=resultado
        )
        return ResultadoPipeline(
            ruta,
            factura.hash_pdf,
            estado="cuarentena",
            detalle="; ".join(resultado.motivos_de_falla()),
        )

    conceptos_normalizados = {}
    for i, c in enumerate(factura.conceptos):
        concepto, _score = homologar_concepto(c.descripcion, diccionario)
        if concepto:
            conceptos_normalizados[i] = concepto

    guardar_factura(con, factura, conceptos_normalizados=conceptos_normalizados)
    return ResultadoPipeline(ruta, factura.hash_pdf, estado="guardada")
