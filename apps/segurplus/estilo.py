"""Paleta institucional y estilo compartido para los gráficos Plotly del
tablero -- mismos colores que `.streamlit/config.toml` (el tema de los
widgets) y que usan los documentos/presentaciones de la consultora.

UI pura: cero cálculo acá (CLAUDE.md, cáscara fina) -- solo formato visual
de figuras que ya vienen armadas con los datos de `core/`.
"""

from __future__ import annotations

from typing import Final

import plotly.graph_objects as go

NAVY: Final = "#14324D"
DORADO: Final = "#B08D3E"
GRIS: Final = "#5A5A5A"
FONDO: Final = "#F2F5F7"

PALETA: Final = {"navy": NAVY, "dorado": DORADO, "gris": GRIS, "fondo": FONDO}

# Mismo orden que chartCategoricalColors en .streamlit/config.toml, para
# que un gráfico Plotly y un gráfico nativo de Streamlit (st.bar_chart,
# etc.) usen la misma secuencia de colores en la misma comparación.
SECUENCIA_CATEGORICA: Final = [NAVY, DORADO, GRIS, "#7C98AC", "#D8C48A"]


def aplicar_estilo(fig: go.Figure, *, formato_moneda: bool = True) -> go.Figure:
    """Aplica el estilo institucional a una figura ya armada -- fuente,
    márgenes, `hovermode` unificado y, opcionalmente, formato `$` en el eje
    de valores. Modifica `fig` in-place y también la devuelve, para poder
    encadenar (`fig = aplicar_estilo(fig)`)."""
    fig.update_layout(
        font_family="Helvetica, Arial, sans-serif",
        font_color=NAVY,
        colorway=SECUENCIA_CATEGORICA,
        hovermode="x unified",
        margin={"l": 40, "r": 20, "t": 40, "b": 40},
        plot_bgcolor="white",
        paper_bgcolor="white",
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02},
    )
    if formato_moneda:
        fig.update_yaxes(tickprefix="$", separatethousands=True)
    return fig
