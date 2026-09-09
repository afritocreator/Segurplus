"""Fixtures compartidas para testear core/macro/ SIN depender de la red
(ver docs/backlog.md B-09). Los 4 módulos de core/macro/ hasta ahora solo
tenían smoke tests contra las APIs reales -- útiles para confirmar que
la fuente sigue respondiendo, pero cero cobertura de la lógica de
transformación/cacheo en sí (que es la parte que se puede romper con un
refactor sin que nadie note, ya que el smoke test sigue pasando mientras
la API real no cambie)."""

from __future__ import annotations

import json as json_module
import os
from typing import NoReturn

import pytest


def saltar_si_no_hay_red(exc: Exception, fuente: str) -> NoReturn:
    """Qué hacer cuando un smoke test de red no puede pegarle a la API
    real. En el CI normal (docs/backlog.md B-26) que una fuente pública
    esté caída no es un bug NUESTRO -- se skipea, para no romper el PR
    de un socio por algo que no controla. Pero el workflow semanal
    `salud-fuentes-macro.yml` (docs/backlog.md B-33) existe justamente
    para detectar fuentes caídas o que cambiaron de formato -- ahí un
    skip sería un falso verde, peor que no tener el workflow. La
    variable de entorno MACRO_RED_OBLIGATORIA=1 (que solo setea ese
    workflow) es la única diferencia entre los dos comportamientos."""
    if os.environ.get("MACRO_RED_OBLIGATORIA") == "1":
        raise AssertionError(f"FUENTE CAÍDA O CAMBIADA: {fuente} no respondió. {exc!r}") from exc
    pytest.skip(f"Sin conexión o {fuente} no respondió: {exc}")


class FakeResponse:
    """Sustituto mínimo de requests.Response para monkeypatchear
    requests.get en los tests de core/macro/."""

    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self) -> None:
        pass

    def json(self):
        return self._payload

    @property
    def text(self) -> str:
        return json_module.dumps(self._payload)
