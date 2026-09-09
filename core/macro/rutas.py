"""Resuelve dónde vive el cache de cada serie macro, desde `__file__` y no
desde el directorio de trabajo (CWD).

Por qué existe: antes, cada módulo de `core/macro/` tenía su propio
`CACHE_PATH = Path("data/macro/x.parquet")`, relativo al CWD. Eso "andaba"
mientras todo se corriera desde la raíz del repo, y fallaba en silencio
apenas alguien lo corría desde otro directorio -- exactamente el bug que
ya se arregló en `scripts/verificar_parametros.py` (ver docs/backlog.md
B-37). El modo de falla es peor que un error: `CACHE_PATH.exists()` da
`False`, el módulo cae en `actualizar_cache()`, sale a la red y escribe
el parquet en un `data/macro/` que puede no ser el del repo.

Se centraliza acá (en vez de repetir `Path(__file__).resolve().parents[2]`
en los 7 módulos) porque es la misma cuenta para los siete, y un CWD
distinto del esperado es justo la clase de bug que aparece al desplegar
en la nube, donde nadie controla el directorio de trabajo (ver
docs/decisiones/ADR-004-app-en-la-nube.md)."""

from __future__ import annotations

from pathlib import Path

DIR_DATOS_MACRO = Path(__file__).resolve().parents[2] / "data" / "macro"


def ruta_cache(nombre_archivo: str) -> Path:
    """Ruta absoluta del parquet `nombre_archivo` dentro de `data/macro/`."""
    return DIR_DATOS_MACRO / nombre_archivo
