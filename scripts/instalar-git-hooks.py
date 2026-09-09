#!/usr/bin/env python3
"""Instala el pre-commit real de git, que protege `data/reales/` sin importar
qué herramienta se use para commitear.

Correr UNA VEZ después de clonar el repo:

    python scripts/instalar-git-hooks.py
"""

from __future__ import annotations

import shutil
import stat
import subprocess
import sys
from pathlib import Path


def main() -> None:
    raiz = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=10
    )
    if raiz.returncode != 0:
        print(
            "No se pudo encontrar la raíz de un repo git desde acá.\n"
            "¿Corriste esto dentro del clon del repo?",
            file=sys.stderr,
        )
        sys.exit(1)

    repo_root = Path(raiz.stdout.strip())
    fuente = repo_root / "scripts" / "pre-commit-facturas.py"
    destino = repo_root / ".git" / "hooks" / "pre-commit"

    if not fuente.exists():
        print(f"No se encontró {fuente}. ¿Está corrupto el clon?", file=sys.stderr)
        sys.exit(1)

    if destino.exists():
        respuesta = input(f"Ya existe {destino}. ¿Sobreescribir? [s/N] ").strip().lower()
        if respuesta != "s":
            print("Cancelado, no se tocó nada.")
            return

    shutil.copy(fuente, destino)
    modo_actual = destino.stat().st_mode
    destino.chmod(modo_actual | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    print(f"OK: pre-commit instalado en {destino}")
    print(
        "A partir de ahora, un commit que toque data/reales/ o contenga un CUIT "
        "válido se bloquea SIEMPRE."
    )


if __name__ == "__main__":
    main()
