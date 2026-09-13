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
  lectura del total (`core/ingesta/pdf_texto.py`), almacenamiento en DuckDB con
  idempotencia y cuarentena (`core/almacenamiento.py`, incluye recargos).
- **Fase 2** — homologación de conceptos (`core/analisis/homologacion.py`, port de
  `match.ts`) con diccionario en `data/conceptos/*.yaml`.
- **Fase 3** — el corazón del análisis: descomposición precio/cantidad
  (`core/analisis/variacion.py`) y variación real deflactada por IPC
  (`core/analisis/real.py`). Ambos con tests de valor calculado a mano.
- **Fase 4** — alertas (`core/analisis/alertas.py`), con umbrales en `data/alertas.yaml`,
  y Excel de salida (`core/reportes/excel.py`: hojas Resumen, Descomposición, Alertas,
  Cuarentena), con botón de descarga en el tablero.
- **Fase 5** — tablero Streamlit (`streamlit_app.py` + `apps/segurplus/paginas/`): cargar
  PDFs, ver evolución con descomposición precio/cantidad, serie temporal y alertas, la
  pantalla de calibración "Sin clasificar", y la cola de cuarentena. Tema institucional en
  `apps/segurplus/estilo.py` + `.streamlit/config.toml`. Listo para publicar en Streamlit
  Community Cloud (ver README.md y `docs/decisiones/ADR-002-deploy.md`, incluido el
  addendum sobre app pública + contraseña).
- **Login con contraseña compartida** (`apps/segurplus/autenticacion.py` +
  `core/autenticacion.py`): la app se publica pública (el único slot privado del plan
  gratis ya lo usa Consultora), así que pide `APP_PASSWORD` antes de mostrar cualquier
  pantalla. Sin esa clave configurada en secrets, no bloquea (desarrollo local).
- `core/pipeline.py` une todo lo anterior en una sola función por PDF
  (`procesar_pdf`), que es lo único que llama la app.

215 tests pasan (1 skipped, requiere `GEMINI_API_KEY` real), `ruff check` limpio. El tablero
se probó levantado localmente (HTTP 200, sin errores de import). Las cuatro páginas del
tablero (Cargar, Evolución, Sin clasificar, Cuarentena) tienen tests con `AppTest` de
Streamlit, no solo el login -- ver `docs/auditoria-2026-09.md`, hallazgo A-19.

## Bug crítico encontrado y corregido con facturas reales (A-28)

Al mirar facturas reales de Movistar (todavía sin cargar en el sistema, solo revisadas a
mano) apareció un bug que invertía el resultado central de la herramienta: el proveedor
factura el mismo concepto con el período pegado a la descripción ("Servicio de telefonía
Agosto 2026", al mes siguiente "...Septiembre 2026"), y sin homologar, la clave de
agrupamiento entre períodos usaba la descripción cruda -- dos claves distintas, así que la
descomposición interpretaba un aumento de PRECIO como si el concepto hubiera desaparecido y
uno nuevo hubiera aparecido, con `efecto_precio = 0` en las dos filas. **Ya está resuelto**
(`core/analisis/homologacion.py::quitar_periodo`, `core/analisis/agregacion.py::_clave`),
con test de punta a punta y verificado que no rompe ninguna guarda de la auditoría anterior
(A-3). Detalle completo en `docs/auditoria-2026-09.md`, hallazgo A-28.

De paso se armó el circuito completo de calibración: el score de cada homologación se
persiste (`score_homologacion`, haya homologado o no), `core/rehomologacion.py` +
`scripts/rehomologar.py` recalculan la homologación de facturas ya guardadas sin volver a
llamar a Gemini, y la pantalla **"Sin clasificar"** del tablero muestra qué conceptos no
homologaron, ordenados por plata, con un botón para re-homologar sin salir de la app.

## Tablero rediseñado

Tema institucional (`.streamlit/config.toml`, paleta navy/dorado de la consultora),
gráfico de serie temporal nominal vs. real (deflactada por IPC) para ver la tendencia de
un servicio a lo largo de todos los períodos cargados (antes solo se comparaban dos meses
elegidos a mano), y la página de Evolución reorganizada con selectores en el sidebar,
métricas + una frase de veredicto ("el cambio fue mayormente por PRECIO/CANTIDAD"), y
pestañas (Descomposición / Serie histórica / Alertas / Detalle) en vez de todo apilado en
una sola pantalla larga.

## Falta (siguiente trabajo)

- **Fase 6** — motor por reglas 100% local, solo si hace falta (ver punto de decisión
  pendiente en `docs/PLAN.md`).
- **Todavía no se cargó ninguna factura real** (aunque ya se revisaron a mano y de ahí
  salió el hallazgo A-28): el pipeline corre de punta a punta contra las fixtures
  sintéticas y el tablero levanta sin errores, pero falta la prueba real con
  `GEMINI_API_KEY` y cargar las facturas de los distintos proveedores.
- `data/conceptos/*.yaml` tiene los alias obvios para arrancar más el alias real de
  Movistar (`servicio_telefonia`) -- se completa con la pantalla "Sin clasificar" a medida
  que se carguen facturas de cada proveedor. El umbral de homologación
  (`data/homologacion.yaml`) sigue siendo un valor conservador (0,60) elegido sin datos --
  ahora que el score se persiste, se puede calibrar con evidencia (histograma en "Sin
  clasificar") en cuanto haya volumen real.
- Hallazgos diferidos hasta tener facturas reales: A-26 (`_parsear_monto` con separadores
  de miles mezclados) y A-27 (alerta de período faltante asume periodicidad mensual, un
  servicio bimestral como el gas dispara falso positivo siempre).
- **Auditoría del rediseño (A-29 a A-48)**: ver
  `docs/auditoria-2026-09-rediseno.md`. Encontró que el propio arreglo de A-28 quedó
  incompleto -- `quitar_periodo` no reconoce los meses abreviados `may` ni `sept`, así que
  el bug se reproduce entero para esos casos (A-29) -- y que la frase de veredicto nueva
  puede mostrar un porcentaje sin sentido cuando los efectos de cantidad y precio tienen
  signos opuestos (A-30). Los dos, más A-31/A-33/A-40, conviene resolverlos antes de cargar
  la primera factura real.
- **Correcciones A-29 a A-48**: el período abreviado reconoce también `may`
  y `sept`; el veredicto no calcula porcentajes cuando los efectos se
  compensan; la re-homologación exige previsualización y confirmación, usa
  transacción y bloquea diccionarios inseguros; los scores no medidos se
  distinguen en la pantalla de calibración. A-26 y A-27 siguen diferidos
  hasta contar con facturas reales.
- **Persistencia en la nube**: el disco de Streamlit Community Cloud gratuito no es
  durable entre reinicios (ver ADR-002). No es grave para probar, sí para depender de
  esto en el día a día — a resolver cuando se decida usarlo en producción.
- **Migración a Vercel evaluada y descartada**: el plan gratuito de Vercel prohíbe uso
  comercial, lo que hubiera roto el "costo cero" ya prometido. Se decidió quedarse en
  Streamlit y mejorar la estética ahí (ver más arriba). Si alguna vez se reconsidera, la
  arquitectura recomendada es Next.js + `core/` en Python detrás de una API, no portar la
  lógica a TypeScript (son ~2100 líneas con >200 tests y revisión financiera).
