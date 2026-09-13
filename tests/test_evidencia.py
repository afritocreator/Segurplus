"""Tests de core/evidencia.py -- sin pegarle a S3 real (boto3 ni siquiera
está instalado por defecto, ver pyproject.toml extra "s3")."""

from __future__ import annotations

from core.evidencia import (
    evidencia_durable_configurada,
    guardar_pdf,
    persistencia_durable_configurada,
)


def test_persistencia_durable_no_exige_s3(monkeypatch):
    """Antes de esta corrección, persistencia_durable_configurada() exigía
    DATABASE_URL Y S3_BUCKET -- un Postgres bien configurado sin S3 (la
    decisión tomada: S3 es opcional, ver ADR-003) seguía reportando "modo
    local". La durabilidad de los DATOS depende solo de DATABASE_URL."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host/db")
    monkeypatch.delenv("S3_BUCKET", raising=False)
    assert persistencia_durable_configurada() is True
    assert evidencia_durable_configurada() is False


def test_persistencia_durable_falsa_sin_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert persistencia_durable_configurada() is False


def test_evidencia_durable_verdadera_con_s3_bucket(monkeypatch):
    monkeypatch.setenv("S3_BUCKET", "mi-bucket")
    assert evidencia_durable_configurada() is True


def test_guardar_pdf_sin_bucket_ni_directorio_no_guarda_nada(monkeypatch):
    monkeypatch.delenv("S3_BUCKET", raising=False)
    monkeypatch.delenv("EVIDENCIA_DIR", raising=False)
    assert guardar_pdf("abc123", b"contenido") is None


def test_guardar_pdf_local_es_idempotente(tmp_path, monkeypatch):
    monkeypatch.delenv("S3_BUCKET", raising=False)
    monkeypatch.setenv("EVIDENCIA_DIR", str(tmp_path))

    ruta_1 = guardar_pdf("abc123", b"contenido original")
    assert ruta_1 == str(tmp_path / "abc123.pdf")
    assert (tmp_path / "abc123.pdf").read_bytes() == b"contenido original"

    # Volver a "guardar" el mismo hash con contenido distinto no lo pisa --
    # el hash ya identifica el PDF, así que no debería hacer falta rescribir.
    ruta_2 = guardar_pdf("abc123", b"contenido distinto")
    assert ruta_2 == ruta_1
    assert (tmp_path / "abc123.pdf").read_bytes() == b"contenido original"
