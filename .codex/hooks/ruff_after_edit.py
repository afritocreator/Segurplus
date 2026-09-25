#!/usr/bin/env python3
"""Ejecuta Ruff solamente sobre archivos Python recién editados por Codex."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
CABECERA_PATCH_RE = re.compile(
    r"^\*\*\* (?:Add|Update|Delete) File: (?P<ruta>.+)$", re.MULTILINE
)


def archivos_python(payload: dict[str, Any]) -> list[Path]:
    entrada = payload.get("tool_input") or payload.get("input") or {}
    candidatos: list[str] = []
    if isinstance(entrada, dict):
        for clave in ("path", "file", "file_path"):
            valor = entrada.get(clave)
            if isinstance(valor, str):
                candidatos.append(valor)
        parche = entrada.get("patch") or entrada.get("command")
        if isinstance(parche, str):
            candidatos.extend(m.group("ruta").strip() for m in CABECERA_PATCH_RE.finditer(parche))

    resultado: list[Path] = []
    for candidato in candidatos:
        ruta = Path(candidato)
        if not ruta.is_absolute():
            ruta = REPO_ROOT / ruta
        try:
            relativa = ruta.resolve().relative_to(REPO_ROOT.resolve())
        except (OSError, ValueError):
            continue
        if relativa.suffix == ".py" and ruta.exists():
            resultado.append(relativa)
    return sorted(set(resultado), key=str)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    archivos = archivos_python(payload)
    if not archivos:
        return 0

    proceso = subprocess.run(
        [sys.executable, "-m", "ruff", "check", *map(str, archivos)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=25,
    )
    if proceso.returncode == 0:
        return 0

    detalle = (proceso.stdout + proceso.stderr).strip()
    if "No module named ruff" in detalle:
        mensaje = "Ruff no está instalado en este entorno; ejecutá pip install -e '.[dev]'."
    else:
        mensaje = f"Ruff encontró problemas en los archivos editados:\n{detalle[:6000]}"
    print(json.dumps({"systemMessage": mensaje}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
