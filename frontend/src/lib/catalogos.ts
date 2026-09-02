// Catálogos DGII (subconjunto de los usados por el formulario). Ver los XSD
// en services/facturas_api/xsd/ para la lista completa y oficial de cada
// enumeración.

export const TIPOS_ECF: { value: string; label: string }[] = [
  { value: "31", label: "31 - Factura de Crédito Fiscal Electrónica" },
  { value: "32", label: "32 - Factura de Consumo Electrónica" },
  { value: "33", label: "33 - Nota de Débito Electrónica" },
  { value: "34", label: "34 - Nota de Crédito Electrónica" },
  { value: "41", label: "41 - Compras Electrónico" },
  { value: "43", label: "43 - Gastos Menores Electrónico" },
  { value: "44", label: "44 - Regímenes Especiales Electrónico" },
  { value: "45", label: "45 - Gubernamental Electrónico" },
  { value: "46", label: "46 - Comprobante de Exportaciones Electrónico" },
  { value: "47", label: "47 - Comprobante para Pagos al Exterior Electrónico" },
];

export const TIPOS_INGRESOS = [
  { value: "01", label: "01 - Ingresos por operaciones (No financieros)" },
  { value: "02", label: "02 - Ingresos Financieros" },
  { value: "03", label: "03 - Ingresos Extraordinarios" },
  { value: "04", label: "04 - Ingresos por Arrendamientos" },
  { value: "05", label: "05 - Ingresos por Venta de Activo Depreciable" },
  { value: "06", label: "06 - Otros Ingresos" },
];

export const TIPOS_PAGO = [
  { value: "1", label: "Contado" },
  { value: "2", label: "Crédito" },
  { value: "3", label: "Gratuito" },
];

export const FORMAS_PAGO = [
  { value: "1", label: "Efectivo" },
  { value: "2", label: "Cheque/Transferencia/Depósito" },
  { value: "3", label: "Tarjeta de Débito/Crédito" },
  { value: "4", label: "Venta a Crédito" },
  { value: "5", label: "Bonos o Certificados de regalo" },
  { value: "6", label: "Permuta" },
  { value: "7", label: "Nota de crédito" },
  { value: "8", label: "Otras Formas de pago" },
];

export const INDICADORES_FACTURACION = [
  { value: "1", label: "ITBIS 1 (18%)", tasa: 0.18 },
  { value: "2", label: "ITBIS 2 (16%)", tasa: 0.16 },
  { value: "3", label: "ITBIS 3 (0%)", tasa: 0 },
  { value: "4", label: "Exento (E)", tasa: 0 },
  { value: "0", label: "No Facturable", tasa: 0 },
];

export const INDICADOR_BIEN_SERVICIO = [
  { value: "1", label: "Bien" },
  { value: "2", label: "Servicio" },
];

export const CODIGOS_MODIFICACION = [
  { value: "1", label: "Anula el NCF modificado" },
  { value: "2", label: "Corrige texto del comprobante modificado" },
  { value: "3", label: "Corrige montos del NCF modificado" },
  { value: "4", label: "Reemplazo NCF emitido en contingencia" },
  { value: "5", label: "Referencia Factura Consumo Electrónica" },
];

// Subconjunto de unidades de medida (UnidadMedidaType) más comunes.
export const UNIDADES_MEDIDA = [
  { value: "43", label: "UND - Unidad" },
  { value: "34", label: "PZA - Pieza" },
  { value: "6", label: "CAJ - Caja/Cajón" },
  { value: "13", label: "DOC - Docena" },
  { value: "19", label: "HOR - Hora" },
  { value: "26", label: "M - Metro" },
  { value: "21", label: "KG - Kilogramo" },
  { value: "23", label: "LB - Libra" },
  { value: "24", label: "LITRO - Litro" },
];
