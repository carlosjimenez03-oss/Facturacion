import pytest

from libs.ecf_common.auth.jwt_tokens import ALGORITMO, TokenInvalidoError, emitir_token, validar_token


def test_emitir_y_validar_token_roundtrip(credencial_prueba):
    emitido = emitir_token(credencial_prueba, "131234567")

    assert validar_token(credencial_prueba, emitido.token) == "131234567"


def test_emitido_expone_fechas_en_formato_universal(credencial_prueba):
    emitido = emitir_token(credencial_prueba, "131234567")

    assert emitido.expedido_str.endswith("Z")
    assert emitido.expira_str.endswith("Z")
    assert "T" in emitido.expedido_str


def test_token_alterado_es_invalido(credencial_prueba):
    emitido = emitir_token(credencial_prueba, "131234567")

    with pytest.raises(TokenInvalidoError):
        validar_token(credencial_prueba, emitido.token + "x")


def test_token_firmado_por_otro_certificado_es_invalido(credencial_prueba, credencial_con_rnc):
    otra = credencial_con_rnc("999999999")
    emitido = emitir_token(otra, "999999999")

    with pytest.raises(TokenInvalidoError):
        validar_token(credencial_prueba, emitido.token)


def test_token_expirado_es_invalido(credencial_prueba):
    import jwt
    from datetime import datetime, timedelta, timezone

    hace_dos_horas = datetime.now(timezone.utc) - timedelta(hours=2)
    token_vencido = jwt.encode(
        {"sub": "131234567", "iat": hace_dos_horas, "exp": hace_dos_horas + timedelta(hours=1)},
        credencial_prueba.private_key,
        algorithm=ALGORITMO,
    )

    with pytest.raises(TokenInvalidoError):
        validar_token(credencial_prueba, token_vencido)


def test_token_sin_claim_sub_es_invalido(credencial_prueba):
    import jwt
    from datetime import datetime, timedelta, timezone

    ahora = datetime.now(timezone.utc)
    token_sin_sub = jwt.encode(
        {"iat": ahora, "exp": ahora + timedelta(hours=1)},
        credencial_prueba.private_key,
        algorithm=ALGORITMO,
    )

    with pytest.raises(TokenInvalidoError):
        validar_token(credencial_prueba, token_sin_sub)
