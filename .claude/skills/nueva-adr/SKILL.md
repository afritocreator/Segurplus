---
name: nueva-adr
description: Crea un nuevo Architecture Decision Record en docs/decisiones/ siguiendo la estructura y el tono de los ADRs existentes del repo. Usar cuando se agrega una dependencia nueva (obligatorio por CLAUDE.md, "Stack cerrado"), se cambia de proveedor de hosting/persistencia, o se toma cualquier decisión de arquitectura que valga la pena dejar por escrito para quien lea el repo después.
---

# Nueva ADR

Los ADRs de Segurplus viven en `docs/decisiones/`, numerados secuencialmente
(`ADR-001-...` a `ADR-005-...`). Antes de escribir uno nuevo:

1. Mirá el último número usado en `docs/decisiones/` y usá el siguiente.
2. Leé al menos un ADR existente (por ejemplo `ADR-005-fastapi-y-render.md`) para
   mantener el mismo tono: directo, en español, sin relleno, con ejemplos concretos
   del propio repo en vez de justificaciones genéricas.
3. Si esta decisión **reemplaza** una ADR anterior, decilo explícito en el
   encabezado ("Reemplaza a `ADR-00X-....md`") y actualizá el estado de la vieja
   a "reemplazada".

## Estructura esperada

```markdown
# ADR-0XX: <título corto, la decisión en sí, no el problema>

**Estado**: aceptado | propuesto | reemplazado. **Fecha**: YYYY-MM-DD.
[Reemplaza a `ADR-00X-....md`.]

## Contexto

Qué problema concreto motivó esto. Citar quién lo pidió o qué se rompió, no
"para mejorar la arquitectura" en abstracto.

## Decisión

La decisión en una o dos frases, en negrita el término clave.

## Por qué [la opción elegida]

Razones concretas, ancladas en este repo (líneas de código, tests existentes,
restricciones ya documentadas como "costo cero" o "sin JavaScript en web/").

## Por qué no [alternativas descartadas]

Cada alternativa seria que se consideró y por qué no, en una sección corta.

## Limitaciones conocidas de esta decisión

Honesto sobre qué se sacrifica. Los ADRs de este repo no venden la decisión
como perfecta.
```

## Reglas del repo que toda ADR nueva tiene que respetar

- **Nunca depender de un servicio pago** salvo que el usuario lo haya aprobado
  explícitamente -- "costo cero" es una restricción repetida en varios ADRs
  (ver ADR-002, ADR-003).
- **Nada de JavaScript en `web/`** ni frameworks de frontend -- ver ADR-005 y
  CLAUDE.md, sección "Stack cerrado".
- Si la ADR agrega una dependencia de Python, agregala también a
  `pyproject.toml` (como dependencia normal o como extra opcional si no la
  necesita todo el mundo, ver el patrón de `s3` y `vision` en `pyproject.toml`)
  y explicá esa elección en la propia ADR.
- Actualizá `CLAUDE.md` si la decisión cambia la lista de stack cerrado, y
  `docs/estado.md` si cambia qué está en progreso o reemplazado.

## Al terminar

Mostrale al usuario la ruta del archivo nuevo y un resumen de una frase de la
decisión -- no hace falta pegar el contenido completo en el chat si ya se
escribió el archivo.
