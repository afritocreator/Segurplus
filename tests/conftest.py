"""Protección de toda la suite contra escribir en la base de producción.

docs/auditoria-2026-09-piloto.md, hallazgo A-49: `core/almacenamiento.py::conectar()`
mira `DATABASE_URL` cuando no se le pasa una `ruta` explícita -- y varios tests (los
de páginas con `AppTest`, en `tests/apps/`) monkeypatchean `RUTA_BASE` y llaman
`conectar()` SIN argumentos, así que la única forma de protegerlos es que
`DATABASE_URL` no exista en el entorno mientras corre la suite, sin importar qué
tenga configurado quien la corre localmente.

Esto es la capa que realmente importa (la otra es que `conectar(ruta)` con una ruta
explícita ya ignora `DATABASE_URL` por sí sola, ver esa función) -- entre las dos,
ningún test puede terminar escribiendo en Postgres real por accidente.
"""

from __future__ import annotations

import os

import pytest

_VARIABLES_DE_PERSISTENCIA_REAL = ("DATABASE_URL", "S3_BUCKET", "EVIDENCIA_DIR")


@pytest.fixture(autouse=True, scope="session")
def _sin_persistencia_real_durante_los_tests():
    """Saca las variables que apuntan a infraestructura real del entorno
    durante TODA la corrida de tests, y las restaura al final -- un test
    individual que necesite volver a ponerlas (ver tests/test_conexion_
    postgres_real.py, marcado `red_real`) usa `monkeypatch.setenv` dentro
    de sí mismo, que actúa después de esto y se deshace solo."""
    valores_originales = {var: os.environ.pop(var, None) for var in _VARIABLES_DE_PERSISTENCIA_REAL}
    try:
        yield
    finally:
        for var, valor in valores_originales.items():
            if valor is not None:
                os.environ[var] = valor
