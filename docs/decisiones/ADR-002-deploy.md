# ADR-002: Streamlit Community Cloud para el deploy, no Vercel

**Estado**: aceptado. **Fecha**: 2026-09-09.

## Contexto

Se necesita que Segurplus quede accesible desde las computadoras del trabajo, sin
instalar nada, gratis. `afritocreator/Kleric-` (repo hermano) despliega en Vercel porque
es una app Next.js.

## Decisión

Se publica en **Streamlit Community Cloud** (share.streamlit.io), no en Vercel.

## Por qué no Vercel

Vercel está construido para aplicaciones Next.js/serverless: funciones que responden y
terminan, o un frontend estático. Streamlit necesita un **proceso de servidor persistente**
con estado en memoria y una conexión en vivo con el navegador (WebSocket) — el modelo de
ejecución de Vercel no soporta eso. Correr Segurplus ahí requeriría reescribir el tablero
entero en Next.js/React, lo que tira el trabajo ya hecho: todo el análisis financiero
(deflactor por IPC, descomposición precio/cantidad) ya está escrito y probado en Python,
heredado de `afritocreator/Consultora` (ver CLAUDE.md, stack cerrado).

## Por qué Streamlit Community Cloud

- Gratis, sin límite de tiempo, pensado exactamente para este stack (Python + Streamlit).
- Mismo servicio que ya usa `Consultora` (ver `requirements.txt` ahí, mismo patrón).
- Deploy desde GitHub directo, sin configuración de infraestructura.
- Secrets (como `GEMINI_API_KEY`) se cargan desde el dashboard, nunca en el repo.

## Consecuencias

- El disco del servidor gratuito **no es persistente** entre reinicios por inactividad —
  `data/reales/facturas.duckdb` puede perderse. Mientras se prueba la herramienta no es
  grave (recargar el mismo lote de PDFs no duplica nada, es idempotente por hash), pero
  si el equipo empieza a depender de esto día a día, hay que decidir dónde persiste la
  base real (opciones: un volumen pago, o exportar a Excel/Sheets después de cada carga
  como respaldo — no se resuelve en este ADR).
- Si en algún momento se necesita autenticación, multiusuario real o un dominio propio, ahí
  sí conviene reevaluar el stack — no antes.
