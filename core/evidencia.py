"""Almacenamiento privado de PDF original, separado de la base transaccional.

El default del piloto guarda el PDF en una carpeta local (`EVIDENCIA_DIR`) --
suficiente mientras la app corre en un único proceso, pero NO durable si el
servidor de Streamlit Community Cloud se reinicia (ver ADR-003). Un bucket S3
compatible (`S3_BUCKET`, extra opcional `s3` de pyproject.toml) es la opción
para cuando eso deje de ser aceptable.
"""

from __future__ import annotations

import os
from pathlib import Path


def persistencia_durable_configurada() -> bool:
    """True si la base transaccional (facturas, decisiones, casos) está en
    PostgreSQL en vez de en el DuckDB local -- lo único de lo que depende que
    un reinicio del servidor no pierda datos operativos. NO depende de
    `S3_BUCKET`: la evidencia del PDF es una preocupación aparte (ver
    `evidencia_durable_configurada`), no una condición para que la base de
    datos sea durable -- antes esta función exigía los dos, así que un
    `DATABASE_URL` bien configurado sin S3 seguía mostrando "modo local"."""
    return bool(os.environ.get("DATABASE_URL"))


def evidencia_durable_configurada() -> bool:
    """True si el PDF original se guarda en un bucket S3 compatible en vez de
    en una carpeta local del servidor (que se pierde en un reinicio de
    Streamlit Community Cloud)."""
    return bool(os.environ.get("S3_BUCKET"))


def guardar_pdf(hash_pdf: str, contenido: bytes) -> str | None:
    """Guarda un PDF por hash, idempotentemente, y devuelve su URI de evidencia.

    Nunca usa ACL pública. Las credenciales se resuelven por el proveedor de
    infraestructura, no desde el código ni desde el PDF.
    """
    bucket = os.environ.get("S3_BUCKET")
    if bucket:
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover - depende del deploy
            raise RuntimeError("S3_BUCKET requiere instalar boto3.") from exc
        cliente = boto3.client(
            "s3",
            endpoint_url=os.environ.get("S3_ENDPOINT_URL") or None,
            region_name=os.environ.get("S3_REGION") or None,
        )
        clave = f"segurplus/documentos/{hash_pdf}.pdf"
        cliente.put_object(
            Bucket=bucket,
            Key=clave,
            Body=contenido,
            ContentType="application/pdf",
            ServerSideEncryption="AES256",
        )
        return f"s3://{bucket}/{clave}"

    directorio = os.environ.get("EVIDENCIA_DIR")
    if not directorio:
        return None
    destino = Path(directorio) / f"{hash_pdf}.pdf"
    destino.parent.mkdir(parents=True, exist_ok=True)
    if not destino.exists():
        destino.write_bytes(contenido)
    return str(destino)


def leer_pdf(ruta_evidencia: str | None) -> bytes | None:
    """Lee el PDF original a partir de la URI que devolvió `guardar_pdf`
    (`s3://bucket/clave` o una ruta de archivo local) -- para mostrarlo en
    la pantalla de confirmación (`apps/segurplus/paginas/confirmar.py`,
    `st.pdf`). `None` si no hay evidencia (`ruta_evidencia` vacía, ej.
    `EVIDENCIA_DIR` sin configurar) o si la lectura falla por cualquier
    motivo -- NUNCA lanza: esa pantalla cae a mostrar el texto extraído
    (`facturas.texto_extraido`) en su lugar, nunca se cae por esto."""
    if not ruta_evidencia:
        return None
    if ruta_evidencia.startswith("s3://"):
        try:
            import boto3
        except ImportError:
            return None
        bucket, _, clave = ruta_evidencia.removeprefix("s3://").partition("/")
        try:
            cliente = boto3.client(
                "s3",
                endpoint_url=os.environ.get("S3_ENDPOINT_URL") or None,
                region_name=os.environ.get("S3_REGION") or None,
            )
            return cliente.get_object(Bucket=bucket, Key=clave)["Body"].read()
        except Exception:  # noqa: BLE001 -- nunca romper la pantalla por esto
            return None
    try:
        return Path(ruta_evidencia).read_bytes()
    except OSError:
        return None
