"""Almacenamiento privado de PDF original, separado de la base transaccional.

Producción usa un bucket S3 compatible configurado por secrets. El fallback
local existe solo para desarrollo y no se anuncia como persistencia durable.
"""

from __future__ import annotations

import os
from pathlib import Path


def persistencia_durable_configurada() -> bool:
    """True únicamente si base PostgreSQL y bucket privado están configurados."""
    return bool(os.environ.get("DATABASE_URL") and os.environ.get("S3_BUCKET"))


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
