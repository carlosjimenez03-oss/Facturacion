import { calcularMontoItem, calcularTotales, decimal2, fechaADgii } from "./format";
import type { FacturaDatos, FormaPago, ItemFactura } from "./types";
import type { TipoEcfConfig } from "./tipoConfig";

export interface FormState {
  tipoEcf: string;
  encf: string;
  fechaVencimientoSecuencia: string;
  tipoIngresos: string;
  tipoPago: string;
  formasPago: FormaPago[];

  rncEmisor: string;
  razonSocialEmisor: string;
  direccionEmisor: string;
  fechaEmision: string;
  correoEmisor: string;

  compradorRnc: string;
  compradorIdentificadorExtranjero: string;
  razonSocialComprador: string;
  correoComprador: string;
  direccionComprador: string;

  items: ItemFactura[];

  ncfModificado: string;
  fechaNcfModificado: string;
  codigoModificacion: string;
  indicadorNotaCredito: string;
}

export function estadoInicial(tipoEcf: string): FormState {
  const hoy = new Date().toISOString().slice(0, 10);
  return {
    tipoEcf,
    encf: "",
    fechaVencimientoSecuencia: "",
    tipoIngresos: "01",
    tipoPago: "1",
    formasPago: [],
    rncEmisor: "",
    razonSocialEmisor: "",
    direccionEmisor: "",
    fechaEmision: hoy,
    correoEmisor: "",
    compradorRnc: "",
    compradorIdentificadorExtranjero: "",
    razonSocialComprador: "",
    correoComprador: "",
    direccionComprador: "",
    items: [nuevoItem(1)],
    ncfModificado: "",
    fechaNcfModificado: "",
    codigoModificacion: "1",
    indicadorNotaCredito: "0",
  };
}

export function nuevoItem(numeroLinea: number): ItemFactura {
  return {
    NumeroLinea: String(numeroLinea),
    IndicadorFacturacion: "1",
    NombreItem: "",
    IndicadorBienoServicio: "1",
    CantidadItem: "1",
    PrecioUnitarioItem: "0",
    MontoItem: "0",
  };
}

/** Arma `FacturaDatos` a partir del formulario, respetando el orden de
 * secuencia del XSD (ver el comentario en xml_builder.py: el orden de las
 * claves del objeto determina el orden de los elementos XML generados). */
export function construirDatos(form: FormState, config: TipoEcfConfig): FacturaDatos {
  const idDoc: Record<string, unknown> = {
    TipoeCF: form.tipoEcf,
    eNCF: form.encf,
  };
  if (config.requiereFechaVencimientoSecuencia && form.fechaVencimientoSecuencia) {
    idDoc.FechaVencimientoSecuencia = fechaADgii(form.fechaVencimientoSecuencia);
  }
  if (config.requiereIndicadorNotaCredito) {
    idDoc.IndicadorNotaCredito = form.indicadorNotaCredito;
  }
  if (config.requiereTipoIngresos) {
    idDoc.TipoIngresos = form.tipoIngresos;
  }
  idDoc.TipoPago = form.tipoPago;
  if (form.formasPago.length > 0) {
    idDoc.TablaFormasPago = { FormaDePago: form.formasPago };
  }

  const emisor: Record<string, unknown> = {
    RNCEmisor: form.rncEmisor,
    RazonSocialEmisor: form.razonSocialEmisor,
    DireccionEmisor: form.direccionEmisor,
  };
  if (form.correoEmisor) emisor.CorreoEmisor = form.correoEmisor;
  emisor.FechaEmision = fechaADgii(form.fechaEmision);

  let comprador: Record<string, unknown> | undefined;
  if (config.compradorVisible && (form.compradorRnc || form.compradorIdentificadorExtranjero || form.razonSocialComprador)) {
    comprador = {};
    if (form.compradorRnc) comprador.RNCComprador = form.compradorRnc;
    if (!form.compradorRnc && form.compradorIdentificadorExtranjero) {
      comprador.IdentificadorExtranjero = form.compradorIdentificadorExtranjero;
    }
    if (form.razonSocialComprador) comprador.RazonSocialComprador = form.razonSocialComprador;
    if (form.correoComprador) comprador.CorreoComprador = form.correoComprador;
    if (form.direccionComprador) comprador.DireccionComprador = form.direccionComprador;
  }

  const totalesCalculados = calcularTotales(form.items);
  const totales: Record<string, unknown> = {};
  if (totalesCalculados.montoGravadoTotal > 0) {
    totales.MontoGravadoTotal = decimal2(totalesCalculados.montoGravadoTotal);
  }
  if (totalesCalculados.montoExento > 0) {
    totales.MontoExento = decimal2(totalesCalculados.montoExento);
  }
  if (totalesCalculados.totalITBIS > 0) {
    totales.TotalITBIS = decimal2(totalesCalculados.totalITBIS);
  }
  totales.MontoTotal = decimal2(totalesCalculados.montoTotal);

  const items: ItemFactura[] = form.items.map((item, idx) => {
    const montoItem = calcularMontoItem(item);
    const salida: ItemFactura = {
      NumeroLinea: String(idx + 1),
      IndicadorFacturacion: item.IndicadorFacturacion,
      NombreItem: item.NombreItem,
      IndicadorBienoServicio: item.IndicadorBienoServicio,
      CantidadItem: decimal2(Number(item.CantidadItem) || 0),
      PrecioUnitarioItem: Number(item.PrecioUnitarioItem || 0).toFixed(4),
      MontoItem: decimal2(montoItem),
    };
    if (item.DescripcionItem) salida.DescripcionItem = item.DescripcionItem;
    if (item.UnidadMedida) salida.UnidadMedida = item.UnidadMedida;
    if (item.DescuentoMonto) salida.DescuentoMonto = decimal2(Number(item.DescuentoMonto) || 0);
    return salida;
  });

  const datos: FacturaDatos = {
    Encabezado: {
      Version: "1.0",
      IdDoc: idDoc,
      Emisor: emisor,
      ...(comprador ? { Comprador: comprador } : {}),
      Totales: totales,
    },
    DetallesItems: { Item: items },
  };

  if (config.informacionReferenciaRequerida || form.ncfModificado) {
    datos.InformacionReferencia = {
      ...(form.ncfModificado ? { NCFModificado: form.ncfModificado } : {}),
      ...(form.fechaNcfModificado ? { FechaNCFModificado: fechaADgii(form.fechaNcfModificado) } : {}),
      CodigoModificacion: form.codigoModificacion,
    };
  }

  return datos;
}
