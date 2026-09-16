# Auditoría de Segurplus — circuito de confirmación de carga y vocabulario del tablero (septiembre 2026)

**Este documento solo audita. No se corrigió nada acá.** Cada hallazgo trae la evidencia
que lo reproduce; las correcciones van en un plan aparte, después de revisar esto. Misma
regla que las auditorías anteriores (`docs/auditoria-2026-09.md`, A-1 a A-28;
`docs/auditoria-2026-09-rediseno.md`, A-29 a A-48; `docs/auditoria-2026-09-piloto.md`,
A-49 a A-59; `docs/auditoria-2026-09-facturas-reales.md`, B-1 a B-6, y su revisión C-1 a
C-16). Esta serie arranca en **D-1** y llega a **D-25**.

**Alcance**: los cinco commits `98add1c`..`aea8be1` (~1.900 líneas netas), que nadie
revisó todavía:

- **`98add1c`, `61a31af`, `21a4c50`, `658a41f`** — el plan de confirmación de carga: el
  pipeline deja de decidir solo y siempre deja un borrador; pantalla nueva "Confirmar
  carga" con el PDF al lado; `cargar.py`/`revision.py`/`cuarentena.py` reconectados.
- **`aea8be1`** — el plan de vocabulario: traductores de slugs de alerta, concepto y
  estado de caso; renombre de pestañas y leyenda en Evolución; espejo en el Excel; fix
  del conteo de `facturas_cargadas`.

**Advertencia**: **todo el código auditado acá lo escribí yo**, en la misma sesión, contra
un plan que yo mismo propuse. Una auditoría del propio trabajo vale menos que una externa
y conviene leerla sabiendo eso. Cuatro de los cinco hallazgos altos son errores de diseño
míos, no descuidos de implementación: D-2 y D-14 son directamente decisiones equivocadas
del plan de vocabulario que terminé ayer.

**Método**: lectura completa de cada archivo nuevo o modificado, más **verificación
ejecutando código** — los bloques de evidencia de D-1, D-2, D-5, D-6, D-13, D-16 y D-22
son salida real de Python, no interpretación de lectura.

**Motivo**: el circuito de carga se reemplazó entero y el resultado todavía no se ejerció
con facturas reales. Varias garantías que antes hacía cumplir el código ahora dependen de
que una persona confirme bien una pantalla.

---

## Resumen ejecutivo

**Un hallazgo alto contamina el análisis con datos inventados; los otros cuatro son
pérdidas de trazabilidad o de señal que el circuito viejo sí daba.**

- **D-1 — una fila vacía del editor entra al análisis como un concepto llamado "nan"**,
  con su plata, sin que nada lo bloquee. Es el único hallazgo que produce un número falso
  en pantalla, o sea el que rompe la regla de oro del proyecto.
- **D-2 — la traducción de conceptos no funciona en el caso normal**: un consumo con
  unidad (kWh, m³, GB) sigue mostrándose como `consumo_energia [kwh]`. El plan de
  vocabulario que acabo de dar por terminado no resuelve el caso más frecuente, y el test
  que escribí no lo detecta porque prueba el slug pelado, que el pipeline nunca produce.
- **D-3 — el reporte al cliente perdió la señal de completitud**: la hoja de cuarentena
  siempre dirá cero y nada avisa que hay N facturas sin confirmar, así que un total puede
  estar incompleto sin una sola advertencia.
- **D-4 — nada queda registrado de lo que corrigió la persona** al confirmar: el plan
  prometía escribir en `correcciones_factura` y no se implementó.
- **D-5 — al confirmar se borran `texto_extraido` y `motivo_carga`**, verificado
  ejecutando: se pierde el respaldo del texto del PDF y el rastro de que Gemini no pudo
  leer esa factura.

El resto (D-6 a D-25) son medios y bajos: concurrencia, cobertura de tests, documentación
desactualizada y decisiones de vocabulario a medio camino.

---

## Altos

### D-1 — Una fila sin descripción entra al análisis como un concepto llamado "nan"

`core/extraccion/esquema.py::conceptos_desde_filas` descarta las filas sin descripción
con `str(fila.get("descripcion") or "").strip()`. El `or ""` no cumple su intención
cuando la celda viene de pandas: **`NaN` es truthy**, así que `str(NaN)` da la cadena
`"nan"` y la fila NO se descarta.

```
record 1: {'descripcion': nan, 'cantidad': 1.0, 'unidad': None, 'precio_unitario': 500.0, 'importe': 500.0}
[Concepto(descripcion='Abono', cantidad=4.0, ...),
 Concepto(descripcion='nan', cantidad=1.0, unidad=None, precio_unitario=500.0, importe=500.0)]
```

Por qué no lo atrapa ningún control: la línea **cierra aritméticamente** (1 × 500 = 500),
así que `validar_factura` la da por buena y el botón "Confirmar" queda habilitado. El
concepto se guarda, se homologa (score bajísimo, no matchea nada), y aparece en Evolución
y en el Excel del cliente como `(sin_homologar) Nan` con plata asociada — un concepto que
no existe en ninguna factura.

Cuándo pasa: el editor arranca con una fila en blanco (`_tabla_conceptos_vacia`) y con
`num_rows="dynamic"` cada fila agregada nace vacía. Alcanza con que alguien tipee el
importe antes que la descripción y confirme.

Mismo patrón en `montos_desde_filas` (impuestos, recargos, créditos): un impuesto llamado
"nan" pasaría igual.

### D-2 — La traducción de conceptos no se aplica cuando el concepto tiene unidad

La clave que produce `agregar_conceptos` no es el slug pelado: cuando la línea tiene
unidad, es `_etiqueta(concepto, unidad)` → `"consumo_energia [kwh]"`. `etiqueta_legible`
busca esa cadena completa en `data/etiquetas_conceptos.yaml`, no la encuentra, y devuelve
el slug crudo.

```
clave agregada: ['consumo_energia [kwh]']
etiqueta_legible -> 'consumo_energia [kwh]'     # sigue crudo
sin unidad       -> 'Abono móvil'               # solo acá funciona
```

O sea: la traducción funciona para cargos fijos y abonos, y **no funciona justo para los
consumos medidos** (energía en kWh, gas en m³, datos en GB), que son el caso central de la
herramienta. Lo que el usuario pidió arreglar sigue roto en la pantalla más importante.

Agravante de método: `tests/analisis/test_agregacion.py::test_etiqueta_legible_traduce_
concepto_homologado` pasa `"abono_movil"` a mano — un valor que el pipeline **nunca**
produce para una línea con unidad. Es exactamente lo que C-1 dejó como criterio ("testear
con los valores que el pipeline realmente produce") y lo incumplí en el mismo repo que lo
escribió.

### D-3 — El Excel y el tablero ya no avisan que faltan facturas por confirmar

El reporte al cliente tenía una hoja "Cuarentena" que respondía una pregunta concreta:
*¿este total está completo, o hubo facturas que no entraron?* Desde que `procesar_pdf` no
escribe más en esa tabla, la hoja (ahora "Cuarentena (histórico)") **siempre dirá "No hay
facturas en cuarentena"**, y nada la reemplazó: ni el Excel ni la página de Evolución
miran `estado = 'borrador'`.

Consecuencia concreta: se cargan 12 facturas, se confirman 9, se exporta el Excel. El
reporte muestra un total de 9 facturas presentado como el gasto del período, sin una sola
advertencia de que faltan 3. Antes, esas 3 aparecían listadas con su motivo.

La página de Evolución tiene el mismo agujero: no hay ningún cartel de "hay N borradores
sin confirmar de este servicio".

### D-4 — No se registra ninguna corrección al confirmar

El plan aprobado decía, textual: *"deja las correcciones en `correcciones_factura` (en
bloque, motivo 'corrección al confirmar la carga', actor = usuario)"*. No está
implementado: ni `core/pipeline.py::confirmar_factura` ni `apps/segurplus/paginas/
confirmar.py` llaman a `registrar_correccion`, que ya existe y se usa desde `revision.py`.

Lo que se pierde:

- **Trazabilidad**: una factura confirmada no distingue entre "Gemini la leyó perfecta" y
  "una persona reescribió los cinco importes a mano". El único rastro es un evento
  genérico `carga` en `decisiones_factura` (y ver D-22).
- **Calibración por proveedor**: qué campos hay que corregir siempre para Movistar es el
  dato que diría dónde ajustar el prompt. Se está tirando en cada confirmación.

Es además la garantía que el propio repo se fijó en "Persistencia y trazabilidad"
(`docs/estado.md`): toda corrección humana queda registrada con actor y motivo.

### D-5 — Confirmar borra `texto_extraido` y `motivo_carga`

`guardar_factura` hace UPSERT y pisa las dos columnas con lo que reciba por parámetro;
`confirmar_factura` no las pasa, así que quedan en `NULL`. Verificado ejecutando:

```
ANTES  : ('borrador', True,  'No se pudo leer con Gemini: timeout', '/tmp/evidencia/h1.pdf')
DESPUES: ('aprobada', False, None,                                  '/tmp/evidencia/h1.pdf')
```

Dos pérdidas:

- **`texto_extraido`** es el respaldo visual cuando no hay PDF guardado (el caso de
  `EVIDENCIA_DIR` sin configurar, que es el default del piloto) y la fuente de la doble
  lectura del total. Después de confirmar no se puede volver a verificar nada contra el
  texto del PDF.
- **`motivo_carga`** es el rastro de que esa factura llegó rota. Se borra **justo cuando
  la factura pasa a ser un dato bueno**, que es cuando más importa saber que su origen fue
  una carga manual sobre una lectura fallida.

---

## Medios

### D-6 — `confirmar_factura` no verifica que la factura siga siendo un borrador

`leer_borrador` valida el estado al LEER, pero al ESCRIBIR no hay ningún control: el
UPSERT pisa cualquier fila con ese hash, sea cual sea su estado.

```
¿se puede confirmar DOS veces la misma factura ya aprobada?
  -> SI, sin control de estado. Estado: aprobada
```

Escenario real de pérdida de datos: alguien confirma una factura, otra persona le corrige
la cabecera en "Revisar facturas", y una pestaña vieja todavía abierta en "Confirmar
carga" confirma de nuevo — las correcciones se pierden sin aviso, y la factura vuelve a
`aprobada` con los datos viejos. Con dos usuarios simultáneos (que es el modelo de roles
que la app ya soporta) no es hipotético.

### D-7 — `confirmar_factura` borra la procedencia si el llamador no la arrastra

El mismo UPSERT sobrescribe `ruta_evidencia`, `respuesta_extraida`, `modelo_extraccion`,
`version_prompt` y `version_esquema` con lo que traiga el objeto `FacturaExtraida`.
`confirmar.py` los arrastra a mano (`datos["ruta_evidencia"]`, etc.), pero nada lo
obliga: cualquier otro llamador que arme la factura sin esos campos **borra en silencio el
vínculo con el PDF de evidencia**.

Ya pasa en el propio test `test_de_punta_a_punta_borrador_a_aprobada`, que reconstruye la
factura con `replace(...)` sin evidencia. La función debería preservar del borrador lo que
no se edita, en vez de confiar en la disciplina del llamador.

### D-8 — Una factura que Gemini no pudo leer se anuncia en verde como "lista para confirmar"

En `cargar.py`, todo lo que vuelve con `estado == "borrador"` se cuenta junto y se muestra
como `st.success("N factura(s) listas para confirmar")`. Un borrador vacío por fallo de
extracción entra en esa cuenta, y su `detalle` (el mensaje de error de Gemini, que
`procesar_pdf` sí devuelve) **no se muestra en ninguna parte**.

Regresión respecto del circuito viejo, donde ese caso salía en amarillo con el motivo. Hoy
el usuario se entera recién al abrir la factura en "Confirmar carga" y encontrarla vacía.

### D-9 — Un borrador bloquea volver a subir el PDF, y no hay forma de reintentar la lectura

`factura_ya_procesada` devuelve verdadero para cualquier estado que no sea `rechazada`,
así que un borrador (incluido uno vacío por fallo de Gemini) bloquea la re-subida del
mismo PDF para siempre. Y la pantalla de confirmación no tiene botón "reintentar la
lectura con Gemini".

El único camino para reintentar es descartar el borrador y volver a subir el archivo — que
funciona, pero nadie lo va a deducir: el texto del popover habla de "borrar", no de
"reintentar". La cuarentena vieja tenía un botón "Reintentar" explícito; se eliminó sin un
equivalente visible.

### D-10 — Las ediciones del formulario no se persisten en ningún lado

Todo el estado del formulario de confirmación vive en `st.session_state`. Si se cierra la
pestaña, se recarga, o Streamlit Community Cloud duerme la app (que lo hace por
inactividad), se pierde el trabajo completo de corrección.

Para una factura de Movistar con 20 líneas mal leídas, eso es media hora de trabajo que
puede evaporarse sin aviso. No hay "guardar borrador" ni autosave: el borrador que está en
la base es siempre el original de Gemini, nunca lo editado.

### D-11 — `descartar_borrador` deja huérfano el PDF de evidencia

Borra de `conceptos`, `impuestos`, `recargos`, `creditos`, `alertas` y `facturas`, pero no
toca el archivo (o el objeto S3) que `guardar_pdf` dejó escrito. Después de descartar, el
PDF de una factura real queda en el disco del servidor o en el bucket sin ninguna fila que
lo referencie — invisible para la app y para cualquier borrado posterior.

Dado que `data/reales/` es zona restringida por política del repo, acumular PDFs de
facturas reales sin dueño es un problema de retención de datos, no solo de espacio.

### D-12 — Impuestos, recargos y créditos se leen sin `ORDER BY` y el editor asume posición fija

`leer_borrador` lee conceptos con `ORDER BY orden`, pero las otras tres tablas **no tienen
columna de orden** y se leen sin `ORDER BY`:

```sql
SELECT nombre, importe FROM impuestos WHERE hash_pdf = ?
```

En DuckDB el orden de inserción se respeta de hecho; en PostgreSQL —que es el deploy real
(ADR-003)— no está garantizado y puede cambiar después de un `UPDATE`. `st.data_editor`
guarda las ediciones del usuario **por posición de fila**, así que si el orden cambia
entre dos re-ejecuciones del script, una edición se aplica a la fila equivocada: un
importe corregido termina en otro impuesto.

Baja probabilidad, consecuencia silenciosa y financiera.

### D-13 — Los mensajes de alerta siguen mostrando el slug crudo del concepto

El plan de vocabulario tradujo `Alerta.tipo`, pero el nombre del concepto viaja **dentro
del texto** del mensaje, que se arma en `core/analisis/alertas.py` con `d.concepto` crudo:

```
TIPO:    Salto brusco de cantidad
MENSAJE: "abono_movil": cantidad pasó de 4 a 8 (+100%)
CONCEPTO (columna del Excel): abono_movil
```

Afecta a las cinco reglas que nombran un concepto, y se propaga a tres lugares: la pestaña
Alertas de Evolución, la tabla de Casos (con el mensaje ya persistido así en
`casos_alerta`) y la columna "Concepto" de la hoja Alertas del Excel del cliente. El
trabajo de vocabulario quedó a mitad de camino: se tradujo la etiqueta que encabeza la
alerta y no el texto que la explica.

### D-14 — "Todos los conceptos" es un nombre equivocado para esa pestaña

La pestaña que renombré muestra `componentes_financieros_periodo`: consumos/abonos,
impuestos, recargos, créditos y total pagable. **No muestra conceptos.** Los conceptos, uno
por uno, están en la pestaña "Detalle".

O sea que el renombre empeoró dos cosas a la vez: el nombre nuevo describe mal el
contenido, y colisiona con la pestaña que sí hace lo que el nombre promete. El nombre
anterior ("Composición total") era menos vistoso pero más exacto. Candidatos razonables:
"Impuestos y total" o "Del consumo al total pagable".

### D-15 — `CLAUDE.md` y `docs/PLAN.md` siguen documentando el circuito de cuarentena

`CLAUDE.md`, en la sección que define la regla central del proyecto:

> Si una factura no cierra, va a cuarentena y NO entra al análisis

Eso dejó de ser verdad hace cuatro commits: va a borrador. `docs/PLAN.md` lo repite en seis
lugares (líneas 115, 121, 161, 164, 213, 223), incluida la tabla de criterios de aceptación
de la Fase 1.

Importa más de lo que parece: `CLAUDE.md` es lo primero que lee cualquier persona o agente
que toque este repo, y hoy lo manda a re-implementar un circuito que se eliminó a
propósito.

---

## Bajos

### D-16 — `etiqueta_legible` abre y parsea el YAML en cada llamada

```
200 llamadas: 115.2 ms  (576 us por llamada, cada una abre y parsea el YAML)
```

Se llama una vez por concepto en el gráfico (hasta 12), una por fila en la tabla Detalle
(todas las descomposiciones) y una por fila en el Excel. Con 50 conceptos son ~36 ms por
re-ejecución del script gastados en leer 62 veces el mismo archivo de 9 líneas.

El patrón "sin cache, sin lectura a nivel de módulo" que copié de `_leer_umbrales` está
bien pensado para una función que se llama **una vez por render**; acá se aplicó a una que
se llama una vez por fila. Un `functools.lru_cache` invalidado por `mtime`, o una sola
lectura por render pasada como parámetro, mantiene la propiedad de "un YAML corrupto no
tumba el import" sin el costo.

### D-17 — `NaN` produce mensajes "nan × $nan" en el control aritmético

Mismo origen que D-1 (`float(fila.get("cantidad") or 0)` no convierte `NaN` a 0 porque
`NaN` es truthy), pero acá la consecuencia es solo de presentación: la validación falla
correctamente y bloquea el botón, mostrando `Línea 2 — "Cargo": nan × $nan = $nan`. El
control funciona; el mensaje no ayuda a entender qué falta completar.

### D-18 — La severidad de las alertas sigue cruda en Casos

`casos.py` traduce "Tipo" y "Estado" pero muestra "Severidad" como viene de la base:
`alta`, `media`, `baja`, en minúscula. En Evolución, en cambio, la severidad se muestra
con emoji y color. Inconsistencia dentro del mismo plan de vocabulario.

### D-19 — El invariante central del plan no tiene test propio

"Un borrador es invisible para el análisis" es la garantía que reemplazó a la cuarentena y
lo que hace aceptable guardar facturas sin validar. Ningún test la verifica directamente:
los de punta a punta comprueban que **después** de confirmar la factura aparece
(`totales_por_periodo(con, ...) != {}`), nunca que **antes** no aparecía.

Un test de tres líneas (`assert totales_por_periodo(...) == {}` con el borrador ya
guardado) cubriría el invariante del que depende todo el rediseño.

### D-20 — El circuito real de edición no tiene ningún test de integración

`tests/apps/test_confirmar_app.py` documenta que `AppTest` no puede simular una edición de
`st.data_editor`, y prueba la cáscara con los datos sin editar. Los helpers puros
(`conceptos_desde_filas`, `montos_desde_filas`) sí están testeados por separado.

Queda sin cubrir exactamente el camino que importa: editar una línea rota, ver que se
destraba el botón, confirmar, y que lo guardado sea lo editado. D-1 es precisamente un bug
que vive en ese hueco. Un test que llame a las funciones puras con las filas que dejaría
el editor (incluida una fila vacía) no necesita `AppTest` y lo habría atrapado.

### D-21 — Cambiaron los nombres de hoja del Excel sin aviso

"Descomposición" → "Precio vs. cantidad" y "Cuarentena" → "Cuarentena (histórico)".
Cualquier plantilla, macro o planilla del cliente que referencie esas hojas por nombre deja
de funcionar. No hay consumidores automatizados conocidos, pero el reporte ya se entregó
con los nombres viejos.

### D-22 — Una factura confirmada queda con dos eventos "carga" y el segundo miente

`guardar_factura` registra siempre `_registrar_decision(..., "carga", ..., f"estado
inicial: {estado}")`, incluido el UPSERT de la confirmación:

```
('carga', 'sistema', 'estado inicial: borrador')
('carga', 'ana',     'estado inicial: aprobada')
```

El segundo no es un estado inicial: es la confirmación, que merece su propia acción
(`confirmacion`) para poder distinguirla en el historial de auditoría. Hoy el evento más
importante del circuito nuevo no es distinguible de una carga.

### D-23 — El rol `cargador` puede editar cualquier importe y meterlo al análisis

`confirmar.py` pide `requerir_rol("cargador", "revisor", "responsable", "administrador")`.
Con `revision_humana_obligatoria` en `false` (el default del piloto), el rol más bajo puede
reescribir todos los números de una factura y confirmarlos directo a `aprobada`.

Antes, un `cargador` solo subía PDFs y la aritmética la decidía el código. Es un cambio de
modelo de permisos que el plan no discutió: puede ser deseable, pero debería ser una
decisión explícita.

### D-24 — `sin_clasificar.py` sigue sin `requerir_rol`

Preexistente (no lo introdujeron estos planes), pero el plan de vocabulario tocó la página
sin corregirlo: cualquiera con la contraseña compartida puede correr "Aplicar cambios
confirmados", que reescribe `concepto_normalizado` en **todas** las facturas aprobadas.
`casos.py` y `revision.py`, que escriben mucho menos, sí piden rol.

### D-25 — El aviso de cuarentena histórica se muestra siempre

El `st.caption` que explica que "En cuarentena (histórico)" ya no recibe cargas nuevas se
renderiza aunque la tabla esté vacía y aunque no haya ninguna factura cargada — que va a
ser el caso permanente de cualquier instalación nueva. Debería mostrarse solo si hay algo
histórico que explicar.

---

## Lo que esta auditoría NO cubre

- **El comportamiento con facturas reales de Movistar**, que es lo que motivó los dos
  planes. Nada acá se ejerció contra la API real de Gemini ni contra un PDF real: sigue
  pendiente B-3.
- **La pantalla de confirmación usada por una persona de verdad.** Los hallazgos de UX
  (D-8, D-9, D-10) salen de leer el código, no de ver a alguien cargar diez facturas.
- **El rendimiento con volumen.** `listar_borradores` sin paginar y un `data_editor` por
  factura no se probaron con más de un puñado de borradores.
