"""Lógica compartida de detección de datos reales de facturas en un comando de
shell o en un diff de git. Funciones puras (no leen stdin, no parsean el
protocolo de hooks de Claude Code) para que las use tanto
`.claude/hooks/guard_facturas.py` (solo corre dentro de Claude Code) como el
hook real de git instalado por `scripts/instalar-git-hooks.py` (corre siempre,
sin importar la herramienta usada para commitear).

Adaptado de `scripts/deteccion_datos_cliente.py` de Consultora (mismo problema:
dos barreras que no deben divergir con el tiempo).
"""

from __future__ import annotations

import re
import shlex
import subprocess

CUIT_CANDIDATO_RE = re.compile(r"\b(\d{2})-?(\d{8})-?(\d)\b")

COMANDOS_GIT_SOLO_LECTURA = frozenset({"status", "diff", "log", "show", "branch"})


def digito_verificador_cuit(diez_digitos: str) -> int:
    """Dígito verificador de CUIT/CUIL a partir de los primeros 10 dígitos,
    algoritmo módulo 11 oficial de ARCA."""
    pesos = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)
    resto = sum(int(d) * p for d, p in zip(diez_digitos, pesos)) % 11
    dv = 11 - resto
    if dv == 11:
        return 0
    if dv == 10:
        return 9
    return dv


def contiene_cuit_valido(texto: str) -> str | None:
    """Devuelve el primer CUIT que aparece en `texto` y pasa la validación de
    dígito verificador, o `None` si no hay ninguno."""
    for m in CUIT_CANDIDATO_RE.finditer(texto):
        primeros10 = m.group(1) + m.group(2)
        ultimo = int(m.group(3))
        if digito_verificador_cuit(primeros10) == ultimo:
            return m.group(0)
    return None


_PATRON_CARPETA_REALES = re.compile(r"\bdata/reales/(?!README\.md)")
_FLAGS_MENSAJE_DE_COMMIT = frozenset({"-m", "--message"})


def _tokens_sin_mensaje_de_commit(comando: str) -> list[str]:
    """Igual que en Consultora: excluye el valor de `-m`/`--message` para que
    un commit que solo documenta la protección no se bloquee a sí mismo."""
    try:
        tokens = shlex.split(comando)
    except ValueError:
        return [comando]

    resultado = []
    saltar_siguiente = False
    for token in tokens:
        if saltar_siguiente:
            saltar_siguiente = False
            continue
        if token in _FLAGS_MENSAJE_DE_COMMIT:
            saltar_siguiente = True
            continue
        if token.startswith("--message="):
            continue
        resultado.append(token)
    return resultado


def referencia_carpeta_reales(comando: str) -> bool:
    """True si el comando menciona `data/reales/` (fuera del mensaje de commit)."""
    tokens = _tokens_sin_mensaje_de_commit(comando)
    return any(_PATRON_CARPETA_REALES.search(token) for token in tokens)


def es_comando_git_de_solo_lectura(comando: str) -> bool:
    """True si TODAS las invocaciones de `git` presentes son de solo lectura."""
    subcomandos = re.findall(r"\bgit\s+(\S+)", comando)
    if not subcomandos:
        return True
    return all(s in COMANDOS_GIT_SOLO_LECTURA for s in subcomandos)


def archivos_de_reales_en_stage(cwd: str | None = None) -> list[str]:
    """Paths en el índice de git que caen dentro de `data/reales/`."""
    try:
        resultado = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=cwd,
        )
    except Exception:
        return []
    staged = resultado.stdout.splitlines()
    return [f for f in staged if f.startswith("data/reales/") and f != "data/reales/README.md"]


def cuit_en_diff_staged(cwd: str | None = None) -> str | None:
    """Escanea las líneas AGREGADAS del diff staged buscando un CUIT válido."""
    try:
        resultado = subprocess.run(
            ["git", "diff", "--cached"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=cwd,
        )
    except Exception:
        return None
    diff = resultado.stdout
    agregadas = "\n".join(
        linea
        for linea in diff.splitlines()
        if linea.startswith("+") and not linea.startswith("+++")
    )
    return contiene_cuit_valido(agregadas)
