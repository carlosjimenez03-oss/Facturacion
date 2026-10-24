import { calcularMontoItem, calcularTotales, decimal2, fechaADgii, fechaDeDgii } from "./format";
import type { FacturaDatos, FormaPago, ItemFactura, ItemInventarioLink } from "./types";
import type { CotizacionItem, TasaItbis } from "./erpApi";
import type { TipoEcfConfig } from "./tipoConfig";

/** Línea del formulario de factura: la del e-CF más el lote elegido (metadata de inventario que,
 * como `productoId`, no va al XML; ver `construirItemsInventario`). */
export interface ItemFormulario extends ItemFactura {
  /** Lote del que sale la mercancía; ausente = inventario-api lo toma por FEFO. */
  loteId?: string;
}

export interface FormState {
  tipoEcf: string;
  encf: string;
  fechaVencimientoSecuencia: string;
  tipoIngresos: string;
  tipoPago: string;
  /** Fecha límite de pago (YYYY-MM-DD) para ventas a crédito (TipoPago 2). */
  fechaLimitePago: string;
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

  items: ItemFormulario[];
  /** Almacén de donde sale la mercancía (selector "Almacén"); "" = el principal. */
  almacenId: string;

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
    fechaLimitePago: "",
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
    almacenId: "",
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
  if (form.tipoPago === "2" && form.fechaLimitePago) {
    idDoc.FechaLimitePago = fechaADgii(form.fechaLimitePago);
  }
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
  // En el E32 el nodo Comprador es obligatorio (minOccurs=1) aunque vaya
  // vacío: consumidor final sin identificar.
  if (!comprador && form.tipoEcf === "32") comprador = {};

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

/** Arma el payload `items_inventario` (aparte de `datos`, ver el comentario
 * en ItemFactura.productoId) que le dice a facturas_api qué producto del
 * catálogo y qué cantidad descontar de inventario por cada línea, al
 * enviar la factura, de qué almacén (`almacenId` del formulario; null = el
 * principal) y de qué lote (null = FEFO si el producto maneja lotes).
 * Líneas sin producto vinculado se omiten (por ahora las líneas de servicio
 * sin producto del catálogo no descuentan stock). */
export function construirItemsInventario(items: ItemFormulario[], almacenId = ""): ItemInventarioLink[] {
  return items
    .map((item, idx) => ({ item, numeroLinea: String(idx + 1) }))
    .filter(({ item }) => Boolean(item.productoId))
    .map(({ item, numeroLinea }) => ({
      numero_linea: numeroLinea,
      producto_id: item.productoId as string,
      cantidad: decimal2(Number(item.CantidadItem) || 0),
      almacen_id: almacenId || null,
      lote_id: item.loteId || null,
    }));
}

/** Inverso de construirDatos: reconstruye el FormState a partir de una
 * factura ya guardada, para poder recuperar/editar un borrador.
 * `itemsInventario` (aparte de `datos`, ver ItemFactura.productoId) se
 * vuelve a fusionar en cada línea por NumeroLinea, para que el selector de
 * producto (y de lote) de FacturaForm recupere lo ya elegido; el almacén es
 * el mismo para toda la factura. */
export function datosAFormState(
  tipoEcf: string,
  encf: string,
  datos: FacturaDatos,
  itemsInventario?: ItemInventarioLink[] | null,
): FormState {
  const idDoc = (datos.Encabezado.IdDoc ?? {}) as Record<string, unknown>;
  const emisor = (datos.Encabezado.Emisor ?? {}) as Record<string, unknown>;
  const comprador = (datos.Encabezado.Comprador ?? {}) as Record<string, unknown>;
  const referencia = (datos.InformacionReferencia ?? {}) as Record<string, unknown>;
  const tablaFormasPago = idDoc.TablaFormasPago as { FormaDePago?: FormaPago[] } | undefined;
  const linkPorLinea = new Map((itemsInventario ?? []).map((link) => [link.numero_linea, link]));
  const items: ItemFormulario[] = (datos.DetallesItems.Item.length > 0 ? datos.DetallesItems.Item : [nuevoItem(1)]).map((item) => ({
    ...item,
    productoId: linkPorLinea.get(item.NumeroLinea)?.producto_id,
    loteId: linkPorLinea.get(item.NumeroLinea)?.lote_id ?? undefined,
  }));

  return {
    tipoEcf,
    encf,
    fechaVencimientoSecuencia: idDoc.FechaVencimientoSecuencia
      ? fechaDeDgii(String(idDoc.FechaVencimientoSecuencia))
      : "",
    tipoIngresos: (idDoc.TipoIngresos as string) ?? "01",
    tipoPago: (idDoc.TipoPago as string) ?? "1",
    fechaLimitePago: idDoc.FechaLimitePago ? fechaDeDgii(String(idDoc.FechaLimitePago)) : "",
    formasPago: tablaFormasPago?.FormaDePago ?? [],
    rncEmisor: (emisor.RNCEmisor as string) ?? "",
    razonSocialEmisor: (emisor.RazonSocialEmisor as string) ?? "",
    direccionEmisor: (emisor.DireccionEmisor as string) ?? "",
    fechaEmision: emisor.FechaEmision
      ? fechaDeDgii(String(emisor.FechaEmision))
      : new Date().toISOString().slice(0, 10),
    correoEmisor: (emisor.CorreoEmisor as string) ?? "",
    compradorRnc: (comprador.RNCComprador as string) ?? "",
    compradorIdentificadorExtranjero: (comprador.IdentificadorExtranjero as string) ?? "",
    razonSocialComprador: (comprador.RazonSocialComprador as string) ?? "",
    correoComprador: (comprador.CorreoComprador as string) ?? "",
    direccionComprador: (comprador.DireccionComprador as string) ?? "",
    items,
    almacenId: itemsInventario?.find((link) => link.almacen_id)?.almacen_id ?? "",
    ncfModificado: (referencia.NCFModificado as string) ?? "",
    fechaNcfModificado: referencia.FechaNCFModificado ? fechaDeDgii(String(referencia.FechaNCFModificado)) : "",
    codigoModificacion: (referencia.CodigoModificacion as string) ?? "1",
    indicadorNotaCredito: (idDoc.IndicadorNotaCredito as string) ?? "0",
  };
}

/** Totales con el desglose por tasa de ITBIS (formulario y detalle de la factura). */
export function desglose(items: ItemFactura[]) {
  const t = { subtotal: 0, descuentos: 0, g18: 0, g16: 0, g0: 0, exento: 0, i18: 0, i16: 0, total: 0 };
  for (const item of items) {
    const bruto = (Number(item.CantidadItem) || 0) * (Number(item.PrecioUnitarioItem) || 0);
    const neto = calcularMontoItem(item);
    t.subtotal += bruto;
    t.descuentos += bruto - neto;
    if (item.IndicadorFacturacion === "1") {
      t.g18 += neto;
      t.i18 += neto * 0.18;
    } else if (item.IndicadorFacturacion === "2") {
      t.g16 += neto;
      t.i16 += neto * 0.16;
    } else if (item.IndicadorFacturacion === "3") {
      t.g0 += neto;
    } else if (item.IndicadorFacturacion === "4") {
      t.exento += neto;
    }
  }
  t.total = t.g18 + t.g16 + t.g0 + t.exento + t.i18 + t.i16;
  return t;
}

/** Próximo e-NCF sugerido para un tipo: el mayor ya usado de ese tipo + 1. */
export function siguienteEncf(facturas: { encf: string | null }[], tipoEcf: string): string {
  const prefijo = `E${tipoEcf}`;
  let mayor = 0;
  for (const f of facturas) {
    if (f.encf?.startsWith(prefijo)) mayor = Math.max(mayor, Number(f.encf.slice(3)) || 0);
  }
  return `${prefijo}${String(mayor + 1).padStart(10, "0")}`;
}

/** Tasa de ITBIS de una cotización (erp-api) o de un producto (inventario-api) -> IndicadorFacturacion del e-CF. */
export const INDICADOR_POR_TASA: Record<TasaItbis, string> = { "18": "1", "16": "2", "0": "3", exento: "4" };

/** Convierte las líneas de una cotización en líneas de factura. El descuento
 * de la cotización es un porcentaje; en el e-CF va como monto (DescuentoMonto). */
export function itemsDesdeCotizacion(items: CotizacionItem[]): ItemFactura[] {
  return items.map((linea, idx) => {
    const cantidad = Number(linea.cantidad) || 0;
    const precio = Number(linea.precio_unitario) || 0;
    const pct = Number(linea.descuento_pct) || 0;
    const item: ItemFactura = {
      ...nuevoItem(idx + 1),
      IndicadorFacturacion: INDICADOR_POR_TASA[linea.itbis] ?? "1",
      NombreItem: linea.descripcion,
      IndicadorBienoServicio: linea.producto_id ? "1" : "2",
      CantidadItem: linea.cantidad,
      PrecioUnitarioItem: linea.precio_unitario,
    };
    if (pct > 0) item.DescuentoMonto = decimal2((cantidad * precio * pct) / 100);
    if (linea.producto_id) item.productoId = linea.producto_id;
    return item;
  });
}
