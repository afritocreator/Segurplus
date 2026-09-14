# Estado del proyecto

Ver el plan completo en `docs/PLAN.md`. Esto es el resumen rápido de qué está construido
y qué falta, para no tener que releer el plan entero cada vez.

## Construido (Fases 0-5 del plan completas)

- **Fase 0** — andamiaje: `pyproject.toml`, CI, protección de `data/reales/` (hook de
  Claude Code + pre-commit real de git), subagente `revisor-financiero`, generador de
  facturas sintéticas (`docs/fixtures/generar_fixtures.py`).
- **Fase 1** — extracción: esquema canónico (`core/extraccion/esquema.py`), llamada a
  Gemini (`core/extraccion/gemini.py`, port de Kleric-), validación aritmética
  (`core/extraccion/validacion.py`, port de `validate.ts`), ingesta de PDF con doble
  lectura del total (`core/ingesta/pdf_texto.py`), almacenamiento con idempotencia y
  cuarentena (`core/almacenamiento.py`, incluye recargos, impuestos y créditos).
- **Fase 2** — homologación de conceptos (`core/analisis/homologacion.py`, port de
  `match.ts`) con diccionario en `data/conceptos/*.yaml`.
- **Fase 3** — el corazón del análisis: descomposición precio/cantidad
  (`core/analisis/variacion.py`) y variación real deflactada por IPC
  (`core/analisis/real.py`). Ambos con tests de valor calculado a mano.
- **Fase 4** — alertas (`core/analisis/alertas.py`), con umbrales en `data/alertas.yaml`,
  y Excel de salida (`core/reportes/excel.py`: hojas Resumen, Descomposición, Alertas,
  Cuarentena), con botón de descarga en el tablero.
- **Fase 5** — tablero Streamlit (`streamlit_app.py` + `apps/segurplus/paginas/`): cargar
  PDFs, revisar facturas pendientes, ver evolución con descomposición precio/cantidad,
  serie temporal y alertas, casos operativos, la pantalla de calibración "Sin
  clasificar" (con métricas por proveedor), y la cola de cuarentena. Tema institucional
  en `apps/segurplus/estilo.py` + `.streamlit/config.toml`.
- **Login** (`apps/segurplus/autenticacion.py` + `core/autenticacion.py`): contraseña
  compartida por defecto (transitoria, sin atribución individual), o login OIDC nativo
  de Streamlit con roles (cargador/revisor/responsable/administrador) si se configura
  `OIDC_PROVIDER`.
- `core/pipeline.py` une todo lo anterior en una sola función por PDF
  (`procesar_pdf`), que es lo único que llama la app.

251 tests pasan (1 skipped, requiere `GEMINI_API_KEY` real), `ruff check` limpio.

## Persistencia y trazabilidad

- La fuente de verdad se configura por `DATABASE_URL` -- un PostgreSQL **gratis** (Neon
  o Supabase, no un plan administrado pago: ver el addendum de
  `docs/decisiones/ADR-003-persistencia-durable.md`), no DuckDB local, que queda
  reservado para desarrollo, fixtures y análisis aislado (el disco de Streamlit
  Community Cloud gratuito no sobrevive un reinicio por inactividad).
- El PDF original va por defecto a una carpeta local del servidor (no durable entre
  reinicios, pero la carga es idempotente por hash: se puede volver a subir el mismo
  lote sin duplicar nada). Un bucket S3 compatible (`pip install -e ".[s3]"`,
  `S3_BUCKET`) lo resuelve el día que haga falta, sin tocar código.
- Cada carga, aprobación, rechazo y corrección de cabecera queda registrada con actor,
  momento, motivo y (para correcciones) valor anterior -- `core/almacenamiento.py`,
  tablas `decisiones_factura` y `correcciones_factura`.

## Revisión humana: opcional y configurable

Una factura validada aritméticamente puede quedar `aprobada` directo (default) o
`requiere_revision` (si `data/operacion.yaml::revision_humana_obligatoria` está en
`true`) -- solo las aprobadas impactan Evolución, alertas y Excel. Con una o dos
personas cargando y revisando su propia carga, exigir aprobación de a una antes de ver
el análisis era pura fricción sin beneficio real; el interruptor existe para cuando
haya más gente cargando sin supervisión cruzada. La página **Revisar facturas** permite
aprobar de a una (con corrección de cabecera) o **en lote**, con el mismo rastro de
auditoría en los dos casos.

## Composición del total y casos operativos

Se persisten impuestos, recargos y créditos por separado (no solo el consumo
homologable) -- Evolución y la página de revisión muestran la composición del total
pagable, no solo la descomposición de consumos comparables. Las alertas de facturas
aprobadas generan **casos** deduplicados, asignables (responsable, vencimiento, estado,
evidencia de cierre) en la página **Casos**. Sin notificación automática todavía --
deliberado, hasta que haya evidencia de que la cola tiene severidad y propietarios
correctos (ver `docs/investigacion-2026-09-piloto-operativo.md`).

## Bug crítico de facturas reales (A-28) y su segunda vuelta (A-29, A-30)

Al mirar facturas reales de Movistar apareció un bug que invertía el resultado central
de la herramienta: el proveedor factura el mismo concepto con el período pegado a la
descripción ("Servicio de telefonía Agosto 2026" / "...Septiembre 2026"), y sin
homologar, la clave de agrupamiento usaba la descripción cruda -- dos claves distintas,
así que la descomposición interpretaba un aumento de PRECIO como si el concepto hubiera
desaparecido y uno nuevo hubiera aparecido, con `efecto_precio = 0` en las dos filas
(`core/analisis/homologacion.py::quitar_periodo`, `core/analisis/agregacion.py::_clave`,
detalle en `docs/auditoria-2026-09.md`, hallazgo A-28).

Una revisión posterior (`docs/auditoria-2026-09-rediseno.md`) encontró que la primera
corrección quedó incompleta en dos frentes, los dos ya resueltos:

- **A-29** — `quitar_periodo` no reconocía las abreviaturas `may` ni `sept`: para esos
  meses el bug de A-28 se reproducía entero. Corregido.
- **A-30** — la frase de veredicto (`efecto_dominante`) dividía por la variación neta,
  que puede quedar cerca de cero aunque cantidad y precio se muevan mucho en
  direcciones opuestas -- el caso verificado mostraba "mayormente por PRECIO (1000%)".
  Una primera corrección eliminó el porcentaje absurdo pero se pasó de frenada: dejaba
  el tablero SIN veredicto en cualquier caso de signos opuestos, que es la mitad de los
  casos reales. La versión final divide por la suma de valores absolutos de los tres
  efectos en vez de por la variación neta -- acota siempre a [-1, 1] y sigue
  respondiendo la pregunta correcta cuando hay una respuesta.

De paso se armó el circuito completo de calibración: el score de cada homologación se
persiste (haya homologado o no), `core/rehomologacion.py` + `scripts/rehomologar.py`
recalculan la homologación de facturas ya guardadas sin volver a llamar a Gemini (con
previsualización y confirmación obligatorias, y bloqueo si el diccionario quedó vacío),
y la pantalla **"Sin clasificar"** muestra qué conceptos no homologaron ordenados por
plata, más **métricas de calidad de lectura por proveedor** (tasa de cuarentena, tasa
de conceptos sin homologar) para saber en qué proveedor puntual ajustar el prompt o el
diccionario, en vez de mirar el agregado de todos mezclados.

## Tablero rediseñado

Tema institucional (`.streamlit/config.toml`, paleta navy/dorado de la consultora),
gráfico de serie temporal nominal vs. real (deflactada por IPC) para ver la tendencia
de un servicio a lo largo de todos los períodos cargados, y la página de Evolución con
selectores en el sidebar, métricas + la frase de veredicto, y pestañas (Descomposición
/ Composición total / Serie histórica / Alertas / Detalle) en vez de todo apilado.

## Falta (siguiente trabajo)

- **Auditoría del piloto operativo (A-49 a A-59)**: ver
  `docs/auditoria-2026-09-piloto.md`. **Tres hallazgos conviene resolverlos ANTES de
  cargar la primera factura real**: `conectar()` ignora la ruta que recibe si
  `DATABASE_URL` está en el entorno, así que un `pytest` de rutina puede escribir en la
  base de producción (A-49, crítico); y con `revision_humana_obligatoria` en `false` (el
  default) ninguna factura llega a `requiere_revision`, lo que deja sin salida tanto el
  rechazo y la corrección de cabeceras (A-50) como la re-homologación cuando una factura
  quedó sin servicio detectado (A-51).
- **Fase 6** — motor por reglas 100% local, solo si hace falta (ver punto de decisión
  pendiente en `docs/PLAN.md`).
- **Todavía no se cargó ninguna factura real**: el pipeline corre de punta a punta
  contra las fixtures sintéticas y el tablero levanta sin errores, pero falta la
  prueba real con `GEMINI_API_KEY` y cargar las facturas de los distintos proveedores.
- `data/conceptos/*.yaml` tiene los alias obvios para arrancar (Movistar,
  `servicio_telefonia`) -- se completa con la pantalla "Sin clasificar" a medida que se
  carguen facturas de cada proveedor. El umbral de homologación
  (`data/homologacion.yaml`) sigue siendo un valor conservador (0,60) elegido sin
  datos -- con el score persistido, se puede calibrar con evidencia (histograma en
  "Sin clasificar") en cuanto haya volumen real.
- Hallazgos diferidos hasta tener facturas reales: A-26 (`_parsear_monto` con
  separadores de miles mezclados) y A-27 (alerta de período faltante asume
  periodicidad mensual, un servicio bimestral como el gas dispara falso positivo
  siempre).
- **Postgres real en producción — resuelto**: la app está conectada a un proyecto
  Supabase gratuito ya existente, en su propio schema (`segurplus`) con un rol de base
  dedicado (`segurplus_app`, permisos acotados a ese schema, `search_path` propio) para
  no interferir con los otros productos que viven en ese mismo proyecto. Verificado en
  el propio deploy: las tablas se crean solas al conectar y la app funciona de punta a
  punta. El camino `ConexionPostgres` sigue sin tener un test automatizado en CI contra
  Postgres real (solo el test estático que revisa el SQL en busca de `%` sueltos,
  `tests/test_conexion_postgres.py`), pero ya está verificado a mano en producción.
- **Evidencia del PDF no durable entre reinicios** (carpeta local por defecto) y **sin
  backups verificados** del proveedor gratuito elegido -- ver las limitaciones
  conocidas del addendum de ADR-003. Aceptable mientras se prueba, a revisar antes de
  depender de esto en el día a día.
- **Migración a Vercel evaluada y descartada**: el plan gratuito de Vercel prohíbe uso
  comercial, lo que hubiera roto el "costo cero" ya prometido. Se decidió quedarse en
  Streamlit y mejorar la estética ahí. Si alguna vez se reconsidera, la arquitectura
  recomendada es Next.js + `core/` en Python detrás de una API, no portar la lógica a
  TypeScript (son ~2400 líneas con más de 250 tests y revisión financiera).
