#!/usr/bin/env python3
"""PreToolUse hook de Claude Code: bloquea git add/commit (y cualquier otra
operación de git que no sea de solo lectura) que toque `data/reales/` o
contenga un CUIT válido. Ver CLAUDE.md, zona restringida.

LÍMITE IMPORTANTE: esta barrera SOLO corre dentro de Claude Code. Para
protección fuera de Claude Code existe el hook real de git instalado por
`scripts/instalar-git-hooks.py`, que usa la misma lógica de detección.

Convención de hooks de Claude Code: exit 0 = permitir. Exit 2 = bloquear y el
mensaje de stderr se le muestra al agente como motivo.

Este hook FALLA ABIERTO: si no se puede leer o parsear el payload de entrada,
permite en vez de bloquear.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from deteccion_datos_reales import (  # noqa: E402
    cuit_en_diff_staged,
    es_comando_git_de_solo_lectura,
    referencia_carpeta_reales,
)


def bloquear(motivo: str) -> None:
    print(f"BLOQUEADO por guard_facturas: {motivo}", file=sys.stderr)
    sys.exit(2)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)  # falla abierto -- ver docstring del módulo

    if payload.get("tool_name") != "Bash":
        sys.exit(0)

    command = (payload.get("tool_input") or {}).get("command", "")
    if "git" not in command:
        sys.exit(0)

    if es_comando_git_de_solo_lectura(command):
        sys.exit(0)

    if referencia_carpeta_reales(command):
        bloquear(
            "el comando referencia archivos dentro de data/reales/, que está excluido a propósito."
        )

    cuit = cuit_en_diff_staged()
    if cuit:
        bloquear(f"el diff staged contiene lo que parece un CUIT válido ({cuit}).")

    sys.exit(0)


if __name__ == "__main__":
    main()
