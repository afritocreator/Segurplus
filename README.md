# Segurplus

Análisis automatizado de facturas de servicios: separa, en cada aumento de una factura
de telefonía, energía, gas, agua, seguros o alquiler, cuánto es por cambio de **cantidad**
(más líneas, más consumo) y cuánto por cambio de **precio** unitario, compara contra la
inflación (IPC), y genera alertas (recargos, conceptos nuevos, ítems duplicados).

Ver el plan completo en [`docs/PLAN.md`](docs/PLAN.md) y la decisión de cómo se lee cada
factura en [`docs/decisiones/ADR-001-lectura-de-facturas.md`](docs/decisiones/ADR-001-lectura-de-facturas.md).

Repo hermano de `afritocreator/Consultora` (de ahí se reutiliza el deflactor por IPC y el
patrón de reportes) y de `afritocreator/Kleric-` (de ahí se reutiliza el circuito de
lectura de facturas con IA y su validación aritmética).

## Cómo funciona, en una frase

El modelo (Gemini, gratis) solo extrae texto a JSON. Ningún número se muestra sin pasar
antes por un control aritmético determinístico (`core/extraccion/validacion.py`): subir un
PDF deja un **borrador** editable en **Confirmar carga**, con el PDF al lado -- si algo no
cierra, se corrige ahí mismo; nada entra al análisis hasta que se confirma.

## Instalación

```bash
pip install -e ".[dev]"
python scripts/instalar-git-hooks.py   # una sola vez, protege data/reales/
```

Para usar la extracción con IA (opcional — todo lo demás funciona sin esto):

```bash
export GEMINI_API_KEY=...   # gratis en aistudio.google.com
```

## Correr los tests

```bash
pytest -q
ruff check .
```

La suite normal nunca toca un Postgres real -- corre entera contra DuckDB local
(`tests/conftest.py` saca `DATABASE_URL` del entorno para toda la corrida, así que ni
una `DATABASE_URL` exportada en tu shell puede desviar un test a la base de producción,
ver `docs/auditoria-2026-09-piloto.md`, A-49). El camino PostgreSQL
(`core.almacenamiento.ConexionPostgres`) tiene un test aparte, opcional, que sí pega
contra un Postgres real -- para correrlo, armá una base o un schema descartable
(ver "Crear la base Postgres gratis" más abajo) y:

```bash
TEST_DATABASE_URL="postgresql://..." pytest tests/test_conexion_postgres_real.py -v -m red_real
```

Sin `TEST_DATABASE_URL` configurada (o sin `psycopg` instalado), se skipea solo.

## Correr el tablero localmente

```bash
streamlit run streamlit_app.py
```

## Cómo calibrar la homologación de conceptos

Cada proveedor describe sus conceptos distinto ("Abono Línea Móvil", "Cargo fijo móvil",
"Servicio de telefonía Agosto 2026"...). `core/analisis/homologacion.py` los mapea a un
concepto normalizado (`abono_movil`) contra el diccionario de `data/conceptos/*.yaml`. Con
facturas reales, siempre va a faltar algún alias. El circuito para completarlo:

1. Cargá las facturas del proveedor nuevo (página **Cargar facturas**).
2. Abrí la página **Sin clasificar** del tablero: muestra qué descripciones no homologaron
   a ningún concepto, ordenadas por cuánta plata dejan sin clasificar -- el alias que más
   conviene agregar es el que aparece primero. Trae un snippet YAML listo para pegar y un
   histograma de scores con el umbral marcado, para calibrar `data/homologacion.yaml` con
   evidencia en vez de a ciegas.
3. Agregá el alias que falte a `data/conceptos/<servicio>.yaml`.
4. Volvé a "Sin clasificar" y apretá **"Re-homologar ahora"** -- recalcula la homologación
   de TODO lo ya guardado con el diccionario nuevo, sin volver a llamar a Gemini ni gastar
   la cuota de `max_llamadas_gemini_por_hora` (`data/operacion.yaml`). Lo mismo se puede
   hacer desde la terminal con `python scripts/rehomologar.py --aplicar` (por defecto
   corre en modo dry-run, sin escribir nada -- ver `python scripts/rehomologar.py --help`).
5. Repetir con cada proveedor nuevo. Después de tocar `data/conceptos/*.yaml`, correr
   `pytest tests/analisis/test_homologacion.py` -- hay un test que fija el score exacto de
   un caso límite conocido (A-3, `docs/auditoria-2026-09.md`) y avisa si un alias nuevo le
   robó el match a otro concepto.

**No es solo el diccionario** (docs/auditoria-2026-09-facturas-reales.md, hallazgo C-12):
tocar la LÓGICA de homologación -- `quitar_periodo`, `quitar_detalle_numerico`, el umbral de
`data/homologacion.yaml` -- también cambia cómo homologa lo que ya está cargado, y ese
cambio no se aplica solo. Correr "Re-homologar ahora" (o `scripts/rehomologar.py
--aplicar`) después de CUALQUIER cambio en `core/analisis/homologacion.py`, no solo
después de agregar un alias.

## Diagnosticar una factura que no entra

Subir un PDF SIEMPRE deja un borrador -- si algo no cierra, no se pierde: queda en
**Confirmar carga**, con el PDF al lado, para corregir a mano y confirmar recién cuando
cierra. Si una factura ni siquiera llegó a leerse (Gemini no respondió, el PDF está roto):

1. La página **Cargar facturas** tiene un checkbox "Ver últimos intentos de extracción
   fallidos" con el historial persistido (tabla `intentos_gemini`) -- sigue disponible
   después de recargar la página o de haber cerrado la sesión donde se subió, a diferencia
   del resumen de la corrida, que se pierde al navegar. Sin marcar, no conecta a la base
   (docs/auditoria-2026-09-facturas-reales.md, hallazgo C-7).
2. Para ver exactamente qué le contestó Gemini a una factura puntual, sin tocar la base:

   ```bash
   export GEMINI_API_KEY=...          # la misma que está cargada en Streamlit Cloud
   python scripts/probar_extraccion.py ruta/a/factura.pdf
   ```

   Muestra el JSON crudo que devolvió el modelo, la conversión al esquema canónico, la
   doble lectura del total (`core/ingesta/pdf_texto.py`) y el resultado de cada control
   aritmético -- de solo lectura, no escribe nada en `data/reales/facturas.duckdb`.
3. Si el problema es que un concepto no homologa como debería, seguir el circuito de
   calibración de arriba ("Sin clasificar").

## Publicar en Streamlit Community Cloud (gratis, accesible desde cualquier compu)

No usamos Vercel: Segurplus es Python + Streamlit (para reutilizar el análisis financiero
ya escrito en Consultora), y Vercel no corre este tipo de servidor persistente — eso es
lo que sí resuelve Streamlit Community Cloud, gratis, mismo patrón que ya usa Consultora
(ver `docs/decisiones/ADR-002-deploy.md`).

1. Entrá a [share.streamlit.io](https://share.streamlit.io) con la cuenta de GitHub de la
   organización y elegí "New app".
2. Repo: `afritocreator/Segurplus`, branch: `main`, archivo principal: `streamlit_app.py`.
   **Marcala como pública, no privada** — el plan gratis solo permite una app privada por
   workspace y ese lugar ya lo ocupa Consultora (ver el addendum de
   `docs/decisiones/ADR-002-deploy.md`).
3. En **Advanced settings → Secrets**, configurá como mínimo:
   ```toml
   GEMINI_API_KEY = "la-api-key-real"
   DATABASE_URL = "postgresql://..."
   ```
   (ver `.streamlit/secrets.toml.example` para el formato — ese archivo real nunca se
   commitea, solo se carga acá). Para producción configurá además OIDC y los roles por
   e-mail; `APP_PASSWORD` queda solo como transición del piloto.
4. Deploy. Queda accesible por un link (tipo `segurplus.streamlit.app`) desde cualquier
   computadora con navegador, sin instalar nada — y sin la contraseña, no se puede usar.

**Identidad y trazabilidad**: con `OIDC_PROVIDER`, la aplicación usa el login individual
de Streamlit y asigna roles de cargador, revisor, responsable o administrador. Sin OIDC se
mantiene la contraseña compartida únicamente para el piloto y se la identifica como acceso
transitorio, sin atribución individual.

**Importante sobre los datos**: el disco de Streamlit no es persistente — un reinicio por
inactividad puede perder lo que esté solo en DuckDB local. Por eso `DATABASE_URL` apunta a
un PostgreSQL gratuito (Neon o Supabase, no un plan pago — ver
[`ADR-003`](docs/decisiones/ADR-003-persistencia-durable.md) para el porqué), y sin ese
secret configurado el tablero avisa que está en modo local.

### Crear la base Postgres gratis (Neon o Supabase)

Cualquiera de los dos sirve. Con Neon es directo: creá un proyecto en
[neon.tech](https://neon.tech) (plan gratuito), copiá la **connection string** del
dashboard (`postgresql://...`) y pegala como `DATABASE_URL` en Secrets de Streamlit
Community Cloud (paso 3 de arriba). Al primer `conectar()`, la app corre las
migraciones (`_DDL`) sola — no hace falta crear tablas a mano.

**Con Supabase, dos cosas a tener en cuenta** (verificado a mano, no en la teoría):

- **Usá el "Session pooler", no "Direct connection".** El botón **Connect** del
  proyecto ofrece varias opciones; "Direct" (`db.xxxx.supabase.co`) resuelve solo por
  IPv6, y Streamlit Community Cloud no tiene salida IPv6 — la conexión falla con un
  `OperationalError` genérico. "Session pooler" (`aws-0-...pooler.supabase.com:5432`)
  sí funciona.
- **Si ese proyecto de Supabase ya tiene otros productos**, no uses el rol `postgres`
  para Segurplus (compartiría schema y usuario con todo lo demás). Creá un schema y un
  rol de base dedicados, para que quede completamente aislado:
  ```sql
  CREATE SCHEMA IF NOT EXISTS segurplus;
  CREATE ROLE segurplus_app WITH LOGIN PASSWORD 'una-contraseña-solo-con-letras-y-numeros';
  GRANT ALL ON SCHEMA segurplus TO segurplus_app;
  ALTER ROLE segurplus_app SET search_path TO segurplus;
  ```
  (contraseña **sin** `@ : / ? # %` -- esos caracteres rompen el parseo de la URL si no
  se codifican). Armá la `DATABASE_URL` con ese usuario nuevo, cambiando solo el
  usuario y la contraseña en la URL del pooler que copiaste:
  `postgresql://segurplus_app.<project-ref>:<contraseña>@aws-0-...pooler.supabase.com:5432/postgres`
  -- el `search_path` ya queda atado al rol, no hace falta pasarlo en la URL (pasarlo
  como `?options=-csearch_path%3D...` no funciona de forma confiable a través del
  pooler).

El PDF original, mientras tanto, se guarda en el disco del servidor (no es durable
entre reinicios) — aceptable mientras se prueba, porque la carga es idempotente por
hash: si el servidor reinicia, se puede volver a subir el mismo lote de PDFs sin que se
dupliquen. El día que haga falta que también sea durable, un bucket S3 compatible
(`pip install -e ".[s3]"`, `S3_BUCKET` en Secrets) lo resuelve sin tocar código — ver
`core/evidencia.py` y el ADR-003.

## Estructura

- `core/extraccion/` — esquema canónico, llamada a Gemini, validación aritmética.
- `core/ingesta/` — lectura de texto de PDF, doble lectura del total, hash para idempotencia.
- `core/analisis/` — homologación de conceptos, descomposición precio/cantidad, serie
  temporal, variación real (deflactada por IPC), alertas.
- `core/rehomologacion.py` — recalcula la homologación de facturas ya guardadas sin llamar
  a Gemini (ver "Cómo calibrar la homologación" más arriba).
- `core/deflactor/`, `core/macro/` — copiados de Consultora (ajuste por IPC).
- `core/almacenamiento.py` — Postgres (gratis, Neon/Supabase) en producción y DuckDB
  local en desarrollo; conserva decisiones, correcciones y casos operativos.
- `core/evidencia.py` — PDF original: carpeta local por defecto, bucket S3 compatible
  opcional (extra `s3`).
- `core/operacion.py` — parámetros operativos del piloto (`data/operacion.yaml`), como
  si la revisión humana es obligatoria.
- `apps/segurplus/estilo.py` — paleta institucional y formato compartido de los gráficos.
- `scripts/rehomologar.py` — CLI para re-homologar desde la terminal.
- `docs/fixtures/` — generador de facturas sintéticas en PDF (nunca reales) para los tests.
- `data/reales/` — PDFs y base de datos reales. **Nunca se commitea** (ver CLAUDE.md).
