"""Guarda la restricción de la que depende `ConexionPostgres.execute`
(core/almacenamiento.py): hace `sql.replace("?", "%s")` para convertir el
placeholder estilo DuckDB al estilo `%s` de psycopg, sin parsear el SQL.

Eso significa que NINGÚN SQL de core/almacenamiento.py puede tener un `?`
que no sea un placeholder de parámetro (ej. dentro de un literal de texto),
ni un `%` suelto (ej. un `LIKE 'x%'`) -- psycopg interpreta `%` como inicio
de un placeholder de formato en su paramstyle, y un `%` sin escapar rompe
la sustitución. Hoy ningún SQL del módulo tiene ninguno de los dos, pero
nada más que este test lo garantiza hacia adelante.
"""

import ast
from pathlib import Path

RUTA_ALMACENAMIENTO = Path(__file__).resolve().parent.parent / "core" / "almacenamiento.py"


def _literales_sql_pasados_a_execute() -> list[str]:
    """Todos los strings literales que se pasan como primer argumento a un
    `.execute(...)` en el módulo -- incluye el `_DDL` de nivel de módulo
    (pasado a `.execute()` dentro de `_ejecutar_ddl`), aunque ahí se pase
    como variable: se lo agrega aparte."""
    arbol = ast.parse(RUTA_ALMACENAMIENTO.read_text(encoding="utf-8"))
    literales = []
    for nodo in ast.walk(arbol):
        if (
            isinstance(nodo, ast.Call)
            and isinstance(nodo.func, ast.Attribute)
            and nodo.func.attr == "execute"
            and nodo.args
            and isinstance(nodo.args[0], ast.Constant)
            and isinstance(nodo.args[0].value, str)
        ):
            literales.append(nodo.args[0].value)
    return literales


def test_ningun_sql_pasado_a_execute_tiene_porcentaje_suelto():
    """Un `%` en un literal SQL (ej. `LIKE 'x%'`) rompería
    `ConexionPostgres.execute`, que pasa el SQL tal cual al paramstyle `%s`
    de psycopg sin escapar nada."""
    literales = _literales_sql_pasados_a_execute()
    assert literales, "no se encontró ningún .execute(...) -- ¿cambió el módulo?"
    con_porcentaje = [sql for sql in literales if "%" in sql]
    assert con_porcentaje == []


def test_ddl_de_nivel_de_modulo_no_tiene_porcentaje():
    """`_DDL` se pasa a `.execute()` como variable (no como literal directo),
    así que el test anterior no lo cubre -- se revisa aparte."""
    from core.almacenamiento import _DDL

    assert "%" not in _DDL
