"""Test de core/operacion.py contra el data/operacion.yaml real del repo."""

from zoneinfo import ZoneInfo

from core.operacion import (
    max_llamadas_gemini_por_hora,
    revision_humana_obligatoria,
    zona_horaria,
)


def test_revision_humana_obligatoria_lee_el_yaml_real():
    # data/operacion.yaml del repo trae el default del piloto: false.
    assert revision_humana_obligatoria() is False


def test_max_llamadas_gemini_por_hora_lee_el_yaml_real():
    # docs/auditoria-2026-09-facturas-reales.md, B-4: antes hardcodeada en
    # core/extraccion/gemini.py.
    assert max_llamadas_gemini_por_hora() == 30


def test_zona_horaria_lee_el_yaml_real():
    # docs/auditoria-2026-09-facturas-reales.md, hallazgo C-8: el equipo
    # está en Tandil, Buenos Aires.
    assert zona_horaria() == "America/Argentina/Buenos_Aires"
    ZoneInfo(zona_horaria())  # no lanza -- es un nombre de zona válido
