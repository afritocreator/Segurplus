"""Comprueba un respaldo cifrado antes de restaurarlo en una base de prueba.

No conecta a producción ni ejecuta ``pg_restore``. Descifra en memoria,
valida el manifiesto y, si se indica ``--extraer``, deja una copia temporal
para que el operador la restaure manualmente en un entorno aislado.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import shutil
import tempfile
import zipfile
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as archivo:
        for bloque in iter(lambda: archivo.read(1024 * 1024), b""):
            digest.update(bloque)
    return digest.hexdigest()


def _descifrar(archivo: Path, contrasena: str) -> bytes:
    contenido = archivo.read_bytes()
    if len(contenido) < 4 + 16 + 12 or contenido[:4] != b"SGP1":
        raise ValueError("No es un respaldo Segurplus SGP1 válido.")
    sal, nonce, cifrado = contenido[4:20], contenido[20:32], contenido[32:]
    llave = hashlib.scrypt(contrasena.encode(), salt=sal, n=2**15, r=8, p=1, dklen=32)
    try:
        return AESGCM(llave).decrypt(nonce, cifrado, b"segurplus-backup-v1")
    except InvalidTag as exc:
        raise ValueError("Contraseña incorrecta o respaldo alterado.") from exc


def verificar(archivo: Path, *, extraer: Path | None = None) -> dict[str, object]:
    """Valida hashes y devuelve el manifiesto; nunca restaura una base."""
    contrasena = getpass.getpass("Contraseña del respaldo: ")
    zip_descifrado = _descifrar(archivo, contrasena)
    with tempfile.TemporaryDirectory(prefix="segurplus-verificar-") as temporal:
        raiz = Path(temporal)
        zip_path = raiz / "respaldo.zip"
        zip_path.write_bytes(zip_descifrado)
        with zipfile.ZipFile(zip_path) as contenido:
            nombres = contenido.namelist()
            if "manifest.json" not in nombres or "segurplus.dump" not in nombres:
                raise ValueError("El respaldo no contiene manifiesto y base esperados.")
            contenido.extractall(raiz)
        manifiesto = json.loads((raiz / "manifest.json").read_text(encoding="utf-8"))
        base = manifiesto["base"]
        if _sha256(raiz / str(base["archivo"])) != base["sha256"]:
            raise ValueError("El hash del volcado de base no coincide.")
        for pdf in manifiesto.get("pdfs", []):
            ruta = raiz / "pdf" / str(pdf["key"]).removeprefix("segurplus/documentos/")
            if not ruta.is_file() or _sha256(ruta) != pdf["sha256"]:
                raise ValueError(f"El PDF no coincide con el manifiesto: {pdf['key']}")
        if extraer is not None:
            destino = extraer.resolve()
            if destino.exists() and any(destino.iterdir()):
                raise ValueError("La carpeta de extracción debe estar vacía.")
            destino.mkdir(parents=True, exist_ok=True)
            for origen in raiz.iterdir():
                if origen != zip_path:
                    objetivo = destino / origen.name
                    if origen.is_dir():
                        shutil.copytree(origen, objetivo)
                    else:
                        shutil.copy2(origen, objetivo)
    return manifiesto


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archivo", type=Path, help="Archivo .zip.aes a comprobar")
    parser.add_argument(
        "--extraer", type=Path, help="Carpeta vacía aislada para inspección y pg_restore manual"
    )
    argumentos = parser.parse_args()
    resultado = verificar(argumentos.archivo, extraer=argumentos.extraer)
    print(f"Respaldo íntegro: {len(resultado.get('pdfs', []))} PDFs verificados.")
