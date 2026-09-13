"""Test de apps/segurplus/estilo.py -- UI pura (paleta + formato de
figuras Plotly), sin cálculo, pero vale la pena candar que aplicar_estilo
no rompe una figura ni cambia los datos que ya tiene."""

import plotly.graph_objects as go

from apps.segurplus.estilo import NAVY, PALETA, SECUENCIA_CATEGORICA, aplicar_estilo


def test_paleta_tiene_los_cuatro_colores_institucionales():
    assert set(PALETA) == {"navy", "dorado", "gris", "fondo"}
    assert PALETA["navy"] == NAVY


def test_secuencia_categorica_empieza_con_navy():
    assert SECUENCIA_CATEGORICA[0] == NAVY


def test_aplicar_estilo_no_toca_los_datos_de_la_figura():
    fig = go.Figure()
    fig.add_bar(x=["a", "b"], y=[1, 2])
    resultado = aplicar_estilo(fig)
    assert resultado is fig  # modifica in-place y devuelve la misma figura
    assert list(fig.data[0].y) == [1, 2]


def test_aplicar_estilo_formato_moneda_opcional():
    fig_con_moneda = aplicar_estilo(go.Figure(), formato_moneda=True)
    assert fig_con_moneda.layout.yaxis.tickprefix == "$"

    fig_sin_moneda = aplicar_estilo(go.Figure(), formato_moneda=False)
    assert fig_sin_moneda.layout.yaxis.tickprefix is None
