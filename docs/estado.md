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

339 tests pasan (3 skipped, requieren `GEMINI_API_KEY` o `TEST_DATABASE_URL` reales),
`ruff check` limpio.

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

## Primera carga con facturas reales (B-1 a B-6)

El usuario probó cargar facturas reales de dos proveedores nuevos (luz -- Usina Popular
y Municipal de Tandil -- y gas -- Camuzzi) y las tres cosas fallaban a la vez: algunas
daban error, otras iban a cuarentena, y una se guardaba en verde pero después no
aparecía en ningún lado. Seis hallazgos nuevos, cinco ya resueltos:

- **B-1 (crítico, resuelto)** — las facturas de luz imprimen el período como "07/2022"
  (mes/año, sin día). `_normalizar_fecha` solo aceptaba `YYYY-MM-DD` y `DD/MM/YYYY`, así
  que `periodo_desde` quedaba en `None` -- la factura VALIDABA BIEN, se guardaba, y la
  UI decía en verde "guardada y validada", pero todo el análisis filtra
  `periodo_desde IS NOT NULL`: quedaba invisible, sin ningún aviso. Ahora
  `_normalizar_fecha` acepta `MM/YYYY`, `YYYY-MM` y `DD-MM-YYYY`, y el pipeline nunca deja
  una factura "aprobada" sin `periodo_desde` o `servicio` -- queda `requiere_revision`
  con el estado nuevo `necesita_datos`, visible en "Cargar facturas".
- **B-2 (alto, resuelto)** — "Cargo Fijo (414,4500 / 30.5 x 8)": el detalle de cálculo
  pegado a la descripción (distinto cada mes) hundía el score de homologación de
  "cargo fijo" -- un alias EXACTO -- por debajo del umbral, y además rompía la clave de
  agrupamiento entre meses (la misma enfermedad de A-28, otro patrón). Nueva
  `quitar_detalle_numerico()` en `core/analisis/homologacion.py`.
- **B-3 (alto, pendiente de confirmar con `GEMINI_API_KEY` real)** — las líneas de
  impuesto imprimen base imponible E importe en la misma línea; el prompt no decía cuál
  de los dos números es "importe". `PROMPT_EXTRACCION` se reforzó con esa regla (con un
  ejemplo numérico) y con un aviso sobre layouts a dos columnas (las facturas de gas);
  `extraer_con_gemini` ahora también recibe el texto plano ya extraído del PDF como
  apoyo. No se pudo verificar contra la API real en este entorno -- correr
  `scripts/probar_extraccion.py` sobre facturas reales es el paso que falta.
- **B-4 (medio, resuelto)** — el tope de llamadas por hora contaba filas de `facturas` +
  `cuarentena`, un proxy: una extracción que fallaba no dejaba fila en ninguna de las
  dos, así que no contaba, aunque sí gastó cuota real de la API. Nueva tabla
  `intentos_gemini` (un registro por cada llamada real), tope movido a
  `data/operacion.yaml::max_llamadas_gemini_por_hora`.
- **B-5 (medio, resuelto)** — un fallo de extracción no dejaba ningún rastro, se perdía
  al recargar la página. Ahora cada intento (éxito o fracaso) queda persistido, con un
  expander en "Cargar facturas" que muestra los últimos fallos.
- **B-6 (bajo, resuelto)** — A-27 (diferido desde la primera auditoría) se volvió real:
  las facturas de gas son bimestrales, y `alertas_por_periodo_faltante` asumía
  periodicidad mensual siempre. Ahora usa el `periodo_hasta` que la propia factura
  declara para calcular el próximo período esperado -- funciona para cualquier
  cadencia, sin inferir un patrón.

Las cuatro facturas reales que sirvieron para encontrar esto NO se commitearon
(CLAUDE.md), pero se agregó `docs/fixtures/sintetico/gas_2026-07.pdf` (generada con
`docs/fixtures/generar_fixtures.py`) que reproduce la forma que rompía -- período
"MM/AAAA" sin día, bimestral, detalle numérico pegado a la descripción -- para dejar
cobertura de regresión real sin depender de ningún dato de un proveedor o cliente.

## Auditoría de las correcciones B-1 a B-6 (C-1 a C-16) — los 16 resueltos o documentados

Después de cerrar B-1 a B-6, se auditó ese mismo trabajo (sin corregir nada en el
momento, ver `docs/auditoria-2026-09-facturas-reales.md`) y salieron **16 hallazgos
nuevos, dos de ellos regresiones del propio trabajo**: **C-1**, la normalización de
`periodo_hasta` al primer día del mes (B-1) contradecía la alerta de período que usa
`periodo_hasta + 1 día` (B-6) -- rompía el caso mensual con período `MM/AAAA` (falso
"falta un período" en CADA par de meses consecutivos), justo el caso de las facturas de
luz. Y **C-2**, un JSON válido pero incompleto (a un concepto le falta una clave)
escapaba como `KeyError` sin envolver, anulando el conteo del tope (B-4) y el registro de
diagnóstico (B-5) para esa clase de fallo.

Los 16 se corrigieron en un plan de 7 bloques, cada uno con su propio commit, tests y
`pytest`/`ruff` verdes:

- **C-1** — `_normalizar_fecha` normaliza `periodo_hasta` al ÚLTIMO día del mes cuando
  el dato es solo `MM/AAAA`/`AAAA-MM` (`periodo_desde` sigue yendo al primero);
  `alertas_por_periodo_faltante` además tolera datos YA guardados con la normalización
  vieja (reinterpreta un `periodo_hasta` en día 1 como fin de ese mes).
- **C-2** — `factura_desde_json` se llama dentro de un `try` que envuelve
  `KeyError`/`TypeError`/`ValueError` en `ExtraccionError`, con el JSON crudo adjunto
  (`respuesta_cruda`) para que B-4 y B-5 vuelvan a cubrir este caso.
- **C-3, C-4, C-6** — el circuito "factura en `necesita_datos` → corregir a mano":
  `registrar_correccion` valida `servicio` contra `SERVICIOS_CONOCIDOS`, la corrección de
  `servicio` en el tablero pasa a `st.selectbox`, el `ValueError` de una fecha mal escrita
  se atrapa en vez de tirar traceback, y el mensaje de `necesita_datos` aclara que además
  de corregir hay que aprobar.
- **C-5** — `quitar_detalle_numerico` deja de sacar cualquier paréntesis sin letras: ahora
  exige una operación entre dos números, así que "Medidor (8399554)" o "Cargo (1)" quedan
  intactos.
- **C-7** — el expander de diagnóstico de "Cargar facturas" pasa a `st.checkbox` sin
  marcar por default: sin marcarlo, cero conexiones a la base.
- **C-8, C-11** — `proxima_ventana_libre` devuelve un `timedelta` (no la hora del
  servidor) y recibe el `tope` para mirar la llamada que efectivamente hace falta que
  salga de la ventana; el pipeline muestra la hora en la zona horaria del equipo
  (`core.operacion.zona_horaria`, `America/Argentina/Buenos_Aires`).
- **C-9, C-10** — índice + purga por retención (30 días) en `intentos_gemini`; un intento
  exitoso ya no duplica `respuesta_extraida` en esa tabla.
- **C-12, C-16** — documentación: re-homologar hace falta también al tocar la lógica
  (no solo el diccionario); las 35 citas a `auditoria-2026-09-piloto.md, hallazgo B-N`
  se redirigieron a este documento, que es donde efectivamente viven.
- **C-13, C-15** — fixtures: `invariant=1` para que regenerarlas no cambie los bytes de
  las demás sin motivo; la fixture de gas pasa a IVA 27% (real para luz y gas).
- **C-14 — no corregido, a propósito**: `Recargo energia`/`Impuesto energia electrica`
  homologando a `consumo_energia` es un problema de que el MODELO los clasificó como
  concepto en vez de recargo/impuesto, no del diccionario -- absorberlo mejor en el
  diccionario escondería el síntoma. Depende de cómo se comporte el prompt (B-3).

Ver el estado final de cada hallazgo, con el commit que lo resolvió, en la sección de
cierre de `docs/auditoria-2026-09-facturas-reales.md`.

## Tablero rediseñado

Tema institucional (`.streamlit/config.toml`, paleta navy/dorado de la consultora),
gráfico de serie temporal nominal vs. real (deflactada por IPC) para ver la tendencia
de un servicio a lo largo de todos los períodos cargados, y la página de Evolución con
selectores en el sidebar, métricas + la frase de veredicto, y pestañas (Descomposición
/ Composición total / Serie histórica / Alertas / Detalle) en vez de todo apilado.

## Plan de confirmación de carga

Después de la auditoría de arriba, el usuario volvió a probar con facturas reales:
las de luz entraron bien, pero las de Movistar fallaron de las tres formas a la vez
(error al leer, cuarentena, "faltan datos") -- y además la interpretación de los
datos resultaba confusa. El problema de fondo era que **el pipeline decidía solo**:
leía con Gemini, validaba, y escribía el resultado final sin que nadie viera nada
antes. Si algo salía mal, la factura terminaba en uno de tres callejones sin salida
distintos (cuarentena con un botón "reintentar" que no arreglaba nada; "necesita
datos", que mandaba a corregir de a un campo sin ver el PDF; o un mensaje que se
perdía al recargar la página).

Este plan trae el patrón de Klericó (`app/(app)/facturas/[id]/review-grid.tsx`): al
subir, la factura queda como un **borrador** editable, con el **PDF original al
lado**, y nada entra al análisis hasta que se confirma.

- **`core/pipeline.py::procesar_pdf`** ya no decide nada -- solo extrae con Gemini y
  deja SIEMPRE un `estado="borrador"` en `facturas`, sea cual sea su calidad
  (aritmética rota, sin período/servicio, o la extracción fallada del todo, que ahora
  deja un borrador VACÍO para completar a mano en vez de perderse). Nueva
  `confirmar_factura`: guarda como definitiva una factura ya editada -- re-homologa
  con las descripciones CORREGIDAS (no las de Gemini), valida aritméticamente y exige
  período+servicio, redundante a propósito con lo que la pantalla ya bloquea.
- **`core/almacenamiento.py`**: nuevo estado `"borrador"`, columnas `motivo_carga` y
  `texto_extraido` en `facturas`, y `listar_borradores`/`leer_borrador`/
  `descartar_borrador` -- la cola que lee la pantalla nueva. Ninguna consulta del
  análisis necesitó cambiar: todas ya filtraban `estado = 'aprobada'`.
- **`apps/segurplus/paginas/confirmar.py`** (nueva): el PDF a la izquierda (`st.pdf`,
  con fallback al texto extraído si no hay evidencia guardada) y todo editable a la
  derecha -- cabecera, conceptos/impuestos/recargos/créditos en `st.data_editor`. El
  control aritmético corre EN VIVO mientras se edita, en castellano y línea por línea,
  más la doble lectura contra el total impreso en el PDF. El botón "Confirmar
  factura" queda bloqueado hasta que la aritmética cierra y hay período y servicio,
  con el motivo exacto de por qué no se puede todavía.
- **`apps/segurplus/paginas/cargar.py`** ahora dice "N factura(s) listas para
  confirmar" y enlaza a la pantalla nueva, en vez de mostrar cinco categorías
  distintas. **`revision.py`** queda solo para lo YA aprobado (corregir una cabecera
  o rechazar algo que resultó mal después) -- deja de ser el lugar donde se arregla
  una carga. **`cuarentena.py`** pasa a ser un archivo histórico de solo lectura:
  `procesar_pdf` ya no le escribe filas nuevas.

Esto resuelve que una factura que no entra deje de ser un callejón sin salida -- se
corrige a mano, con el PDF a la vista, en vez de perderse. **No resuelve** que Gemini
lea mejor una factura de Movistar de entrada -- eso necesita una factura real para
diagnosticar y sigue dependiendo de B-3 (nunca verificado contra la API real).

## Vocabulario claro en el tablero

Segunda parte del pedido de arriba ("la interpretación de los datos es confusa"): la
causa de fondo era que identificadores internos del sistema se mostraban tal cual al
usuario, en varios lugares a la vez -- los 7 tipos de alerta (`salto_de_cantidad`,
`precio_sobre_ipc`, ...), los 9 `concepto_normalizado` homologados (`abono_movil`,
`consumo_energia`, ...) y los 4 estados de un caso (`en_analisis`, ...), tanto en el
tablero como en el Excel exportado al cliente.

- **`core/analisis/alertas.py::etiqueta_tipo`** y **`apps/segurplus/paginas/casos.py`
  ::ETIQUETAS_ESTADO_CASO** traducen los slugs de sistema a texto humano, sin tocar la
  clave real (`Alerta.tipo`, `ESTADOS_CASO`).
- **`data/etiquetas_conceptos.yaml`** (nuevo, leído por
  `core.analisis.agregacion.etiqueta_legible`) traduce los 9 conceptos homologados --
  antes, `etiqueta_legible` solo capitalizaba texto SIN homologar y dejaba los
  homologados (ej. `abono_movil`) crudos. A propósito FUERA de `data/conceptos/`: ese
  directorio lo combina entero `core.analisis.diccionario.cargar_diccionario(None)`
  como `{concepto: [alias]}`, y este archivo es `{concepto: "texto"}` -- adentro,
  corrompía el diccionario real agregando cada carácter del texto como alias (bug
  real, encontrado al escribir el test de esta función).
- **`apps/segurplus/paginas/evolucion.py`**: tabs "Descomposición"/"Composición
  total" → "Precio vs. cantidad"/"Todos los conceptos"; leyenda "Efecto
  cantidad/precio/cruzado" → "Por cantidad"/"Por precio"/"Efecto combinado" (ahora en
  `core.analisis.variacion`, para que el gráfico, la tabla Detalle y el Excel digan
  siempre lo mismo); `help=` corto en "Variación real"/"Inflación del período".
- **`core/reportes/excel.py`**: mismas traducciones en el reporte que llega al
  cliente -- no tenía sentido resolverlo solo en el tablero.
- **Bug encontrado de paso**: `core.almacenamiento.metricas_por_proveedor` contaba
  "Facturas cargadas" con un `count(*)` sin filtrar `estado`, así que desde que existe
  el borrador (arriba) incluía facturas todavía sin confirmar. Fix: excluye
  `estado = 'borrador'`. "En cuarentena" quedó relabeleado como "(histórico)" en
  `sin_clasificar.py` y el Excel -- ya no recibe filas nuevas desde el plan anterior.

## Auditoría D-1 a D-25 y sus correcciones

`docs/auditoria-2026-09-confirmacion.md` revisó los dos planes de arriba (confirmación de
carga + vocabulario) sin corregir nada; los 25 hallazgos ya están corregidos, en bloques
separados sobre la misma rama. Los más importantes:

- **Un concepto fantasma "nan" podía entrar al análisis** (D-1): una fila del editor sin
  completar llegaba con celdas `NaN`, que `or` no atrapa -- `conceptos_desde_filas`/
  `montos_desde_filas` ahora la descartan con helpers NaN-safe.
- **La traducción de conceptos no aplicaba a los consumos con unidad** (D-2): la clave real
  de un consumo medido lleva el sufijo `" [unidad]"` -- `etiqueta_legible` ahora lo separa
  antes de traducir. De paso se cacheó por `mtime` (D-16, antes se re-leía el YAML en cada
  fila) y se tradujo el concepto también dentro de los mensajes de alerta (D-13).
- **`confirmar_factura` pasó a ser la autoridad sobre estado y procedencia** (D-5, D-6,
  D-7): exige que exista un borrador antes de confirmar (cierra la doble confirmación),
  preserva `ruta_evidencia`/`respuesta_extraida`/`modelo_extraccion`/`texto_extraido`/
  `motivo_carga` desde la base en vez de confiar en lo que arme el formulario, y registra
  las correcciones de cabecera (`correcciones_factura`, D-4) y una acción "confirmacion"
  distinguible de "carga" en el historial (D-22).
- **Avisos de incompletitud que faltaban**: Evolución y el Excel ahora dicen cuántas
  facturas de un servicio siguen sin confirmar (D-3); `cargar.py` separa los borradores
  que llegaron con un problema de los que salieron limpios (D-8).
- **Nombres de hoja del Excel cambiados sin aviso (D-21)**: `"Descomposición"` →
  `"Precio vs. cantidad"`, `"Cuarentena"` → `"Cuarentena (histórico)"` -- si tenés una
  plantilla propia que las referencia por nombre, hay que actualizarla. Sin consumidores
  automatizados conocidos dentro del repo.
- El resto (D-9 a D-12, D-14, D-17 a D-20, D-23 a D-25) son mejoras de robustez, wording y
  permisos de menor alcance -- ver el propio documento de auditoría para el detalle de
  cada uno. **D-23** (qué rol mínimo puede confirmar una factura) se dejó como decisión
  consciente: `cargador` sigue pudiendo confirmar.

## Rediseño de septiembre 2026: medir la lectura, sacar Streamlit, hacerlo entendible

El usuario reportó que la herramienta no sirve para lo que se construyó: cuesta que lea
las facturas (incluso las de luz y gas), y cuando las lee bien el análisis no se entiende.
Plan en curso, con tres decisiones tomadas: (1) el resultado tiene que ser un texto en
castellano que explique el mes, en una sola pantalla sin pestañas; (2) la herramienta sale
de Streamlit a una web propia en Python (FastAPI + HTML plano, reusando `core/` tal cual);
(3) antes de cambiar de proveedor de IA, medir con un banco de facturas reales -- cambiar
sin medir es adivinar dos veces.

**Bloque 1 -- banco de medición** (`docs/banco_extraccion.md`, `scripts/banco_extraccion.py`):
convierte "¿lee bien las facturas?" en un número. Compara, campo a campo, lo que un
proveedor extrajo contra una verdad de referencia tipeada a mano mirando el PDF real
(nunca generada por un modelo). La verdad de referencia vive en `data/reales/banco/`
(zona restringida, nunca se commitea). Con las primeras 4 facturas reales usadas para
armarla (2 de luz, 2 de gas), confirmó algo importante: el layout a dos columnas de las
facturas de gas es tan ambiguo que ni con las coordenadas x/y exactas de cada línea de
texto se puede reconstruir con certeza qué valor corresponde a qué concepto -- ni un
humano con precisión de punto puede desentrañarlo, solo el total y el subtotal quedan
100% verificables ahí. **B-3 (el prompt reforzado para impuestos con dos montos) sigue sin
poder verificarse contra la API real**: este entorno de desarrollo no tiene
`GEMINI_API_KEY`. Es el bloqueante real para avanzar con evidencia -- correr el banco con
una clave real es el próximo paso concreto.

**Bloque 2 -- capa de proveedores intercambiable** (`core/extraccion/proveedores/`):
agrega un adaptador genérico para cualquier proveedor compatible con la API de OpenAI
(`openai_compat.py`, sirve a Groq/Cerebras/SambaNova/OpenRouter con el mismo código) y un
render de PDF a PNG (`render.py`, PyMuPDF, ver ADR-004) para los que solo aceptan imagen.
Del `Informe Técnico Semanal de APIs Gratuitas de Modelos de Lenguaje` (18/09/2026): sus
cinco recomendaciones principales son modelos de **texto**, no pueden leer una factura --
de los proveedores gratis investigados, **solo Groq tiene modelos multimodales de verdad**
en el tier gratis (Llama 4 Scout, Qwen 3.6), así que es el único candidato nuevo real para
`data/extraccion.yaml`. **El pipeline real (`core/pipeline.py::procesar_pdf`) todavía NO
usa esta cascada** -- sigue llamando a Gemini directo, a propósito: sin una clave de Groq
para medirlo contra el banco, recablear el camino real de carga sería la misma adivinanza
que este plan existe para evitar. La cascada (con reintento y backoff) está lista y
probada con proveedores simulados (21 tests, sin pegarle a ninguna API real) para el día
que haya una clave.

**Bloque 3 -- que el modelo también clasifique el concepto**: cada línea de concepto del
JSON Schema ganó `concepto_sugerido`, con el mismo mecanismo de `enum` que ya usa
`servicio` -- la lista de conceptos normalizados conocidos (`cargar_diccionario()` de
TODOS los servicios, ya que el modelo todavía no sabe con certeza de qué servicio es la
factura). El modelo elige uno de esos valores o `null` si no está seguro; nunca decide
nada por sí solo -- la homologación por similitud de texto
(`core/analisis/homologacion.py`) sigue siendo la que corre en `confirmar_factura`, sin
tocar. Defensivo: un `concepto_sugerido` fuera del enum conocido (un proveedor que no
respete el schema tan estricto como Gemini) se descarta a `None` en `factura_desde_json`,
en vez de dejar pasar un slug inventado. El banco (Bloque 1) ahora también mide esto:
`data/reales/banco/*.yaml` ganó un campo opcional `concepto_correcto` por línea (solo
donde es inequívoco -- las líneas ambiguas de gas, ver Bloque 1, se dejan sin él a
propósito) y `comparar_factura` reporta qué % de esas líneas el proveedor clasificó bien.
**Deliberadamente fuera de este bloque**: usar `concepto_sugerido` como respaldo dentro
de `confirmar_factura` cuando Dice no encuentra nada, y mostrar un aviso cuando Dice y el
modelo no coinciden. Preservar la sugerencia del modelo a través del editor de
"Confirmar carga" (donde el usuario puede corregir la descripción, agregar o borrar
líneas) necesita o una columna nueva en la base para guardarla desde el borrador, o un
emparejamiento por descripción/importe entre el borrador y lo editado -- ninguna de las
dos formas es segura de hacer bien sin las pantallas nuevas del Bloque 4, que van a
reemplazar `confirmar.py` de todos modos. Construir esa plomería sobre una pantalla que
se va a borrar sería trabajo tirado.

## Falta (siguiente trabajo)

- **Auditoría del piloto operativo (A-49 a A-59) — resuelta**: ver
  `docs/auditoria-2026-09-piloto.md`, con el criterio de cada corrección marcado en el
  propio documento. Los tres bloqueantes ya no lo son: `conectar()` ignora `DATABASE_URL`
  cuando recibe una ruta explícita (A-49), y tanto el rechazo/corrección de cabeceras
  (A-50) como la re-homologación con una factura sin servicio detectado (A-51) tienen
  salida sin necesitar `requiere_revision`. No queda ningún hallazgo propio de esa
  auditoría pendiente antes de cargar facturas reales.
- **Fase 6** — motor por reglas 100% local, solo si hace falta (ver punto de decisión
  pendiente en `docs/PLAN.md`).
- **Primera carga con facturas reales -- probada, cinco de seis hallazgos resueltos**:
  ver "Primera carga con facturas reales (B-1 a B-6)" arriba. Falta **B-3** (el prompt
  reforzado para líneas de impuesto con dos montos): no se pudo verificar contra la API
  real en este entorno -- correr `scripts/probar_extraccion.py` sobre las facturas
  reales de luz y gas es el paso que falta antes de confiar en que quedó resuelto de
  verdad. Con Movistar/Metrotel ("no identifica nada" en la primera prueba) probablemente
  se solapa con B-3 o con un formato de factura todavía no visto -- conviene reintentar
  después de confirmar B-3, y si sigue fallando, correr `probar_extraccion.py` sobre esas
  también.
- `data/conceptos/*.yaml` tiene los alias obvios para arrancar (Movistar,
  `servicio_telefonia`; luz, `energia`) -- se completa con la pantalla "Sin clasificar" a
  medida que se carguen facturas de cada proveedor. El umbral de homologación
  (`data/homologacion.yaml`) sigue siendo un valor conservador (0,60) elegido sin
  datos -- con el score persistido, se puede calibrar con evidencia (histograma en
  "Sin clasificar") en cuanto haya volumen real.
- Hallazgo diferido, todavía sin facturas reales que lo ejerciten: A-26 (`_parsear_monto`
  con separadores de miles mezclados). A-27 (alerta de período faltante asumía
  periodicidad mensual) se volvió real con las facturas de gas y quedó **resuelto** como
  B-6, arriba.
- **Postgres real en producción — resuelto**: la app está conectada a un proyecto
  Supabase gratuito ya existente, en su propio schema (`segurplus`) con un rol de base
  dedicado (`segurplus_app`, permisos acotados a ese schema, `search_path` propio) para
  no interferir con los otros productos que viven en ese mismo proyecto. Verificado en
  el propio deploy: las tablas se crean solas al conectar y la app funciona de punta a
  punta. El camino `ConexionPostgres` ya tiene un test que lo ejercita de verdad
  (`tests/test_conexion_postgres_real.py`, marcado `red_real`, se salta salvo que exista
  `TEST_DATABASE_URL` -- ver el README), además del test estático que revisa el SQL en
  busca de `%` sueltos (`tests/test_conexion_postgres.py`).
- **Evidencia del PDF no durable entre reinicios** (carpeta local por defecto) y **sin
  backups verificados** del proveedor gratuito elegido -- ver las limitaciones
  conocidas del addendum de ADR-003. Aceptable mientras se prueba, a revisar antes de
  depender de esto en el día a día.
- **Migración a Vercel evaluada y descartada**: el plan gratuito de Vercel prohíbe uso
  comercial, lo que hubiera roto el "costo cero" ya prometido. Se decidió quedarse en
  Streamlit y mejorar la estética ahí. Si alguna vez se reconsidera, la arquitectura
  recomendada es Next.js + `core/` en Python detrás de una API, no portar la lógica a
  TypeScript (son ~2400 líneas con más de 250 tests y revisión financiera).
