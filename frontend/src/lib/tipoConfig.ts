// Reglas estructurales por tipo de e-CF que maneja el formulario (mostrar/
// ocultar secciones, marcar campos requeridos en la UI). Es una GUÍA para la
// experiencia de usuario, no la fuente de verdad -- la validación real y
// autoritativa ocurre en el backend contra el XSD oficial de cada tipo (ver
// services/facturas_api/xsd/eCF_<tipo>.xsd), cuyos errores se muestran tal
// cual al usuario tras "Generar XML". Para 44, 45 y 46 estas reglas son una
// aproximación conservadora (comprador y referencia opcionales); revisar el
// XSD correspondiente antes de asumirlas exactas en producción.
export interface TipoEcfConfig {
  compradorRncRequerido: boolean;
  compradorVisible: boolean;
  informacionReferenciaRequerida: boolean;
  requiereIndicadorNotaCredito: boolean;
  requiereTipoIngresos: boolean;
  requiereFechaVencimientoSecuencia: boolean;
  etiqueta: string;
}

const DEFAULT_CONFIG: TipoEcfConfig = {
  compradorRncRequerido: false,
  compradorVisible: true,
  informacionReferenciaRequerida: false,
  requiereIndicadorNotaCredito: false,
  requiereTipoIngresos: true,
  requiereFechaVencimientoSecuencia: true,
  etiqueta: "",
};

export const TIPO_ECF_CONFIG: Record<string, TipoEcfConfig> = {
  "31": { ...DEFAULT_CONFIG, compradorRncRequerido: true, etiqueta: "Crédito Fiscal" },
  "32": { ...DEFAULT_CONFIG, compradorRncRequerido: false, etiqueta: "Consumo" },
  "33": {
    ...DEFAULT_CONFIG,
    informacionReferenciaRequerida: true,
    etiqueta: "Nota de Débito",
  },
  "34": {
    ...DEFAULT_CONFIG,
    informacionReferenciaRequerida: true,
    requiereIndicadorNotaCredito: true,
    requiereFechaVencimientoSecuencia: false,
    etiqueta: "Nota de Crédito",
  },
  "41": {
    ...DEFAULT_CONFIG,
    compradorRncRequerido: true,
    requiereTipoIngresos: false,
    etiqueta: "Compras",
  },
  "43": {
    ...DEFAULT_CONFIG,
    compradorVisible: false,
    requiereTipoIngresos: false,
    etiqueta: "Gastos Menores",
  },
  "44": { ...DEFAULT_CONFIG, requiereTipoIngresos: false, etiqueta: "Regímenes Especiales" },
  "45": { ...DEFAULT_CONFIG, requiereTipoIngresos: false, etiqueta: "Gubernamental" },
  "46": { ...DEFAULT_CONFIG, requiereTipoIngresos: false, etiqueta: "Exportaciones" },
  "47": { ...DEFAULT_CONFIG, compradorRncRequerido: false, requiereTipoIngresos: false, etiqueta: "Pagos al Exterior" },
};

export function configParaTipo(tipoEcf: string): TipoEcfConfig {
  return TIPO_ECF_CONFIG[tipoEcf] ?? DEFAULT_CONFIG;
}
