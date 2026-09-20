# Banco de medición de extracción

## Por qué existe

Antes de este banco, nadie había medido nunca si Gemini lee bien una
factura real. `docs/auditoria-2026-09-facturas-reales.md` (hallazgo B-3) y
`docs/estado.md` documentan el mismo pendiente desde el principio: el
refuerzo del prompt para las líneas de impuesto con dos montos (base
imponible + importe) se escribió y se commiteó, pero **nunca se ejecutó una
sola vez contra la API real**, por falta de `GEMINI_API_KEY` en el entorno
de desarrollo.

Cambiar de proveedor de IA (Groq, Cerebras, SambaNova, lo que sea) sin haber
medido primero es adivinar dos veces en vez de una. Este banco convierte
"¿lee bien las facturas?" en un número reproducible, para que la decisión de
qué proveedor usar salga de una tabla y no de una opinión.

## Cómo funciona

`scripts/banco_extraccion.py` corre uno o más proveedores registrados
contra un set fijo de facturas reales, y compara campo a campo lo que cada
uno extrajo contra una **verdad de referencia** tipeada a mano mirando el
PDF (no generada por ningún modelo, para no medir un modelo contra sí
mismo).

```
export GEMINI_API_KEY=...
python scripts/banco_extraccion.py                    # todos los proveedores registrados
python scripts/banco_extraccion.py --proveedor gemini  # uno solo
```

Salida: una tabla con, por factura y proveedor, el % de campos de cabecera
correctos, el % de líneas de concepto encontradas, el % de líneas de
impuesto encontradas (el caso que rompía en la auditoría: tomar la base
imponible en vez del importe), si el subtotal y el total coinciden, si la
validación aritmética de `core/extraccion/validacion.py` cierra, y cuánto
tardó. Nunca escribe en ninguna base de datos: es de solo lectura, mismo
criterio que `scripts/probar_extraccion.py`.

## Dónde vive la verdad de referencia (y por qué no está acá)

En `data/reales/banco/`: un `<nombre>.pdf` (la factura real) al lado de un
`<nombre>.yaml` (los datos correctos, tipeados a mano). Esa carpeta está
**siempre vacía en este repositorio** -- es zona restringida
(`data/reales/`, ver CLAUDE.md, protegida además por
`.claude/hooks/guard_facturas.py` y el pre-commit real de git). Ni los PDF
ni los YAML de verdad se commitean nunca; contienen datos reales de un
cliente (nombre, CUIT, importes).

Armar el set de verdad es un paso manual: conseguir una factura real,
mirarla, y tipear a mano en el YAML lo que dice -- emisor, CUIT, servicio,
período, cada concepto (descripción, cantidad, unidad, importe), cada
impuesto, subtotal y total. Cuanto más variado el set (proveedores,
layouts, a una columna y a dos), más vale la medición.

### Una lección real del primer set armado

Las primeras cuatro facturas reales usadas para armar este banco (dos de
luz de un municipio, dos de gas de una distribuidora) confirmaron algo que
la auditoría ya sospechaba: el layout a dos columnas de las facturas de gas
hace que ni siquiera un humano mirando las coordenadas x/y exactas de cada
línea de texto del PDF pueda reconstruir con certeza qué valor corresponde
a qué concepto -- la columna de etiquetas y la columna de valores son dos
flujos de texto independientes con interlineado distinto. La verdad de
referencia para esos casos agrupa las líneas ambiguas en un solo ítem en
vez de inventar una asignación fila por fila que no se puede verificar (ver
el comentario en el YAML de esa factura, si existe, para el detalle). El
banco sigue siendo útil en ese caso: el total y el subtotal de esas
facturas SÍ son 100% verificables, y son lo que más importa.

## Añadir un proveedor nuevo

`scripts/banco_extraccion.py::PROVEEDORES` es un diccionario chico
`nombre -> función(pdf_bytes, texto_extraido) -> FacturaExtraida`. Agregar
un proveedor nuevo es agregar una entrada ahí (ver
`core/extraccion/proveedores/` una vez que exista, Bloque 2 del plan de
rediseño de septiembre 2026 -- ese directorio es la versión genérica de
este registro, para cuando el pipeline real también soporte más de un
proveedor, no solo este script de medición).

## Qué hacer con el resultado

El número que importa (el % de campos correctos, promediado sobre todas las
facturas del set) se publica en `docs/estado.md`, nunca acá ni en el
código: este documento explica el método, no lleva resultados que quedarían
desactualizados en el primer commit siguiente.
