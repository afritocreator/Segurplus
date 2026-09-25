---
name: release-verifier
description: Verificar de forma independiente un corte o despliegue de Segurplus en GitHub, Render y Supabase, usando solo datos sintéticos y distinguiendo código, prueba local y producción.
---

# Verificación de release de Segurplus

Usá esta skill después de que exista un candidato concreto a despliegue. La verificación
es de solo lectura salvo que el usuario autorice expresamente el despliegue o la migración.

1. Identificá commit y rama exactos; comparalos con `render.yaml` y el servicio Render.
2. Confirmá `ruff check .`, `pytest -q` y el resultado de GitHub Actions.
3. Revisá migraciones pendientes con las herramientas de Supabase sin mostrar secretos ni
   contenido de facturas.
4. Comprobá salud, logs y versión desplegada en Render.
5. Ejecutá únicamente un recorrido sintético: login, carga, revisión, aprobación, análisis
   y exportación. No uses `data/reales/`.
6. Compará la versión de cálculo de pantalla y Excel.

Informá por separado `implementado`, `probado localmente`, `verificado en CI` y
`verificado en producción`. Si no podés demostrar una capa, marcala como pendiente; no la
infieras.
