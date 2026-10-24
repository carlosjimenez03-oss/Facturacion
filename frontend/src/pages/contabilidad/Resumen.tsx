// Resumen de Contabilidad: tablero con los totales del balance al cierre del período, el resultado del mes
// (ingresos, costos, gastos y utilidad) y lo que falta por contabilizar.
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { IconAlerta } from "../../layout/icons";
import { contabilidadApi } from "../../lib/contabilidadApi";
import type { ResumenContabilidad } from "../../lib/contabilidadApi";
import { formatoMonto } from "../../lib/marca";
import { AvisoError, Cargando } from "../ingresos/IngresosShell";
import { fechaLegible, mensajeError } from "../ingresos/ingresosUtil";
import { montoContable, nombrePeriodo, periodosRecientes } from "../impuestos/impuestosUtil";
import { rangoDePeriodo, usePeriodoContable } from "./contabilidadUtil";

function Kpi({ k, v, s, mal }: { k: string; v: string; s?: string; mal?: boolean }) {
  return (
    <div className="card stat">
      <span className="k">{k}</span>
      <span className={mal ? "v down" : "v"}>{v}</span>
      {s && <span className="s">{s}</span>}
    </div>
  );
}

export function ResumenTab() {
  const [periodo, setPeriodo] = usePeriodoContable();
  const [datos, setDatos] = useState<ResumenContabilidad | null>(null);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const rango = rangoDePeriodo(periodo);

  const cargar = useCallback(() => {
    setCargando(true);
    setError(null);
    contabilidadApi
      .resumen(rangoDePeriodo(periodo))
      .then(setDatos)
      .catch((err) => {
        setDatos(null);
        setError(mensajeError(err));
      })
      .finally(() => setCargando(false));
  }, [periodo]);

  useEffect(cargar, [cargar]);

  const ingresos = Number(datos?.ingresos ?? 0);
  const costos = Number(datos?.costos ?? 0);
  const gastos = Number(datos?.gastos ?? 0);
  const utilidad = Number(datos?.utilidad_neta ?? 0);
  const maximo = Math.max(ingresos, costos, gastos, Math.abs(utilidad), 1);
  const barras = [
    { k: "Ingresos", v: ingresos, color: "#14B8A6" },
    { k: "Costos", v: costos, color: "#F59E0B" },
    { k: "Gastos", v: gastos, color: "#F97316" },
    { k: "Utilidad neta", v: utilidad, color: utilidad < 0 ? "#DC2626" : "#0F766E" },
  ];
  const nombre = nombrePeriodo(periodo);
  const cuadra = datos ? Math.abs(Number(datos.activos) - Number(datos.pasivos) - Number(datos.patrimonio)) < 0.005 : true;

  return (
    <>
      <section className="card card-b ct-ctl">
        <div className="fld">
          <label className="lbl" htmlFor="rs-per">Período</label>
          <select id="rs-per" className="inp" value={periodo} onChange={(e) => setPeriodo(e.target.value)}>
            {periodosRecientes(24).map((p) => (
              <option key={p} value={p}>
                {nombrePeriodo(p, true)}
              </option>
            ))}
          </select>
        </div>
        <span className="hint grow">
          Solo asientos contabilizados · {fechaLegible(rango.desde)} – {fechaLegible(rango.hasta)}
        </span>
        <Link className="btn btn-o" to={`/contabilidad/reportes?desde=${rango.desde}&hasta=${rango.hasta}`}>
          Ver reportes
        </Link>
        <Link className="btn btn-p" to="/contabilidad/asientos/nuevo">
          + Asiento manual
        </Link>
      </section>

      {error && <AvisoError titulo="No se pudo cargar el resumen contable." mensaje={error} onReintentar={cargar} />}
      {cargando && !datos && (
        <section className="card">
          <Cargando />
        </section>
      )}

      {datos && datos.documentos_por_contabilizar > 0 && (
        <div className="al al-w" role="status">
          <IconAlerta />
          <span style={{ flex: 1 }}>
            <b>
              {datos.documentos_por_contabilizar} {datos.documentos_por_contabilizar === 1 ? "documento" : "documentos"} de {nombre} sin su asiento al día.
            </b>{" "}
            Facturas, cobros, compras, pagos o movimientos bancarios nuevos o modificados: genere los asientos del período para que los reportes los incluyan.
          </span>
          <Link className="btn btn-o btn-sm" to={`/contabilidad/asientos?periodo=${periodo}`}>
            Generar asientos
          </Link>
        </div>
      )}
      {datos && datos.documentos_no_contabilizables > 0 && (
        <div className="al al-d" role="status">
          <IconAlerta />
          <span style={{ flex: 1 }}>
            <b>
              {datos.documentos_no_contabilizables} {datos.documentos_no_contabilizables === 1 ? "documento no se puede" : "documentos no se pueden"} contabilizar.
            </b>{" "}
            Falta una cuenta por defecto o la clasificación del 606; vea el detalle en Asientos.
          </span>
          <Link className="btn btn-o btn-sm" to={`/contabilidad/asientos?periodo=${periodo}`}>
            Ver detalle
          </Link>
        </div>
      )}

      {datos && (
        <>
          <div className="ct-kpis">
            <Kpi k="Activos" v={`RD$ ${montoContable(datos.activos)}`} s={`Al ${fechaLegible(datos.hasta)}`} />
            <Kpi k="Pasivos" v={`RD$ ${montoContable(datos.pasivos)}`} s={`Al ${fechaLegible(datos.hasta)}`} />
            <Kpi k="Patrimonio" v={`RD$ ${montoContable(datos.patrimonio)}`} s={cuadra ? "Incluye el resultado del ejercicio" : "No cuadra con activos − pasivos: revise el balance general"} />
            <Kpi k={`Utilidad neta · ${nombre}`} v={`RD$ ${montoContable(datos.utilidad_neta)}`} s={ingresos > 0 ? `Margen ${((utilidad / ingresos) * 100).toFixed(1)} %` : "Sin ingresos en el período"} mal={utilidad < 0} />
          </div>

          <div className="ct-split">
            <section className="card">
              <div className="card-h">
                <div>
                  <h2>Resultado de {nombre}</h2>
                  <span className="hint">Ingresos netos de devoluciones, como en el IT-1</span>
                </div>
                <Link className="btn btn-g btn-sm" to={`/contabilidad/reportes?reporte=resultados&desde=${rango.desde}&hasta=${rango.hasta}`}>
                  Estado de resultados
                </Link>
              </div>
              <div className="ct-barra">
                {barras.map((b) => (
                  <div key={b.k} className="ct-barra-f">
                    <span>{b.k}</span>
                    <div className="pb" aria-hidden="true">
                      <span style={{ width: `${Math.round((Math.abs(b.v) / maximo) * 100)}%`, background: b.color }} />
                    </div>
                    <span className="num">RD$ {montoContable(b.v)}</span>
                  </div>
                ))}
              </div>
            </section>

            <section className="card">
              <div className="card-h">
                <h2>Asientos de {nombre}</h2>
                <Link className="btn btn-g btn-sm" to={`/contabilidad/asientos?periodo=${periodo}`}>
                  Ver asientos
                </Link>
              </div>
              <div className="card-b">
                <dl className="kv" style={{ margin: 0 }}>
                  <dt>Contabilizados</dt>
                  <dd>{datos.asientos_contabilizados}</dd>
                  <dt>Manuales</dt>
                  <dd>{datos.asientos_manuales}</dd>
                  <dt>Borradores</dt>
                  <dd>{datos.borradores}</dd>
                  <dt>Por contabilizar</dt>
                  <dd>{datos.documentos_por_contabilizar}</dd>
                  <dt>Costos y gastos</dt>
                  <dd>RD$ {formatoMonto(costos + gastos)}</dd>
                </dl>
              </div>
            </section>
          </div>
        </>
      )}
    </>
  );
}

