// Catálogo de Reportes (Reportes.dc.html del prototipo): cada reporte sabe qué filtros pide, cómo cargarse de
// erp-api (agregado en el servidor) y cómo convertir la respuesta en tablas de celdas — las mismas que se ven en
// pantalla, se exportan a CSV y se imprimen.
import { reportesApi } from "../../lib/operacionApi";
import type {
  AgruparCompras,
  AgruparVentas,
  FilaAntiguedad,
  FilaCompras,
  FilaItbis,
  FilaRetencion,
  FilaVentas,
  MontosVenta,
  Periodo,
  ReporteAntiguedad,
} from "../../lib/operacionApi";
import { TIPOS_BIENES_SERVICIOS } from "../../lib/formato606Api";
import { formatoRnc } from "../../lib/marca";
import { fechaLegible } from "../ingresos/ingresosUtil";
import { nombrePeriodo } from "../impuestos/impuestosUtil";

export type Celda = string | number | null;
export type FormatoColumna = "texto" | "mono" | "monto" | "cantidad" | "entero" | "pct" | "veces";

export interface Columna {
  t: string;
  formato?: FormatoColumna;
}

export interface TablaReporte {
  titulo?: string;
  columnas: Columna[];
  filas: Celda[][];
  total?: Celda[];
}

export interface ResultadoReporte {
  tablas: TablaReporte[];
  advertencias: string[];
  /** Período efectivo (p. ej. meses completos del ITBIS) o fecha de corte. */
  subtitulo?: string;
}

export interface Filtro {
  clave: string;
  label: string;
  tipo: "select" | "texto" | "numero";
  opciones?: { value: string; label: string }[];
  placeholder?: string;
  /** Opciones que se cargan aparte (almacenes, categorías del inventario). */
  dinamico?: "almacenes" | "categorias";
}

export interface DefReporte {
  id: string;
  cat: string;
  t: string;
  d: string;
  usaPeriodo: boolean;
  filtros: Filtro[];
  cargar: (periodo: Periodo, filtros: Record<string, string>) => Promise<ResultadoReporte>;
}

const n = (v: string | number | null | undefined): number | null => (v === null || v === undefined || v === "" ? null : Number(v));

const MESES_CORTOS = (p: string) => (/^\d{6}$/.test(p) ? nombrePeriodo(p, true) : p);

const TIPOS_ECF = [
  { value: "31", label: "E31 · Crédito fiscal" },
  { value: "32", label: "E32 · Consumo" },
  { value: "33", label: "E33 · Nota de débito" },
  { value: "34", label: "E34 · Nota de crédito" },
  { value: "44", label: "E44 · Regímenes especiales" },
  { value: "45", label: "E45 · Gubernamental" },
  { value: "46", label: "E46 · Exportaciones" },
];

const CLIENTE: Filtro = { clave: "cliente_rnc", label: "Cliente (RNC o cédula)", tipo: "texto", placeholder: "1-31-23456-7" };
const PROVEEDOR: Filtro = { clave: "proveedor_rnc", label: "Proveedor (RNC o cédula)", tipo: "texto", placeholder: "1-31-11111-1" };
const CATEGORIA_INV: Filtro = { clave: "categoria", label: "Categoría", tipo: "select", dinamico: "categorias", opciones: [] };

// --- Ventas ---------------------------------------------------------

const COLS_VENTA: Columna[] = [
  { t: "Gravado 18 %", formato: "monto" },
  { t: "ITBIS 18 %", formato: "monto" },
  { t: "Gravado 16 %", formato: "monto" },
  { t: "ITBIS 16 %", formato: "monto" },
  { t: "Exento / 0 %", formato: "monto" },
  { t: "Subtotal", formato: "monto" },
  { t: "ITBIS", formato: "monto" },
  { t: "Total", formato: "monto" },
];

const montosVenta = (m: MontosVenta): Celda[] => [
  n(m.gravado_18),
  n(m.itbis_18),
  n(m.gravado_16),
  n(m.itbis_16),
  (n(m.exento) ?? 0) + (n(m.gravado_0) ?? 0),
  n(m.subtotal),
  n(m.itbis),
  n(m.total),
];

function reporteVentas(agrupar: AgruparVentas | "periodo"): DefReporte["cargar"] {
  return async (p, f) => {
    const ag: AgruparVentas = agrupar === "periodo" ? ((f.agrupar as AgruparVentas) || "mes") : agrupar;
    const r = await reportesApi.ventas({ ...p, agrupar: ag, cliente_rnc: f.cliente_rnc || undefined, tipo_ecf: f.tipo_ecf ? Number(f.tipo_ecf) : undefined });
    const primera: Columna = { t: { mes: "Mes", dia: "Día", cliente: "Cliente", producto: "Producto", tipo_ecf: "Tipo de e-CF" }[ag] };
    const extra: Columna[] = ag === "cliente" ? [{ t: "RNC / cédula", formato: "mono" }] : [];
    const docs: Columna[] = [{ t: ag === "producto" ? "Facturas" : "Documentos", formato: "entero" }, ...(ag === "producto" ? [{ t: "Cantidad", formato: "cantidad" as const }] : [])];
    const fila = (x: FilaVentas): Celda[] => [
      x.etiqueta,
      ...(ag === "cliente" ? [x.detalle ? formatoRnc(x.detalle) : "—"] : []),
      x.documentos,
      ...(ag === "producto" ? [n(x.cantidad)] : []),
      ...montosVenta(x),
    ];
    const cantidadTotal = r.filas.reduce((s, x) => s + (n(x.cantidad) ?? 0), 0);
    return {
      advertencias: r.advertencias,
      tablas: [
        {
          columnas: [primera, ...extra, ...docs, ...COLS_VENTA],
          filas: r.filas.map(fila),
          total: ["Total", ...(ag === "cliente" ? [""] : []), r.totales.documentos, ...(ag === "producto" ? [cantidadTotal] : []), ...montosVenta(r.totales)],
        },
      ],
    };
  };
}

// --- Compras ---------------------------------------------------------

const COLS_COMPRA: Columna[] = [
  { t: "Comprobantes", formato: "entero" },
  { t: "Servicios", formato: "monto" },
  { t: "Bienes", formato: "monto" },
  { t: "ITBIS facturado", formato: "monto" },
  { t: "ITBIS retenido", formato: "monto" },
  { t: "ISR retenido", formato: "monto" },
  { t: "Total", formato: "monto" },
];

const montosCompra = (x: FilaCompras): Celda[] => [
  x.documentos,
  n(x.monto_servicios),
  n(x.monto_bienes),
  n(x.itbis_facturado),
  n(x.itbis_retenido),
  n(x.isr_retenido),
  n(x.total),
];

function reporteCompras(agrupar: AgruparCompras): DefReporte["cargar"] {
  return async (p, f) => {
    const r = await reportesApi.compras({
      ...p,
      agrupar,
      proveedor_rnc: f.proveedor_rnc || undefined,
      tipo_bienes_servicios: f.tipo_bienes_servicios ? Number(f.tipo_bienes_servicios) : undefined,
    });
    const proveedor = agrupar === "proveedor";
    return {
      advertencias: r.advertencias,
      tablas: [
        {
          columnas: [{ t: proveedor ? "Proveedor" : agrupar === "mes" ? "Mes" : "Tipo de bienes y servicios (606)" }, ...(proveedor ? [{ t: "RNC / cédula", formato: "mono" as const }] : []), ...COLS_COMPRA],
          filas: r.filas.map((x) => [x.etiqueta, ...(proveedor ? [formatoRnc(x.detalle)] : []), ...montosCompra(x)]),
          total: ["Total", ...(proveedor ? [""] : []), ...montosCompra(r.totales)],
        },
      ],
    };
  };
}

// --- Antigüedad ---------------------------------------------------------

const TRAMOS: Columna[] = [
  { t: "Por vencer", formato: "monto" },
  { t: "1–30 días", formato: "monto" },
  { t: "31–60 días", formato: "monto" },
  { t: "61–90 días", formato: "monto" },
  { t: "Más de 90", formato: "monto" },
  { t: "Total", formato: "monto" },
];

const TRAMO_LABEL = { por_vencer: "Por vencer", d1_30: "1–30 días", d31_60: "31–60 días", d61_90: "61–90 días", mas_90: "Más de 90" };

const tramos = (x: FilaAntiguedad): Celda[] => [n(x.por_vencer), n(x.d1_30), n(x.d31_60), n(x.d61_90), n(x.mas_90), n(x.total)];

function tablasAntiguedad(r: ReporteAntiguedad): ResultadoReporte {
  const quien = r.tipo === "cxc" ? "Cliente" : "Proveedor";
  return {
    subtitulo: `Saldos al ${fechaLegible(r.fecha_corte)}`,
    advertencias: [],
    tablas: [
      {
        titulo: `Por ${quien.toLowerCase()}`,
        columnas: [{ t: quien }, { t: "RNC / cédula", formato: "mono" }, { t: "Documentos", formato: "entero" }, ...TRAMOS],
        filas: r.filas.map((x) => [x.nombre, x.rnc ? formatoRnc(x.rnc) : "—", x.documentos, ...tramos(x)]),
        total: ["Total", "", r.totales.documentos, ...tramos(r.totales)],
      },
      {
        titulo: "Detalle por documento",
        columnas: [
          { t: "Documento", formato: "mono" },
          { t: quien },
          { t: "Emisión" },
          { t: "Vence" },
          { t: "Días vencida", formato: "entero" },
          { t: "Tramo" },
          { t: "Pendiente", formato: "monto" },
        ],
        filas: r.documentos.map((d) => [d.documento, d.nombre, fechaLegible(d.fecha_emision), fechaLegible(d.vence), d.dias_vencida, TRAMO_LABEL[d.tramo], n(d.pendiente)]),
        total: ["Total", "", "", "", null, "", n(r.totales.total)],
      },
    ],
  };
}

// --- ITBIS ---------------------------------------------------------

const filaItbis = (x: FilaItbis): Celda[] => [
  n(x.ventas_gravadas),
  n(x.itbis_ventas),
  n(x.compras),
  n(x.itbis_compras),
  n(x.itbis_costo),
  n(x.itbis_adelantado),
  n(x.itbis_retenido),
  n(x.diferencia),
];

// --- Retenciones ---------------------------------------------------------

const COLS_RET: Columna[] = [
  { t: "Comprobantes", formato: "entero" },
  { t: "Monto base", formato: "monto" },
  { t: "ISR retenido", formato: "monto" },
  { t: "ITBIS retenido", formato: "monto" },
  { t: "Total retenido", formato: "monto" },
];
const ret = (x: FilaRetencion): Celda[] => [x.documentos, n(x.monto_base), n(x.isr_retenido), n(x.itbis_retenido), n(x.total)];

const METODO: Record<string, string> = { efectivo: "Efectivo", transferencia: "Transferencia", tarjeta: "Tarjeta", cheque: "Cheque" };

export const REPORTES: DefReporte[] = [
  {
    id: "ventas-periodo",
    cat: "Ventas",
    t: "Ventas por período",
    d: "e-CF aceptados por mes o día, con el desglose del IT-1",
    usaPeriodo: true,
    filtros: [
      { clave: "agrupar", label: "Agrupar por", tipo: "select", opciones: [{ value: "mes", label: "Mes" }, { value: "dia", label: "Día" }] },
      { clave: "tipo_ecf", label: "Tipo de e-CF", tipo: "select", opciones: [{ value: "", label: "Todos" }, ...TIPOS_ECF] },
      CLIENTE,
    ],
    cargar: reporteVentas("periodo"),
  },
  {
    id: "ventas-cliente",
    cat: "Ventas",
    t: "Ventas por cliente",
    d: "Clientes con mayor facturación",
    usaPeriodo: true,
    filtros: [{ clave: "tipo_ecf", label: "Tipo de e-CF", tipo: "select", opciones: [{ value: "", label: "Todos" }, ...TIPOS_ECF] }],
    cargar: reporteVentas("cliente"),
  },
  {
    id: "ventas-producto",
    cat: "Ventas",
    t: "Ventas por producto",
    d: "Unidades y montos vendidos por línea de e-CF",
    usaPeriodo: true,
    filtros: [CLIENTE],
    cargar: reporteVentas("producto"),
  },
  {
    id: "ventas-tipo",
    cat: "Ventas",
    t: "Ventas por tipo de e-CF",
    d: "Crédito fiscal, consumo, notas y regímenes especiales",
    usaPeriodo: true,
    filtros: [CLIENTE],
    cargar: reporteVentas("tipo_ecf"),
  },
  {
    id: "itbis",
    cat: "Impuestos",
    t: "ITBIS ventas vs. compras",
    d: "Facturado, adelantado en el 606 y retenido, por mes",
    usaPeriodo: true,
    filtros: [],
    cargar: async (p) => {
      const r = await reportesApi.itbis(p);
      return {
        subtitulo: `${nombrePeriodo(r.desde.slice(0, 7).replace("-", ""), true)} – ${nombrePeriodo(r.hasta.slice(0, 7).replace("-", ""), true)} (meses completos)`,
        advertencias: r.advertencias,
        tablas: [
          {
            columnas: [
              { t: "Mes" },
              { t: "Ventas gravadas", formato: "monto" },
              { t: "ITBIS facturado", formato: "monto" },
              { t: "Compras (606)", formato: "monto" },
              { t: "ITBIS en compras", formato: "monto" },
              { t: "Llevado al costo", formato: "monto" },
              { t: "ITBIS adelantado", formato: "monto" },
              { t: "Retenido a proveedores", formato: "monto" },
              { t: "Diferencia", formato: "monto" },
            ],
            filas: r.filas.map((x) => [MESES_CORTOS(x.periodo), ...filaItbis(x)]),
            total: ["Total", ...filaItbis(r.totales)],
          },
        ],
      };
    },
  },
  {
    id: "compras-proveedor",
    cat: "Compras",
    t: "Compras por proveedor",
    d: "Proveedores con mayor volumen (606)",
    usaPeriodo: true,
    filtros: [PROVEEDOR],
    cargar: reporteCompras("proveedor"),
  },
  {
    id: "compras-categoria",
    cat: "Compras",
    t: "Compras por categoría 606",
    d: "Por tipo de bienes y servicios comprados",
    usaPeriodo: true,
    filtros: [
      PROVEEDOR,
      {
        clave: "tipo_bienes_servicios",
        label: "Tipo de bienes y servicios",
        tipo: "select",
        opciones: [{ value: "", label: "Todos" }, ...Object.entries(TIPOS_BIENES_SERVICIOS).map(([v, l]) => ({ value: v, label: l }))],
      },
    ],
    cargar: reporteCompras("categoria"),
  },
  {
    id: "antiguedad-cxc",
    cat: "Cobros",
    t: "Antigüedad de cuentas por cobrar",
    d: "Saldos por días de vencimiento",
    usaPeriodo: false,
    filtros: [CLIENTE],
    cargar: async (_p, f) => tablasAntiguedad(await reportesApi.antiguedadCxc(f.cliente_rnc || undefined)),
  },
  {
    id: "antiguedad-cxp",
    cat: "Pagos",
    t: "Antigüedad de cuentas por pagar",
    d: "Lo que se debe a proveedores por vencimiento",
    usaPeriodo: false,
    filtros: [PROVEEDOR],
    cargar: async (_p, f) => tablasAntiguedad(await reportesApi.antiguedadCxp(f.proveedor_rnc || undefined)),
  },
  {
    id: "cobros-pagos",
    cat: "Finanzas",
    t: "Cobros y pagos del período",
    d: "Dinero recibido de clientes y pagado a proveedores",
    usaPeriodo: true,
    filtros: [],
    cargar: async (p) => {
      const r = await reportesApi.cobrosPagos(p);
      const fila = (x: { cantidad_cobros: number; cobros: string; cantidad_pagos: number; pagos: string; neto: string }): Celda[] => [
        x.cantidad_cobros,
        n(x.cobros),
        x.cantidad_pagos,
        n(x.pagos),
        n(x.neto),
      ];
      return {
        advertencias: r.advertencias,
        tablas: [
          {
            titulo: "Por mes",
            columnas: [{ t: "Mes" }, { t: "Cobros", formato: "entero" }, { t: "Cobrado", formato: "monto" }, { t: "Pagos", formato: "entero" }, { t: "Pagado", formato: "monto" }, { t: "Neto", formato: "monto" }],
            filas: r.filas.map((x) => [MESES_CORTOS(x.periodo), ...fila(x)]),
            total: ["Total", ...fila(r.totales)],
          },
          {
            titulo: "Por método",
            columnas: [{ t: "Método" }, { t: "Cobrado", formato: "monto" }, { t: "Pagado", formato: "monto" }],
            filas: r.por_metodo.map((m) => [METODO[m.metodo] ?? m.metodo, n(m.cobros), n(m.pagos)]),
          },
          {
            titulo: "Movimientos",
            columnas: [{ t: "Fecha" }, { t: "Tipo" }, { t: "Documento", formato: "mono" }, { t: "Cliente / proveedor" }, { t: "Método" }, { t: "Referencia" }, { t: "Monto", formato: "monto" }],
            filas: r.movimientos.map((m) => [fechaLegible(m.fecha), m.tipo === "cobro" ? "Cobro" : "Pago", m.documento, m.contraparte, METODO[m.metodo] ?? m.metodo, m.referencia, n(m.tipo === "cobro" ? m.monto : `-${m.monto}`)]),
            total: ["Neto", "", "", "", "", "", n(r.totales.neto)],
          },
        ],
      };
    },
  },
  {
    id: "inventario-valorizado",
    cat: "Inventario",
    t: "Inventario valorizado",
    d: "Existencias y valor al costo promedio",
    usaPeriodo: false,
    filtros: [{ clave: "almacen_id", label: "Almacén", tipo: "select", dinamico: "almacenes", opciones: [] }, CATEGORIA_INV],
    cargar: async (_p, f) => {
      const r = await reportesApi.inventarioValorizado({ almacen_id: f.almacen_id || undefined, categoria: f.categoria || undefined });
      return {
        subtitulo: `Existencias al ${fechaLegible(r.fecha_corte)}`,
        advertencias: r.advertencias,
        tablas: [
          {
            columnas: [
              { t: "SKU", formato: "mono" },
              { t: "Producto" },
              { t: "Categoría" },
              { t: "Existencia", formato: "cantidad" },
              { t: "Costo promedio", formato: "monto" },
              { t: "Valor al costo", formato: "monto" },
              { t: "Precio de venta", formato: "monto" },
              { t: "Valor a precio de venta", formato: "monto" },
            ],
            filas: r.filas.map((x) => [x.sku, x.bajo_minimo ? `${x.nombre} · bajo mínimo` : x.nombre, x.categoria, n(x.existencia), n(x.costo_promedio), n(x.valor), n(x.precio_venta), n(x.valor_venta)]),
            total: [`${r.totales.productos} productos`, "", "", n(r.totales.existencia), null, n(r.totales.valor), null, n(r.totales.valor_venta)],
          },
        ],
      };
    },
  },
  {
    id: "rotacion",
    cat: "Inventario",
    t: "Rotación de inventario",
    d: "Entradas, salidas y veces que rota la existencia",
    usaPeriodo: true,
    filtros: [CATEGORIA_INV],
    cargar: async (p, f) => {
      const r = await reportesApi.rotacion({ ...p, categoria: f.categoria || undefined });
      const fila = (x: (typeof r.filas)[number]): Celda[] => [
        n(x.existencia_inicial),
        n(x.entradas),
        n(x.salidas),
        n(x.existencia_final),
        n(x.costo_salidas),
        n(x.inventario_promedio),
        n(x.rotacion),
        n(x.dias_inventario),
      ];
      return {
        advertencias: r.advertencias,
        tablas: [
          {
            columnas: [
              { t: "SKU", formato: "mono" },
              { t: "Producto" },
              { t: "Inicial", formato: "cantidad" },
              { t: "Entradas", formato: "cantidad" },
              { t: "Salidas", formato: "cantidad" },
              { t: "Final", formato: "cantidad" },
              { t: "Costo de salidas", formato: "monto" },
              { t: "Existencia promedio", formato: "cantidad" },
              { t: "Rotación", formato: "veces" },
              { t: "Días de inventario", formato: "cantidad" },
            ],
            filas: r.filas.map((x) => [x.sku, x.nombre, ...fila(x)]),
            total: ["", "Total", ...fila(r.totales)],
          },
        ],
      };
    },
  },
  {
    id: "mas-vendidos",
    cat: "Inventario",
    t: "Productos más vendidos",
    d: "Salidas por factura del Kardex",
    usaPeriodo: true,
    filtros: [
      { clave: "limite", label: "Mostrar", tipo: "select", opciones: [{ value: "10", label: "10 primeros" }, { value: "20", label: "20 primeros" }, { value: "50", label: "50 primeros" }] },
      CATEGORIA_INV,
    ],
    cargar: async (p, f) => {
      const r = await reportesApi.masVendidos({ ...p, limite: Number(f.limite) || 20, categoria: f.categoria || undefined });
      return {
        advertencias: r.advertencias,
        tablas: [
          {
            columnas: [
              { t: "#", formato: "entero" },
              { t: "SKU", formato: "mono" },
              { t: "Producto" },
              { t: "Categoría" },
              { t: "Cantidad", formato: "cantidad" },
              { t: "Facturas", formato: "entero" },
              { t: "Costo de lo vendido", formato: "monto" },
              { t: "Participación", formato: "pct" },
            ],
            filas: r.filas.map((x) => [x.posicion, x.sku, x.nombre, x.categoria, n(x.cantidad), x.facturas, n(x.costo), n(x.participacion)]),
            total: [null, "", "Total del período", "", n(r.total_cantidad), null, n(r.total_costo), r.filas.length ? 100 : null],
          },
        ],
      };
    },
  },
  {
    id: "retenciones",
    cat: "Impuestos",
    t: "Retenciones del período",
    d: "Efectuadas a proveedores y las que nos hicieron",
    usaPeriodo: true,
    filtros: [],
    cargar: async (p) => {
      const r = await reportesApi.retenciones(p);
      return {
        advertencias: r.advertencias,
        tablas: [
          {
            titulo: "Efectuadas a proveedores (por fecha de pago)",
            columnas: [{ t: "Proveedor" }, { t: "RNC / cédula", formato: "mono" }, ...COLS_RET],
            filas: r.efectuadas.map((x) => [x.etiqueta, formatoRnc(x.detalle), ...ret(x)]),
            total: ["Total", "", ...ret(r.totales_efectuadas)],
          },
          {
            titulo: "Por tipo de retención",
            columnas: [{ t: "Tipo" }, { t: "Declaración" }, ...COLS_RET],
            filas: r.por_tipo.map((x) => [x.etiqueta, x.clave === "itbis" ? "IT-1" : "IR-17", ...ret(x)]),
          },
          {
            titulo: "Retenidas por clientes (e-CF de venta aceptados)",
            columnas: [{ t: "Cliente" }, { t: "RNC / cédula", formato: "mono" }, ...COLS_RET],
            filas: r.sufridas.map((x) => [x.etiqueta, x.detalle ? formatoRnc(x.detalle) : "—", ...ret(x)]),
            total: ["Total", "", ...ret(r.totales_sufridas)],
          },
        ],
      };
    },
  },
];

export const REPORTE_POR_ID = Object.fromEntries(REPORTES.map((r) => [r.id, r])) as Record<string, DefReporte>;
