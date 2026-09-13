# Auditoría de Segurplus — rediseño + facturas reales (septiembre 2026)

**Este documento solo audita. No se corrigió nada acá.** Cada hallazgo trae la evidencia que
lo reproduce; las correcciones van en un plan aparte, después de revisar esto. Misma regla
que `docs/auditoria-2026-09.md`, la auditoría anterior (hallazgos A-1 a A-28), cuya
numeración esta continúa: acá van **A-29 a A-48**.

**Alcance**: los nueve commits `f4e71c4`..`6d8e1dd` (~2.100 líneas), que entraron dos frentes
de una sola pasada:

- **Frente 1 — corrección de A-28** (el período pegado a la descripción daba vuelta la
  descomposición precio/cantidad) más el circuito de calibración completo:
  `quitar_periodo`, clave de agrupamiento estable, `score_homologacion` persistido,
  `core/rehomologacion.py` + `scripts/rehomologar.py`, pantalla "Sin clasificar".
- **Frente 2 — rediseño del tablero**: tema institucional, `apps/segurplus/estilo.py`,
  serie temporal nominal vs. real, pestañas, frase de veredicto (`efecto_dominante`).

**Método**: lectura completa de cada archivo nuevo o modificado, más **verificación
ejecutando el código** — los bloques de evidencia de A-29, A-30 y A-31 son salida real de
Python, no lectura. El subagente `revisor-financiero` rehusó revisar este repo por quinta
vez (fuera de mandato), así que esta auditoría es el reemplazo de esa revisión, no un extra.

**Motivo**: está por entrar la primera tanda de facturas reales de varios proveedores. El
Frente 1 existe precisamente porque mirar facturas reales a mano destapó A-28. Antes de
cargarlas conviene saber qué quedó mal en la corrección de A-28.

---

## Resumen ejecutivo

**Dos hallazgos bloquean la carga de facturas reales, y los dos están en lo que se acaba de
construir, no en código viejo.**

- **A-29 — el bug estrella del release sigue vivo para mayo y para `sept`.** `quitar_periodo`
  reconoce 12 de las 14 grafías de mes que usan las facturas argentinas: le falta la
  abreviatura **`may`** (tiene `mayo`, no `may`) y **`sept`** (tiene `sep` y `set`). Para esos
  dos casos, A-28 se reproduce entero y sin atenuantes: dos conceptos fantasma,
  `efecto_precio = 0`, toda la variación imputada falsamente a cantidad, y dos alertas falsas
  por mes. El docstring de la función, además, **afirma** que `may` está contemplado.
- **A-30 — el veredicto nuevo del tablero puede imprimir un porcentaje absurdo, en el
  escenario exacto que el proyecto existe para analizar.** `efecto_dominante` divide por la
  variación total, que es la suma *algebraica* de efectos que pueden cancelarse entre sí. Si
  la empresa da de baja líneas mientras el proveedor aumenta el precio —el caso Movistar
  literal—, el tablero muestra **"El cambio de $+100.00 fue mayormente por PRECIO (1000%)"**.
  No hay ningún test de signos opuestos: los seis tests de `efecto_dominante` usan efectos del
  mismo signo. CLAUDE.md llama a esto "el peor error posible de esta empresa".

Después de esos dos, el patrón que más se repite es que **la UI saltea las garantías que el
código de `core/` y el CLI sí respetan**: el botón "Re-homologar ahora" escribe en la base sin
dry-run ni confirmación (A-32) cuando el CLI fue diseñado a propósito para no poder ser
destructivo por accidente, y encima descarta con un `st.rerun()` el informe de regresiones que
acababa de mostrar; `sin_clasificar.py` hardcodea un umbral (A-34) contra la regla explícita de
CLAUDE.md; y `evolucion.py:99` deja fuera del `try` el mismo `date.fromisoformat` que ochenta
líneas más abajo está cuidadosamente protegido (A-33).

**Lo que está bien y conviene no tocar**: la identidad algebraica
`efecto_cantidad + efecto_precio + efecto_cruzado = variación total` cierra siempre y está bien
testeada, incluso en los casos borde nuevos. `guardar_factura` sí es idempotente (borra
`conceptos` y `recargos` por hash antes de insertar). El buffer del Excel hace `seek(0)` antes
de devolverse, así que la descarga no sale vacía. Las fechas se normalizan a ISO o a `None` en
la extracción (A-11 quedó bien cerrado), y `totales_por_periodo` filtra los `NULL`. La decisión
de sumar `conceptos.importe` y no `facturas.total` en la serie está bien argumentada y es
consistente con la comparación de dos períodos. Y la elección de que `quitar_periodo` sea
conservadora (no borrar cualquier dígito, para no fusionar "Línea 1" con "Línea 2") sigue
siendo la correcta: el problema de A-29 es de cobertura de la lista de meses, no del criterio.

---

## Críticos

### A-29 — `quitar_periodo` no reconoce `may` ni `sept`: A-28 vuelve entero para esos meses

**Dónde**: `core/analisis/homologacion.py:50-53` (`_MESES`).

La alternancia lista los 12 nombres completos y 12 abreviaturas, pero las abreviaturas no
cubren dos casos:

```
enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre|
ene|feb|mar|abr|jun|jul|ago|sep|set|oct|nov|dic
                    ^^^ falta may                    ^^^ falta sept
```

Salida real de `quitar_periodo("cargo <mes> 2026")` para las 14 grafías:

```
ene   -> 'cargo'
feb   -> 'cargo'
mar   -> 'cargo'
abr   -> 'cargo'
may   -> 'cargo may'    <== el mes NO se saca
jun   -> 'cargo'
jul   -> 'cargo'
ago   -> 'cargo'
sep   -> 'cargo'
sept  -> 'cargo sept'   <== el mes NO se saca
set   -> 'cargo'
oct   -> 'cargo'
nov   -> 'cargo'
dic   -> 'cargo'
```

Y de punta a punta, con el mismo escenario del test que justificó todo el Frente 1 (un concepto
sin homologar, $30.000 en un mes y $36.000 al siguiente, o sea **un aumento de precio puro**):

```
claves mayo : ['(sin_homologar) servicio medido may']
claves junio: ['(sin_homologar) servicio medido']

'(sin_homologar) servicio medido'     : efecto_precio=0.0  efecto_cantidad=36000.0
'(sin_homologar) servicio medido may' : efecto_precio=0.0  efecto_cantidad=-30000.0
```

Lo correcto sería **una sola fila** con `efecto_precio = 6000`. Esto es A-28 sin ninguna
diferencia: dos claves, dos conceptos fantasma, `efecto_precio = 0` en ambas filas, la variación
entera imputada falsamente a cantidad, y las dos alertas falsas (`concepto_nuevo` +
`concepto_desaparecido`) que A-28 también producía.

**Por qué importa más de lo que parece**: no es un caso raro. Son dos de las catorce grafías que
un proveedor puede usar, `may` es de las más comunes en facturas que abrevian, y el fallback
`(sin_homologar) <texto>` es justamente la red de seguridad pensada para los proveedores cuyos
conceptos todavía no están en ningún YAML — o sea, todos los de la primera carga. La red tiene
dos agujeros.

**Agravante de documentación**: el docstring de `quitar_periodo` (líneas 84-87) dice
textualmente que la contrapartida aceptada es que "un mes abreviado de 3 letras (`mar`, `may`,
`ago`) puede coincidir con una palabra real". Cita `may` como ejemplo de algo que la función
hace, y no lo hace. Cualquiera que lea el docstring para decidir si confiar en la función se va
a llevar la idea equivocada.

**Nota adicional para quien corrija**: al agregar `may`, revisar el falso positivo que el propio
docstring anticipa — `\bmay\b` no debería comerse nada raro, pero `\bsept\b` y `\bmar\b` merecen
un test explícito de "no rompe una descripción que contiene esa palabra". No corregir acá.

---

### A-30 — `efecto_dominante` no acota la proporción: el tablero puede decir "mayormente por PRECIO (1000%)"

**Dónde**: `core/analisis/variacion.py:153-169`, mostrado por
`apps/segurplus/paginas/evolucion.py:245-256`.

La función suma los efectos de todos los conceptos y divide cada uno por la variación total:

```python
variacion_total = suma_cantidad + suma_precio + suma_cruzado
proporcion_precio = suma_precio / variacion_total
```

El problema es que `variacion_total` es la suma **algebraica**: los efectos pueden tener signos
opuestos y cancelarse, dejando un denominador arbitrariamente chico mientras los numeradores
siguen siendo grandes. Escenario verificado —y es literalmente el caso de uso que motivó el
proyecto: la empresa da de baja líneas mientras el proveedor aumenta el precio unitario—:

```
sube_precio  : efecto_cantidad=0     efecto_precio=1000  var=1000
baja_cantidad: efecto_cantidad=-900  efecto_precio=0     var=-900

-> variacion total = 100
-> efecto_dominante([a, b], umbral=0.60) == ('precio', 10.0)
-> el tablero imprime: "El cambio de $+100.00 fue mayormente por PRECIO (1000%)"
```

`evolucion.py` lo muestra tal cual con `f"({abs(proporcion_dominante):.0%})"`, sin ninguna
verificación de rango. Cuanto mejor se cancelan los efectos, más grande el porcentaje: con una
variación total cercana a cero el número tiende a infinito. Y la frase no es solo fea — es
**falsa en sustancia**: decir "mayormente por precio" cuando el precio explica 1000% de una
variación que quedó casi en cero le da al usuario una lectura completamente equivocada de lo que
pasó (pasaron dos cosas grandes en direcciones opuestas, que es una historia muy distinta de
"subió el precio").

**Cobertura**: los seis tests de `efecto_dominante` en `tests/analisis/test_variacion.py:154-202`
usan todos efectos del mismo signo (solo precio, solo cantidad, mixto con ambos positivos, sin
variación, dos conceptos ambos de precio). **No hay ningún test con efectos de signo opuesto**,
que es el único caso donde la función se rompe.

**Relación con la regla de oro**: CLAUDE.md dice que "un error de signo en un ratio que ve un
cliente es el peor error posible de esta empresa". Este es un cociente cuyo denominador puede
cambiar de signo y de magnitud independientemente del numerador, mostrado como porcentaje, en
la frase más visible del tablero.

**Líneas de corrección posibles** (para el plan, no para acá): dividir por `Σ|efectos|` en vez
de por la suma algebraica, que acota naturalmente a [0, 1] y responde la pregunta correcta
("¿qué parte del movimiento total fue precio?"); o exigir que `|variacion_total|` supere un
mínimo —relativo a `total_0`, y desde YAML, no hardcodeado— antes de emitir cualquier veredicto,
mostrando en su lugar "hubo movimientos grandes que se compensaron".

---

## Altos

### A-31 — `scripts/rehomologar.py` crashea con `TypeError` ante cualquier fila anterior al Bloque 3

**Dónde**: `scripts/rehomologar.py:86-89`.

```python
print(
    f"  {c.descripcion!r}: {c.concepto_antes} "
    f"({c.score_antes:.3f}) -> sin clasificar ({c.score_despues:.3f})"
)
```

`score_antes` es `float | None` (así está declarado en `CambioHomologacion`), y vale `None` para
**toda fila guardada antes de que existiera la columna `score_homologacion`** — es decir,
cualquier factura cargada antes de este release, que es exactamente la población que el script
existe para re-homologar. Reproducido:

```
TypeError: unsupported format string passed to NoneType.__format__
```

Rompe en el bloque de **REGRESIONES**, que es la salida más importante del script (el aviso de
que un alias nuevo le robó el match a otro concepto), y rompe el proceso entero antes de llegar
a la sección de nuevos/cambios y antes de cualquier `--aplicar`.

**Cobertura**: `tests/test_rehomologacion.py` usa `score_antes=0.3` en los dos únicos tests que
construyen un `CambioHomologacion` con ese campo (líneas 181 y 207). El caso `None` —el caso
real— no se prueba nunca.

**Nota**: se autocura después de una corrida exitosa (porque `aplicar_cambios` escribe siempre el
score), lo que lo hace más fácil de pasar por alto: falla una sola vez, justo la primera, que es
la que importa.

---

### A-32 — el botón "Re-homologar ahora" escribe sin dry-run, descarta su propio informe y filtra la conexión

**Dónde**: `apps/segurplus/paginas/sin_clasificar.py:89-116`.

Tres problemas distintos en el mismo bloque:

**1. Saltea la garantía de no-destructividad que el CLI sí tiene.** `scripts/rehomologar.py` fue
diseñado explícitamente con dry-run por defecto: su propio docstring dice que escribe "solo con
`--aplicar`", y el plan del bloque lo justificó con "toca `data/reales/facturas.duckdb`, no puede
ser destructivo por accidente". La UI llama a `aplicar_cambios(con, cambios)` directo, sin
previsualización y sin confirmación, sobre la misma base real. El mismo riesgo que se cuidó en la
terminal queda abierto a un click.

**2. `st.rerun()` borra el informe que acaba de escribir.** El orden es:

```python
aplicar_cambios(con, cambios)          # línea  99
...                                     # 100-113: escribe regresiones y cambios en pantalla
st.rerun()                              # línea 114
```

El `st.rerun()` reejecuta la página desde cero, así que **todo lo renderizado en las líneas
100-113 se descarta antes de que el usuario lo vea**. Las regresiones —el aviso de "un alias
nuevo le robó el match a otro concepto", el motivo entero por el que ese bloque existe— aparecen
y desaparecen en el mismo instante. El usuario ve la tabla actualizada y nada más: ningún rastro
de qué cambió ni de qué se rompió.

**3. `con.close()` queda inalcanzable.** Está en la línea 116, después del `st.rerun()` de la 114.
Cada re-homologación desde la UI deja una conexión DuckDB abierta.

---

### A-33 — `evolucion.py:99` tumba la página entera con la `ValueError` que A-11/A-12 buscaron evitar

**Dónde**: `apps/segurplus/paginas/evolucion.py:99`.

```python
alertas_periodo_faltante = alertas_por_periodo_faltante([date.fromisoformat(p) for p in periodos])
```

Sin `try`. Sesenta y ocho líneas más abajo, el **mismo** `date.fromisoformat` sobre los **mismos**
datos está protegido con todo el cuidado del mundo (líneas 166-178), con un mensaje que explica
que el problema es el formato de fecha y no el IPC — la corrección de A-12, con su comentario
citando el hallazgo.

Esa protección es inalcanzable: si un `periodo_desde` de la base tiene formato inválido, la
página revienta con un traceback en la línea 99, mucho antes. Y el caso no es hipotético — el
propio comentario de A-12 lo anticipa: *"se normaliza al extraer, pero un dato viejo en la base
pudo guardarse antes de ese fix"*. La normalización de A-11 protege lo que entra por
`factura_desde_json`, no lo que ya está guardado ni lo que entre por otra vía.

Consecuencia adicional: como el error es una excepción no capturada, `con.close()` (línea 378)
tampoco corre. Ver A-48.

---

## Medios

### A-34 — umbral hardcodeado en la UI, contra la regla explícita de CLAUDE.md

**Dónde**: `apps/segurplus/paginas/sin_clasificar.py:52-54`.

```python
"¿Le falta poco?": "🟡 agregá un alias" if score >= umbral - 0.15 else "⚪ concepto nuevo",
```

El `0.15` es el que decide, para cada concepto sin clasificar, si la pantalla le dice al usuario
"esto casi homologa, agregale un alias" o "esto es un concepto nuevo de verdad" — o sea, dirige
el trabajo de calibración, que es la razón de ser de la pantalla. No está en ningún YAML.

CLAUDE.md: *"Nunca hardcodear parámetros… Van en `data/…*.yaml`"*. El resto del repo lo respeta
con disciplina (`umbral_coincidencia` en `data/homologacion.yaml`, los cinco umbrales de
`data/alertas.yaml`, `umbral_efecto_dominante` recién agregado en este mismo release). Este es
el único número de decisión que quedó incrustado en el código.

---

### A-35 — `aplicar_cambios` reescribe todas las filas, informa mal el conteo, y no usa transacción

**Dónde**: `core/rehomologacion.py:129-140`.

```python
for c in cambios:
    con.execute("UPDATE conceptos SET concepto_normalizado = ?, score_homologacion = ? ...")
return len(cambios)
```

Tres cosas:

- **Escribe también los `sin_cambio`.** `recalcular` devuelve un `CambioHomologacion` por *cada
  fila de la base*, no solo por las que cambiaron, y `aplicar_cambios` no filtra. Re-homologar
  una base de 10.000 conceptos para corregir 3 hace 10.000 `UPDATE`.
- **El conteo que devuelve es engañoso.** El docstring dice "Devuelve cuántas filas se tocaron",
  y el script lo imprime como `"Aplicado: N fila(s) actualizada(s)"` — con N igual al total de
  filas evaluadas, no a las que cambiaron. Un usuario que corrige un alias y lee "Aplicado: 4.312
  filas actualizadas" no tiene forma de saber que solo cambiaron 3. (La UI sí filtra por
  `tipo != "sin_cambio"` para decidir si mostrar algo, pero después igual pasa la lista completa
  a `aplicar_cambios`.)
- **Sin `BEGIN`/`COMMIT`.** Si falla a mitad —disco lleno, la base tomada por otro proceso—, queda
  parcialmente re-homologada, sin forma de saber hasta dónde llegó. Es una escritura masiva sobre
  `data/reales/facturas.duckdb`, la base real.

---

### A-36 — `recalcular` lee `data/homologacion.yaml` del disco una vez por fila

**Dónde**: `core/rehomologacion.py:83`, vía `core/analisis/homologacion.py:137`.

```python
concepto, score = homologar_concepto(fila.descripcion, diccionario)   # sin umbral=
```

Como no se pasa `umbral`, cada llamada ejecuta `umbral_coincidencia()`, que hace `read_text()` +
`yaml.safe_load()` del archivo. Una fila, una lectura de disco y un parseo de YAML.

Lo notable es que **el docstring del propio `recalcular` presume exactamente de lo contrario**:

> `diccionarios_por_servicio`: … se carga UNA VEZ por servicio afuera de acá, no una vez por fila
> (con decenas de conceptos por servicio, releer el YAML por cada uno sería decenas de lecturas
> de disco redundantes).

El cuidado se puso en el diccionario y se pasó por alto en el umbral, que se lee desde adentro
del mismo bucle. Mismo patrón, menos grave, en `efecto_dominante` → `_umbral_dominancia()` (una
lectura por render de página) y en `homologar_concepto` en general.

**Nota para quien corrija**: la razón por la que estos YAML se leen sin cache está documentada y
es buena (un YAML corrupto no debe tumbar el import ni la app). La corrección no es cachear a
nivel de módulo, sino leer una vez por *operación* y pasar el valor hacia adentro — que es lo que
ya hace el parámetro `umbral` de `homologar_concepto`, hoy sin usar desde `recalcular`.

Relacionado: `homologar_concepto` recalcula `quitar_periodo(c)` para **cada alias de cada
concepto en cada llamada** (línea 143). Sobre una base entera son cientos de miles de
sustituciones de regex sobre los mismos textos constantes del diccionario.

---

### A-37 — los empates de score se resuelven por el orden del YAML, en silencio

**Dónde**: `core/analisis/homologacion.py:144`.

```python
if score > mejor_score:      # estricto
```

Ante dos conceptos con score idéntico gana el primero que aparezca al iterar el diccionario — o
sea, el orden en que `_combinar` leyó los archivos (`comunes.yaml` primero, después
`<servicio>.yaml`) y el orden de las claves dentro de cada YAML. **Reordenar un archivo de
conceptos, o renombrarlo de forma que cambie su orden alfabético, cambia el resultado de la
homologación sin que nada avise.**

Hoy es latente, pero este release aumentó la superficie: `data/conceptos/telefonia.yaml` ahora
tiene dos conceptos con alias casi idénticos como texto —

```yaml
servicio_telefonia:
  - servicio telefonico
abono_movil:
  - abono telefonico
```

— que compiten de cerca por cualquier descripción del estilo "Abono servicio telefónico". El
tripwire de `test_umbral_por_defecto_se_lee_del_yaml_real` vigila el score de un caso conocido,
pero no detecta un empate resuelto por orden.

**Además, conceptualmente**: `servicio_telefonia` y `abono_movil` pueden ser el mismo concepto
económico para Movistar (el "servicio de telefonía" *es* el abono). Si el proveedor cambia el
texto de un mes al otro, la serie se parte en dos conceptos homologados distintos — más difícil
de detectar que el caso sin homologar, porque la pantalla "Sin clasificar" no lo muestra. Vale
decidir con las facturas reales en mano si son uno o dos conceptos.

---

### A-38 — un score `NULL` se muestra como `0.00`, indistinguible de "no se parece a nada"

**Dónde**: `core/almacenamiento.py:203` (`score or 0.0`) y
`apps/segurplus/paginas/sin_clasificar.py:51,75`.

Las filas cargadas antes del Bloque 3 tienen `score_homologacion` en `NULL`: el score no se midió,
no es que haya dado bajo. `conceptos_sin_clasificar` lo colapsa a `0.0`, y la pantalla las
presenta como score `0,000` con el semáforo "⚪ concepto nuevo" — la misma pinta que un concepto
genuinamente sin parecido.

Importa por dónde va a parar ese número: **el histograma de scores es la evidencia con la que se
va a calibrar `umbral_coincidencia`**, que hoy es un 0,60 conservador elegido sin datos (el propio
`data/homologacion.yaml` lo dice, y lo dice también el README). Un conjunto de observaciones
falsas amontonadas en el extremo bajo distorsiona exactamente la distribución que se quiere leer.

Lo correcto sería distinguir los tres estados (`NULL` = no medido, `0.0` = medido y sin parecido,
`> 0` = medido) y excluir los no medidos del histograma, o mostrarlos aparte con un "falta
re-homologar para medirlos".

---

### A-39 — la comparación admite períodos iguales o invertidos, sin aviso

**Dónde**: `apps/segurplus/paginas/evolucion.py:102-103` y 247-261.

Los dos selectores se alimentan de la misma lista completa de períodos, sin ninguna restricción
cruzada. Dos consecuencias:

- **Mismo período en los dos.** Todo da cero, `efecto_dominante` devuelve `"sin_variacion"`, y
  la cadena `if tipo == "precio" / elif "cantidad" / elif "mixto"` **no tiene rama para
  `"sin_variacion"`**: no se imprime ninguna leyenda. Queda un hueco mudo donde debería estar la
  frase principal del tablero, sin que nada explique por qué.
- **Período base posterior al de comparación.** Se calcula todo al revés (un aumento se muestra
  como caída) y nada lo marca. El caption del veredicto, las métricas y el Excel salen todos con
  el signo invertido respecto de lo que el usuario probablemente quiso preguntar.

Los valores por defecto son correctos (anteúltimo vs. último), así que esto solo aparece si
alguien toca los selectores — pero tocarlos es el uso normal de la página.

---

### A-40 — re-homologar con un diccionario vacío borra homologaciones en masa

**Dónde**: `core/rehomologacion.py:82` + `core/analisis/diccionario.py:59-67`.

`recalcular` hace `diccionarios_por_servicio.get(fila.servicio, {})`. Con un diccionario vacío,
`homologar_concepto` no entra al bucle, devuelve `(None, 0.0)`, y `aplicar_cambios` escribe ese
`NULL` sobre el `concepto_normalizado` que había. Las rutas que llevan a un diccionario vacío o
empobrecido:

- `data/conceptos/` no existe o no es legible → `cargar_diccionario` devuelve `{}` para **todos**
  los servicios.
- El YAML de un servicio se renombra o se borra → ese servicio degrada a solo `comunes.yaml`,
  perdiendo sus conceptos propios.

El script las reporta como `regresion` antes de aplicar (bueno), pero igual las aplica si se pasó
`--aplicar`; la UI ni siquiera da la oportunidad de mirar (A-32). El daño es silencioso y no
trivial de revertir: el `concepto_normalizado` anterior no se guarda en ningún lado.

Falta una guarda del tipo "si el diccionario de un servicio quedó vacío, no escribas nada de ese
servicio y avisá" — el mismo criterio de fallo seguro que ya usa `cargar_diccionario` al decidir
no combinar todos los YAML cuando conoce el servicio.

**Relacionado**: cuando `fila.servicio` es `NULL`, `scripts/rehomologar.py:71` llama a
`cargar_diccionario(None)`, que combina **todos** los YAML de todos los servicios — reintroduciendo
para esas filas la competencia entre servicios que A-3 vino a eliminar. Es consistente con lo que
hace `core/pipeline.py`, así que no es una regresión de este release, pero queda como hueco
conocido.

---

### A-41 — el docstring de `agregar_conceptos` describe el bug que el Bloque 2 eliminó

**Dónde**: `core/analisis/agregacion.py:111-113`.

> Las filas sin homologar (`concepto_normalizado is None`) se agrupan bajo su propia descripción
> tal cual, para no perderlas del análisis

"Bajo su propia descripción tal cual" **es literalmente A-28**: es la descripción cruda, la que
genera dos claves para "…Agosto 2026" y "…Septiembre 2026". El Bloque 2 cambió eso a
`PREFIJO_SIN_HOMOLOGAR + quitar_periodo(descripcion)` y actualizó el docstring de `_clave` con
una explicación larga y correcta, pero dejó este párrafo intacto unas líneas más abajo, en la
función pública que alguien abre primero.

Un docstring que describe el comportamiento anterior es peor que ninguno: el próximo que lea esto
para entender si la clave es estable entre períodos va a concluir que no lo es.

---

### A-42 — quedó sin implementar el porcentaje que el plan del Bloque 5 prometía

**Dónde**: `apps/segurplus/paginas/sin_clasificar.py:39-44`.

El plan del bloque pedía, textualmente: *"Métricas: cuánta plata está sin clasificar **y qué % del
total** (el cociente va a `core/` con test)"*. El porcentaje no existe: la pantalla muestra el
importe absoluto y el conteo de conceptos distintos, y no hay ninguna función en `core/` ni ningún
test que calcule el cociente.

Sin denominador, la métrica principal de la pantalla no es interpretable: "$180.000 sin clasificar"
no dice si es el 2% de la facturación (irrelevante) o el 60% (el análisis entero no significa
nada). Y es justo el número que tendría que decidir si vale la pena seguir agregando alias.

---

### A-43 — el importe sin clasificar suma pesos nominales de meses distintos

**Dónde**: `apps/segurplus/paginas/sin_clasificar.py:40` y `core/almacenamiento.py:196-201`.

`sum(importe …)` sobre `conceptos_sin_clasificar`, que agrupa por `(servicio, descripcion)` a
través de **todos los períodos cargados**, sin deflactar — y el `ORDER BY sum(c.importe) DESC` que
define la priorización "la plata manda" usa ese mismo total nominal.

Es el error contra el que existe todo `core/deflactor/`, y que el README del proyecto pone como
premisa: *"un balance argentino leído en pesos nominales miente"*. Con inflación argentina y un
año de facturas cargadas, un concepto que apareció mucho hace doce meses pesa menos de lo que
debería frente a uno reciente, y el orden de la tabla —que es lo que dirige en qué alias trabajar
primero— sale sesgado hacia lo nuevo.

No es grave con dos o tres meses cargados. Sí lo es en el uso que el README describe (ir
completando los YAML proveedor por proveedor a lo largo del tiempo).

---

## Bajos / mejoras

### A-44 — el Excel se regenera entero en cada rerun de la página

**Dónde**: `apps/segurplus/paginas/evolucion.py:361-370`.

`generar_reporte_excel(...)` se llama incondicionalmente **antes** del `st.download_button`, sin
`@st.cache_data`. Streamlit reejecuta el archivo completo ante cualquier interacción, así que
cambiar de pestaña o mover un selectbox arma el workbook entero con openpyxl —cuatro hojas, con
formato— aunque nadie lo descargue nunca. Debería estar detrás de un cache con las mismas claves
(servicio y los dos períodos) o generarse a demanda.

### A-45 — trabajo redundante en el render

`_acumular` recorre las filas **seis veces por render**: `agregar_conceptos`,
`conceptos_con_cantidad_neta_cero` y `conceptos_con_cantidad_neta_negativa` la llaman cada una por
su cuenta, × 2 períodos (`evolucion.py:118-119, 143-148`). Las tres funciones podrían compartir un
único acumulado.

`serie_nominal_y_real` (`core/analisis/serie.py:43-50`) llama a `a_pesos_constantes` una vez por
período; con `df_ipc=None` eso es una llamada a `leer_ipc()` —lectura del parquet, o descarga si
no existe— **por cada punto de la serie**. La página sí pasa `df_ipc` (bien), pero la firma invita
al uso costoso desde cualquier otro llamador, y nada lo advierte en el docstring.

### A-46 — formato de moneda en convención estadounidense

Todo el tablero usa `f"${x:,.2f}"` → `$1,234.56`. En Argentina se escribe `$1.234,56`. Aparece en
las métricas y el delta (`evolucion.py:232-240`), la frase de veredicto (249, 254, 259), la métrica
de importe sin clasificar (`sin_clasificar.py:43`) y los ejes de los gráficos vía
`separatethousands` (`estilo.py:40`). El resto del proyecto está íntegramente en español y
pensado para lectores argentinos; los números no acompañan.

### A-47 — desprolijidades de parámetros y constantes

- **`data/alertas.yaml`**: se agregó `umbral_efecto_dominante` sin tocar
  `vigencia_desde: "2026-09-01"`, que el propio archivo define como "desde cuándo rige este set de
  umbrales (por si se recalibran)". El campo pierde sentido si no se mantiene.
- **`apps/segurplus/estilo.py`**: `FONDO` (`#F2F5F7`) se exporta como constante y se incluye en
  `PALETA`, pero ningún gráfico lo usa — `aplicar_estilo` fija `plot_bgcolor="white"` y
  `paper_bgcolor="white"`. O los gráficos deberían usar el fondo institucional, o la constante no
  debería sugerir que lo hacen.
- **`ordenar_por_severidad`** (`core/analisis/alertas.py`): `_ORDEN_SEVERIDAD.get(a.severidad, 99)`
  manda cualquier severidad desconocida al fondo de la lista en silencio. Un typo en un
  `severidad=` de `alertas.py` se traduce en una alerta que se muestra siempre última, sin ninguna
  señal de que algo está mal.

### A-48 — conexiones DuckDB sin `try/finally` en las páginas

`evolucion.py` y `sin_clasificar.py` abren con `conectar()` al principio y cierran con
`con.close()` al final del script, sin `try/finally` ni context manager. Cualquier excepción
intermedia —A-33 es un ejemplo concreto, A-32 otro— salta el cierre. Streamlit reejecuta el
archivo entero en cada interacción, así que las conexiones sin cerrar se acumulan mientras el
proceso siga arriba. Se agrava con la limitación de concurrencia de DuckDB ya anotada en A-15.

---

## Tabla resumen

| # | Severidad | Dónde | Qué |
|---|---|---|---|
| A-29 | **Crítico** | `core/analisis/homologacion.py:50-53` | `_MESES` no incluye `may` ni `sept`: A-28 se reproduce entero (efecto_precio=0, dos conceptos fantasma) para esos meses |
| A-30 | **Crítico** | `core/analisis/variacion.py:162-169` | `efecto_dominante` divide por la suma algebraica: con efectos de signo opuesto el tablero dice "mayormente por PRECIO (1000%)". Sin test de signos opuestos |
| A-31 | Alto | `scripts/rehomologar.py:88` | `TypeError` al formatear `score_antes=None`: crashea con cualquier fila anterior al Bloque 3, justo en el bloque de regresiones |
| A-32 | Alto | `apps/segurplus/paginas/sin_clasificar.py:89-116` | El botón re-homologa sin dry-run ni confirmación (el CLI sí lo exige), el `st.rerun()` descarta el informe de regresiones, y deja `con.close()` inalcanzable |
| A-33 | Alto | `apps/segurplus/paginas/evolucion.py:99` | `date.fromisoformat` fuera del `try`: un período mal formado tumba la página antes de llegar a la protección de A-12 |
| A-34 | Medio | `apps/segurplus/paginas/sin_clasificar.py:53` | Umbral `0.15` hardcodeado — decide el semáforo de calibración, contra la regla explícita de CLAUDE.md |
| A-35 | Medio | `core/rehomologacion.py:129-140` | `aplicar_cambios` escribe también los `sin_cambio`, informa el total como "filas actualizadas", y no usa transacción |
| A-36 | Medio | `core/rehomologacion.py:83` | `recalcular` lee `data/homologacion.yaml` una vez por fila, contradiciendo su propio docstring |
| A-37 | Medio | `core/analisis/homologacion.py:144` | Empates de score resueltos por orden del YAML, en silencio; agravado por `servicio_telefonia` vs `abono_movil`, que se solapan |
| A-38 | Medio | `core/almacenamiento.py:203` | `score or 0.0` colapsa "no medido" con "sin parecido"; contamina el histograma que sirve para calibrar el umbral |
| A-39 | Medio | `apps/segurplus/paginas/evolucion.py:102-103` | Se pueden elegir períodos iguales (veredicto mudo, sin rama para `sin_variacion`) o invertidos (todo con el signo al revés, sin aviso) |
| A-40 | Medio | `core/rehomologacion.py:82` | Un diccionario vacío o un YAML de servicio faltante borra `concepto_normalizado` en masa, sin guarda |
| A-41 | Medio | `core/analisis/agregacion.py:111-113` | El docstring de `agregar_conceptos` sigue describiendo el fallback por descripción cruda, que *es* el bug A-28 |
| A-42 | Medio | `apps/segurplus/paginas/sin_clasificar.py:39-44` | Falta el "% del total sin clasificar" que el plan prometía con función en `core/` y test: la métrica queda sin denominador |
| A-43 | Medio | `apps/segurplus/paginas/sin_clasificar.py:40` | El importe sin clasificar suma pesos nominales de meses distintos sin deflactar, y con eso ordena la priorización |
| A-44 | Bajo | `apps/segurplus/paginas/evolucion.py:361-370` | El Excel se genera entero en cada rerun, sin cache, aunque nadie lo descargue |
| A-45 | Bajo | `agregacion.py`, `core/analisis/serie.py:43-50` | `_acumular` recorre las filas 6 veces por render; `serie_nominal_y_real` con `df_ipc=None` lee el IPC una vez por período |
| A-46 | Bajo | tablero completo | Formato de moneda `$1,234.56` (convención estadounidense) en toda la UI |
| A-47 | Bajo | `data/alertas.yaml`, `estilo.py`, `alertas.py` | `vigencia_desde` sin actualizar; `FONDO` exportado y sin usar; severidad desconocida degradada en silencio |
| A-48 | Bajo | `evolucion.py`, `sin_clasificar.py` | Conexiones DuckDB sin `try/finally`: cualquier excepción intermedia las deja abiertas |

---

## Qué conviene resolver antes de cargar la primera factura real

**Bloqueantes** — tocan números que el usuario va a leer, o pueden dañar la base:

- **A-29** y **A-30**: los dos rompen el resultado central de la herramienta, y los dos están en
  código escrito para arreglar o mostrar ese mismo resultado. A-29 además necesita un test que
  recorra las 14 grafías, no solo la que falló.
- **A-31**: hace que el circuito de calibración falle en su primer uso real.
- **A-33** y **A-40**: uno tumba la pantalla principal, el otro puede borrar homologaciones sin
  vuelta atrás.

**Conviene, pero puede esperar a tener facturas cargadas**: A-32 y A-34 (el circuito de
calibración se vuelve confiable), A-37 y A-38 (deciden con qué evidencia se calibra el umbral),
A-42 y A-43 (hacen interpretable la pantalla de sin clasificar).

**Puede esperar**: el resto. A-46 es cosmético pero se nota en cada pantalla; conviene agruparlo
con cualquier otro retoque de UI en vez de hacerlo solo.

**Siguen diferidos de la auditoría anterior**, sin cambios: **A-26** (`_parsear_monto` con
separadores de miles mezclados) y **A-27** (alerta de período faltante que asume periodicidad
mensual y hace que un servicio bimestral alerte siempre). Los dos necesitan facturas reales para
verificarse, así que su momento es justamente después de la primera carga.
