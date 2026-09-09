import time

from libs.ecf_common.auth.semilla_store import SemillaStore


def test_emitir_devuelve_valor_unico_y_fecha():
    store = SemillaStore()

    valor1, fecha1 = store.emitir()
    valor2, _ = store.emitir()

    assert valor1 != valor2
    assert fecha1.tzinfo is not None


def test_consumir_valor_vigente_es_true_una_sola_vez():
    store = SemillaStore()
    valor, _ = store.emitir()

    assert store.consumir(valor) is True
    assert store.consumir(valor) is False


def test_consumir_valor_desconocido_es_false():
    store = SemillaStore()

    assert store.consumir("no-existe") is False


def test_consumir_valor_expirado_es_false():
    store = SemillaStore(ttl_segundos=0)
    valor, _ = store.emitir()
    time.sleep(0.01)

    assert store.consumir(valor) is False
