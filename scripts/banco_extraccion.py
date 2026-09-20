#!/usr/bin/env python3
"""Banco de medición de extracción: corre uno o más proveedores de lectura
de facturas contra un set de facturas reales con verdad de referencia
tipeada a mano, y publica un porcentaje de acierto por proveedor.

Por qué existe (docs/estado.md, plan de rediseño de septiembre 2026): el
proyecto arrastraba un pendiente desde `docs/auditoria-2026-09-facturas-
reales.md` (hallazgo B-3) -- el refuerzo del prompt de extracción para las
líneas de impuesto con dos montos NUNCA se verificó contra la API real, por
falta de GEMINI_API_KEY en el entorno de desarrollo. Cambiar de proveedor
(Groq, Cerebras, SambaNova...) sin medir primero es adivinar. Este script
convierte "¿lee bien?" en un número, con las mismas facturas siempre, para
que la decisión de proveedor salga de una tabla y no de una opinión.

Uso:
    export GEMINI_API_KEY=...      # y/o GROQ_API_KEY, según el proveedor
    python scripts/banco_extraccion.py --proveedor gemini
    python scripts/banco_extraccion.py --proveedor gemini --proveedor groq_scout

Lee de `data/reales/banco/`: para cada `<nombre>.pdf` busca su
`<nombre>.yaml` de verdad de referencia. Esa carpeta está en la zona
restringida (`data/reales/`, ver CLAUDE.md): nunca se commitea, y este
script nunca escribe ahí ni en ninguna base de datos -- es de solo lectura,
mismo criterio que `scripts/probar_extraccion.py`.

Si la carpeta no existe o está vacía (por ejemplo, en un chequeo de CI que
nunca tuvo acceso a facturas reales), el script lo dice y termina sin
error: no hay nada que medir, no es una falla del banco.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Permite correr el script sin `pip install -e .` (mismo patrón que probar_extraccion.py).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.analisis.homologacion import normalizar, similitud  # noqa: E402
from core.extraccion.esquema import FacturaExtraida  # noqa: E402
from core.extraccion.gemini import ExtraccionError, extraer_con_gemini  # noqa: E402
from core.extraccion.validacion import validar_factura  # noqa: E402
from core.ingesta.pdf_texto import extraer_texto, total_impreso  # noqa: E402

RUTA_BANCO = Path(__file__).resolve().parent.parent / "data" / "reales" / "banco"

# Campos de cabecera comparables 1 a 1 (texto o fecha ISO). subtotal/total se
# comparan aparte, con tolerancia numérica en vez de igualdad de texto.
_CAMPOS_CABECERA_TEXTO = (
    "emisor",
    "cuit",
    "servicio",
    "periodo_desde",
    "periodo_hasta",
    "fecha_emision",
    "fecha_vencimiento",
    "numero_comprobante",
    "moneda",
)

# Tolerancia para "es la misma línea": mismo criterio de $1 que
# core/extraccion/validacion.py usa para cuadrar una línea de concepto.
_TOLERANCIA_IMPORTE = 1.0
# Un slug de un proveedor real puede describir la misma línea con palabras
# bastante distintas de las que un humano tipeó a mano en la verdad de
# referencia (ver, p. ej., data/reales/banco/gas_1.yaml) -- un umbral bajo
# de similitud alcanza porque el importe ya hace la mayor parte del trabajo
# de identificar la línea correcta.
_UMBRAL_SIMILITUD_DESCRIPCION = 0.2


@dataclass
class ResultadoComparacion:
    """Cuánto de la verdad de referencia de UNA factura reconstruyó UNA
    corrida de un proveedor -- ver `comparar_factura` para cómo se arma."""

    factura: str
    proveedor: str
    cabecera_correctos: int
    cabecera_total: int
    conceptos_correctos: int
    conceptos_total: int
    impuestos_correctos: int
    impuestos_total: int
    subtotal_ok: bool
    total_ok: bool
    aritmetica_cierra: bool | None  # None = no se pudo evaluar (ver error)
    segundos: float
    error: str | None = None

    @property
    def score_cabecera(self) -> float:
        return _porcentaje(self.cabecera_correctos, self.cabecera_total)

    @property
    def score_conceptos(self) -> float:
        return _porcentaje(self.conceptos_correctos, self.conceptos_total)

    @property
    def score_impuestos(self) -> float:
        return _porcentaje(self.impuestos_correctos, self.impuestos_total)


def _porcentaje(correctos: int, total: int) -> float:
    return (correctos / total) if total else 1.0


def _texto_iguales(a: object, b: object) -> bool:
    """Compara dos valores de cabecera tolerando mayúsculas, acentos y
    espacios -- lo que puede variar entre proveedores sin que sea un error
    de lectura (ver `core.analisis.homologacion.normalizar`, misma función
    que ya usa el resto del proyecto para esto)."""
    if a is None or b is None:
        return a is None and b is None
    return normalizar(str(a)) == normalizar(str(b))


def _numero_cerca(
    a: float | None, b: float | None, *, tolerancia: float = _TOLERANCIA_IMPORTE
) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= tolerancia


def _emparejar_lineas(extraidas: list[tuple[str, float]], verdad: list[tuple[str, float]]) -> int:
    """Cuenta cuántas líneas de `verdad` tienen una línea correspondiente en
    `extraidas`: mismo importe (tolerancia de $1) y descripción parecida
    (Dice sobre bigramas, umbral bajo -- sirve para no exigir el mismo
    texto literal que un modelo distinto redacta distinto). Cada línea
    extraída se usa como máximo una vez (no se puede "acertar dos veces"
    con la misma línea)."""
    disponibles = list(extraidas)
    correctos = 0
    for descripcion_v, importe_v in verdad:
        mejor_idx = None
        mejor_score = 0.0
        for idx, (descripcion_e, importe_e) in enumerate(disponibles):
            if not _numero_cerca(importe_e, importe_v):
                continue
            score = similitud(descripcion_e, descripcion_v)
            if score > mejor_score:
                mejor_score = score
                mejor_idx = idx
        if mejor_idx is not None and mejor_score >= _UMBRAL_SIMILITUD_DESCRIPCION:
            correctos += 1
            disponibles.pop(mejor_idx)
    return correctos


def comparar_factura(
    extraida: FacturaExtraida, verdad: dict, *, total_impreso_pdf: float | None = None
) -> tuple[int, int, int, int, int, int, bool, bool, bool | None]:
    """Compara una `FacturaExtraida` contra su verdad de referencia (el dict
    parseado de un YAML de `data/reales/banco/`). Devuelve las cuentas
    crudas -- `comparar_factura_resultado` arma el dataclass completo, esta
    función queda separada porque es la que prueba
    `tests/scripts/test_banco_extraccion.py` sin necesitar un
    `FacturaExtraida` completo de verdad ni tocar la aritmética."""
    cabecera_correctos = sum(
        1
        for campo in _CAMPOS_CABECERA_TEXTO
        if _texto_iguales(getattr(extraida, campo), verdad.get(campo))
    )
    cabecera_total = len(_CAMPOS_CABECERA_TEXTO)

    conceptos_verdad = [
        (c["descripcion"], float(c["importe"])) for c in verdad.get("conceptos", [])
    ]
    conceptos_extraidos = [(c.descripcion, c.importe) for c in extraida.conceptos]
    conceptos_correctos = _emparejar_lineas(conceptos_extraidos, conceptos_verdad)

    impuestos_verdad = [(i["nombre"], float(i["importe"])) for i in verdad.get("impuestos", [])]
    impuestos_extraidos = [(i.nombre, i.importe) for i in extraida.impuestos]
    impuestos_correctos = _emparejar_lineas(impuestos_extraidos, impuestos_verdad)

    subtotal_ok = _numero_cerca(extraida.subtotal, verdad.get("subtotal"))
    total_ok = _numero_cerca(extraida.total, verdad.get("total"))

    aritmetica_cierra: bool | None
    try:
        resultado_validacion = validar_factura(extraida, total_impreso=total_impreso_pdf)
        aritmetica_cierra = resultado_validacion.factura_valida
    except Exception:  # noqa: BLE001 -- una factura muy incompleta puede no ser evaluable
        aritmetica_cierra = None

    return (
        cabecera_correctos,
        cabecera_total,
        conceptos_correctos,
        len(conceptos_verdad),
        impuestos_correctos,
        len(impuestos_verdad),
        subtotal_ok,
        total_ok,
        aritmetica_cierra,
    )


# --- Proveedores ------------------------------------------------------------
# Registro chico a propósito: el Bloque 2 del plan (capa de proveedores
# intercambiable, core/extraccion/proveedores/) reemplaza esto por algo más
# genérico cuando Groq/Cerebras/SambaNova entren en juego. Hasta entonces,
# un dict alcanza y no hay que inventar una abstracción que todavía no se usa
# (CLAUDE.md: "sin abstracciones que no se usan todavía").


def _leer_con_gemini(pdf_bytes: bytes, texto_extraido: str) -> FacturaExtraida:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ExtraccionError("Falta GEMINI_API_KEY en el entorno.")
    return extraer_con_gemini(pdf_bytes, api_key=api_key, texto_extraido=texto_extraido)


PROVEEDORES = {
    "gemini": _leer_con_gemini,
}


@dataclass
class _Fixture:
    nombre: str
    pdf: Path
    verdad: dict = field(repr=False)
    texto: str = field(repr=False)


def _cargar_fixtures(directorio: Path) -> list[_Fixture]:
    fixtures = []
    for pdf in sorted(directorio.glob("*.pdf")):
        yaml_path = pdf.with_suffix(".yaml")
        if not yaml_path.exists():
            print(f"  (aviso) {pdf.name} no tiene {yaml_path.name} -- se omite", file=sys.stderr)
            continue
        verdad = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        documento = extraer_texto(pdf)
        fixtures.append(_Fixture(nombre=pdf.stem, pdf=pdf, verdad=verdad, texto=documento.texto))
    return fixtures


def correr_banco(
    proveedores: list[str], *, directorio: Path = RUTA_BANCO
) -> list[ResultadoComparacion]:
    resultados: list[ResultadoComparacion] = []
    fixtures = _cargar_fixtures(directorio)
    for nombre_proveedor in proveedores:
        leer = PROVEEDORES[nombre_proveedor]
        for fx in fixtures:
            inicio = time.monotonic()
            try:
                extraida = leer(fx.pdf.read_bytes(), fx.texto)
                error = None
            except ExtraccionError as exc:
                extraida = None
                error = str(exc)
            segundos = time.monotonic() - inicio

            if extraida is None:
                resultados.append(
                    ResultadoComparacion(
                        factura=fx.nombre,
                        proveedor=nombre_proveedor,
                        cabecera_correctos=0,
                        cabecera_total=len(_CAMPOS_CABECERA_TEXTO),
                        conceptos_correctos=0,
                        conceptos_total=len(fx.verdad.get("conceptos", [])),
                        impuestos_correctos=0,
                        impuestos_total=len(fx.verdad.get("impuestos", [])),
                        subtotal_ok=False,
                        total_ok=False,
                        aritmetica_cierra=None,
                        segundos=segundos,
                        error=error,
                    )
                )
                continue

            total_pdf = total_impreso(fx.texto)
            (
                cab_ok,
                cab_total,
                con_ok,
                con_total,
                imp_ok,
                imp_total,
                subtotal_ok,
                total_ok,
                cierra,
            ) = comparar_factura(extraida, fx.verdad, total_impreso_pdf=total_pdf)
            resultados.append(
                ResultadoComparacion(
                    factura=fx.nombre,
                    proveedor=nombre_proveedor,
                    cabecera_correctos=cab_ok,
                    cabecera_total=cab_total,
                    conceptos_correctos=con_ok,
                    conceptos_total=con_total,
                    impuestos_correctos=imp_ok,
                    impuestos_total=imp_total,
                    subtotal_ok=subtotal_ok,
                    total_ok=total_ok,
                    aritmetica_cierra=cierra,
                    segundos=segundos,
                )
            )
    return resultados


def imprimir_tabla(resultados: list[ResultadoComparacion]) -> None:
    encabezado = (
        f"{'Proveedor':12s} {'Factura':10s} {'Cabecera':>10s} {'Conceptos':>10s} "
        f"{'Impuestos':>10s} {'Subtot.':>8s} {'Total':>6s} {'Cierra':>7s} {'Seg.':>6s}"
    )
    print(encabezado)
    print("-" * len(encabezado))
    for r in resultados:
        if r.error:
            print(f"{r.proveedor:12s} {r.factura:10s}  ERROR: {r.error}")
            continue
        if r.aritmetica_cierra is None:
            cierra = "?"
        else:
            cierra = "sí" if r.aritmetica_cierra else "NO"
        print(
            f"{r.proveedor:12s} {r.factura:10s} "
            f"{r.score_cabecera:>9.0%} {r.score_conceptos:>9.0%} {r.score_impuestos:>9.0%} "
            f"{'sí' if r.subtotal_ok else 'NO':>8s} {'sí' if r.total_ok else 'NO':>6s} "
            f"{cierra:>7s} {r.segundos:>5.1f}s"
        )

    print()
    proveedores_vistos = list(dict.fromkeys(r.proveedor for r in resultados))
    for proveedor in proveedores_vistos:
        filas = [r for r in resultados if r.proveedor == proveedor and not r.error]
        if not filas:
            continue
        con_error = sum(1 for r in resultados if r.proveedor == proveedor and r.error)
        promedio_cabecera = sum(r.score_cabecera for r in filas) / len(filas)
        promedio_conceptos = sum(r.score_conceptos for r in filas) / len(filas)
        promedio_impuestos = sum(r.score_impuestos for r in filas) / len(filas)
        print(
            f"{proveedor}: promedio cabecera {promedio_cabecera:.0%}, "
            f"conceptos {promedio_conceptos:.0%}, impuestos {promedio_impuestos:.0%} "
            f"({len(filas)}/{len(filas) + con_error} facturas leídas sin error)"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--proveedor",
        action="append",
        dest="proveedores",
        choices=sorted(PROVEEDORES),
        help="Puede repetirse. Default: todos los registrados.",
    )
    parser.add_argument(
        "--dir",
        type=Path,
        default=RUTA_BANCO,
        help=f"Carpeta con los PDF y YAML de verdad (default: {RUTA_BANCO}).",
    )
    args = parser.parse_args()

    if not args.dir.exists() or not any(args.dir.glob("*.pdf")):
        print(
            f"No hay facturas en {args.dir} -- nada que medir. "
            "Ver docs/banco_extraccion.md para cómo armar el set de verdad.",
        )
        return 0

    proveedores = args.proveedores or sorted(PROVEEDORES)
    resultados = correr_banco(proveedores, directorio=args.dir)
    if not resultados:
        print("Ninguna factura tenía un YAML de verdad al lado -- nada que medir.")
        return 0
    imprimir_tabla(resultados)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
