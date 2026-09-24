# Runbook — corte seguro al piloto durable

Este procedimiento no se ejecuta contra facturas reales hasta que el cambio
esté integrado, probado y exista una ventana de mantenimiento. Las claves se
guardan solo en los paneles de sus proveedores; nunca en Git, logs o tickets.

## Precondiciones

- La rama desplegable pasó `pytest -q` y `ruff check .`.
- El servicio Render tiene una URL HTTPS estable y los secretos `APP_PASSWORD`
  y `SECRET_KEY` configurados. El piloto interno usa una contraseña compartida;
  no requiere una cuenta de Google.
- Se creó `segurplus-documentos`, bucket privado de Supabase Storage. Se creó
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
   `APP_PASSWORD`, `S3_BUCKET`, `S3_ENDPOINT_URL`, `S3_REGION`,
   `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `DATABASE_URL`,
   `SECRET_KEY` y `SEGURPLUS_PRODUCTION=1`.
4. Desplegar el commit aprobado. Verificar inicio de sesión con la contraseña
   compartida, rechazo de una contraseña incorrecta, carga manual sintética y
   lectura del PDF recién guardado. No enviar un PDF real a Gemini durante esta prueba.
   La primera conexión de la aplicación aplica las migraciones aditivas en el
   esquema `segurplus`; el panel de Supabase no es dueño de esas tablas, por
   lo que no se debe intentar suplantar ese rol ni cambiar ownership para
   sortearlo.
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

Usar desde la PC controlada del operador, con PostgreSQL client tools
instaladas y las variables de Render disponibles solo durante la ejecución:

```powershell
python scripts/respaldo_operador.py E:\Respaldos\Segurplus
```

El script solicita una contraseña de cifrado dos veces, produce un archivo
`*.zip.aes` y no deja el ZIP sin cifrar fuera de una carpeta temporal. No
guardar esa contraseña junto al archivo ni en Render. Registrar fuera de Git:
fecha, nombre del archivo, cantidad de PDFs y hashes del `manifest.json`.

Para el ensayo mensual, copiar el archivo a una PC aislada, descifrarlo con
la contraseña guardada por el operador y verificarlo sin conectar a producción:

```powershell
python scripts/verificar_respaldo.py E:\Respaldos\Segurplus\segurplus-respaldo-AAAA.zip.aes --extraer E:\Prueba\Segurplus
```

El parámetro `--extraer` debe ser una carpeta vacía de una PC aislada. Solo
después de que informe integridad, restaurar **sobre una base de prueba vacía**
con `pg_restore --dbname <URL-DE-PRUEBA> segurplus.dump`. Luego
comparar `manifest.json` contra los PDFs restaurados y los conteos por estado
de `facturas`, `decisiones_factura`, `correcciones_factura` y `casos_alerta`.
Nunca usar una URL de producción como destino de `pg_restore`.

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
