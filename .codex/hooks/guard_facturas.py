#!/usr/bin/env python3
"""Bloquea herramientas de Codex que intenten tocar datos reales o incluir un CUIT."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from deteccion_datos_reales import contiene_cuit_valido  # noqa: E402

RUTA_REALES_RE = re.compile(
    r"(?i)(?:^|[\\/\s\"'=])data[\\/]reales[\\/](?!README\.md(?:$|[\s\"']))"
)
CABECERA_PATCH_RE = re.compile(
    r"^\*\*\* (?:Add|Update|Delete) File: (?P<ruta>.+)$", re.MULTILINE
)


def _texto_estructurado(valor: Any) -> str:
    return json.dumps(valor, ensure_ascii=False, sort_keys=True)


def _rutas_objetivo(payload: dict[str, Any]) -> list[str]:
    entrada = payload.get("tool_input") or payload.get("input") or {}
    rutas: list[str] = []
    if isinstance(entrada, dict):
        for clave in ("path", "file", "file_path", "workdir", "cwd"):
            valor = entrada.get(clave)
            if isinstance(valor, str):
                rutas.append(valor)
        parche = entrada.get("patch") or entrada.get("command")
        if isinstance(parche, str):
            rutas.extend(m.group("ruta").strip() for m in CABECERA_PATCH_RE.finditer(parche))
    return rutas


def motivo_bloqueo(payload: dict[str, Any]) -> str | None:
    entrada = payload.get("tool_input") or payload.get("input") or {}
    nombre = str(payload.get("tool_name") or "")

    for ruta in _rutas_objetivo(payload):
        normalizada = "/" + ruta.replace("\\", "/").lstrip("/")
        if RUTA_REALES_RE.search(normalizada):
            return "la herramienta apunta a data/reales/, zona excluida del trabajo de agentes"

    if nombre in {"Bash", "exec_command"} and isinstance(entrada, dict):
        comando = entrada.get("command") or entrada.get("cmd") or ""
        if isinstance(comando, str) and RUTA_REALES_RE.search("/" + comando.replace("\\", "/")):
            return "el comando intenta acceder a data/reales/, zona excluida"

    cuit = contiene_cuit_valido(_texto_estructurado(entrada))
    if cuit:
        return f"la entrada contiene lo que parece un CUIT válido ({cuit})"
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:
        print(f"BLOQUEADO por guard_facturas: entrada inválida ({exc}).", file=sys.stderr)
        return 2

    motivo = motivo_bloqueo(payload)
    if motivo:
        print(f"BLOQUEADO por guard_facturas: {motivo}.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
