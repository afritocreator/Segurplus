# ADR-003: PostgreSQL gratis para el piloto; S3 opcional, no default

**Estado**: aceptado, con addendum. **Fecha**: 2026-09-13. **Addendums**: 2026-09-13,
2026-09-23.

## Decisión

La fuente de verdad del piloto es PostgreSQL, configurado en `DATABASE_URL`. DuckDB
queda como modo local para desarrollo, fixtures y análisis aislado -- no se considera
durable cuando la app está desplegada en Streamlit Community Cloud (el disco gratuito
no sobrevive un reinicio por inactividad).

El PDF original se conserva por defecto en una carpeta local (`EVIDENCIA_DIR`,
`core/evidencia.py`) -- suficiente mientras la app corre en un único proceso.

## Addendum (2026-09-13): Postgres gratis, no administrado pago; S3 opcional

La propuesta original de este ADR pedía PostgreSQL **administrado** (pago) y un bucket
S3 privado como default. Se revisó esa decisión: Segurplus es una herramienta interna
con "costo cero" ya prometido por escrito -- la misma restricción que descartó Vercel
(ver `docs/estado.md`) -- y con una o dos personas usándola, ninguno de los dos gastos
está justificado todavía.

**Postgres gratis en vez de administrado pago**: un plan gratuito de Neon o Supabase
resuelve el problema real (el disco de Streamlit Community Cloud no es durable) sin
costo. La capa de acceso (`core/almacenamiento.py::ConexionPostgres`) no distingue
entre un Postgres gratis y uno pago -- es la misma `DATABASE_URL`, así que no hay nada
que reescribir para pasar a un plan pago si el piloto crece y necesita más
retención/backups de los que ofrece el free tier.

**S3 pasa a opcional, no default**: sin una razón concreta para pagar almacenamiento
de objetos hoy, `boto3` se movió a un extra opcional (`pip install -e ".[s3]"`,
`pyproject.toml`) y se sacó de `requirements.txt`. El PDF original vive en una carpeta
local del servidor -- ver la limitación de esto abajo.

## Limitaciones conocidas de esta versión reducida

- **La evidencia en `EVIDENCIA_DIR` no es durable en Streamlit Community Cloud**: mismo
  problema que DuckDB, el disco se pierde al reiniciar. Aceptable mientras se prueba
  (la carga es idempotente por hash, se puede resubir el PDF); no para depender del
  día a día. El día que haga falta, `S3_BUCKET` + el extra `s3` activan
  `core/evidencia.py::guardar_pdf` sin cambiar el resto del código.
- **Sin backups verificados**: un plan gratuito de Neon/Supabase no ofrece el mismo
  SLA de retención/restauración que uno administrado pago. Aceptable para un piloto
  con datos reconstruibles desde los PDFs originales; a revisar antes de depender de
  la base como único registro de decisiones (aprobaciones, correcciones, casos).

## Seguridad y operación

- Secrets (`DATABASE_URL`, y `S3_BUCKET`/`S3_ENDPOINT_URL` si se activa el extra `s3`)
  se configuran fuera del repositorio, en Secrets de Streamlit Community Cloud.
- Si se activa S3 en el futuro: el bucket no debe recibir ACL pública, y cada carga
  debe pedir cifrado de servidor AES-256 (`core/evidencia.py::guardar_pdf` ya lo hace).
- Sin `DATABASE_URL`, la aplicación usa DuckDB local y no debe considerarse registro
  operativo cotidiano.

## Consecuencia

Se agrega `psycopg[binary]` como dependencia obligatoria. `boto3` es un extra opcional
(`s3`), no instalado por defecto. Los dos con imports diferidos, para no forzar
servicios externos en tests ni desarrollo local. La capa de almacenamiento conserva SQL
compatible con DuckDB y PostgreSQL (`?` como placeholder, traducido a `%s` en
`ConexionPostgres.execute` -- ver la restricción documentada ahí y en
`tests/test_conexion_postgres.py`).

## Addendum (2026-09-23): el PDF cae a la misma base cuando no hay S3 ni disco

`docs/auditoria-2026-09-web.md`, E-4: en Render (el deploy de `web/`, ver ADR-005) no
hay disco persistente ni bucket S3 configurado por defecto -- `EVIDENCIA_DIR` se
perdía en cada reinicio del proceso, y sin el PDF, la pantalla **Revisar** no podía
mostrar la factura al lado del formulario.

En vez de forzar S3 (que vuelve a traer el costo que este ADR evitó en el primer
addendum) o un disco persistente pago de Render, `core/evidencia.py::guardar_pdf` /
`leer_pdf` / `borrar_pdf` ganaron un tercer nivel de fallback: **S3 si está
configurado → `EVIDENCIA_DIR` si está → la misma base de datos**, en una tabla nueva
(`documentos_pdf`, `hash_pdf` como clave, contenido en base64 sobre una columna
`VARCHAR`). Base64 en `VARCHAR` en vez de un tipo binario nativo porque DuckDB
(`BLOB`) y PostgreSQL (`BYTEA`) no comparten un tipo binario con el mismo nombre, y el
DDL de `core/almacenamiento.py` es un solo string que corre contra los dos motores --
`VARCHAR` sí es compatible en ambos sin bifurcar el DDL por motor.

**Costo**: base64 infla el tamaño ~33% contra el PDF original, y el plan gratuito de
Supabase tiene un límite de fila razonable pero no ilimitado. Por eso se agregó un
tope de 10 MB por PDF al subirlo (`web/app.py`, `_TAMANIO_MAXIMO_PDF_BYTES`) -- una
factura escaneada gigante se rechaza con un mensaje claro en vez de llenar la cuota
gratuita silenciosamente.

**No cambia la prioridad**: si en algún momento se configura `EVIDENCIA_DIR` con un
volumen persistente o `S3_BUCKET`, el PDF vuelve a preferir esas opciones -- la base
es el último recurso, no el nuevo default en todos los entornos.
