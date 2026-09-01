from datetime import datetime, timedelta, timezone

from libs.ecf_common.auth.schemas import TokenResponse
from libs.ecf_common.auth.token_cache import TokenCache, parse_dgii_datetime


def test_parse_dgii_datetime_con_fraccion_larga():
    # Ejemplo real tomado de la documentación de la DGII (7 dígitos de fracción).
    parsed = parse_dgii_datetime("2019-03-13T14:33:32.8617792-04:00")

    assert parsed is not None
    assert parsed.year == 2019
    assert parsed.month == 3
    assert parsed.day == 13


def test_parse_dgii_datetime_formato_simple():
    parsed = parse_dgii_datetime("2026-01-01T00:00:00Z")

    assert parsed is not None
    assert parsed.tzinfo is not None


def test_parse_dgii_datetime_invalido_retorna_none():
    assert parse_dgii_datetime("no-es-una-fecha") is None


def _token(expira: datetime) -> TokenResponse:
    return TokenResponse(
        token="t",
        expira=expira.isoformat(),
        expedido=datetime.now(timezone.utc).isoformat(),
    )


def test_cache_vacio_no_esta_vigente():
    cache = TokenCache()

    assert cache.vigente() is None


def test_cache_con_token_futuro_esta_vigente():
    cache = TokenCache(margen_segundos=10)
    futuro = datetime.now(timezone.utc) + timedelta(minutes=30)

    cache.guardar(_token(futuro))

    assert cache.vigente() is not None


def test_cache_con_token_a_punto_de_expirar_no_esta_vigente():
    cache = TokenCache(margen_segundos=60)
    en_treinta_segundos = datetime.now(timezone.utc) + timedelta(seconds=30)

    cache.guardar(_token(en_treinta_segundos))

    assert cache.vigente() is None


def test_cache_con_token_expirado_no_esta_vigente():
    cache = TokenCache()
    pasado = datetime.now(timezone.utc) - timedelta(minutes=5)

    cache.guardar(_token(pasado))

    assert cache.vigente() is None
