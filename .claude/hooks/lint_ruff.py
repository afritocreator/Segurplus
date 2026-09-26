#!/usr/bin/env python3
"""PostToolUse hook de Claude Code: corre `ruff check` sobre el archivo que
se acaba de editar/crear, para detectar warnings en el momento en vez de
recién al final (ver CLAUDE.md, Definition of Done: "ruff check sin warnings").

Convención de hooks de Claude Code: exit 0 = permitir seguir, no bloquea
nada (PostToolUse no puede impedir la edición, ya pasó). stderr con salida
de ruff, si hay, se le muestra al agente como feedback.

Este hook FALLA ABIERTO: si no se puede leer/parsear el payload, o el
archivo no existe (por ejemplo si se borró después), no hace nada.
"""

import json
import subprocess
import sys
from pathlib import Path


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    tool_input = payload.get("tool_input") or {}
    ruta = tool_input.get("file_path")
    if not ruta or not ruta.endswith(".py"):
        sys.exit(0)

    if not Path(ruta).exists():
        sys.exit(0)

    try:
        resultado = subprocess.run(
            ["ruff", "check", ruta],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:
        sys.exit(0)  # ruff no instalado o no encontrado -- no bloquea

    if resultado.returncode != 0:
        print(resultado.stdout or resultado.stderr, file=sys.stderr)

    sys.exit(0)


if __name__ == "__main__":
    main()
