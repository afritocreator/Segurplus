"""Formato de importes, fechas y nombres de servicio para lectores
argentinos -- nada de esto es un cálculo, solo cómo se muestra un número o
un slug ya calculado/validado en otro lado."""

from __future__ import annotations

from datetime import date

_MESES = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)

# docs/auditoria-2026-09-web.md, E-13: los slugs de `SERVICIOS_CONOCIDOS`
# (core/extraccion/esquema.py) son para guardar y comparar, no para
# mostrar -- "energia" sin tilde, "telefonia" sin tilde, quedaban tal
# cual en el título y las métricas de la pantalla "Ver".
_NOMBRES_SERVICIO = {
    "telefonia": "Telefonía",
    "energia": "Luz",
    "gas": "Gas",
    "agua": "Agua",
    "seguro": "Seguro",
    "alquiler": "Alquiler",
    "otro": "Otro",
}


def nombre_servicio(servicio: str) -> str:
    """`"energia"` -> `"Luz"`. Un slug sin traducción conocida se muestra
    con la primera letra en mayúscula en vez de fallar -- un servicio
    nuevo sin entrada acá sigue siendo mejor que una pantalla rota."""
    return _NOMBRES_SERVICIO.get(servicio, servicio.capitalize())


def mes_anio(periodo_iso: str) -> str:
    """`"2026-08-01"` -> `"agosto de 2026"`. Si el formato no se puede
    interpretar (no debería pasar -- `periodo_desde` ya se normalizó a ISO
    en `core/extraccion/esquema.py` antes de llegar acá), devuelve el
    valor tal cual en vez de lanzar: una fecha rara en pantalla sigue
    siendo mejor que una pantalla rota."""
    try:
        fecha = date.fromisoformat(periodo_iso)
    except ValueError:
        return periodo_iso
    return f"{_MESES[fecha.month - 1]} de {fecha.year}"


def porcentaje_ar(valor: float, *, con_signo: bool = True) -> str:
    """`con_signo=True` (default): `+15,0%` / `-15,0%`, para cuando el
    signo es la única forma de saber la dirección. `con_signo=False`:
    `15,0%` sin signo, para cuando la dirección YA la dice una palabra
    ("subió", "bajó") -- forzar el signo en ese caso terminaba en "bajó un
    +23%" (docs/auditoria-2026-09-web.md, E-2), un signo de más delante de
    una baja. Formato argentino (coma decimal), no el `8.0%` en inglés que
    devuelve `f"{x:.1%}"` -- ver E-13 de la misma auditoría."""
    if con_signo:
        texto = f"{valor * 100:+.1f}%"
    else:
        texto = f"{abs(valor) * 100:.1f}%"
    texto = texto.replace(".", ",").replace(",0%", "%")
    # Un valor que redondea a cero no tiene dirección -- "+0%" sugeriría
    # una suba mínima en vez de "sin cambio".
    return "0%" if texto == "+0%" else texto


def pesos_ars(valor: float, *, signo: bool = False) -> str:
    """`$1.234,56` (o `$+1.234,56`) sin depender del locale del sistema.

    Redondea PRIMERO y decide el signo sobre el valor YA redondeado
    (docs/auditoria-2026-09-piloto.md, A-59): antes, un valor negativo que
    redondea a cero (ej. `-0.004`) mostraba `"$-0,00"` -- un signo menos
    delante de un cero, que no significa nada. `round(-0.004, 2)` da
    `-0.0`, y en Python `-0.0 < 0` es `False`, así que decidir el signo
    después de redondear alcanza para que ese caso no arrastre el signo
    del valor sin redondear."""
    redondeado = round(valor, 2)
    prefijo = "+" if signo and redondeado >= 0 else ""
    entero = f"{abs(redondeado):,.2f}"
    entero = entero.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"${prefijo}{'-' if redondeado < 0 else ''}{entero}"
