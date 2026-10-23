from services.anulacion_encf.app.detalle import extraer_detalle_anulacion

ANECF = b"""<?xml version="1.0" encoding="UTF-8"?>
<ANECF>
  <Encabezado>
    <Version>1.0</Version>
    <RncEmisor>131897542</RncEmisor>
    <CantidadeNCFAnulados>8</CantidadeNCFAnulados>
    <FechaHoraAnulacioneNCF>14-10-2026 09:30:00</FechaHoraAnulacioneNCF>
  </Encabezado>
  <DetalleAnulacion>
    <Anulacion>
      <NoLinea>1</NoLinea>
      <TipoeCF>31</TipoeCF>
      <TablaRangoSecuenciasAnuladaseNCF>
        <Secuencias>
          <SecuenciaeNCFDesde>E310000000010</SecuenciaeNCFDesde>
          <SecuenciaeNCFHasta>E310000000012</SecuenciaeNCFHasta>
        </Secuencias>
        <Secuencias>
          <SecuenciaeNCFDesde>E310000000020</SecuenciaeNCFDesde>
          <SecuenciaeNCFHasta>E310000000020</SecuenciaeNCFHasta>
        </Secuencias>
      </TablaRangoSecuenciasAnuladaseNCF>
      <CantidadeNCFAnulados>4</CantidadeNCFAnulados>
    </Anulacion>
    <Anulacion>
      <NoLinea>2</NoLinea>
      <TipoeCF>32</TipoeCF>
      <TablaRangoSecuenciasAnuladaseNCF>
        <Secuencias>
          <SecuenciaeNCFDesde>E320000000100</SecuenciaeNCFDesde>
          <SecuenciaeNCFHasta>E320000000103</SecuenciaeNCFHasta>
        </Secuencias>
      </TablaRangoSecuenciasAnuladaseNCF>
      <CantidadeNCFAnulados>4</CantidadeNCFAnulados>
    </Anulacion>
  </DetalleAnulacion>
</ANECF>"""


def test_extrae_rangos_y_fecha():
    detalle = extraer_detalle_anulacion(ANECF)

    assert detalle["fecha_hora_anulacion"] == "14-10-2026 09:30:00"
    assert detalle["rangos"] == [
        {"tipo_ecf": "31", "desde": "E310000000010", "hasta": "E310000000012"},
        {"tipo_ecf": "31", "desde": "E310000000020", "hasta": "E310000000020"},
        {"tipo_ecf": "32", "desde": "E320000000100", "hasta": "E320000000103"},
    ]


def test_xml_ilegible_no_falla():
    assert extraer_detalle_anulacion(b"no es xml") == {"fecha_hora_anulacion": None, "rangos": []}
