"""Copia evidencia desde PostgreSQL a un bucket privado, sin borrar originales.

Por defecto solo muestra el alcance. Con ``--aplicar`` verifica SHA-256 del
origen y del objeto remoto antes de cambiar la URI en una transacción. Las
filas de ``documentos_pdf`` se conservan hasta que exista un backup externo
verificado y una restauración de prueba: este script nunca las elimina.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import os

from core.almacenamiento import ConexionPostgres, _registrar_version, transaccion
from core.evidencia import guardar_pdf, leer_pdf


def migrar(*, aplicar: bool = False) -> tuple[int, int]:
    if not os.environ.get("DATABASE_URL") or not os.environ.get("S3_BUCKET"):
        raise RuntimeError("La migración exige DATABASE_URL y S3_BUCKET configurados.")
    # El modo informativo no debe ejecutar el DDL que `conectar()` aplica.
    con = ConexionPostgres(os.environ["DATABASE_URL"])
    try:
        cursor = con.execute(
            "SELECT f.hash_pdf, f.ruta_evidencia, d.contenido_b64 "
            "FROM facturas f JOIN documentos_pdf d ON d.hash_pdf = f.hash_pdf "
            "WHERE f.ruta_evidencia = 'db://' || f.hash_pdf ORDER BY f.hash_pdf"
        ).fetchall()
        total_bytes = 0
        cantidad = 0
        for hash_pdf, ruta_antigua, contenido_b64 in cursor:
            cantidad += 1
            contenido = base64.b64decode(contenido_b64, validate=True)
            if hashlib.sha256(contenido).hexdigest() != hash_pdf:
                raise RuntimeError(f"El PDF {hash_pdf} no coincide con su SHA-256; se detuvo.")
            total_bytes += len(contenido)
            if not aplicar:
                continue
            ruta_nueva = guardar_pdf(hash_pdf, contenido, con=con)
            recuperado = leer_pdf(ruta_nueva, con=con)
            if recuperado is None or hashlib.sha256(recuperado).hexdigest() != hash_pdf:
                raise RuntimeError(f"El objeto remoto {hash_pdf} no pasó la verificación.")
            with transaccion(con):
                resultado = con.execute(
                    "UPDATE facturas SET ruta_evidencia = ?, actualizado_en = now() "
                    "WHERE hash_pdf = ? AND ruta_evidencia = ?",
                    [ruta_nueva, hash_pdf, ruta_antigua],
                )
                if resultado.rowcount != 1:
                    raise RuntimeError(f"La factura {hash_pdf} cambió durante la migración.")
                _registrar_version(
                    con, hash_pdf, actor="migracion", motivo="PDF verificado en bucket privado"
                )
        return cantidad, total_bytes
    finally:
        con.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aplicar", action="store_true", help="Copia y verifica; no borra DB")
    argumentos = parser.parse_args()
    cantidad, total_bytes = migrar(aplicar=argumentos.aplicar)
    modo = "Migrados" if argumentos.aplicar else "Pendientes"
    print(f"{modo}: {cantidad} PDFs, {total_bytes / 1024**2:.1f} MB.")
    if argumentos.aplicar:
        print("Las copias originales en documentos_pdf siguen intactas.")


if __name__ == "__main__":
    main()
