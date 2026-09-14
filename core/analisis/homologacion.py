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
from dataclasses import dataclass
from pathlib import Path

import yaml

RUTA_HOMOLOGACION = Path(__file__).resolve().parents[2] / "data" / "homologacion.yaml"


def umbral_coincidencia() -> float:
    """Sin cache y sin lectura a nivel de módulo, a propósito -- un YAML
    corrupto no debe tumbar el import ni la app Streamlit (mismo patrón que
    `core/analisis/alertas.py::_leer_umbrales`). Ver `data/homologacion.yaml`
    para el valor y por qué es provisorio (docs/auditoria-2026-09.md, A-3).

    Pública (no `_umbral_coincidencia`): la usa también
    `apps/segurplus/paginas/sin_clasificar.py` para el semáforo de "le
    falta poco" y la línea vertical del histograma de scores."""
    datos = yaml.safe_load(RUTA_HOMOLOGACION.read_text(encoding="utf-8"))
    if not isinstance(datos, dict) or "umbral_coincidencia" not in datos:
        raise ValueError(
            f"{RUTA_HOMOLOGACION} no tiene la forma esperada (falta 'umbral_coincidencia')"
        )
    return float(datos["umbral_coincidencia"])


def margen_cerca_del_umbral() -> float:
    """Margen configurable para marcar un concepto como candidato a alias."""
    datos = yaml.safe_load(RUTA_HOMOLOGACION.read_text(encoding="utf-8"))
    if not isinstance(datos, dict) or "margen_cerca_del_umbral" not in datos:
        raise ValueError(f"{RUTA_HOMOLOGACION} no tiene 'margen_cerca_del_umbral'")
    return float(datos["margen_cerca_del_umbral"])


def normalizar(texto: str) -> str:
    texto = texto.lower()
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")  # saca acentos
    texto = re.sub(r"[^a-z0-9\s]", " ", texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto


_MESES = (
    r"enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|"
    r"octubre|noviembre|diciembre|ene|feb|mar|abr|may|jun|jul|ago|sep|sept|set|oct|nov|dic"
)


@dataclass(frozen=True)
class ResultadoHomologacion:
    concepto: str | None
    score: float
    candidatos_empatados: tuple[str, ...] = ()

    def __iter__(self):
        """Compatibilidad transitoria con los llamadores que desempaquetan dos valores."""
        yield self.concepto
        yield self.score


def quitar_periodo(texto: str) -> str:
    """Saca de `texto` (ya pasado por `normalizar`, o texto crudo) lo que sea
    inequívocamente un PERÍODO de facturación -- nombre de mes (completo o
    abreviado) y año de 4 dígitos, sueltos o pegados ("agosto 2026", "08/2026",
    "ago-2026") -- y nada más.

    Por qué existe: varios proveedores (Movistar, verificado) facturan el
    mismo concepto con el período pegado a la descripción ("Servicio de
    telefonía Agosto 2026", al mes siguiente "...Septiembre 2026"). Sin esto,
    `normalizar` conserva los dígitos y el mes, dos descripciones del MISMO
    concepto en meses distintos bajan de score 0,588 a 0,444 contra el
    diccionario -- por debajo del umbral (`data/homologacion.yaml`, 0,60) --
    y terminan homologando como "None" cada una por su lado, lo que en
    `core/analisis/agregacion.py` las agrupa bajo CLAVES DISTINTAS (una por
    período). El resultado que ve el usuario: la descomposición precio/
    cantidad interpreta un aumento de PRECIO real como si el concepto de
    agosto hubiera desaparecido y uno nuevo hubiera aparecido en septiembre
    -- efecto_precio queda en 0 y toda la variación cae, falsamente, en
    efecto_cantidad. Justo lo que la herramienta existe para distinguir.

    Deliberadamente CONSERVADORA -- NO saca cualquier número suelto. Sacar
    todo dígito fusionaría "Línea 1"/"Línea 2" o "Medidor 1"/"Medidor 2" en
    un solo concepto, perdiendo la granularidad que una empresa con varias
    líneas o medidores facturados por separado necesita (verificado: la
    versión agresiva colapsa esos casos). "Plan 5GB" vs "Plan 20GB" tampoco
    se tocan: el dígito pegado a una letra (sin espacio) no matchea ningún
    patrón de acá.

    Contrapartida aceptada: un mes abreviado de 3 letras ("mar", "may", "ago")
    puede coincidir con una palabra real dentro de una descripción (ej. "mar"
    en "Mar del Plata"). Se prefiere este costo a no reconocer las
    abreviaturas, que son comunes en facturas reales.

    Guarda: si la descripción es SOLO un período (ej. una descripción rota
    que dijera literalmente "Agosto 2026"), quitar todo dejaría un string
    vacío -- eso colapsaría cualquier par de descripciones así bajo la misma
    clave "(sin_homologar) ", que es peor que no limpiar nada. En ese caso
    se devuelve el texto normalizado sin tocar.
    """
    limpio = normalizar(texto)
    sin_periodo = re.sub(rf"\b({_MESES})\b", " ", limpio)
    sin_periodo = re.sub(r"\b\d{1,2}\s+(19|20)\d{2}\b", " ", sin_periodo)  # "08 2026"
    sin_periodo = re.sub(r"\b(19|20)\d{2}\b", " ", sin_periodo)  # año suelto
    sin_periodo = re.sub(r"\s+", " ", sin_periodo).strip()
    return sin_periodo or limpio


# Paréntesis cuyo CONTENIDO es pura aritmética -- dígitos, separadores
# decimales/de miles, operadores (/ x × * + -) y porcentaje. Deliberadamente
# no incluye letras: un paréntesis con una palabra ("Línea 2", "Medidor 3")
# no matchea y queda intacto -- ver el docstring de `quitar_detalle_numerico`.
_PATRON_DETALLE_NUMERICO = re.compile(r"\([\s\d.,/x×*+%-]*\)", re.IGNORECASE)


def quitar_detalle_numerico(texto: str) -> str:
    """Saca de `texto` (CRUDO, sin pasar por `normalizar` todavía -- a
    diferencia de `quitar_periodo`, que sí puede recibir texto ya
    normalizado) los paréntesis cuyo contenido es solo aritmética, y nada
    más.

    Por qué existe (docs/auditoria-2026-09-piloto.md, hallazgo B-2): caso
    real, dos facturas de luz de la Usina Popular de Tandil (Tandil,
    Buenos Aires) facturan "Cargo Fijo" con el detalle del cálculo pegado
    a la descripción -- "Cargo Fijo (414,4500 / 30.5 x 8)" en julio,
    "Cargo Fijo (455,8900 / 30.5 x 21)" en agosto. Ese detalle CAMBIA todos
    los meses, aunque "cargo fijo" sea un alias EXACTO de
    `data/conceptos/comunes.yaml`. Sin sacarlo, el score contra el
    diccionario cae de 1,0 a ~0,545 -- por debajo del umbral -- y, peor:
    es la misma enfermedad que A-28. Sin homologar, `agregacion.py::_clave`
    usa la descripción como clave de agrupamiento -- distinta cada mes --
    así que la descomposición ve "un concepto que desaparece" + "uno que
    aparece" con `efecto_precio == 0`, cuando la causa real puede ser un
    aumento de PRECIO real.

    Deliberadamente CONSERVADORA, igual que `quitar_periodo`: solo saca un
    paréntesis si TODO su contenido es aritmética -- ningún paréntesis con
    una palabra con significado se toca, para no fusionar dos conceptos
    distintos (ej. "Consumo (Línea 2)" sigue distinto de "Consumo (Línea 3)").

    Tiene que llamarse ANTES de `normalizar`/`quitar_periodo`, que ya
    convierten los paréntesis en espacios sueltos y pierden la distinción
    entre "paréntesis con números" y "paréntesis con palabras" -- por eso
    esta función, a diferencia de esas dos, espera texto crudo. El
    resultado sigue siendo texto crudo (sin normalizar): pasa a
    `quitar_periodo`/`normalizar` después, en `homologar_concepto` y en
    `core/analisis/agregacion.py::_clave`.

    Guarda igual que `quitar_periodo`: si sacar los paréntesis deja el
    string vacío, se devuelve el texto original sin tocar."""
    sin_detalle = _PATRON_DETALLE_NUMERICO.sub(" ", texto)
    sin_detalle = re.sub(r"\s+", " ", sin_detalle).strip()
    return sin_detalle or texto


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
    descripcion: str, diccionario: dict[str, list[str]], *, umbral: float | None = None
) -> ResultadoHomologacion:
    """Busca el concepto normalizado más parecido a `descripcion` dentro de
    `diccionario` ({concepto_normalizado: [alias, alias, ...]}).

    `umbral`: por defecto se lee de `data/homologacion.yaml`
    (`umbral_coincidencia`) -- se puede pasar explícito para tests, sin
    depender del archivo.

    Devuelve un resultado sin concepto si el mejor score queda por debajo del umbral —
    eso NO se descarta silenciosamente: en `core/analisis/alertas.py` se
    convierte en la alerta "concepto nuevo sin clasificar", que suele ser
    justo el cargo que se coló.

    La comparación corre sobre `quitar_periodo(quitar_detalle_numerico(descripcion))`
    contra lo mismo aplicado a cada candidato -- ver esas dos funciones
    para el porqué (un mes/año o un detalle de cálculo pegado a la
    descripción no debe impedir el match; `quitar_detalle_numerico` va
    PRIMERO porque necesita el texto crudo, antes de que `quitar_periodo`
    lo normalice y pierda la distinción entre paréntesis numéricos y
    paréntesis con palabras).
    """
    umbral = umbral if umbral is not None else umbral_coincidencia()
    descripcion_sin_periodo = quitar_periodo(quitar_detalle_numerico(descripcion))
    mejor_score = 0.0
    mejores: list[str] = []
    for concepto, alias in diccionario.items():
        candidatos = [concepto, *alias]
        score = max(
            similitud(descripcion_sin_periodo, quitar_periodo(quitar_detalle_numerico(c)))
            for c in candidatos
        )
        if score > mejor_score:
            mejor_score = score
            mejores = [concepto]
        elif score == mejor_score:
            mejores.append(concepto)

    if mejor_score < umbral:
        return ResultadoHomologacion(None, mejor_score)
    if len(mejores) != 1:
        # No elegir por el orden incidental del YAML: requiere revisión humana.
        return ResultadoHomologacion(None, mejor_score, tuple(sorted(mejores)))
    return ResultadoHomologacion(mejores[0], mejor_score)
