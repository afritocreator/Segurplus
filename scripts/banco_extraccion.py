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
from core.extraccion.proveedores import (
    RUTA_CONFIGURACION as RUTA_CONFIGURACION_EXTRACCION,  # noqa: E402
)
from core.extraccion.proveedores import _leer_con_proveedor, leer_configuracion  # noqa: E402
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
    concepto_sugerido_correctos: int = 0
    concepto_sugerido_total: int = 0
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

    @property
    def score_concepto_sugerido(self) -> float | None:
        """`None` (no `1.0`) cuando la verdad de esta factura no tiene
        ningún `concepto_correcto` marcado -- a diferencia de cabecera/
        conceptos/impuestos, acá "no hay nada que medir" es un caso real y
        frecuente (ver data/reales/banco/gas_1.yaml), no debería promediar
        como si el proveedor hubiera acertado todo."""
        if self.concepto_sugerido_total == 0:
            return None
        return _porcentaje(self.concepto_sugerido_correctos, self.concepto_sugerido_total)


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


def _contar_concepto_sugerido(
    extraidas: list[tuple[str, float, str | None]],
    verdad: list[tuple[str, float, str | None]],
) -> tuple[int, int]:
    """De las líneas de `verdad` que tienen `concepto_correcto` (no todas lo
    tienen -- una línea ambigua, como las de `data/reales/banco/gas_1.yaml`,
    se deja deliberadamente sin uno), cuenta cuántas: (a) el proveedor
    emparejó por importe+descripción (mismo criterio que `_emparejar_lineas`)
    y (b) el `concepto_sugerido` de esa línea coincide con el
    `concepto_correcto` de la verdad. Bloque 3 del plan de rediseño de
    septiembre 2026."""
    disponibles = list(extraidas)
    correctos = 0
    total = 0
    for descripcion_v, importe_v, concepto_correcto in verdad:
        if concepto_correcto is None:
            continue
        total += 1
        mejor_idx = None
        mejor_score = 0.0
        for idx, (descripcion_e, importe_e, _sugerido) in enumerate(disponibles):
            if not _numero_cerca(importe_e, importe_v):
                continue
            score = similitud(descripcion_e, descripcion_v)
            if score > mejor_score:
                mejor_score = score
                mejor_idx = idx
        if mejor_idx is None or mejor_score < _UMBRAL_SIMILITUD_DESCRIPCION:
            continue
        _descripcion_e, _importe_e, sugerido_e = disponibles.pop(mejor_idx)
        if sugerido_e == concepto_correcto:
            correctos += 1
    return correctos, total


def comparar_factura(
    extraida: FacturaExtraida, verdad: dict, *, total_impreso_pdf: float | None = None
) -> tuple[int, int, int, int, int, int, bool, bool, bool | None, int, int]:
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

    conceptos_verdad_sugerido = [
        (c["descripcion"], float(c["importe"]), c.get("concepto_correcto"))
        for c in verdad.get("conceptos", [])
    ]
    conceptos_extraidos_sugerido = [
        (c.descripcion, c.importe, c.concepto_sugerido) for c in extraida.conceptos
    ]
    concepto_sugerido_correctos, concepto_sugerido_total = _contar_concepto_sugerido(
        conceptos_extraidos_sugerido, conceptos_verdad_sugerido
    )

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
        concepto_sugerido_correctos,
        concepto_sugerido_total,
    )


# --- Proveedores ------------------------------------------------------------
# Cada entrada de data/extraccion.yaml se registra acá bajo su propio
# nombre, para poder correr `--proveedor groq_scout` sin tocar el resto de
# la cascada (ver core/extraccion/proveedores/, Bloque 2 del plan). Un YAML
# corrupto o ausente no debe tumbar el import de este script -- solo el
# proveedor "gemini" (el único ya verificado) queda si `leer_configuracion`
# falla, para que el banco siga siendo usable con la línea de base de hoy.


def _leer_con_gemini(pdf_bytes: bytes, texto_extraido: str) -> FacturaExtraida:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ExtraccionError("Falta GEMINI_API_KEY en el entorno.")
    return extraer_con_gemini(pdf_bytes, api_key=api_key, texto_extraido=texto_extraido)


def _registro_proveedores() -> dict:
    registro = {"gemini": _leer_con_gemini}
    try:
        configuraciones = leer_configuracion()
    except (OSError, ValueError) as exc:
        print(f"  (aviso) no se pudo leer {RUTA_CONFIGURACION_EXTRACCION}: {exc}", file=sys.stderr)
        return registro
    for config in configuraciones:
        if config.nombre == "gemini":
            continue  # ya está arriba, con su propio wrapper simple

        def _leer(pdf_bytes: bytes, texto_extraido: str, _config=config) -> FacturaExtraida:
            return _leer_con_proveedor(_config, pdf_bytes, texto_extraido)

        registro[config.nombre] = _leer
    return registro


PROVEEDORES = _registro_proveedores()


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
                        concepto_sugerido_correctos=0,
                        concepto_sugerido_total=sum(
                            1
                            for c in fx.verdad.get("conceptos", [])
                            if c.get("concepto_correcto") is not None
                        ),
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
                sugerido_ok,
                sugerido_total,
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
                    concepto_sugerido_correctos=sugerido_ok,
                    concepto_sugerido_total=sugerido_total,
                )
            )
    return resultados


def _formato_score_opcional(score: float | None) -> str:
    return "n/a" if score is None else f"{score:.0%}"


def imprimir_tabla(resultados: list[ResultadoComparacion]) -> None:
    encabezado = (
        f"{'Proveedor':12s} {'Factura':10s} {'Cabecera':>10s} {'Conceptos':>10s} "
        f"{'Impuestos':>10s} {'Concepto*':>10s} {'Subtot.':>8s} {'Total':>6s} "
        f"{'Cierra':>7s} {'Seg.':>6s}"
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
            f"{_formato_score_opcional(r.score_concepto_sugerido):>10s} "
            f"{'sí' if r.subtotal_ok else 'NO':>8s} {'sí' if r.total_ok else 'NO':>6s} "
            f"{cierra:>7s} {r.segundos:>5.1f}s"
        )
    print(
        "* Concepto: % de concepto_sugerido correcto, solo sobre líneas con "
        "concepto_correcto en la verdad."
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
        scores_sugerido = [
            r.score_concepto_sugerido for r in filas if r.score_concepto_sugerido is not None
        ]
        promedio_sugerido = (
            _formato_score_opcional(sum(scores_sugerido) / len(scores_sugerido))
            if scores_sugerido
            else "n/a"
        )
        print(
            f"{proveedor}: promedio cabecera {promedio_cabecera:.0%}, "
            f"conceptos {promedio_conceptos:.0%}, impuestos {promedio_impuestos:.0%}, "
            f"concepto_sugerido {promedio_sugerido} "
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
