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
antes por un control aritmético determinístico (`core/extraccion/validacion.py`): si una
factura no cierra, va a cuarentena y no entra al análisis.

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
   la cuota de `MAX_LLAMADAS_POR_HORA`. Lo mismo se puede hacer desde la terminal con
   `python scripts/rehomologar.py --aplicar` (por defecto corre en modo dry-run, sin
   escribir nada -- ver `python scripts/rehomologar.py --help`).
5. Repetir con cada proveedor nuevo. Después de tocar `data/conceptos/*.yaml`, correr
   `pytest tests/analisis/test_homologacion.py` -- hay un test que fija el score exacto de
   un caso límite conocido (A-3, `docs/auditoria-2026-09.md`) y avisa si un alias nuevo le
   robó el match a otro concepto.

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
   S3_BUCKET = "segurplus-evidencia-privada"
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

**Importante sobre los datos**: el disco de Streamlit no es persistente. La operación usa
PostgreSQL administrado como fuente de verdad y un bucket privado S3 compatible para los
PDF originales; el tablero avisa cuando esos dos secrets no están configurados. Ver
[`ADR-003`](docs/decisiones/ADR-003-persistencia-durable.md).

## Estructura

- `core/extraccion/` — esquema canónico, llamada a Gemini, validación aritmética.
- `core/ingesta/` — lectura de texto de PDF, doble lectura del total, hash para idempotencia.
- `core/analisis/` — homologación de conceptos, descomposición precio/cantidad, serie
  temporal, variación real (deflactada por IPC), alertas.
- `core/rehomologacion.py` — recalcula la homologación de facturas ya guardadas sin llamar
  a Gemini (ver "Cómo calibrar la homologación" más arriba).
- `core/deflactor/`, `core/macro/` — copiados de Consultora (ajuste por IPC).
- `core/almacenamiento.py` — PostgreSQL administrado en producción y DuckDB local en
  desarrollo; conserva decisiones, correcciones y casos operativos.
- `core/evidencia.py` — PDF original en almacenamiento privado S3 compatible.
- `apps/segurplus/estilo.py` — paleta institucional y formato compartido de los gráficos.
- `scripts/rehomologar.py` — CLI para re-homologar desde la terminal.
- `docs/fixtures/` — generador de facturas sintéticas en PDF (nunca reales) para los tests.
- `data/reales/` — PDFs y base de datos reales. **Nunca se commitea** (ver CLAUDE.md).
