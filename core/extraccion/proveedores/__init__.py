"""Capa de proveedores de extracción intercambiable (Bloque 2 del plan de
rediseño de septiembre 2026 -- ver docs/banco_extraccion.md).

`core/extraccion/gemini.py` sigue siendo el único proveedor que
`core/pipeline.py::procesar_pdf` llama en producción -- no se recablea acá
a propósito. Sin una clave de API real de un segundo proveedor (Groq,
Cerebras, SambaNova) para MEDIR contra el banco, cambiar el camino real de
carga sería la misma adivinanza que este plan existe para evitar (ver
`docs/banco_extraccion.md`). Este paquete existe para que
`scripts/banco_extraccion.py` pueda correr varios proveedores sobre las
mismas facturas y publicar una tabla -- el día que esa tabla diga que otro
proveedor conviene primero, migrar `procesar_pdf` a `leer_factura_cascada`
es un cambio de una línea.

`data/extraccion.yaml` (nunca hardcodeado, ver CLAUDE.md) declara, en
orden, la cascada de proveedores a intentar: si el primero falla
(`ExtraccionError` -- cuota agotada, red caída, JSON inválido), se
reintenta con backoff corto y, agotados los reintentos, se pasa al
siguiente proveedor de la lista."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import yaml

from core.extraccion.esquema import FacturaExtraida
from core.extraccion.gemini import ExtraccionError, es_error_transitorio

RUTA_CONFIGURACION = Path(__file__).resolve().parents[3] / "data" / "extraccion.yaml"

_TIPOS_CONOCIDOS = ("gemini", "openai_compat")


@dataclass(frozen=True)
class ConfiguracionProveedor:
    """Un renglón de `data/extraccion.yaml`."""

    nombre: str
    tipo: str  # "gemini" | "openai_compat" -- ver _leer_con_proveedor
    modelo: str
    variable_entorno_clave: str | None = None
    base_url: str | None = None
    acepta_imagen: bool = False
    timeout_segundos: float = 30.0


def leer_configuracion(ruta: Path = RUTA_CONFIGURACION) -> list[ConfiguracionProveedor]:
    """Lee y valida `data/extraccion.yaml`. Sin cache y sin lectura a nivel
    de módulo, a propósito -- mismo criterio que `core/analisis/alertas.py`
    y `core/analisis/homologacion.py`: un YAML corrupto no debe tumbar el
    import."""
    datos = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    if not isinstance(datos, dict) or "proveedores" not in datos:
        raise ValueError(f"{ruta} no tiene la forma esperada (falta 'proveedores')")

    configuraciones = []
    for fila in datos["proveedores"]:
        tipo = fila.get("tipo")
        if tipo not in _TIPOS_CONOCIDOS:
            raise ValueError(
                f"{ruta}: tipo de proveedor desconocido {tipo!r} "
                f"(conocidos: {', '.join(_TIPOS_CONOCIDOS)})"
            )
        configuraciones.append(
            ConfiguracionProveedor(
                nombre=fila["nombre"],
                tipo=tipo,
                modelo=fila["modelo"],
                variable_entorno_clave=fila.get("variable_entorno_clave"),
                base_url=fila.get("base_url"),
                acepta_imagen=bool(fila.get("acepta_imagen", False)),
                timeout_segundos=float(
                    fila.get("timeout_segundos", datos.get("timeout_segundos", 30.0))
                ),
            )
        )
    if not configuraciones:
        raise ValueError(f"{ruta}: 'proveedores' está vacío")
    return configuraciones


def _leer_con_proveedor(
    config: ConfiguracionProveedor, pdf_bytes: bytes, texto_extraido: str | None
) -> FacturaExtraida:
    import os

    if config.tipo == "gemini":
        from core.extraccion.gemini import extraer_con_gemini

        api_key = os.environ.get(config.variable_entorno_clave or "") or None
        return extraer_con_gemini(pdf_bytes, api_key=api_key, texto_extraido=texto_extraido)

    if config.tipo == "openai_compat":
        from core.extraccion.proveedores.openai_compat import extraer_con_openai_compat

        if not config.base_url:
            raise ExtraccionError(
                f"Proveedor {config.nombre!r}: falta 'base_url' en la configuración."
            )
        return extraer_con_openai_compat(
            pdf_bytes,
            base_url=config.base_url,
            modelo=config.modelo,
            variable_entorno_clave=config.variable_entorno_clave,
            texto_extraido=texto_extraido,
            acepta_imagen=config.acepta_imagen,
            timeout_segundos=config.timeout_segundos,
        )

    raise ExtraccionError(f"Tipo de proveedor desconocido: {config.tipo!r}")  # pragma: no cover


def leer_factura_cascada(
    pdf_bytes: bytes,
    *,
    texto_extraido: str | None = None,
    configuraciones: list[ConfiguracionProveedor] | None = None,
    intentos_por_proveedor: int = 2,
    espera_entre_intentos: float = 1.0,
) -> FacturaExtraida:
    """Intenta cada proveedor de `configuraciones` (default: `data/extraccion.yaml`)
    en orden, con `intentos_por_proveedor` reintentos y backoff exponencial
    corto por proveedor antes de pasar al siguiente. Si todos fallan, lanza
    `ExtraccionError` con el detalle de cada fallo -- el llamador (hoy,
    `scripts/banco_extraccion.py`; el día de mañana, `core/pipeline.py`)
    sigue tratándolo igual que un fallo de Gemini solo: la factura queda
    como borrador vacío para completar a mano, nunca se pierde.

    Un error PERMANENTE (falta la clave de API, JSON mal formado) no se
    reintenta dentro del mismo proveedor -- pasa directo al siguiente de
    la lista, sin la espera de `espera_entre_intentos`
    (docs/auditoria-2026-09-web.md, E-21): reintentar "falta la clave" solo
    iba a fallar exactamente igual las veces que quedaran, gastando tiempo
    sin ganar nada. Solo un error TRANSITORIO (`es_error_transitorio`, ver
    `core.extraccion.gemini`) amerita reintentar el mismo proveedor."""
    configuraciones = configuraciones if configuraciones is not None else leer_configuracion()
    errores: list[str] = []
    for config in configuraciones:
        for intento in range(intentos_por_proveedor):
            try:
                return _leer_con_proveedor(config, pdf_bytes, texto_extraido)
            except ExtraccionError as exc:
                errores.append(f"{config.nombre} (intento {intento + 1}): {exc}")
                if intento < intentos_por_proveedor - 1 and es_error_transitorio(exc):
                    time.sleep(espera_entre_intentos * (2**intento))
                else:
                    break
    raise ExtraccionError("Todos los proveedores de extracción fallaron:\n" + "\n".join(errores))
