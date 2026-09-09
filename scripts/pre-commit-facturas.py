#!/usr/bin/env python
"""pre-commit real de git: corre SIEMPRE, sin importar qué herramienta se use
para commitear -- a diferencia de `.claude/hooks/guard_facturas.py`, que solo
protege dentro de Claude Code.

Cada socio corre `python scripts/instalar-git-hooks.py` una vez después de
clonar, y ese script copia este archivo a `.git/hooks/pre-commit`.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_raiz = subprocess.run(
    ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=10
).stdout.strip()
sys.path.insert(0, str(Path(_raiz) / "scripts"))
from deteccion_datos_reales import archivos_de_reales_en_stage, cuit_en_diff_staged  # noqa: E402


def main() -> int:
    reales = archivos_de_reales_en_stage()
    if reales:
        print(
            f"COMMIT BLOQUEADO (pre-commit): hay archivos de data/reales/ en el stage:\n"
            f"{', '.join(reales)}",
            file=sys.stderr,
        )
        print("data/reales/ está excluido a propósito -- ver CLAUDE.md.", file=sys.stderr)
        return 1

    cuit = cuit_en_diff_staged()
    if cuit:
        print(
            f"COMMIT BLOQUEADO (pre-commit): el diff staged contiene lo que "
            f"parece un CUIT válido ({cuit}).",
            file=sys.stderr,
        )
        print(
            "Si es un dato sintético, usá un número que no pase la validación "
            "de dígito verificador.",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
