"""Formato de importes para lectores argentinos."""

from __future__ import annotations


def pesos_ars(valor: float, *, signo: bool = False) -> str:
    """`$1.234,56` (o `$+1.234,56`) sin depender del locale del sistema."""
    prefijo = "+" if signo and valor >= 0 else ""
    entero = f"{abs(valor):,.2f}"
    entero = entero.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"${prefijo}{'-' if valor < 0 else ''}{entero}"
