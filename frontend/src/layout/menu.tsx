import type { ReactNode } from "react";
import {
  IconBancos,
  IconBandeja,
  IconCompras,
  IconConfig,
  IconContabilidad,
  IconContactos,
  IconImpuestos,
  IconIngresos,
  IconInicio,
  IconInventario,
  IconNomina,
  IconPortal,
  IconPos,
  IconReportes,
  IconSync,
  IconTareas,
} from "./icons";

export interface MenuHoja {
  label: string;
  to: string;
}

export interface MenuItem {
  id: string;
  label: string;
  icon: ReactNode;
  to?: string;
  hijos?: MenuHoja[];
}

/** Ruta de un módulo aún sin pantalla en React: muestra la página "En construcción". */
const pendiente = (slug: string) => `/modulo/${slug}`;

export const MENU_PRINCIPAL: (MenuItem | "separador")[] = [
  { id: "inicio", label: "Inicio", icon: <IconInicio />, to: "/" },
  { id: "bandeja", label: "Bandeja de entrada", icon: <IconBandeja />, to: "/bandeja" },
  "separador",
  {
    id: "ingresos",
    label: "Ingresos",
    icon: <IconIngresos />,
    hijos: [
      { label: "Facturas de venta", to: "/facturas" },
      { label: "Notas de crédito", to: "/notas-credito" },
      { label: "Notas de débito", to: "/notas-debito" },
      { label: "Cotizaciones", to: "/cotizaciones" },
      { label: "Cobros", to: "/cobros" },
    ],
  },
  {
    id: "compras",
    label: "Compras",
    icon: <IconCompras />,
    hijos: [
      { label: "Facturas de compra", to: "/compras/facturas" },
      { label: "Comprobantes de compras E41", to: "/compras/e41" },
      { label: "Gastos menores E43", to: "/compras/e43" },
      { label: "Pagos a proveedores", to: "/compras/pagos" },
      { label: "Retenciones", to: "/compras/retenciones" },
    ],
  },
  { id: "contactos", label: "Contactos", icon: <IconContactos />, to: "/contactos" },
  {
    id: "inventario",
    label: "Inventario",
    icon: <IconInventario />,
    hijos: [
      { label: "Productos", to: "/inventario/productos" },
      { label: "Entradas de mercancía", to: "/inventario/compras" },
      { label: "Kardex", to: "/inventario/kardex" },
      { label: "Lotes", to: "/inventario/lotes" },
      { label: "Ajustes", to: "/inventario/ajustes" },
      { label: "Almacenes", to: "/inventario/almacenes" },
    ],
  },
  {
    id: "bancos",
    label: "Bancos",
    icon: <IconBancos />,
    hijos: [
      { label: "Cuentas bancarias", to: pendiente("cuentas-bancarias") },
      { label: "Conciliación", to: pendiente("conciliacion") },
      { label: "Transferencias", to: pendiente("transferencias") },
    ],
  },
  {
    id: "contabilidad",
    label: "Contabilidad",
    icon: <IconContabilidad />,
    hijos: [
      { label: "Catálogo de cuentas", to: pendiente("catalogo-cuentas") },
      { label: "Asientos", to: pendiente("asientos") },
      { label: "Activos fijos", to: pendiente("activos-fijos") },
    ],
  },
  {
    id: "impuestos",
    label: "Impuestos",
    icon: <IconImpuestos />,
    hijos: [
      { label: "Formato 606", to: "/impuestos/formatos/606" },
      { label: "Formato 607", to: "/impuestos/formatos/607" },
      { label: "Formato 608", to: "/impuestos/formatos/608" },
      { label: "Pre-cálculo IT-1", to: "/impuestos/precalculo/it1" },
      { label: "Pre-cálculo IR-2", to: "/impuestos/precalculo/ir2" },
      { label: "IR-17", to: "/impuestos/precalculo/ir17" },
    ],
  },
  { id: "reportes", label: "Reportes", icon: <IconReportes />, to: pendiente("reportes") },
  { id: "tareas", label: "Mis tareas", icon: <IconTareas />, to: pendiente("tareas") },
  "separador",
  { id: "sync", label: "Centro de sincronización", icon: <IconSync />, to: pendiente("sincronizacion") },
  { id: "config", label: "Configuración", icon: <IconConfig />, to: pendiente("configuracion") },
];

export const MENU_SOLUCIONES: MenuItem[] = [
  { id: "nomina", label: "Nómina", icon: <IconNomina />, to: pendiente("nomina") },
  { id: "pos", label: "POS", icon: <IconPos />, to: "/pos" },
  { id: "portal", label: "Portal de clientes", icon: <IconPortal />, to: "/portal-clientes" },
  { id: "ventas", label: "Kontara Ventas", icon: <IconReportes />, to: "/ventas" },
];

/** Descripción de cada módulo pendiente, para la página "En construcción". */
export const MODULOS_PENDIENTES: Record<string, { titulo: string; descripcion: string }> = {
  bandeja: { titulo: "Bandeja de entrada", descripcion: "e-CF recibidos de proveedores con aprobación o rechazo comercial (ARECF/ACECF)." },
  "notas-credito": { titulo: "Notas de crédito", descripcion: "Listado de e-CF tipo 34. Mientras tanto puede emitirlas desde Nueva factura eligiendo el tipo 34." },
  "notas-debito": { titulo: "Notas de débito", descripcion: "Listado de e-CF tipo 33. Mientras tanto puede emitirlas desde Nueva factura eligiendo el tipo 33." },
  cotizaciones: { titulo: "Cotizaciones", descripcion: "Propuestas comerciales sin valor fiscal que se convierten en factura con un clic." },
  cobros: { titulo: "Cobros", descripcion: "Cuentas por cobrar y registro de pagos recibidos de clientes." },
  "compras-e41": { titulo: "Comprobantes de compras E41", descripcion: "e-CF que usted emite al comprar a proveedores informales." },
  "gastos-e43": { titulo: "Gastos menores E43", descripcion: "Peajes, parqueos y gastos pequeños sin comprobante del proveedor." },
  "pagos-proveedores": { titulo: "Pagos a proveedores", descripcion: "Facturas de compra por pagar y sus retenciones." },
  retenciones: { titulo: "Retenciones", descripcion: "ISR e ITBIS retenidos, certificados de retención e IR-17." },
  lotes: { titulo: "Lotes", descripcion: "Control de lotes y fechas de vencimiento." },
  ajustes: { titulo: "Ajustes de inventario", descripcion: "Entradas y salidas por conteo físico, mermas o daños." },
  almacenes: { titulo: "Almacenes", descripcion: "Bodegas y sucursales del inventario." },
  "cuentas-bancarias": { titulo: "Cuentas bancarias", descripcion: "Saldos y movimientos por cuenta." },
  conciliacion: { titulo: "Conciliación bancaria", descripcion: "Cruce del extracto bancario con los registros del sistema." },
  transferencias: { titulo: "Transferencias", descripcion: "Movimientos entre cuentas propias." },
  "catalogo-cuentas": { titulo: "Catálogo de cuentas", descripcion: "Plan de cuentas contable." },
  asientos: { titulo: "Asientos contables", descripcion: "Asientos automáticos y manuales." },
  "activos-fijos": { titulo: "Activos fijos", descripcion: "Registro y depreciación por categoría DGII." },
  "formato-607": { titulo: "Formato 607", descripcion: "Ventas del período a partir de los e-CF aceptados, separadas por forma de pago." },
  "formato-608": { titulo: "Formato 608", descripcion: "Comprobantes anulados con su motivo; permite el reporte en blanco." },
  "it-1": { titulo: "Pre-cálculo IT-1", descripcion: "ITBIS facturado, adelantado con proporcionalidad y retenciones." },
  "ir-2": { titulo: "Pre-cálculo IR-2", descripcion: "Renta neta imponible e ISR anual de sociedades." },
  "ir-17": { titulo: "IR-17", descripcion: "Retenciones de ISR del mes." },
  reportes: { titulo: "Reportes", descripcion: "Estados financieros, ventas, cuentas por cobrar e impuestos." },
  tareas: { titulo: "Mis tareas", descripcion: "Pendientes fiscales y de gestión con sus fechas límite." },
  sincronizacion: { titulo: "Centro de sincronización", descripcion: "Envío de e-CF firmados en contingencia y registro de facturas en papel." },
  configuracion: { titulo: "Configuración", descripcion: "Datos de la empresa, certificado .p12, ambiente DGII, secuencias y envío automático." },
  nomina: { titulo: "Nómina", descripcion: "Cálculo de TSS, ISR e INFOTEP y dispersión por banco." },
  pos: { titulo: "POS", descripcion: "Punto de venta con e-CF de consumo." },
  "portal-clientes": { titulo: "Portal de clientes", descripcion: "Enlace para que sus clientes descarguen sus e-CF y paguen." },
  ventas: { titulo: "Ventas", descripcion: "Automatizaciones de correo y WhatsApp para fidelizar clientes." },
};
