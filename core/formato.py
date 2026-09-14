"""Formato de importes para lectores argentinos."""

from __future__ import annotations


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
