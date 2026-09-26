# Auditoría de prueba integral — 2026-09-25

## Objetivo

Probar Segurplus de punta a punta contra Gemini real (no mockeado), con facturas de
varios tipos de servicio argentinos, siguiendo el circuito completo: subir → extraer
→ borrador editable → doble lectura del total → control aritmético → confirmar → `/ver`
→ Excel. Este documento es la continuación directa de
`docs/auditoria-prueba-facturas-reales-2026-09-25.md` (bloqueada ayer por falta de
`GEMINI_API_KEY` local) y usa las mismas fuentes candidatas que esa auditoría ya había
identificado.

## Criterio de datos

- No se leyó ni se usó `data/reales/` en ningún momento.
- No se incorporó ningún documento real de una persona. Los PDFs "reales" de esta
  prueba son muestras ilustrativas oficiales publicadas por el propio proveedor
  (Movistar, MetroGAS, Edesur), con datos ficticios o redactados por el proveedor
  mismo (nombre pixelado en el caso de Movistar; "datosFicticios" en el nombre de
  archivo de MetroGAS; nombre "GONZALEZ JUAN" y CUIT con dígito verificador inválido
  en Edesur).
- El resto son facturas sintéticas generadas con `docs/fixtures/generar_fixtures.py`
  (ReportLab), ya versionadas en el repo.
- Ningún PDI ni identificador real aparece en este documento.

## Entorno

- Commit probado: `57352ab` (merge en `codex/cierre-auditoria-piloto` que trae los
  arreglos AR-03/AR-05/AR-06 de `codex/auditoria-piloto-web` — ver sección
  "Resolución de merge" más abajo).
- Servidor local: `uvicorn web.app:app`, `SEGURPLUS_DEV=1`, DuckDB en un archivo
  temporal fuera del repo (`core.almacenamiento.RUTA_BASE` redirigida, mismo patrón
  que `tests/web/test_app.py`), `EVIDENCIA_DIR` en un directorio temporal fuera del
  repo. **No se tocó `data/reales/`.**
- `GEMINI_API_KEY`: configurada por el usuario para esta sesión únicamente (variable
  de entorno del proceso del servidor, nunca escrita a disco ni mostrada en este
  documento).
- Modelo: el configurado en `data/extraccion.yaml` (`gemini-3.6-flash`).
- Controles locales antes de empezar y al cerrar: `ruff check .` → sin errores;
  `pytest -q` → 597 passed, 3 skipped (limpio; dos tests habían fallado por timing en
  una corrida anterior bajo carga y pasaron limpio en la corrida final, ver hallazgo
  PI-08).

## Nota sobre la resolución del merge

Al traer `codex/auditoria-piloto-web` (mis arreglos AR-03/05/06) sobre
`codex/cierre-auditoria-piloto` (el cierre de auditoría que subió Codex en paralelo,
commit `999bf42`), `core/pipeline.py` tuvo un conflicto real: los dos lados
reescribieron el mismo bucle de reintento de Gemini de formas distintas. Se combinaron
a mano ambas mejoras en vez de descartar una:

- La reserva atómica de cuota de Codex (`reservar_intento_gemini` /
  `finalizar_intento_gemini`, con lock) reemplaza al chequeo no atómico
  (`llamadas_ultima_hora` + insert aparte) que traía mi versión — la de Codex cierra
  una condición de carrera real entre cargas concurrentes.
- Los mensajes traducidos de mi versión (`_mensaje_operativo_gemini`,
  `_mensaje_tope_gemini`, `core/pipeline.py:121-148`) reemplazan al mensaje crudo
  `f"No se pudo leer con Gemini: {exc}"` que traía la versión de Codex.
- El guardado con limpieza de evidencia huérfana de Codex
  (`_guardar_borrador_con_evidencia`) se mantuvo, pero ahora recibe el mensaje
  traducido en vez del crudo.

La función `reintentar_extraccion_borrador` (que ya combinaba ambos patrones sin
conflicto) sirvió de referencia para confirmar que la combinación es consistente con
el resto del código. **Nota post-mortem:** el import que quedó sin usar
(`llamadas_ultima_hora`) se corrigió localmente pero no se volvió a agregar al commit
antes de pushear -- el merge original (`57352ab`) quedó con un warning de `ruff` que
recién se corrigió en un commit aparte al notarlo durante esta sesión. Ver el commit
`fix: sacar import sin usar que quedó del merge (ruff F401)`.

## Hallazgos

### PI-01 — P0 — El parser de importes del formulario rechaza precios con más de 2 decimales

**Estado:** confirmado con una factura real (MetroGAS, muestra oficial con datos
ficticios).

`web/app.py:917-935` (`_num_desde_texto`, usada por `_factura_desde_form` en
`web/app.py:944`, que alimenta tanto "Guardar cambios sin confirmar" como "Confirmar
factura") valida los importes sin coma con la regex `-?\d+(?:\.\d{1,2})?`
(`web/app.py:928`) — como máximo 2 dígitos decimales.

La factura de MetroGAS trae un concepto de consumo con precio unitario
`18.2888` (4 decimales; precisión habitual en tarifas argentinas de gas/luz por
unidad, donde el precio por m³/kWh es chico y se multiplica por consumos grandes).
Gemini lo extrajo correctamente — coincide exactamente con el valor impreso en el
PDF. Pero al apretar "Guardar cambios sin confirmar" el formulario devuelve:

> No se pudo leer el formulario: Precio unitario (línea 2): importe inválido: '18.2888'

Y la página se vuelve a renderizar con **todos los campos vacíos** (aunque el
borrador en la base de datos no se pierde — se confirmó recargando la página con un
GET limpio, que muestra los datos originales intactos). El usuario ve un mensaje
técnico y un formulario aparentemente vacío, sin indicación de que sus datos siguen
ahí.

**Impacto:** cualquier factura real con un precio unitario de 3 o más decimales
(común en gas y electricidad) no se puede guardar ni confirmar desde la interfaz web,
aunque la extracción haya sido perfecta y la aritmética cierre. Bloquea el circuito
completo para ese tipo de facturas.

**Criterio de cierre:** la regex de `_num_desde_texto` (las dos ramas, con y sin coma,
`web/app.py:924` y `web/app.py:928`) acepta más de 2 decimales — o mejor, delega en
`core.ingesta.pdf_texto.parsear_monto`, que ya maneja separadores argentinos, en vez
de reimplementar el parseo con una regex propia. Además, el error de reconstrucción
del formulario no debería vaciar visualmente los datos ya guardados.

### PI-02 — P1 — El control aritmético no distingue "el modelo inventó un número" de "el modelo leyó bien"

**Estado:** confirmado con `docs/fixtures/sintetico/rota_importe_no_cierra.pdf`.

Esta fixture existe específicamente para probar que la validación aritmética detecta
un comprobante donde el importe impreso no cierra con cantidad × precio (impresos:
5 líneas × $100,00 = $500,00, pero el importe impreso es $800,00 — un error real del
emisor). El PDF no tiene ambigüedad: el precio unitario impreso es 100,00.

Gemini no reportó el precio impreso. Reportó **precio_unitario = 160**, un valor que
no aparece en ningún lugar del PDF, calculado hacia atrás para que
5 × 160 = 800 (el importe impreso, también reportado). El resultado: `core/extraccion/
validacion.py` ve una factura internamente consistente (cantidad × precio = importe,
subtotal y total cuadran) y muestra "La aritmética cierra" sin ninguna advertencia —
el botón "Confirmar factura" queda disponible sin fricción.

**Impacto:** esto es exactamente lo que el CLAUDE.md del proyecto llama "la regla que
separa esto de confiar en la IA" — el modelo debe **extraer**, nunca **decidir** un
número. Acá el modelo decidió un precio unitario que no está impreso en ningún lado,
y el control aritmético determinístico no tiene forma de distinguirlo de una lectura
fiel, porque solo verifica consistencia interna entre los campos que el propio modelo
reportó, no fidelidad contra el documento. Para un campo sin doble lectura
independiente (como si tiene el total, vía `pdfplumber`/regex), no hay red de
seguridad. Un usuario que no mire la factura con atención podría confirmar un precio
unitario fabricado.

**Criterio de cierre:** no hay un fix mecánico simple (el total sí tiene doble lectura
por texto/regex; replicar eso para cada línea de conceptos requeriría layout
posicional, que es justo el problema que ya está documentado como difícil para gas en
`docs/banco_extraccion.md`). Como mínimo, vale la pena registrar esto como limitación
conocida y explícita en `docs/decisiones/ADR-001-lectura-de-facturas.md` o
`docs/estado.md`, ya que hoy el documento da a entender que el control aritmético es
una garantía más fuerte de lo que es.

### PI-03 — P1 — Bonificaciones que ya vienen netas en el "subtotal" impreso rompen la identidad Σconceptos = subtotal

**Estado:** confirmado con la muestra oficial de Movistar (factura móvil ilustrativa,
3 páginas).

La factura trae una bonificación ("Bonificacion Movistar Movil", $9.914,71) que en el
diseño original de la factura ya está **restada** antes de la columna "TOTAL SIN
IMPUESTOS" (el subtotal impreso, $13.302,25). Gemini clasificó correctamente la
bonificación como **crédito** ($9.914,71) — no como concepto negativo, que es lo
correcto según el esquema. El problema es el campo `subtotal`: Gemini copió el valor
ya neteado que aparece impreso ($13.302,25) en vez de derivarlo como
`Σ conceptos` ($23.217, el abono antes de la bonificación), que es la identidad que
exige `core/extraccion/validacion.py` (`CLAUDE.md`: "Σ conceptos = subtotal").

Resultado: "La suma de los conceptos no coincide con el subtotal" y "Subtotal +
impuestos + recargos - créditos no coincide con el total" — ambos en rojo. Pero si
`subtotal` hubiera sido 23.217 (= Σ conceptos) en vez de 13.302,25, todo hubiera
cerrado: 23.217 + 2.797,74 (impuestos) - 9.914,71 (crédito) = 16.100,03 ≈ $16.099,99
impreso (diferencia de centavos por redondeo, dentro de tolerancia).

**Impacto:** esta factura, correctamente itemizada por el modelo, no se puede
confirmar sin corrección manual, aunque los tres números que importan (concepto,
crédito, total) sean todos correctos — el único campo mal ubicado es `subtotal`. Es
un patrón que probablemente se repite en cualquier proveedor que imprima el subtotal
ya neto de bonificaciones (común en telefonía móvil con planes promocionales).

**Criterio de cierre:** el prompt de extracción (`core/extraccion/gemini.py`) podría
ser más explícito sobre que `subtotal` es siempre la suma bruta de `conceptos`, nunca
un valor impreso que ya reste créditos -- con un ejemplo en el prompt de una factura
con bonificación, similar al que ya existe para las líneas de impuesto con doble
monto (ver hallazgo B-3 de auditorías previas).

### PI-04 — Confirmado — AR-03 (PDF sin capa de texto) está resuelto para los tres proveedores probados

**Estado:** positivo, con evidencia nueva.

Las mismas fuentes que ayer fallaban en AR-03 (rechazadas antes de llegar a Gemini
por no tener capa de texto) hoy se procesan:

- **MetroGAS** (imagen "Factura_modelo_ClienteR_datosFicticios"): extracción
  correcta y completa -- emisor, CUIT, N° de comprobante, período de liquidación,
  9 líneas de impuestos/tasas municipales y provinciales, subtotal y total, todos
  coincidentes con la imagen. Bloqueada después por PI-01, no por AR-03.
- **Edesur** (imagen "factura_desglose_2026"): llegó a Gemini (no rechazada por falta
  de texto), Gemini devolvió 503 por saturación transitoria -- ver PI-05, confirmación
  positiva aparte.
- **Movistar** (3 páginas, imagen "img-factura-unimovil"): extracción correcta y
  completa -- emisor, CUIT, N° de factura, período de facturación (ciclo, no mes
  calendario), fecha de emisión y vencimiento, concepto, impuestos (incluido "Ley
  27.430 Impuestos Internos", poco común), y el crédito de bonificación. Bloqueada
  después por PI-03, no por AR-03.

### PI-05 — Confirmado — Los mensajes traducidos de error de Gemini funcionan con un fallo real, no solo simulado

**Estado:** positivo.

La factura de Edesur disparó un 503 real de Gemini (probablemente por el tamaño/
complejidad de la imagen, 1.8 MB). El borrador vacío resultante mostró:

> Gemini está temporalmente saturado. Reintentá desde este borrador más tarde.

en vez del error crudo de la librería `google-genai`. Esto es exactamente el
comportamiento que buscaba el hallazgo AR-06 de ayer, y funcionó con un fallo real de
la API, no solo con el mock de los tests.

### PI-06 — Confirmado — El botón de reintentar Gemini está correctamente restringido a modo producción, en el cliente y en el servidor

**Estado:** positivo, con una limitación de cobertura anotada.

En `SEGURPLUS_DEV=1` (sin `SEGURPLUS_PRODUCTION=1`), el borrador de Edesur no mostró
el botón "Reintentar lectura con Gemini" -- correcto, porque `pipeline.procesar_pdf`
solo registra la clasificación de consentimiento (`apto_gemini`) cuando
`SEGURPLUS_PRODUCTION=1` (`core/pipeline.py`), y `reintentar_extraccion_borrador`
exige esa clasificación. Se confirmó que la restricción también aplica llamando la
ruta `POST /revisar/{hash}/reintentar` directamente (sin pasar por el botón): devuelve
"Este borrador no admite un reintento con Gemini." -- es decir, no es solo una
condición de plantilla, el endpoint la exige también.

**Limitación de cobertura:** esto significa que el flujo de reintento exitoso (un
segundo intento que sí extrae bien) no se pudo ejercitar en este entorno local sin
`SEGURPLUS_PRODUCTION=1` + bucket S3 + Postgres real, que están fuera del alcance de
esta prueba. Queda pendiente para una verificación en Render.

### PI-07 — Confirmado — Detección de duplicados, alertas → Casos, y Sin clasificar

**Estado:** positivo.

- Volver a subir `energia_2026-07.pdf` (ya confirmada) no generó un borrador nuevo --
  la detección de duplicados por hash lo descartó silenciosamente.
- Las 4 alertas generadas por las comparaciones de energía y telefonía (precio sobre
  IPC ×2, recargo, salto de cantidad) aparecieron correctamente en `/casos`, cada una
  con su flujo de estado (abierto/en_análisis/resuelto/descartado). Esto contradice
  -- en el sentido de que ya está resuelto -- el hallazgo N-02 de la auditoría del
  24/09 ("las alertas de comparación nunca llegan a Casos").
- `/sin-clasificar` no mostró errores con las facturas aprobadas en esta sesión (todos
  los conceptos matchearon el diccionario de homologación).

### PI-08 — Informativo — Un test de Streamlit (legacy) es sensible a la carga de la máquina

**Estado:** no reproducible de forma consistente; probablemente no es una regresión.

`tests/apps/test_sin_clasificar_app.py::test_pagina_renderiza_sin_errores` falló una
vez (`RuntimeError: AppTest script run timed out after 3(s)`, un timeout fijo de
Streamlit `AppTest`) durante una corrida de la suite completa bajo carga pesada
(justo después de instalar dependencias y con varias corridas de pytest encadenadas).
En una corrida posterior, en las mismas condiciones de código, pasó sin problema (597
passed, 0 failed). El archivo que ejercita (`apps/segurplus/paginas/sin_clasificar.py`)
no fue tocado por ningún cambio de esta sesión. Como `apps/segurplus/` está en camino
de baja (CLAUDE.md: se borra cuando `web/` complete el recorrido manual), no amerita
investigación más profunda, pero vale la pena que quien lo vea de nuevo sepa que ya
pasó antes.

### PI-09 — Informativo — Lógica de traducción de errores de Gemini duplicada entre `core/pipeline.py` y `web/app.py`

**Estado:** detectado durante la resolución del merge, no un bug de comportamiento.

`web/app.py:658-683` (dentro de `_contexto_detalle`) reimplementa, con sus propios
`if "503" in detalle / "429" in detalle / ...`, la misma clasificación de errores que
ahora vive en `core/pipeline.py:121-135` (`_mensaje_operativo_gemini`, agregada en
esta sesión para PI-05/AR-06). Como `motivo_carga` ya llega traducido desde
`core/pipeline.py` antes de guardarse, el bloque de `web/app.py` hoy opera sobre un
mensaje que ya no es el crudo de Gemini -- no fue reproducible ningún caso donde esto
produjera un mensaje incorrecto (el `else: aviso_carga = motivo_carga` cubre el caso
general), pero es lógica duplicada que puede divergir con el tiempo. Candidato a
limpieza: que `web/app.py` use directamente `motivo_carga` como `aviso_carga` sin
reclasificarlo.

### PI-10 — P1 — Los PDFs de fixtures no están marcados como binarios; `core.autocrlf` los corrompe en Windows

**Estado:** confirmado.

Al correr `python docs/fixtures/generar_fixtures.py` en este entorno (Windows,
`core.autocrlf=true`) y comparar el resultado contra lo que ya está commiteado, los 6
PDFs salieron distintos byte a byte -- no por un cambio de contenido, sino porque el
committeado tiene saltos de línea `\n` dentro del stream binario del PDF y el
regenerado localmente tiene `\r\n`. El repo no tiene `.gitattributes`, así que ningún
patrón marca `*.pdf` (ni `*.duckdb`, ni `*.parquet`) como binario -- quedan sujetos a
la normalización de fin de línea de línea de Git como cualquier archivo de texto.
`git check-attr -a` sobre uno de estos PDFs confirma que Git le aplica un diff driver
de texto (`astextplain`, para mostrar diffs legibles) pero no lo excluye de la
conversión CRLF/LF.

**Impacto:** cualquier persona en Windows con `core.autocrlf=true` (la config
recomendada por Git for Windows) que regenere las fixtures y las commitee va a
introducir una corrupción silenciosa -- el PDF puede dejar de abrir bien, o
`git diff`/`git status` van a mostrar cambios espurios en un archivo que no cambió de
contenido real. Se descartó la regeneración local de esta sesión (`git checkout --
docs/fixtures/sintetico/`) para no introducir ese ruido.

**Criterio de cierre:** agregar un `.gitattributes` con al menos
`*.pdf binary`, `*.duckdb binary`, `*.parquet binary` (y cualquier otro binario del
repo, como las imágenes que pueda tener `docs/fixtures/`).

## Matriz de ejecución

| Documento | Ingesta PDF | Extracción | Doble lectura | Aritmética | Confirmación | Resultado |
|---|---:|---:|---:|---:|---:|---|
| Energía, sintética, jul+ago 2026 | Pasa | Pasa | Pasa (con texto) | Cierra | Confirmada ×2 | Sin hallazgos; `/ver` y Excel verificados a mano |
| Gas, sintética, jul 2026 | Pasa | Pasa | Pasa (con texto) | Cierra | Confirmada | Sin hallazgos |
| Telefonía, sintética, jul+ago 2026 (con recargo) | Pasa | Pasa | Pasa (con texto) | Cierra | Confirmada ×2 | Sin hallazgos; `/ver` y alertas verificados a mano |
| `rota_importe_no_cierra.pdf` (sintética, a propósito rota) | Pasa | Precio fabricado (PI-02) | Pasa (con texto) | Cierra igual (falso positivo) | No confirmada (dejada así a propósito) | PI-02 |
| MetroGAS, muestra oficial (imagen, sin texto) | Pasa (AR-03 ok) | Correcta | Manual (sin texto) -- verificado a mano | Cierra | Bloqueada por PI-01 al guardar | PI-01, PI-04 |
| Edesur, muestra oficial (imagen, sin texto) | Pasa (AR-03 ok) | 503 de Gemini | No llegó a extraer | No aplica | Borrador vacío, mensaje traducido | PI-04, PI-05, PI-06 |
| Movistar, muestra oficial (imagen, 3 páginas, sin texto) | Pasa (AR-03 ok) | Correcta | Manual (sin texto) -- no se completó | No cierra (PI-03) | No confirmada | PI-03, PI-04 |

## Seguimiento de hallazgos anteriores

| ID anterior | Descripción | Estado hoy |
|---|---|---|
| AR-01 | No se podía correr extracción real local (sin API key) | Resuelto para esta sesión (API key provista por el usuario) |
| AR-02 | Deploy pide contraseña | No aplica -- esta prueba fue local, no contra Render |
| AR-03 | PDF sin capa de texto rechazado antes de Gemini | **Resuelto**, confirmado con 3 proveedores (PI-04) |
| AR-05 | 503 de Gemini deja un borrador vacío | Comportamiento esperado y correcto -- el borrador vacío es intencional (ver CLAUDE.md, nunca se pierde una carga) |
| AR-06 | Error crudo de Gemini mostrado en la UI | **Resuelto**, confirmado con un 503 real (PI-05) |
| N-02 | Alertas de comparación nunca llegan a Casos | **Resuelto** (PI-07) |
| N-12 | Monto argentino `1.234,56` no se parsea | Parcialmente -- el caso con coma decimal y separador de miles funciona (ver `tests/test_cierre_auditoria.py`), pero el caso sin coma con más de 2 decimales quedó roto (PI-01) |

## Qué falta (fuera del alcance de esta sesión)

- Agua, seguros, alquiler, ABL, internet/cable: no se generaron fixtures nuevas ni se
  encontraron muestras públicas para estos servicios en el tiempo de esta sesión.
- Casos borde pendientes: separador de miles mixto en un monto real (más allá de lo
  que ya cubre `tests/test_cierre_auditoria.py`), nota de crédito como documento
  completo (no solo como línea), factura en USD (debe rechazarse), 10 PDFs en una
  sola carga, archivo > 10 MB, archivo no-PDF.
- Verificación en Render (AR-02 seguía bloqueada): el comportamiento de producción
  (`SEGURPLUS_PRODUCTION=1`, bucket S3, Postgres) no se ejerció -- en particular, el
  flujo de reintentar-y-que-funcione (PI-06) y el bloqueo de subida sin clasificación
  expresa.
- Claro y Personal (telefonía): identificadas como fuentes candidatas por la auditoría
  de ayer, no llegaron a probarse en esta sesión por tiempo.

## Controles locales de regresión

- `ruff check .`: **All checks passed** (antes y después de esta sesión).
- `pytest -q`: **597 passed, 3 skipped** en la corrida final (una corrida intermedia,
  bajo carga pesada de la máquina, tuvo 2 fallas de timing no relacionadas con el
  código tocado; ver PI-08 para una de ellas -- las otras dos, en
  `tests/test_almacenamiento.py` y `tests/apps/test_cargar_app.py`, son una colisión
  de ID por timestamp de segundo completo en `_id_auditoria` bajo corridas rápidas
  consecutivas, reproducida solo en corrida completa y no en aislamiento; no se
  investigó más a fondo por no ser parte del alcance de esta auditoría, pero queda
  anotado como una fragilidad conocida del esquema de IDs de auditoría).
