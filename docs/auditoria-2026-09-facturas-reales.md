# Auditoría de Segurplus — correcciones de la primera carga con facturas reales (septiembre 2026)

**Este documento solo audita. No se corrigió nada acá.** Cada hallazgo trae la evidencia
que lo reproduce; las correcciones van en un plan aparte, después de revisar esto. Misma
regla que las tres auditorías anteriores (`docs/auditoria-2026-09.md`, A-1 a A-28;
`docs/auditoria-2026-09-rediseno.md`, A-29 a A-48; `docs/auditoria-2026-09-piloto.md`,
A-49 a A-59).

**Alcance**: los seis commits `282139f`..`ee671ad` (~1.300 líneas), que corrigieron los
hallazgos **B-1 a B-6** encontrados al intentar cargar facturas reales por primera vez.
Este documento tiene dos partes:

- **Parte 1** — los hallazgos **B-1 a B-6**, que motivaron esas correcciones y **ya están
  resueltos**, documentados acá por primera vez (ver C-16: 35 referencias del código los
  citaban contra un archivo que no los contenía).
- **Parte 2** — los hallazgos **C-1 a C-16**, nuevos, encontrados auditando esas mismas
  correcciones. **Ninguno está corregido.**

**Advertencia sobre esta auditoría**: acá audito código que escribí yo mismo, en la misma
sesión, pocas horas antes. Los dos hallazgos más graves (**C-1** y **C-2**) son
regresiones o agujeros de mi propio trabajo — C-1 es una alerta falsa que **rompió un caso
que antes funcionaba bien**, y se va a disparar en la próxima carga de facturas de luz.
Una auditoría del propio trabajo vale menos que una externa; conviene leerla sabiendo eso.

**Método**: lectura completa del diff de los seis commits, más **verificación ejecutando
código** — los bloques de evidencia de C-1, C-2, C-3, C-5, C-8, C-11 y C-14 son salida
real de Python, no interpretación de lectura.

**Motivo**: el usuario intentó cargar facturas reales (dos de luz de la Usina Popular y
Municipal de Tandil, dos de gas de Camuzzi), fallaron de tres formas distintas, se
corrigieron seis hallazgos, y antes de volver a probar conviene saber qué quedó mal de esa
corrección.

---

## Parte 1 — Los hallazgos que motivaron las correcciones (B-1 a B-6, resueltos)

Encontrados con cuatro facturas reales de un taller contable (empresa ficticia
"AGROMAQ S.A."), que **no se commitearon** (CLAUDE.md: nunca facturas reales en el repo) —
se reprodujo su forma en `docs/fixtures/sintetico/gas_2026-07.pdf`. Los síntomas que
reportó el usuario fueron tres a la vez: algunas facturas daban error, otras iban a
cuarentena, y otras decían "guardada" y después no aparecían en ningún lado.

### B-1 — Una factura que se guarda y el análisis nunca ve (crítico, resuelto)

Las dos facturas de luz imprimen su período como `Período: 07/2022` — mes/año, sin día, la
única información de período que traen. `_normalizar_fecha`
(`core/extraccion/esquema.py`) solo aceptaba `YYYY-MM-DD` y `DD/MM/YYYY`, así que
`periodo_desde` quedaba en `None`. Con eso la factura **validaba bien aritméticamente, se
guardaba, y la UI decía en verde "1 factura guardada y validada"** — pero todas las
consultas del análisis filtran `WHERE f.periodo_desde IS NOT NULL`, así que desaparecía.
Reproducido de punta a punta: `factura_valida: True`, una factura aprobada en la base, y
`totales_por_periodo()` devolviendo `{}`. Peor: `conceptos_sin_clasificar` **también**
filtra por período, así que tampoco aparecía en la pantalla que existe justamente para
encontrar lo que quedó afuera.

**Resuelto** (commit `282139f`), en tres capas: `_normalizar_fecha` acepta `MM/YYYY`,
`YYYY-MM` y `DD-MM-YYYY` (mes/año → primer día del mes); el pipeline ya no deja una
factura "aprobada" si le falta `periodo_desde` o `servicio` — queda `requiere_revision`
y `ResultadoPipeline` devuelve el estado nuevo `necesita_datos`, que `cargar.py` muestra
aparte y **no** como éxito; y `registrar_correccion` normaliza los campos de fecha con la
misma función, para que corregir a mano no reintroduzca el bug por otra vía.

### B-2 — El detalle de cálculo pegado a la descripción rompía la homologación (alto, resuelto)

Las facturas de luz facturan `Cargo Fijo (414,4500 / 30.5 x 8)` en julio y
`Cargo Fijo (455,8900 / 30.5 x 21)` en agosto. `cargo fijo` es un alias **exacto** de
`data/conceptos/comunes.yaml`, pero el detalle entre paréntesis hundía el score a 0,545 —
por debajo del umbral 0,60. **0 de 5 conceptos de la factura homologaban.** Y no era solo
un problema de clasificación: como el paréntesis cambia todos los meses, sin homologar
`agregacion.py::_clave` generaba una clave distinta por mes, así que la descomposición veía
"un concepto que desaparece" + "uno que aparece" con `efecto_precio = 0` — la misma
enfermedad de A-28, con otro patrón, ocultando un aumento de precio real.

**Resuelto** (commit `42d7107`): nueva `quitar_detalle_numerico()` en
`core/analisis/homologacion.py`, aplicada antes de comparar contra el diccionario y en
`agregacion.py::_clave`. Alias `energia` agregado a `data/conceptos/energia.yaml` (factura
ya vista, no inventado). **Ver C-5**: la guarda de esa función resultó menos conservadora
de lo que documenta.

### B-3 — Las líneas de impuesto traen dos montos (alto, corrección sin verificar)

Las facturas de luz imprimen base imponible **e** importe en la misma línea
(`I.V.A. (27,000%)  2.301,90  621,51`), y el prompt no decía cuál de los dos es
"importe". Si el modelo toma el primero en las 8 líneas de impuestos, la suma da ~18.000
en vez de ~1.394, el total no cierra, y la factura va a **cuarentena**. Las de gas son
peor: layout a dos columnas que `pdfplumber` entrega entrelazado.

**Corregido pero NO verificado** (commit `e9afaa8`): `PROMPT_EXTRACCION` explica que el
importe es el monto más a la derecha (con ejemplo numérico) y avisa sobre layouts a dos
columnas; `extraer_con_gemini` recibe también el texto plano ya extraído del PDF como
segunda vista. No se pudo probar contra la API real por falta de `GEMINI_API_KEY` en el
entorno de desarrollo — **sigue pendiente** correr `scripts/probar_extraccion.py` sobre
facturas reales. Ver **C-2**, que además rompe ese script para una clase de fallo.

### B-4 — El tope de llamadas protegía contra el número equivocado (medio, resuelto)

`llamadas_ultima_hora` contaba filas de `facturas` + `cuarentena`: un **proxy**, no las
llamadas reales. Una extracción que fallaba (`ExtraccionError`, ej. un 429/503 de Gemini)
no dejaba fila en ninguna de las dos tablas, así que **no contaba**, aunque sí había
gastado cuota real de la API. Reintentar un lote con varias facturas que fallan la
extracción podía agotar la cuota real de Gemini sin que este freno se activara nunca.

**Resuelto** (commit `1e99bce`): tabla nueva `intentos_gemini`, un registro por cada
llamada real (salga bien o mal); el tope se movió a
`data/operacion.yaml::max_llamadas_gemini_por_hora` (CLAUDE.md: nunca hardcodeado) y el
mensaje dice a qué hora reintentar. **Ver C-2, C-8, C-9 y C-11**.

### B-5 — Un fallo de extracción no dejaba rastro (medio, resuelto)

El mensaje de error vivía solo en la corrida de Streamlit en curso y se perdía al navegar
a otra pantalla: por eso el usuario no podía decir qué error había visto.

**Resuelto** (commit `1e99bce`): cada intento queda persistido con mensaje y la respuesta
cruda del modelo cuando existe; la página "Cargar facturas" suma un expander "Últimos
intentos de extracción fallidos". El README documenta `scripts/probar_extraccion.py` como
primer paso de diagnóstico. **Ver C-2, C-7 y C-10**.

### B-6 — La alerta de período faltante asumía periodicidad mensual (bajo, corrección defectuosa)

A-27 (diferido desde la primera auditoría) se volvió real: las facturas de gas son
bimestrales (`Periodo de Lectura: 01/07/2022- 31/08/2022`), y
`alertas_por_periodo_faltante` calculaba el período esperado como "un mes después del
anterior", así que alertaba en **todas** las comparaciones de un servicio bimestral aunque
no faltara nada.

**Corregido** (commit `4eb2686`) pasando a usar el `periodo_hasta` que declara la propia
factura anterior. **La corrección está mal: ver C-1**, que es el hallazgo más grave de
esta auditoría — rompe el caso mensual y no arregla el bimestral en el camino real de
datos.

---

## Parte 2 — Hallazgos nuevos (C-1 a C-16, ninguno corregido)

## Resumen ejecutivo

**Dos hallazgos altos conviene resolverlos antes de volver a cargar facturas reales.**

- **C-1 — la corrección de B-6 rompió un caso que antes funcionaba.** Combinada con B-1,
  la alerta "puede faltar cargar un período" ahora salta en **cada par de meses
  consecutivos** de una factura que imprime el período como `MM/AAAA` — exactamente las
  facturas de luz que motivaron todo el paquete. Y para el caso bimestral que B-6 venía a
  arreglar, tampoco lo arregla cuando el período viene en ese formato. Mis tests no lo
  agarraron porque usan un valor de `periodo_hasta` que el pipeline nunca produce.
- **C-2 — un JSON válido pero incompleto anula B-4 y B-5 a la vez.** `factura_desde_json`
  lanza `KeyError` fuera de `ExtraccionError`, así que esa clase de fallo deja al usuario
  con un mensaje que dice literalmente `'descripcion'`, no cuenta la llamada contra el tope
  (que es lo que B-4 existía para hacer) y no deja ningún rastro (que es lo que B-5 existía
  para hacer). Es el agujero que ese paquete venía a tapar, todavía abierto.

Después de esos, el patrón que más se repite es que **el circuito de reparación que creó
B-1 está a medio terminar**: C-3 (corregir `servicio` a mano puede romper la homologación),
C-4 (corregir una fecha mal escrita rompe la pantalla) y C-6 (el mensaje dice "corregilo"
pero además hay que aprobarla) están los tres sobre el camino "la factura quedó en
`necesita_datos` → la corrijo a mano", que es el primero que el usuario va a recorrer.

**Lo que está bien y conviene no tocar**: la normalización de `MM/AAAA` de B-1 resuelve el
problema de raíz y está bien acotada (sigue devolviendo `None` ante algo ambiguo, nunca
inventa un día). El estado `necesita_datos` es el criterio correcto: separar "se guardó" de
"se guardó y el análisis la ve" es justo la distinción que faltaba. La tabla
`intentos_gemini` es la estructura correcta para el tope y el diagnóstico, aunque le falten
detalles (C-9, C-10). Y `quitar_detalle_numerico` resuelve bien el caso que motivó B-2
—el score pasa de 0,545 a 1,0 y la clave de agrupamiento queda estable entre meses—
aunque su guarda haya quedado corta (C-5).

---

## Altos

### C-1 — La alerta de período faltante da falso positivo en CADA mes (regresión)

**Dónde**: `core/analisis/alertas.py::alertas_por_periodo_faltante`, contra
`core/extraccion/esquema.py::_normalizar_fecha`.

B-6 y B-1 se contradicen. B-1 normaliza un período impreso como `MM/AAAA` al **primer día
del mes**, y lo hace tanto para `periodo_desde` como para `periodo_hasta`. B-6 calcula el
próximo período esperado como `periodo_hasta_anterior + 1 día`. Para una factura que solo
imprime `Período: 07/2022` —el caso exacto que motivó B-1— eso da
`desde = hasta = 2022-07-01`, y el período esperado pasa a ser el **2 de julio**.
Verificado ejecutando:

```
--- luz mensual, la factura solo imprime el mes (hasta = mismo mes) ---
  meses consecutivos jul/ago/sep -> 2 alerta(s)
   ! Puede faltar cargar un período entre 2022-07-01 y 2022-08-01 (hueco de 31 días)
   ! Puede faltar cargar un período entre 2022-08-01 y 2022-09-01 (hueco de 31 días)

--- gas bimestral como lo normaliza B-1 (hasta = primer día del 2do mes) ---
  bimestres consecutivos jul-ago / sep-oct -> 1 alerta(s)
   ! Puede faltar cargar un período entre 2022-07-01 y 2022-09-01 (hueco de 62 días)
```

Dos cosas a la vez: **B-6 rompió el caso mensual**, que antes funcionaba bien (con
`_mes_siguiente`, meses consecutivos daban 0 alertas), **y tampoco arregla el bimestral**
cuando el período viene en `MM/AAAA`. La corrección solo funciona si el modelo devuelve
`periodo_hasta` como fecha completa de fin de mes (`31/08/2022`) — que es lo que permite la
factura de gas de Camuzzi con su "Periodo de Lectura", pero no la de luz.

**Por qué los tests no lo agarraron**: el test
`test_servicio_bimestral_con_periodo_hasta_no_alerta_nunca`
(`tests/analisis/test_alertas.py`) usa `date(2026, 8, 31)` —último día del mes— un valor
que el pipeline **nunca produce** para una factura `MM/AAAA`. El test codifica un supuesto
que el camino real de datos contradice, y por eso pasa en verde mientras el
comportamiento real está roto. Es la lección más importante de esta auditoría: testear con
los valores que **realmente** salen del pipeline, no con los que uno supone.

**Impacto inmediato**: al cargar las dos facturas de luz (07/2022 y 08/2022, consecutivas),
Evolución va a mostrar "puede faltar cargar un período" aunque no falte nada — y esa alerta
además se materializa como **caso operativo** (`sincronizar_casos_alertas`), así que deja
trabajo colgado en la pantalla de Casos.

**Nota de diseño para la corrección**: el problema de fondo es que `MM/AAAA` no dice nada
sobre la **cobertura** de la factura, y B-6 necesita exactamente eso. Normalizar
`periodo_hasta` al primer día del mes es una pérdida de información: para un período
mes/año, el fin de cobertura natural es el **último** día de ese mes, no el primero. Pero
tocar eso afecta a B-1, así que la corrección debería pensarse sobre los dos hallazgos a la
vez, no sobre uno solo.

### C-2 — Un JSON válido pero incompleto anula B-4 y B-5 a la vez

**Dónde**: `core/extraccion/esquema.py::factura_desde_json`, llamada desde
`core/extraccion/gemini.py::extraer_con_gemini` **fuera** de cualquier `try`.

`factura_desde_json` accede a `c["descripcion"]`, `c["precio_unitario"]` e `c["importe"]`
por índice directo. Si Gemini devuelve un JSON **sintácticamente válido** pero con un
concepto al que le falta una de esas claves, lanza `KeyError` — y como esa llamada está
fuera del `try` de `extraer_con_gemini`, no se envuelve en `ExtraccionError`. Verificado:

```
Gemini devuelve JSON VALIDO pero con un concepto sin descripcion
  KeyError SIN ENVOLVER -> escapa de ExtraccionError: 'descripcion'
```

Y de punta a punta en el pipeline:

```
procesar_pdf PROPAGO KeyError: KeyError('descripcion')  <-- no lo maneja el pipeline
  mensaje que veria el usuario en cargar.py: "'descripcion'"
llamadas contadas para el tope: 0
intentos fallidos registrados: 0
```

Tres consecuencias, las tres contra lo que B-4 y B-5 venían a resolver:

- El mensaje que ve el usuario es literalmente `'descripcion'` (el nombre de la clave que
  faltó) — no dice qué factura, ni qué pasó, ni qué hacer.
- La llamada **sí gastó** cuota de Gemini pero **no se cuenta** para el tope: es
  exactamente el tipo de fallo que B-4 existía para contar.
- **No queda ningún rastro** en `intentos_gemini`: es exactamente el tipo de fallo que B-5
  existía para registrar.

Además rompe `scripts/probar_extraccion.py`: al reescribirlo para llamar a
`extraer_con_gemini` (B-3), un `KeyError` ya no lo atrapa el `except ExtraccionError` y el
script termina con traceback **sin mostrar el JSON crudo** — justo el caso que su docstring
promete cubrir ("así se ve exactamente qué manda el modelo, incluso si `factura_desde_json`
fallara al interpretarlo"). La reescritura de B-3 eliminó la duplicación de lógica pero
perdió esa garantía.

**Por qué importa ahora**: el objetivo declarado de B-5 es poder diagnosticar por qué una
factura no entra. Esta es precisamente la clase de fallo más probable con un proveedor
nuevo cuyo layout el modelo lee a medias — y es la que queda sin diagnóstico.

---

## Medios

### C-3 — Corregir `servicio` a mano puede romper toda la homologación (A-3 por otra vía)

**Dónde**: `core/almacenamiento.py::registrar_correccion` +
`apps/segurplus/paginas/revision.py::_formulario_correccion`.

B-1 agregó validación de fechas a `registrar_correccion`, pero **no de `servicio`** — y
`servicio` es uno de los dos campos que el mensaje de `necesita_datos` le pide al usuario
que corrija. El formulario es un `st.text_input` libre, sin acotar a
`SERVICIOS_CONOCIDOS`. Verificado:

```
servicio guardado: luz
SERVICIOS_CONOCIDOS incluye "luz"? False
diccionario para "luz"    : ['cargo_fijo']
diccionario para "energia": ['cargo_fijo', 'consumo_energia']
```

Escribir "luz" en vez de "energia" —lo más natural del mundo para quien mira una factura de
luz— se acepta en silencio, y a partir de ahí esa factura homologa solo contra
`comunes.yaml`: pierde `consumo_energia`. Es exactamente A-3 (por lo que el esquema de
extracción tiene un `enum`), llegando por el camino de reparación que creó B-1. El `enum`
protege lo que devuelve el modelo, pero no lo que escribe una persona.

### C-4 — Corregir una fecha mal escrita rompe la pantalla de Revisión

**Dónde**: `apps/segurplus/paginas/revision.py::_formulario_correccion`.

B-1 hizo que `registrar_correccion` lance `ValueError` ante una fecha no interpretable, con
un mensaje pensado para el usuario ("No se pudo interpretar 'x' como fecha -- probá
AAAA-MM-DD, DD/MM/AAAA o MM/AAAA si solo tenés mes y año"). Pero el formulario la llama
**sin `try`/`except`**, así que ese mensaje nunca se muestra: escribir "julio 2022" tira el
traceback de Streamlit encima de la página. El mensaje cuidado que se escribió para esa
situación es, hoy, inalcanzable.

### C-5 — `quitar_detalle_numerico` fusiona medidores, líneas y cuotas numeradas

**Dónde**: `core/analisis/homologacion.py::quitar_detalle_numerico`.

La guarda documentada ("nunca toca un paréntesis con palabras con significado") solo
protege paréntesis **con letras**. Un paréntesis con solo números que **identifica** algo
—en vez de calcular— también se saca. Verificado:

```
  Medidor (8399554)      -> 'Medidor'
  Cargo (1) y (2)        -> 'Cargo y'
  Abono (x2)             -> 'Abono'
  Consumo (Linea 2)      -> 'Consumo (Linea 2)'    (bien, tiene letras)
  Plan (5 GB)            -> 'Plan (5 GB)'          (bien, tiene letras)
```

`Medidor (8399554)` y `Medidor (8399555)` colapsan en el mismo concepto — justo la
granularidad que el docstring de `quitar_periodo` documenta explícitamente NO romper
("sacar todo dígito fusionaría 'Línea 1'/'Línea 2' o 'Medidor 1'/'Medidor 2'... la versión
agresiva colapsa esos casos, verificado"). Para una empresa con varios medidores o líneas
facturados por separado, eso suma dos consumos distintos bajo un solo concepto y hace
perder la comparación por medidor.

Las cuatro facturas reales que tengo imprimen el medidor **sin** paréntesis
(`Medidor 8399554`), así que hoy no se dispara — pero es una bomba para el próximo
proveedor que los use. `(x2)` es el mismo problema por otro lado: es una **cantidad**, no
un cálculo, y fusiona `Abono (x2)` con `Abono (x3)`.

### C-6 — El mensaje de `necesita_datos` dice media verdad

**Dónde**: `core/pipeline.py`, el `detalle` del `ResultadoPipeline`.

El mensaje dice: *"Se guardó pero falta completar: periodo_desde -- corregilo en 'Revisar
facturas' para que entre al análisis"*. Pero corregir **no alcanza**:
`registrar_correccion` no toca `estado`, así que la factura queda en `requiere_revision` y
sigue fuera del análisis hasta que además se la **apruebe**. El usuario puede corregir el
período, ver "Corrección registrada" en verde, volver a Evolución y no encontrar nada —
sin ninguna pista de que falta un paso. Es una versión más suave del mismo problema que
B-1 vino a resolver: decir que algo está listo cuando todavía no impacta el análisis.

### C-7 — La pantalla de Carga ahora pega a la base en cada render

**Dónde**: `apps/segurplus/paginas/cargar.py:134`.

El expander de diagnóstico de B-5 quedó a nivel de script, **fuera** del
`if archivos and st.button(...)`. Antes esa página no tocaba la base salvo al procesar;
ahora abre conexión y consulta en **cada** re-ejecución del script — y Streamlit re-ejecuta
el script entero ante cualquier interacción con cualquier widget. Va en contra del criterio
que A-52 y A-53 dejaron establecido (navegar no debería costar round-trips contra un
Postgres remoto), y además acopla la pantalla de carga a que la base esté disponible: si
Postgres no responde, una página que antes renderizaba bien ahora falla entera.

### C-8 — El "probá después de las HH:MM" muestra la hora del servidor, no la del usuario

**Dónde**: `core/almacenamiento.py::proxima_ventana_libre` + `core/pipeline.py`.

`proxima_ventana_libre` devuelve el `creado_en` de la base —un datetime **naive**, en la
hora del servidor— y el mensaje lo formatea con `strftime('%H:%M')` sin convertir nada.
Verificado que vuelve sin `tzinfo`. En Streamlit Community Cloud el servidor corre en UTC y
el usuario está en Tandil (UTC-3), así que el mensaje le va a decir una hora **3 horas
adelantada** respecto de su reloj: "probá después de las 21:51" cuando en su reloj se
destraba a las 18:51.

### C-16 — 35 referencias del código apuntan a hallazgos que no existen en el documento citado

**Dónde**: 35 sitios entre `core/`, `apps/`, `data/`, `scripts/` y `tests/`.

El código, los YAML y los tests citan 35 veces
`docs/auditoria-2026-09-piloto.md, hallazgo B-N`. Ese archivo contiene **A-49 a A-59 y nada
más**: no tiene ni un B-N. Hasta este documento, los hallazgos B-1 a B-6 solo vivían en
mensajes de commit y en un resumen de `docs/estado.md`. Cualquiera que siguiera una de esas
referencias —incluido quien escribió el código, en la sesión siguiente— no encontraba nada.

La Parte 1 de este documento cierra el agujero del lado del contenido: los hallazgos ahora
existen y están explicados. **Falta apuntar las 35 referencias a este archivo**, que es
trabajo del próximo plan.

---

## Bajos

### C-9 — `intentos_gemini` crece sin límite y sin índice

Una fila por llamada, nunca se purga, sin índice sobre `creado_en`. `llamadas_ultima_hora`
hace `count(*) ... WHERE creado_en > now() - INTERVAL '1 hour'` sobre la tabla entera **por
cada PDF procesado**. Con el volumen de hoy no se nota; con meses de uso contra un Postgres
gratuito (con límite de espacio y sin autovacuum agresivo), sí.

### C-10 — La respuesta cruda del modelo se guarda dos veces

En una extracción exitosa, `respuesta_extraida` va a `facturas` (por `guardar_factura`) y
**la misma cadena** va a `intentos_gemini.respuesta_cruda`. Son los datos completos de cada
factura duplicados, sin purga, en una base gratuita con límite de espacio. Para el
diagnóstico que motivó B-5 solo hacen falta los **fallidos**; en los exitosos es
redundante.

### C-11 — Si el conteo ya superó el tope, el mensaje nombra una hora que sigue bloqueada

`proxima_ventana_libre` asume que sacando la llamada más vieja de la ventana alcanza para
destrabar. Si hay más llamadas que el tope —porque se bajó el tope, o por la carrera entre
dos sesiones que el propio docstring admite— el mensaje nombra una hora en la que va a
seguir bloqueado. Verificado con 4 llamadas y tope 1: dice "probá después de las 21:51",
pero a esa hora quedan 3 llamadas en la ventana.

### C-12 — B-2 no recalcula lo que ya estaba cargado

`quitar_detalle_numerico` cambia cómo homologa una factura **nueva**, pero
`conceptos.concepto_normalizado` de las facturas **ya guardadas** se calculó al ingresarlas
con la lógica vieja. Para que el arreglo alcance lo cargado antes hay que correr
"Re-homologar ahora" o `scripts/rehomologar.py --aplicar`. El circuito existe y está
documentado en general, pero **no está dicho en ningún lado como paso posterior a este
cambio puntual** (ni en `docs/estado.md`, ni en el README, ni en el mensaje del commit), y
es fácil que se pase por alto.

### C-13 — Regenerar las fixtures cambió los bytes de las cinco PDFs viejas

Correr `generar_fixtures.py` para agregar la de gas regeneró todas, y ReportLab embebe un
timestamp de creación: cinco PDFs con bytes distintos y contenido idéntico en el diff del
commit `ee671ad`. Ruido que hace más difícil distinguir un cambio real de uno espurio en la
próxima revisión del repo.

### C-14 — Falsos positivos de homologación preexistentes (NO es regresión de B-2)

Al revisar el alias `energia` que agregó B-2, apareció que `consumo_energia` absorbe cosas
que no son consumo. **Verificado que ya pasaba antes del alias**:

```
concepto                        SIN alias energia        CON alias energia
  Recargo energia               consumo_energia (0.621)  consumo_energia (0.632)
  Impuesto energia electrica    consumo_energia (0.800)  consumo_energia (0.800)
  Energia (7,5151 x 56)         consumo_energia (0.600)  consumo_energia (1.000)
```

El alias sube el score (y ese era su objetivo: sacar el caso real del borde exacto del
umbral, 0,600) pero **no causó** los falsos positivos. Se anota igual porque un **recargo**
absorbido como consumo normal es justo lo que la herramienta existe para alertar. Hoy no se
dispara mientras el modelo clasifique esas líneas como `recargo`/`impuesto` —nunca llegan a
homologarse, porque `homologar_concepto` solo corre sobre `factura.conceptos`— así que el
riesgo real depende de que B-3 funcione. Mismo caso que el `IIBB Cargo Fijo` ya registrado
como test de comportamiento conocido en `tests/analisis/test_homologacion.py`.

### C-15 — La fixture nueva usa IVA 21% y luz/gas en Argentina llevan 27%

`docs/fixtures/sintetico/gas_2026-07.pdf` reproduce la **forma** que rompía (período
`MM/AAAA`, detalle entre paréntesis) pero con la alícuota del generador genérico. No afecta
ningún test —todos usan los totales calculados por la propia fixture— pero la hace menos
representativa de la factura real que pretende imitar.

---

## Tabla resumen

| # | Severidad | Dónde | Qué |
|---|---|---|---|
| C-1 | **Alto** | `core/analisis/alertas.py` + `core/extraccion/esquema.py` | B-6 y B-1 se contradicen: falso "falta un período" en CADA par de meses consecutivos con período `MM/AAAA`, y el bimestral tampoco queda resuelto. Rompió un caso que antes funcionaba |
| C-2 | **Alto** | `core/extraccion/gemini.py`, `core/extraccion/esquema.py`, `core/pipeline.py` | Un JSON válido pero incompleto lanza `KeyError` fuera de `ExtraccionError`: mensaje inútil, llamada no contada (anula B-4), sin rastro (anula B-5), y `probar_extraccion.py` con traceback |
| C-3 | Medio | `core/almacenamiento.py::registrar_correccion` | Corregir `servicio` a un valor fuera de `SERVICIOS_CONOCIDOS` se acepta en silencio y pierde el diccionario del servicio (A-3 por otra vía) |
| C-4 | Medio | `apps/segurplus/paginas/revision.py` | El `ValueError` que agregó B-1 no se atrapa: traceback en vez del mensaje de ayuda |
| C-5 | Medio | `core/analisis/homologacion.py` | `quitar_detalle_numerico` saca paréntesis numéricos que IDENTIFICAN (medidor, línea, cuota), fusionando conceptos distintos |
| C-6 | Medio | `core/pipeline.py` | El mensaje de `necesita_datos` dice "corregilo" pero además hay que aprobarla; corregir solo no la hace entrar al análisis |
| C-7 | Medio | `apps/segurplus/paginas/cargar.py` | El expander de diagnóstico conecta a la base en cada render, aunque no se suba nada |
| C-8 | Medio | `core/almacenamiento.py`, `core/pipeline.py` | La hora del "probá después de las HH:MM" es la del servidor (UTC), no la del usuario (UTC-3) |
| C-16 | Medio | 35 sitios | Referencias a `auditoria-2026-09-piloto.md, hallazgo B-N` apuntando a un archivo que no tiene ningún B-N |
| C-9 | Bajo | `core/almacenamiento.py` | `intentos_gemini` sin purga ni índice sobre `creado_en` |
| C-10 | Bajo | `core/pipeline.py` | `respuesta_cruda` duplica `facturas.respuesta_extraida` en cada extracción exitosa |
| C-11 | Bajo | `core/almacenamiento.py` | Si el conteo supera el tope, el mensaje nombra una hora que sigue bloqueada |
| C-12 | Bajo | operación | B-2 no recalcula lo ya cargado: hay que re-homologar, y no está dicho como paso posterior |
| C-13 | Bajo | `docs/fixtures/sintetico/` | Regenerar fixtures cambió los bytes de las cinco viejas sin cambio funcional |
| C-14 | Bajo | `data/conceptos/` | `Recargo energia` e `Impuesto energia electrica` homologan a `consumo_energia` — **preexistente**, no lo causó el alias de B-2 |
| C-15 | Bajo | `docs/fixtures/generar_fixtures.py` | La fixture de gas usa IVA 21%; luz y gas reales llevan 27% |

---

## Qué conviene resolver antes de volver a cargar facturas reales

**Bloqueantes**:

- **C-1**, antes que nada: es una regresión que rompió un caso que funcionaba, y se dispara
  con las facturas de luz apenas se carguen dos meses consecutivos. Además ensucia la
  pantalla de Casos con trabajo operativo inventado. Conviene corregirlo mirando B-1 y B-6
  **juntos**: el problema de fondo es que normalizar `MM/AAAA` al primer día pierde la
  información de cobertura que B-6 necesita.
- **C-2**: mientras esté, toda una clase de fallo de extracción queda sin mensaje útil, sin
  contar contra el tope y sin rastro — justo cuando el objetivo declarado es poder
  diagnosticar por qué una factura no entra. Es también lo que hace inservible a
  `probar_extraccion.py` en ese caso, que es la herramienta recomendada para cerrar B-3.

**Conviene hacerlo junto con lo anterior, porque es el mismo camino**: **C-3**, **C-4** y
**C-6** están los tres sobre el circuito "la factura quedó en `necesita_datos` → la corrijo
a mano", que es el primero que el usuario va a recorrer apenas una factura no traiga
período o servicio. Corregir uno solo deja el camino igual de roto.

**Puede esperar**: C-5 (no se dispara con los proveedores actuales, pero sí con el
próximo que numere medidores entre paréntesis), C-7, C-8, C-16.

**Mantenimiento**: C-9 a C-15.

**Sigue pendiente de la auditoría anterior**: **B-3** no está verificado contra la API real
—hace falta `GEMINI_API_KEY` y correr `scripts/probar_extraccion.py` sobre las facturas de
luz y gas—, y **A-26** (`_parsear_monto` con separadores de miles mezclados) sigue diferido,
sin facturas reales que lo ejerciten todavía.
