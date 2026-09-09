"""Comparación de contraseña para el gate de acceso de la app pública.

Por qué existe (ver docs/decisiones/ADR-002-deploy.md, sección "app pública
con contraseña"): el plan gratuito de Streamlit Community Cloud permite una
sola app PRIVADA por workspace, y ese lugar ya lo ocupa `Consultora`. Para
no pagar un plan superior mientras Segurplus está en prueba, se despliega
como app pública pero con un login simple de contraseña compartida.

Esto NO es control de acceso real (no hay usuarios, no hay auditoría de
quién entró, la contraseña se comparte por fuera de la app) -- es una
barrera contra quien encuentra el link por casualidad, no contra un
atacante decidido. Documentado así a propósito para no venderlo como más
de lo que es.

`hmac.compare_digest` en vez de `==` para que el tiempo de la comparación
no filtre cuántos caracteres coinciden (timing attack) -- barato de hacer
bien, así que se hace bien aunque el resto del esquema sea deliberadamente
simple.
"""

from __future__ import annotations

import hmac


def verificar_contrasena(ingresada: str, correcta: str) -> bool:
    """True si `ingresada` coincide con `correcta`, en tiempo constante."""
    return hmac.compare_digest(ingresada.encode("utf-8"), correcta.encode("utf-8"))
