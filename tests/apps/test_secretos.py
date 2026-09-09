"""Test de apps/segurplus/secretos.py -- docs/auditoria-2026-09.md, hallazgo
A-8: `leer_secret` tiene que distinguir "no hay secrets.toml" (devuelve
None) de cualquier otro error real (lo propaga)."""

import pytest
from streamlit.errors import StreamlitSecretNotFoundError

import apps.segurplus.secretos as secretos_mod
from apps.segurplus.secretos import leer_secret


class _SecretsQueNoExiste:
    def get(self, clave):
        raise StreamlitSecretNotFoundError("no secrets.toml")


class _SecretsRoto:
    def get(self, clave):
        raise ValueError("secrets.toml mal formado")


def test_sin_secrets_toml_devuelve_none(monkeypatch):
    monkeypatch.setattr(secretos_mod.st, "secrets", _SecretsQueNoExiste())
    assert leer_secret("APP_PASSWORD") is None


def test_error_real_al_leer_secrets_se_propaga(monkeypatch):
    monkeypatch.setattr(secretos_mod.st, "secrets", _SecretsRoto())
    with pytest.raises(ValueError, match="mal formado"):
        leer_secret("APP_PASSWORD")
