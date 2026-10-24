// Recibos de pago de un período (uno por empleado; ?empleado=<id> para uno solo). Se imprimen con
// el diálogo del navegador (una hoja por recibo) o se guardan como PDF desde ahí.
import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { EMPRESA_NOMBRE, formatoMonto, formatoRnc } from "../../lib/marca";
import { nominaApi, type EmpresaNomina, type LineaNomina, type Periodo } from "../../lib/nominaApi";
import { AvisoError, Cargando } from "../ingresos/IngresosShell";
import { fechaLegible, mensajeError } from "../ingresos/ingresosUtil";
import { Encabezado, NominaShell } from "./NominaShell";
import { capitalizar, formatoCedula, porcentaje } from "./nominaUtil";

function Fila({ k, v }: { k: string; v: string | null | undefined }) {
  if (!Number(v)) return null;
  return (
    <tr>
      <td>{k}</td>
      <td className="num">{formatoMonto(v)}</td>
    </tr>
  );
}

function Recibo({ p, l, empresa }: { p: Periodo; l: LineaNomina; empresa: EmpresaNomina | null }) {
  const tasa = (k: string) => porcentaje(String(p.parametros?.[k] ?? 0));
  return (
    <article className="nm-recibo" aria-label={`Recibo de ${l.nombre}`}>
      <div className="nm-recibo-h">
        <div>
          <h2>Recibo de pago de nómina</h2>
          <div className="mut">{capitalizar(p.nombre)} · del {fechaLegible(p.fecha_inicio)} al {fechaLegible(p.fecha_fin)}</div>
        </div>
        <div style={{ textAlign: "right" }}>
          <b>{empresa?.razon_social || EMPRESA_NOMBRE}</b>
          {empresa?.rnc && <div className="mut">RNC {formatoRnc(empresa.rnc)}</div>}
          <div className="mut">Fecha de pago: {fechaLegible(p.fecha_pago)}</div>
        </div>
      </div>
      <div className="nm-recibo-cols" style={{ marginBottom: 14 }}>
        <div>
          <b>{l.nombre}</b>
          <div className="mut">Cédula {formatoCedula(l.cedula)}</div>
          <div className="mut">{[l.cargo, l.departamento].filter(Boolean).join(" · ") || "—"}</div>
        </div>
        <div>
          <div className="mut">Salario mensual: RD$ {formatoMonto(l.salario_mensual)}</div>
          <div className="mut">Cuenta: {l.banco ?? "—"} {l.cuenta_bancaria ?? ""}</div>
          {l.pagado && <div className="mut">Pagado {l.referencia_pago ? `· ref. ${l.referencia_pago}` : ""}</div>}
        </div>
      </div>
      <div className="nm-recibo-cols">
        <table>
          <thead><tr><th>Ingresos</th><th className="num">RD$</th></tr></thead>
          <tbody>
            <tr><td>Salario</td><td className="num">{formatoMonto(l.salario)}</td></tr>
            <Fila k="Horas extra" v={l.horas_extra} />
            <Fila k="Comisiones / otros ingresos" v={l.otros_ingresos} />
            <Fila k="Bonificación" v={l.bonificacion} />
            <Fila k="Ingresos exentos" v={l.ingresos_exentos} />
            <Fila k="Regalía pascual" v={l.regalia} />
            <tr className="tot"><td>Total ingresos</td><td className="num">{formatoMonto(l.total_ingresos)}</td></tr>
          </tbody>
        </table>
        <table>
          <thead><tr><th>Descuentos</th><th className="num">RD$</th></tr></thead>
          <tbody>
            <tr><td>AFP ({tasa("afp_empleado")})</td><td className="num">{formatoMonto(l.afp)}</td></tr>
            <tr><td>SFS ({tasa("sfs_empleado")})</td><td className="num">{formatoMonto(l.sfs)}</td></tr>
            <tr><td>ISR retenido</td><td className="num">{formatoMonto(l.isr)}</td></tr>
            <Fila k="INFOTEP (0.5 % bonificación)" v={l.infotep_empleado} />
            <Fila k="Otros descuentos" v={l.otros_descuentos} />
            <tr className="tot"><td>Total descuentos</td><td className="num">{formatoMonto(l.total_descuentos)}</td></tr>
          </tbody>
        </table>
      </div>
      <div className="nm-neto">
        <span>Neto a pagar</span>
        <span>RD$ {formatoMonto(l.neto)}</span>
      </div>
      <div className="nm-firmas">
        <span>Recibido conforme (empleado)</span>
        <span>Por la empresa</span>
      </div>
    </article>
  );
}

export function RecibosPage() {
  const { id = "" } = useParams();
  const [params] = useSearchParams();
  const empleado = params.get("empleado");
  const [p, setP] = useState<Periodo | null>(null);
  const [empresa, setEmpresa] = useState<EmpresaNomina | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([nominaApi.periodo(id), nominaApi.empresa().catch(() => null)])
      .then(([periodo, e]) => {
        setP(periodo);
        setEmpresa(e);
      })
      .catch((err) => setError(mensajeError(err)));
  }, [id]);

  const lineas = (p?.lineas ?? []).filter((l) => !empleado || l.empleado_id === empleado);

  return (
    <NominaShell>
      <div className="crumb no-imprimir">
        <Link to="/nomina/periodos">Nómina</Link>
        <span>/</span>
        {p && <Link to={`/nomina/periodos/${id}`}>{capitalizar(p.nombre)}</Link>}
        <span>/</span>
        <span>Recibos</span>
      </div>
      <Encabezado
        titulo="Recibos de pago"
        sub={p ? `${capitalizar(p.nombre)} · ${lineas.length} recibo${lineas.length === 1 ? "" : "s"}` : undefined}
        acciones={
          <>
            {empleado && <Link to={`/nomina/periodos/${id}/recibos`} className="btn btn-o">Ver todos</Link>}
            <button type="button" className="btn btn-p" onClick={() => window.print()} disabled={!p || p.estado === "borrador"}>
              Imprimir / guardar PDF
            </button>
          </>
        }
      />
      {error && <AvisoError mensaje={error} />}
      {!p && !error && <Cargando />}
      {p && p.estado === "borrador" && <AvisoError mensaje="Calcule la nómina para generar los recibos." />}
      {p && p.estado !== "borrador" && lineas.map((l) => <Recibo key={l.empleado_id} p={p} l={l} empresa={empresa} />)}
    </NominaShell>
  );
}
