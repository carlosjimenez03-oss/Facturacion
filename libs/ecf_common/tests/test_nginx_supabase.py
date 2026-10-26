"""frontend/nginx.conf reenvía /auth/v1/* al gateway de Supabase (Envoy).
Envoy responde 426 Upgrade Required a HTTP/1.0, que es lo que nginx usa por
defecto con proxy_pass: sin `proxy_http_version 1.1` el inicio de sesión
falla con "Error 426 del servicio de inicio de sesión"."""
import re
from pathlib import Path

NGINX = Path(__file__).resolve().parents[3] / "frontend" / "nginx.conf"


def _bloques_que_van_al_gateway() -> list[str]:
    texto = NGINX.read_text(encoding="utf-8")
    bloques = re.findall(r"location[^{]*\{[^{}]*\}", texto)
    return [b for b in bloques if "$supabase_gateway" in b]


def test_hay_rutas_hacia_supabase_auth():
    assert len(_bloques_que_van_al_gateway()) >= 2


def test_las_rutas_hacia_supabase_usan_http_1_1():
    for bloque in _bloques_que_van_al_gateway():
        assert "proxy_http_version 1.1;" in bloque, bloque
        assert 'proxy_set_header Connection "";' in bloque, bloque
