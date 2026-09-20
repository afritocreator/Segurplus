"""App web de Segurplus (FastAPI + HTML plano), reemplazo del tablero
Streamlit -- ver `docs/decisiones/ADR-005-fastapi-y-render.md` y el plan de
rediseño de septiembre 2026 en `docs/estado.md`.

Reusa `core/` sin tocarlo: esta carpeta es la única que sabe de HTTP,
formularios y cookies. Ningún cálculo financiero ni de extracción vive
acá -- mismo criterio de "cáscara fina" que ya seguían `apps/segurplus/`.
"""
