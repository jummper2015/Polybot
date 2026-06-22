# tests/unit/test_requirements_pyproject_deps_r2_2.py

"""
R2.2-paper-verify — Fix #2.

Asegura que las deps críticas faltantes están en requirements.txt
y pyproject.toml:

- `psycopg2-binary` ≥ 2.9.9 — alembic upgrade head requiere driver sync;
  sin esto el container entra en restart-loop (ModuleNotFoundError).
- `pyarrow` ≥ 15.0.0 — import chain
  src.quantitative.post_trade → src.quantitative.walk_forward →
  src.backtesting.parquet_loader requiere pyarrow.
  Sin esto, /api/v1/dashboard/quant-metrics devuelve HTTP 500.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REQUIREMENTS   = ROOT / "requirements.txt"
PYPROJECT_TOML = ROOT / "pyproject.toml"

REQUIRED_DEPS: dict[str, tuple[str, str, bool]] = {
    # name        ->  (constraints_regex, version_floor, in_dev_only)
    "psycopg2-binary": (r"psycopg2-binary\s*>=\s*2\.9\.9", "2.9.9", False),
    "pyarrow":         (r"pyarrow\s*>=\s*15\.0\.0",         "15.0.0", False),
}


def _version_tuple(s: str) -> tuple[int, ...]:
    """Convierte '2.9.9' o '15.0' en tuple (2,9,9) o (15,0)."""
    return tuple(int(p) for p in s.split("."))


def test_requirements_txt_lists_psycopg2_binary() -> None:
    """requirements.txt debe tener `psycopg2-binary>=X.Y.Z` con floor >= 2.9.9."""
    text = REQUIREMENTS.read_text(encoding="utf-8")
    assert "psycopg2-binary" in text, (
        "psycopg2-binary ausente de requirements.txt (alembic falla)"
    )
    import re
    m = re.search(r"psycopg2-binary\s*>=\s*([0-9]+(?:\.[0-9]+){0,2})", text)
    assert m is not None, "psycopg2-binary sin floor de versión en requirements.txt"
    assert _version_tuple(m.group(1)) >= (2, 9, 9), (
        f"psycopg2-binary version {m.group(1)} < 2.9.9 (incompatible con sql 2.0)"
    )


def test_requirements_txt_lists_pyarrow() -> None:
    """requirements.txt debe tener `pyarrow>=X.Y.Z` con floor >= 15.0.0."""
    text = REQUIREMENTS.read_text(encoding="utf-8")
    assert "pyarrow" in text, (
        "pyarrow ausente de requirements.txt (quant.post_trade no se importa)"
    )
    import re
    m = re.search(r"pyarrow\s*>=\s*([0-9]+(?:\.[0-9]+){0,2})", text)
    assert m is not None, "pyarrow sin floor de versión en requirements.txt"
    assert _version_tuple(m.group(1)) >= (15, 0, 0), (
        f"pyarrow version {m.group(1)} < 15.0.0 (breaking API en parquet)"
    )


def test_pyproject_toml_deps_list_psycopg2_binary() -> None:
    """pyproject.toml dependencies debe incluir psycopg2-binary."""
    text = PYPROJECT_TOML.read_text(encoding="utf-8")
    assert "psycopg2-binary" in text, (
        "psycopg2-binary ausente de pyproject.toml dependencies "
        "(venv editable no lo instalará)."
    )


def test_pyproject_toml_deps_list_pyarrow() -> None:
    """pyproject.toml dependencies debe incluir pyarrow."""
    text = PYPROJECT_TOML.read_text(encoding="utf-8")
    assert "pyarrow" in text, (
        "pyarrow ausente de pyproject.toml dependencies."
    )


def test_deps_placed_in_db_section_only() -> None:
    """
    Sanity check: las deps críticas viven en la sección DB de
    requirements.txt, no en dev/test (no son opcionales).
    Implementación line-based (evita edge cases con Unicode box drawing
    y DOTALL).
    """
    lines = REQUIREMENTS.read_text(encoding="utf-8").splitlines()

    # 1) encuentra el header de la sección DB (substring match para tolerar
    #    el bullet "─" que lstrip("# ") no removería).
    db_start = next(
        (
            i for i, line in enumerate(lines)
            if "Database" in line or "Base de Datos" in line
        ),
        None,
    )
    assert db_start is not None, (
        "Header 'Database' o 'Base de Datos' no encontrado en requirements.txt"
    )

    # 2) encuentra el siguiente header de sección (después del actual).
    next_section = None
    for i in range(db_start + 1, len(lines)):
        stripped = lines[i].strip()
        # Un header de sección es un comentario que empieza con "# ──" o "# ---".
        if stripped.startswith("#") and "──" in stripped or stripped.startswith("#") and "---" in stripped:
            # Asegurarse de que es un section header (largo, no comentario corto).
            if len(stripped) > 15:
                next_section = i
                break

    assert next_section is not None, (
        "No se encontró siguiente sección DB (sección DB última del archivo?)"
    )

    # 3) verifica que psycopg2-binary + pyarrow viven entre db_start y next_section.
    db_block = "\n".join(lines[db_start:next_section])
    assert "psycopg2-binary" in db_block, (
        "psycopg2-binary fuera de la sección DB de requirements.txt."
    )
    assert "pyarrow" in db_block, (
        "pyarrow fuera de la sección DB de requirements.txt."
    )
