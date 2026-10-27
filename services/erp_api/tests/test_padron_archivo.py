"""Lector del padrón de RNC de la DGII (DGII_RNC.zip, padron_archivo.py):
líneas de ejemplo con los casos que se ven en el archivo real y los que
podría traer un cambio de formato, y los límites del ZIP."""
import zipfile
from datetime import date

import pytest

from services.erp_api.app.padron_archivo import (
    ArchivoPadronError,
    FilaPadron,
    LectorPadron,
    decodificar,
    leer_fecha,
    parsear_linea,
)

MB = 1024 * 1024

# Diseño de 11 columnas: RNC|razón social|nombre comercial|actividad|4 sin uso|fecha|estado|régimen
LINEA = "101000001|COMPAÑÍA DOMINICANA DE PRUEBA SRL|LA PRUEBA|VENTA AL POR MAYOR DE ALIMENTOS| | | | |15/03/1998|ACTIVO|NORMAL"


def test_linea_de_11_columnas():
    fila = parsear_linea(LINEA)
    assert fila == FilaPadron(
        rnc="101000001",
        razon_social="COMPAÑÍA DOMINICANA DE PRUEBA SRL",
        nombre_comercial="LA PRUEBA",
        actividad="VENTA AL POR MAYOR DE ALIMENTOS",
        estado="ACTIVO",
        regimen="NORMAL",
        fecha_inicio=date(1998, 3, 15),
    )


def test_cedula_con_guiones_crlf_y_espacios_de_mas():
    fila = parsear_linea("001-0012345-6 |  JUAN   PEREZ  ÑAÑEZ |||| | | |01/02/2010|SUSPENDIDO|RST\r\n")
    assert fila.rnc == "00100123456"
    assert fila.razon_social == "JUAN PEREZ ÑAÑEZ"
    assert fila.nombre_comercial is None and fila.actividad is None
    assert (fila.estado, fila.regimen, fila.fecha_inicio) == ("SUSPENDIDO", "RST", date(2010, 2, 1))


def test_separadores_de_mas_al_final_y_comillas_sueltas():
    fila = parsear_linea('"130000002"|"FERRETERIA "EL MARTILLO" SRL"|EL MARTILLO|FERRETERIA| | | | |07/07/2015|ACTIVO|NORMAL|||')
    assert fila.rnc == "130000002"
    assert fila.razon_social == 'FERRETERIA "EL MARTILLO" SRL'
    assert (fila.estado, fila.regimen) == ("ACTIVO", "NORMAL")


def test_menos_columnas_dos_sin_uso():
    """Diseño de 9 columnas (como lo describen algunas fuentes): el estado
    se encuentra por su valor."""
    fila = parsear_linea("131000003|SERVICIOS XYZ SRL|XYZ|CONSULTORIA| | |2020-05-04|DADO DE BAJA|NORMAL")
    assert (fila.estado, fila.regimen, fila.fecha_inicio) == ("DADO DE BAJA", "NORMAL", date(2020, 5, 4))


def test_estado_desconocido_usa_las_posiciones_de_siempre():
    fila = parsear_linea("101000004|ALGO SA||ACT| | | | |01/01/2001|EN REVISION|ESPECIAL")
    assert (fila.estado, fila.regimen, fila.fecha_inicio) == ("EN REVISION", "ESPECIAL", date(2001, 1, 1))


def test_estado_en_minusculas_y_cese_temporal():
    fila = parsear_linea("101000005|X SRL|||| | | |31/12/1999|cese temporal|normal")
    assert (fila.estado, fila.regimen) == ("CESE TEMPORAL", "NORMAL")


@pytest.mark.parametrize(
    "linea",
    [
        "",
        "   ",
        "RNC|RAZON SOCIAL|NOMBRE COMERCIAL|ACTIVIDAD|||||FECHA|ESTADO|REGIMEN",
        "12345|MUY CORTO",
        "1234567890|DIEZ DIGITOS",
        "101000006||SIN NOMBRE",
        "101000007",
    ],
)
def test_lineas_que_no_son_contribuyentes(linea):
    assert parsear_linea(linea) is None


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [
        ("15/03/1998", date(1998, 3, 15)),
        ("5/3/1998", date(1998, 3, 5)),
        ("15-03-1998", date(1998, 3, 15)),
        ("1998-03-15", date(1998, 3, 15)),
        ("1998-03-15T00:00:00", date(1998, 3, 15)),
        ("00/00/0000", None),
        ("31/02/2020", None),
        ("", None),
        ("N/D", None),
    ],
)
def test_fechas(valor, esperado):
    assert leer_fecha(valor) == esperado


def test_codificaciones_cp1252_latin1_y_utf8():
    assert decodificar("CAÑO ÁRBOL – €".encode("cp1252")) == "CAÑO ÁRBOL – €"
    assert decodificar("PEÑA".encode("utf-8")) == "PEÑA"
    # 0x81 no existe en cp1252: Latin-1, sin fallar.
    assert decodificar(b"A\x81B\xd1") == "A\x81BÑ"


def test_nul_y_campos_larguisimos():
    fila = parsear_linea("101000008|NOM\x00BRE|" + "X" * 1000 + "|||||| |01/01/2000|ACTIVO|NORMAL")
    assert fila.razon_social == "NOMBRE"
    assert len(fila.nombre_comercial) == 250


def _txt(lineas: list[str], *, codificacion="cp1252", fin="\r\n", bom=False) -> bytes:
    datos = fin.join(lineas).encode(codificacion) + fin.encode()
    return (b"\xef\xbb\xbf" if bom else b"") + datos


def _zip(tmp_path, contenido: bytes, nombre="TMP/DGII_RNC.TXT", extra: dict[str, bytes] | None = None):
    ruta = tmp_path / "DGII_RNC.zip"
    with zipfile.ZipFile(ruta, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(nombre, contenido)
        for n, c in (extra or {}).items():
            zf.writestr(n, c)
    return ruta


def _lineas_validas(n: int) -> list[str]:
    return [f"1{i:08d}|EMPRESA Ñ {i} SRL|COMERCIAL {i}|ACTIVIDAD|||||01/01/2000|ACTIVO|NORMAL" for i in range(n)]


def test_zip_con_txt_en_cp1252_crlf_lineas_vacias_y_encabezado(tmp_path):
    lineas = ["RNC|NOMBRE|COMERCIAL|ACTIVIDAD|||||FECHA|ESTADO|REGIMEN", "", *_lineas_validas(3), "", ""]
    ruta = _zip(tmp_path, _txt(lineas), extra={"LEEME.pdf": b"%PDF-1.4"})
    lector = LectorPadron(ruta, max_descomprimido=10 * MB)
    filas = list(lector.filas())
    assert [f.rnc for f in filas] == ["100000000", "100000001", "100000002"]
    assert filas[0].razon_social == "EMPRESA Ñ 0 SRL"
    assert lector.miembro == "TMP/DGII_RNC.TXT"
    assert (lector.validas, lector.descartadas) == (3, 1)


def test_txt_suelto_utf8_con_bom(tmp_path):
    ruta = tmp_path / "DGII_RNC.TXT"
    ruta.write_bytes(_txt(_lineas_validas(2), codificacion="utf-8", fin="\n", bom=True))
    filas = list(LectorPadron(ruta, max_descomprimido=MB).filas())
    assert [f.razon_social for f in filas] == ["EMPRESA Ñ 0 SRL", "EMPRESA Ñ 1 SRL"]


def test_linea_sin_salto_mas_larga_que_el_tope_se_descarta(tmp_path):
    ruta = tmp_path / "padron.txt"
    ruta.write_bytes(_txt(_lineas_validas(3)) + b"X" * 20000 + b"\n" + _txt(_lineas_validas(1)))
    lector = LectorPadron(ruta, max_descomprimido=MB)
    assert len(list(lector.filas())) == 4


def test_archivo_que_no_es_el_padron(tmp_path):
    ruta = tmp_path / "otro.txt"
    ruta.write_bytes(b"hola\nmundo\n" * 10)
    with pytest.raises(ArchivoPadronError, match="ningún contribuyente"):
        list(LectorPadron(ruta, max_descomprimido=MB).filas())


def test_formato_cambiado_la_mayoria_de_lineas_no_sirven(tmp_path):
    ruta = tmp_path / "raro.txt"
    ruta.write_bytes(_txt(_lineas_validas(2) + ["101;OTRO;FORMATO"] * 5))
    with pytest.raises(ArchivoPadronError, match="formato"):
        list(LectorPadron(ruta, max_descomprimido=MB).filas())


def test_las_primeras_2000_lineas_sin_contribuyentes_corta_temprano(tmp_path):
    ruta = tmp_path / "basura.txt"
    ruta.write_bytes(b"basura|basura\n" * 3000 + _txt(_lineas_validas(10)))
    lector = LectorPadron(ruta, max_descomprimido=10 * MB)
    with pytest.raises(ArchivoPadronError, match="primeras líneas"):
        list(lector.filas())
    assert lector.lineas == 2000


def test_bomba_de_zip_por_proporcion(tmp_path):
    ruta = _zip(tmp_path, b"\n" * (20 * MB))
    with pytest.raises(ArchivoPadronError, match="comprime demasiado"):
        list(LectorPadron(ruta, max_descomprimido=100 * MB).filas())


def test_zip_que_declara_mas_que_el_maximo(tmp_path):
    ruta = _zip(tmp_path, _txt(_lineas_validas(20000)))
    with pytest.raises(ArchivoPadronError, match="pasa del máximo"):
        list(LectorPadron(ruta, max_descomprimido=MB).filas())


def test_tope_real_aunque_el_zip_mienta(tmp_path):
    """Un TXT suelto (sin tamaño declarado) también tiene tope."""
    ruta = tmp_path / "grande.txt"
    ruta.write_bytes(_txt(_lineas_validas(20000)))
    with pytest.raises(ArchivoPadronError, match="pasa de"):
        list(LectorPadron(ruta, max_descomprimido=MB).filas())


def test_zip_danado_o_vacio(tmp_path):
    dañado = tmp_path / "danado.zip"
    dañado.write_bytes(b"PK\x03\x04" + b"\x00" * 100)
    with pytest.raises(ArchivoPadronError):
        list(LectorPadron(dañado, max_descomprimido=MB).filas())
    vacio = tmp_path / "vacio.zip"
    with zipfile.ZipFile(vacio, "w"):
        pass
    # Un ZIP vacío (solo el directorio central) empieza con PK\x05\x06: no es un ZIP con contenido.
    with pytest.raises(ArchivoPadronError):
        list(LectorPadron(vacio, max_descomprimido=MB).filas())


def test_zip_cortado_a_la_mitad(tmp_path):
    ruta = _zip(tmp_path, _txt(_lineas_validas(5000)))
    datos = ruta.read_bytes()
    ruta.write_bytes(datos[: len(datos) // 2])
    with pytest.raises(ArchivoPadronError):
        list(LectorPadron(ruta, max_descomprimido=100 * MB).filas())


def test_memoria_acotada_lee_en_streaming(tmp_path):
    """El lector es un generador: entrega filas sin haber leído todo."""
    ruta = _zip(tmp_path, _txt(_lineas_validas(50000)))
    lector = LectorPadron(ruta, max_descomprimido=100 * MB)
    filas = lector.filas()
    primeras = [next(filas) for _ in range(10)]
    assert primeras[0].rnc == "100000000"
    assert lector.validas == 10
    filas.close()

