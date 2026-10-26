"""DDL que corre contra el Postgres de Supabase como el rol `postgres` (no
superusuario). En la imagen supabase/postgres 17.6 la extensión supautils
tumba el servidor (señal 11) con `GRANT <rol> TO CURRENT_USER`: todas las
conexiones caen y, como los servicios repiten la migración al arrancar, la
base queda en "recovery mode" en bucle. Los GRANT de roles deben nombrar al
usuario (format('... TO %I', current_user))."""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
PATRON = re.compile(r"\bTO\s+(CURRENT_USER|SESSION_USER|CURRENT_ROLE)\b", re.IGNORECASE)


def _sin_comentarios(texto: str) -> str:
    return "\n".join(linea.split("--", 1)[0] for linea in texto.splitlines())


def test_ningun_ddl_da_roles_a_current_user():
    archivos = [
        *REPO_ROOT.glob("db/init/*.sql"),
        *(p for p in REPO_ROOT.glob("libs/**/*.py") if "tests" not in p.parts),
        *REPO_ROOT.glob("services/*/app/**/*.py"),
    ]
    culpables = [
        f"{p.relative_to(REPO_ROOT)}: {m.group(0)}"
        for p in archivos
        for m in PATRON.finditer(_sin_comentarios(p.read_text(encoding="utf-8")))
    ]
    assert culpables == []
