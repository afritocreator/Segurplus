# Runbook — corte seguro al piloto durable

Este procedimiento no se ejecuta contra facturas reales hasta que el cambio
esté integrado, probado y exista una ventana de mantenimiento. Las claves se
guardan solo en los paneles de sus proveedores; nunca en Git, logs o tickets.

## Precondiciones

- La rama desplegable pasó `pytest -q` y `ruff check .`.
- El servicio Render tiene una URL HTTPS estable. La URI OAuth será
  `https://<host>/auth/google` y debe ser idéntica en Google Cloud y Render.
- Existe `segurplus-documentos`, bucket privado de Supabase Storage. Se creó
  una clave S3 de servidor, y se anotaron endpoint, región y nombre del bucket
  en el gestor de secretos de Render.
- Existe una exportación externa cifrada reciente de base y PDFs, con fecha,
  conteo de facturas y lista de SHA-256. El destino externo queda bajo control
  del operador; no se sube al repositorio.

## Secuencia de corte

1. Pausar cargas nuevas y registrar hora, commit y conteos actuales.
2. Ejecutar `python scripts/migrar_pdfs_a_bucket.py` **sin** `--aplicar`.
   Confirmar cantidad y MB esperados.
3. Configurar en Render, sin eliminar todavía variables existentes:
   `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`,
   `GOOGLE_ALLOWED_EMAILS`, `S3_BUCKET`, `S3_ENDPOINT_URL`, `S3_REGION`,
   `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `DATABASE_URL`,
   `SECRET_KEY` y `SEGURPLUS_PRODUCTION=1`.
4. Desplegar el commit aprobado. Verificar login Google con un correo
   autorizado, rechazo de uno no autorizado, carga manual sintética y lectura
   del PDF recién guardado. No enviar un PDF real a Gemini durante esta prueba.
5. Ejecutar `python scripts/migrar_pdfs_a_bucket.py --aplicar` una vez que el
   bucket y el acceso estén comprobados. Cada objeto se valida con SHA-256; el
   script conserva la copia de base y se interrumpe ante la primera diferencia.
6. Verificar en el tablero que borradores, rechazadas y cuarentena no impactan
   Evolución, Excel ni casos consolidados. Registrar hashes y conteos finales.
7. Recién entonces rotar, en una ventana coordinada, contraseña de PostgreSQL
   y clave Gemini: actualizar el secreto en Render, desplegar, probar conexión
   y extracción sintética, y revocar la credencial anterior. El último paso de
   cambio de contraseña lo realiza el operador en el panel del proveedor.

## Respaldo y restauración mensual

Cada semana el operador exporta la base y todos los objetos de
`segurplus-documentos` a un destino externo cifrado. La evidencia mínima es:
fecha, tamaño, hashes, cantidad de facturas, decisiones y casos.

Una vez por mes se restaura una copia en un entorno aislado o una base de
prueba, sin sobrescribir producción. Se comparan los hashes de PDF, conteos de
facturas por estado, decisiones, correcciones y casos. Si no concilian, el
piloto queda en modo solo revisión hasta corregirlo. Supabase Free no sustituye
esta rutina: no se declara una retención que el proveedor no garantice.

## Reversión

Si falla el login, base o almacenamiento, detener cargas, volver al último
commit desplegado y conservar los objetos y registros creados para diagnóstico.
No borrar PDFs, filas aprobadas, ni versiones de auditoría como paso de
reversión.
