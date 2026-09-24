"""Almacenamiento privado de PDF original, separado de la base transaccional.

Tres opciones, en este orden de prioridad: `S3_BUCKET` (bucket compatible,
durable, la opción recomendada para volumen); `EVIDENCIA_DIR` (carpeta
local -- NO durable si el servidor se reinicia, ver ADR-003); si ninguna
está configurada pero hay una conexión a la base (`con`), el PDF se guarda
ahí mismo, en la tabla `documentos_pdf` de `core/almacenamiento.py`
(codificado en base64, para que la misma columna VARCHAR sirva tanto en
DuckDB como en PostgreSQL sin un tipo binario específico de cada motor).

docs/auditoria-2026-09-web.md, E-4: en el deploy de Render no había ni
`EVIDENCIA_DIR` ni `S3_BUCKET` configurados, así que el PDF nunca se
guardaba -- la pantalla Revisar no podía mostrarlo. Guardarlo en la base
por default (elegido por el usuario en vez de configurar un bucket)
resuelve eso sin pedir ninguna variable de entorno nueva. Ver
`docs/decisiones/ADR-003-persistencia-durable.md` para el addendum."""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

LIMITE_BORRADORES_BUCKET_BYTES = 800 * 1024 * 1024


def _cliente_s3():
    try:
        import boto3
    except ImportError as exc:  # pragma: no cover - depende del deploy
        raise RuntimeError("S3_BUCKET requiere instalar boto3.") from exc
    return boto3.client(
        "s3",
        endpoint_url=os.environ.get("S3_ENDPOINT_URL") or None,
        region_name=os.environ.get("S3_REGION") or None,
    )


def uso_bucket_bytes(cliente: Any, bucket: str) -> int:
    """Cuenta todos los objetos del bucket; un error bloquea una nueva carga."""
    total = 0
    continuacion = None
    while True:
        parametros = {"Bucket": bucket}
        if continuacion:
            parametros["ContinuationToken"] = continuacion
        pagina = cliente.list_objects_v2(**parametros)
        total += sum(int(objeto["Size"]) for objeto in pagina.get("Contents", []))
        if not pagina.get("IsTruncated"):
            return total
        continuacion = pagina.get("NextContinuationToken")
        if not continuacion:
            raise RuntimeError("No se pudo medir por completo el uso del bucket.")


def uso_evidencia_bytes() -> int | None:
    bucket = os.environ.get("S3_BUCKET")
    return uso_bucket_bytes(_cliente_s3(), bucket) if bucket else None


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


def guardar_pdf(hash_pdf: str, contenido: bytes, *, con: Any = None) -> str | None:
    """Guarda un PDF por hash, idempotentemente, y devuelve su URI de evidencia.

    Nunca usa ACL pública. Las credenciales se resuelven por el proveedor de
    infraestructura, no desde el código ni desde el PDF.

    `con` (opcional): conexión ya abierta a la base (`core.almacenamiento.
    conectar()`) -- si no hay `S3_BUCKET` ni `EVIDENCIA_DIR` configurados y
    se pasa una conexión, el PDF se guarda en la tabla `documentos_pdf` en
    vez de perderse (docs/auditoria-2026-09-web.md, E-4)."""
    bucket = os.environ.get("S3_BUCKET")
    if bucket:
        cliente = _cliente_s3()
        if os.environ.get("SEGURPLUS_PRODUCTION") == "1" and (
            uso_bucket_bytes(cliente, bucket) + len(contenido) > LIMITE_BORRADORES_BUCKET_BYTES
        ):
            raise RuntimeError("El bucket se acerca al cupo gratuito: se bloqueó la carga.")
        clave = f"segurplus/documentos/{hash_pdf}.pdf"
        cliente.put_object(
            Bucket=bucket,
            Key=clave,
            Body=contenido,
            ContentType="application/pdf",
        )
        return f"s3://{bucket}/{clave}"

    if os.environ.get("SEGURPLUS_PRODUCTION") == "1":
        raise RuntimeError("Producción requiere un bucket privado para conservar el PDF.")

    directorio = os.environ.get("EVIDENCIA_DIR")
    if directorio:
        destino = Path(directorio) / f"{hash_pdf}.pdf"
        destino.parent.mkdir(parents=True, exist_ok=True)
        if not destino.exists():
            destino.write_bytes(contenido)
        return str(destino)

    if con is None:
        return None
    con.execute(
        "INSERT INTO documentos_pdf (hash_pdf, contenido_b64) VALUES (?, ?) "
        "ON CONFLICT (hash_pdf) DO NOTHING",
        [hash_pdf, base64.b64encode(contenido).decode("ascii")],
    )
    return f"db://{hash_pdf}"


def leer_pdf(ruta_evidencia: str | None, *, con: Any = None) -> bytes | None:
    """Lee el PDF original a partir de la URI que devolvió `guardar_pdf`
    (`s3://bucket/clave`, una ruta de archivo local, o `db://<hash_pdf>`)
    -- para mostrarlo en la pantalla de Revisar (`web/app.py`, antes
    `apps/segurplus/paginas/confirmar.py`). `None` si no hay evidencia
    (`ruta_evidencia` vacía) o si la lectura falla por cualquier motivo --
    NUNCA lanza: esa pantalla cae a mostrar el texto extraído
    (`facturas.texto_extraido`) en su lugar, nunca se cae por esto.

    `con`: obligatoria para leer una URI `db://...` -- si no se pasa, se
    trata como si no hubiera evidencia (`None`), en vez de lanzar."""
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
    if ruta_evidencia.startswith("db://"):
        if con is None:
            return None
        hash_pdf = ruta_evidencia.removeprefix("db://")
        try:
            fila = con.execute(
                "SELECT contenido_b64 FROM documentos_pdf WHERE hash_pdf = ?", [hash_pdf]
            ).fetchone()
            if fila is None or fila[0] is None:
                return None
            return base64.b64decode(fila[0])
        except Exception:  # noqa: BLE001 -- nunca romper la pantalla por esto
            return None
    try:
        return Path(ruta_evidencia).read_bytes()
    except OSError:
        return None


def borrar_pdf(ruta_evidencia: str | None, *, con: Any = None) -> None:
    """Borra el PDF original a partir de la URI que devolvió `guardar_pdf`
    -- usada por `core.almacenamiento.descartar_borrador` (docs/auditoria-
    2026-09-confirmacion.md, D-11): antes, descartar un borrador borraba
    las filas de la base pero dejaba el PDF huérfano en el disco del
    servidor o en el bucket, sin ninguna fila que lo referenciara. Nunca
    lanza -- si `ruta_evidencia` es `None` (no había evidencia guardada) o
    el borrado falla por cualquier motivo, no debe bloquear el descarte del
    borrador en sí."""
    if not ruta_evidencia:
        return
    if ruta_evidencia.startswith("s3://"):
        try:
            import boto3
        except ImportError:
            return
        bucket, _, clave = ruta_evidencia.removeprefix("s3://").partition("/")
        try:
            cliente = boto3.client(
                "s3",
                endpoint_url=os.environ.get("S3_ENDPOINT_URL") or None,
                region_name=os.environ.get("S3_REGION") or None,
            )
            cliente.delete_object(Bucket=bucket, Key=clave)
        except Exception:  # noqa: BLE001 -- nunca bloquear el descarte por esto
            return
        return
    if ruta_evidencia.startswith("db://"):
        if con is None:
            return
        hash_pdf = ruta_evidencia.removeprefix("db://")
        try:
            con.execute("DELETE FROM documentos_pdf WHERE hash_pdf = ?", [hash_pdf])
        except Exception:  # noqa: BLE001 -- nunca bloquear el descarte por esto
            return
        return
    try:
        Path(ruta_evidencia).unlink(missing_ok=True)
    except OSError:
        pass
