export interface Vencimiento {
  formulario: string;
  descripcion: string;
  fecha: Date;
  diasRestantes: number;
}

const OBLIGACIONES = [
  { dia: 10, formulario: "IR-17", descripcion: "Retenciones de ISR del mes anterior" },
  { dia: 15, formulario: "Formatos 606 · 607 · 608", descripcion: "Envío de datos de compras, ventas y anulados" },
  { dia: 20, formulario: "IT-1", descripcion: "Declaración y pago de ITBIS" },
];

/** Si el vencimiento cae sábado o domingo, se traslada al lunes siguiente. */
function habil(fecha: Date): Date {
  const d = new Date(fecha);
  if (d.getDay() === 6) d.setDate(d.getDate() + 2);
  if (d.getDay() === 0) d.setDate(d.getDate() + 1);
  return d;
}

/** Próximos vencimientos fiscales mensuales (IR-17 día 10, 606/607/608 día 15, IT-1 día 20). */
export function proximosVencimientos(hoy = new Date()): Vencimiento[] {
  const base = new Date(hoy.getFullYear(), hoy.getMonth(), hoy.getDate());
  return OBLIGACIONES.map((o) => {
    let fecha = habil(new Date(base.getFullYear(), base.getMonth(), o.dia));
    if (fecha < base) fecha = habil(new Date(base.getFullYear(), base.getMonth() + 1, o.dia));
    const diasRestantes = Math.round((fecha.getTime() - base.getTime()) / 86_400_000);
    return { formulario: o.formulario, descripcion: o.descripcion, fecha, diasRestantes };
  }).sort((a, b) => a.fecha.getTime() - b.fecha.getTime());
}
