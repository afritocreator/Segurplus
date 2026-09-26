#!/usr/bin/env python3
"""PreToolUse hook de Claude Code: bloquea `git commit` si el diff staged
toca `core/analisis/` o `core/extraccion/validacion.py` y los tests de esas
carpetas no pasan. Hace estructural la regla de oro del CLAUDE.md: "nunca
reportar un cálculo como validado sin haber corrido pytest".

Convención de hooks de Claude Code: exit 0 = permitir. Exit 2 = bloquear y el
mensaje de stderr se le muestra al agente como motivo.

Este hook FALLA ABIERTO: si no se puede leer/parsear el payload, si no hay
git, o si el propio pytest no se puede ejecutar, permite en vez de bloquear
-- la barrera real es la revisión humana y el subagente revisor-financiero,
no este hook.
"""

import json
import re
import subprocess
import sys

RUTAS_CRITICAS = ("core/analisis/", "core/extraccion/validacion.py")
TESTS_CRITICOS = ("tests/analisis", "tests/extraccion/test_validacion.py")


def es_git_commit(comando: str) -> bool:
    return bool(re.search(r"\bgit\s+commit\b", comando))


def archivos_criticos_en_stage() -> list[str]:
    try:
        resultado = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return []
    staged = resultado.stdout.splitlines()
    return [f for f in staged if f.startswith(RUTAS_CRITICAS)]


def bloquear(motivo: str) -> None:
    print(f"BLOQUEADO por verificar_pytest_analisis: {motivo}", file=sys.stderr)
    sys.exit(2)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    if payload.get("tool_name") != "Bash":
        sys.exit(0)

    command = (payload.get("tool_input") or {}).get("command", "")
    if "git" not in command or not es_git_commit(command):
        sys.exit(0)

    tocados = archivos_criticos_en_stage()
    if not tocados:
        sys.exit(0)

    try:
        resultado = subprocess.run(
            ["python", "-m", "pytest", "-q", *TESTS_CRITICOS],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except Exception:
        sys.exit(0)  # no se pudo correr pytest -- falla abierto

    if resultado.returncode != 0:
        bloquear(
            "el commit toca "
            + ", ".join(tocados)
            + " y pytest de tests/analisis/tests/extraccion/test_validacion.py "
            "no pasa. Corré pytest y arreglá antes de commitear:\n"
            + (resultado.stdout or resultado.stderr)[-2000:]
        )

    sys.exit(0)


if __name__ == "__main__":
    main()
