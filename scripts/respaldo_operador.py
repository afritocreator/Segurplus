"""Genera un respaldo cifrado de PostgreSQL y los PDFs privados de Segurplus.

Se ejecuta en la PC controlada del operador, no en Render ni en una PC de
carga. Requiere ``pg_dump`` instalado y las variables de entorno de Render:
DATABASE_URL, S3_BUCKET, S3_ENDPOINT_URL, S3_REGION, AWS_ACCESS_KEY_ID y
AWS_SECRET_ACCESS_KEY. Nunca imprime secretos ni la contraseña de cifrado.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from core.evidencia import _cliente_s3


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as archivo:
        for bloque in iter(lambda: archivo.read(1024 * 1024), b""):
            digest.update(bloque)
    return digest.hexdigest()


def _clave() -> bytes:
    primera = getpass.getpass("Contraseña nueva para cifrar el respaldo: ")
    segunda = getpass.getpass("Repetí la contraseña: ")
    if len(primera) < 16:
        raise SystemExit("La contraseña debe tener al menos 16 caracteres.")
    if primera != segunda:
        raise SystemExit("Las contraseñas no coinciden.")
    return primera.encode()


def _volcar_base(destino: Path) -> None:
    if not os.environ.get("DATABASE_URL"):
        raise SystemExit("Falta DATABASE_URL.")
    ejecutable = shutil.which("pg_dump")
    if not ejecutable:
        raise SystemExit("No se encontró pg_dump. Instalá PostgreSQL client tools en esta PC.")
    subprocess.run(
        [
            ejecutable,
            "--dbname", os.environ["DATABASE_URL"],
            "--schema=segurplus",
            "--format=custom",
            "--file", str(destino),
        ],
        check=True,
        env=os.environ.copy(),
    )


def _descargar_pdfs(destino: Path) -> list[dict[str, object]]:
    bucket = os.environ.get("S3_BUCKET")
    if not bucket:
        raise SystemExit("Falta S3_BUCKET.")
    cliente = _cliente_s3()
    manifiesto: list[dict[str, object]] = []
    token = None
    while True:
        parametros = {"Bucket": bucket, "Prefix": "segurplus/documentos/"}
        if token:
            parametros["ContinuationToken"] = token
        pagina = cliente.list_objects_v2(**parametros)
        for objeto in pagina.get("Contents", []):
            clave = objeto["Key"]
            relativo = Path("pdf") / clave.removeprefix("segurplus/documentos/")
            archivo = destino / relativo
            archivo.parent.mkdir(parents=True, exist_ok=True)
            with archivo.open("wb") as salida:
                salida.write(cliente.get_object(Bucket=bucket, Key=clave)["Body"].read())
            manifiesto.append(
                {"key": clave, "bytes": archivo.stat().st_size, "sha256": _sha256(archivo)}
            )
        if not pagina.get("IsTruncated"):
            return manifiesto
        token = pagina.get("NextContinuationToken")
        if not token:
            raise RuntimeError("El listado S3 quedó incompleto.")


def crear_respaldo(destino: Path) -> Path:
    destino = destino.resolve()
    destino.mkdir(parents=True, exist_ok=True)
    clave = _clave()
    marca = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    salida = destino / f"segurplus-respaldo-{marca}.zip.aes"
    with tempfile.TemporaryDirectory(prefix="segurplus-respaldo-") as temporal:
        raiz = Path(temporal)
        dump = raiz / "segurplus.dump"
        _volcar_base(dump)
        pdfs = _descargar_pdfs(raiz)
        manifiesto = {
            "creado_en_utc": marca,
            "formato": "AES-256-GCM + ZIP + pg_dump custom",
            "base": {"archivo": dump.name, "bytes": dump.stat().st_size, "sha256": _sha256(dump)},
            "pdfs": pdfs,
        }
        (raiz / "manifest.json").write_text(json.dumps(manifiesto, indent=2), encoding="utf-8")
        zip_temporal = raiz / "respaldo.zip"
        with zipfile.ZipFile(zip_temporal, "w", compression=zipfile.ZIP_DEFLATED) as archivo_zip:
            for archivo in raiz.rglob("*"):
                if archivo.is_file() and archivo != zip_temporal:
                    archivo_zip.write(archivo, archivo.relative_to(raiz))
        sal = os.urandom(16)
        nonce = os.urandom(12)
        llave = hashlib.scrypt(clave, salt=sal, n=2**15, r=8, p=1, dklen=32)
        cifrado = AESGCM(llave).encrypt(nonce, zip_temporal.read_bytes(), b"segurplus-backup-v1")
        salida.write_bytes(b"SGP1" + sal + nonce + cifrado)
    print(f"Respaldo creado: {salida}")
    print(f"PDFs incluidos: {len(pdfs)}. Guardalo fuera de esta PC y registrá la fecha.")
    return salida


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destino", type=Path, help="Carpeta externa elegida por el operador")
    crear_respaldo(parser.parse_args().destino)
