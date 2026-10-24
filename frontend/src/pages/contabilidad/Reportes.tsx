// Reportes contables (selector como Reportes.dc.html): libro diario, mayor por cuenta, balanza de
// comprobación, estado de resultados y balance general para un rango de fechas, con impresión y CSV.
import { useCallback, useEffect, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { contabilidadApi } from "../../lib/contabilidadApi";
import type { BalanceGeneral, Balanza, CuentaContable, EstadoResultados, LibroDiario, Mayor, SeccionEstado } from "../../lib/contabilidadApi";
import { formatoMonto, formatoRnc } from "../../lib/marca";
import { useDatosEmpresa } from "../../lib/configuracionApi";
import { AvisoError, Cargando, Vacio } from "../ingresos/IngresosShell";
import { descargarCsv, fechaLegible, hoyIso, mensajeError } from "../ingresos/ingresosUtil";
import { montoContable } from "../impuestos/impuestosUtil";
import type { MostrarAviso } from "./Contabilidad";
import { etiquetaCuentaContable, ORIGEN_LABEL } from "./contabilidadUtil";

type Reporte = "diario" | "mayor" | "balanza" | "resultados" | "balance";

const REPORTES: { id: Reporte; cat: string; t: string; d: string }[] = [
  { id: "diario", cat: "Libros", t: "Libro diario", d: "Asientos del período con sus líneas" },
  { id: "mayor", cat: "Libros", t: "Mayor por cuenta", d: "Movimientos y saldo corrido de una cuenta" },
  { id: "balanza", cat: "Control", t: "Balanza de comprobación", d: "Saldos, débitos y créditos por cuenta" },
  { id: "resultados", cat: "Estados financieros", t: "Estado de resultados", d: "Ingresos, costos, gastos y utilidad" },
  { id: "balance", cat: "Estados financieros", t: "Balance general", d: "Activos, pasivos y patrimonio a la fecha" },
];

type Datos =
  | { tipo: "diario"; r: LibroDiario }
  | { tipo: "mayor"; r: Mayor }
  | { tipo: "balanza"; r: Balanza }
  | { tipo: "resultados"; r: EstadoResultados }
  | { tipo: "balance"; r: BalanceGeneral };

type Fila = (string | number | null | undefined)[];

function sangria(nivel: number): CSSProperties {
  return { paddingLeft: 16 + Math.max(nivel - 1, 0) * 16 };
}

function Cuadre({ ok, si, no }: { ok: boolean; si: string; no: string }) {
  return <span className={ok ? "bd b-acep" : "bd b-rech"}>{ok ? si : no}</span>;
}

export function ReportesTab({ mostrarAviso }: { mostrarAviso: MostrarAviso }) {
  const miEmpresa = useDatosEmpresa();
  const [params, setParams] = useSearchParams();
  const reporte = (REPORTES.find((r) => r.id === params.get("reporte"))?.id ?? "balanza") as Reporte;
  const desde = params.get("desde") ?? `${hoyIso().slice(0, 7)}-01`;
  const hasta = params.get("hasta") ?? hoyIso();
  const cuentaId = params.get("cuenta") ?? "";
  const nivel = params.get("nivel") ?? "";
  const [cuentas, setCuentas] = useState<CuentaContable[]>([]);
  const [datos, setDatos] = useState<Datos | null>(null);
  const [cargando, setCargando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function cambiar(valores: Record<string, string>) {
    setParams(
      (prev) => {
        const p = new URLSearchParams(prev);
        for (const [k, v] of Object.entries(valores)) {
          if (v) p.set(k, v);
          else p.delete(k);
        }
        return p;
      },
      { replace: true },
    );
  }

  useEffect(() => {
    contabilidadApi
      .listarCuentas()
      .then(setCuentas)
      .catch(() => setCuentas([]));
  }, []);

  // El mayor necesita una cuenta: por defecto, Bancos o la primera de movimiento.
  useEffect(() => {
    if (reporte !== "mayor" || cuentaId || cuentas.length === 0) return;
    const c = cuentas.find((x) => x.codigo === "1.1.01.03") ?? cuentas.find((x) => x.acepta_movimientos) ?? cuentas[0];
    cambiar({ cuenta: c.id });
  }, [reporte, cuentaId, cuentas]);

  const cargar = useCallback(() => {
    if (desde && hasta && desde > hasta) {
      setError("La fecha «desde» es posterior a «hasta».");
      setDatos(null);
      return;
    }
    const rango = { desde: desde || undefined, hasta: hasta || undefined };
    let promesa: Promise<Datos>;
    switch (reporte) {
      case "diario":
        promesa = contabilidadApi.libroDiario(rango).then((r) => ({ tipo: "diario", r }));
        break;
      case "mayor":
        if (!cuentaId) return;
        promesa = contabilidadApi.mayor(cuentaId, rango).then((r) => ({ tipo: "mayor", r }));
        break;
      case "resultados":
        promesa = contabilidadApi.estadoResultados(rango).then((r) => ({ tipo: "resultados", r }));
        break;
      case "balance":
        promesa = contabilidadApi.balanceGeneral(hasta || undefined).then((r) => ({ tipo: "balance", r }));
        break;
      default:
        promesa = contabilidadApi.balanza({ ...rango, nivel: nivel ? Number(nivel) : undefined }).then((r) => ({ tipo: "balanza", r }));
    }
    setCargando(true);
    setError(null);
    promesa
      .then(setDatos)
      .catch((err) => {
        setDatos(null);
        setError(mensajeError(err));
      })
      .finally(() => setCargando(false));
  }, [reporte, desde, hasta, cuentaId, nivel]);

  useEffect(cargar, [cargar]);

  const actual = REPORTES.find((r) => r.id === reporte)!;
  const periodo = reporte === "balance" ? `Al ${fechaLegible(hasta)}` : `Del ${fechaLegible(desde)} al ${fechaLegible(hasta)}`;
  const vigentes = datos && datos.tipo === reporte ? datos : null;

  function exportar() {
    if (!vigentes) return;
    const filas = filasCsv(vigentes);
    descargarCsv(`${actual.t.replace(/\s+/g, "_")}_${reporte === "balance" ? hasta : `${desde}_${hasta}`}.csv`, filas);
    mostrarAviso(`${actual.t} exportado`, { detalle: "Listo para abrir en Excel." });
  }

  return (
    <div className="ct-reporte" style={{ display: "contents" }}>
      <div className="ct-reps no-imprimir" role="group" aria-label="Reportes contables">
        {REPORTES.map((r) => (
          <button key={r.id} type="button" className={r.id === reporte ? "ct-rep on" : "ct-rep"} aria-pressed={r.id === reporte} onClick={() => cambiar({ reporte: r.id })}>
            <span className="chip">{r.cat}</span>
            <b>{r.t}</b>
            <span>{r.d}</span>
          </button>
        ))}
      </div>

      <section className="card card-b ct-ctl no-imprimir">
        {reporte !== "balance" && (
          <div className="fld">
            <label className="lbl" htmlFor="rp-desde">Desde</label>
            <input id="rp-desde" type="date" className="inp" value={desde} max={hasta || undefined} onChange={(e) => cambiar({ desde: e.target.value })} />
          </div>
        )}
        <div className="fld">
          <label className="lbl" htmlFor="rp-hasta">{reporte === "balance" ? "Al" : "Hasta"}</label>
          <input id="rp-hasta" type="date" className="inp" value={hasta} min={reporte === "balance" ? undefined : desde || undefined} onChange={(e) => cambiar({ hasta: e.target.value })} />
        </div>
        {reporte === "mayor" && (
          <div className="fld" style={{ flex: "2 1 320px" }}>
            <label className="lbl" htmlFor="rp-cta">Cuenta</label>
            <select id="rp-cta" className="inp" value={cuentaId} onChange={(e) => cambiar({ cuenta: e.target.value })}>
              {cuentas.length === 0 && <option value="">Cargando…</option>}
              {cuentas.map((c) => (
                <option key={c.id} value={c.id}>
                  {"  ".repeat(Math.max(c.nivel - 1, 0))}
                  {etiquetaCuentaContable(c)}
                </option>
              ))}
            </select>
          </div>
        )}
        {reporte === "balanza" && (
          <div className="fld">
            <label className="lbl" htmlFor="rp-niv">Detalle</label>
            <select id="rp-niv" className="inp" value={nivel} onChange={(e) => cambiar({ nivel: e.target.value })}>
              <option value="">Todas las cuentas</option>
              <option value="1">Nivel 1 (grupos)</option>
              <option value="2">Hasta nivel 2</option>
              <option value="3">Hasta nivel 3</option>
            </select>
          </div>
        )}
      </section>

      <div className="ct-imp">
        <h1>{actual.t}</h1>
        <p>
          {miEmpresa.razonSocial}
          {miEmpresa.rnc ? ` · RNC ${formatoRnc(miEmpresa.rnc)}` : ""} · {periodo} · Montos en RD$ · solo asientos contabilizados
        </p>
      </div>

      <section className="card">
        <div className="card-h">
          <div>
            <h2>{actual.t}</h2>
            <span className="hint">
              {actual.d} · {periodo}
            </span>
          </div>
          <div className="row no-imprimir">
            {vigentes?.tipo === "balanza" && <Cuadre ok={vigentes.r.cuadrada} si="Cuadrada: débitos = créditos" no="No cuadra" />}
            {vigentes?.tipo === "balance" && <Cuadre ok={vigentes.r.cuadrado} si="Cuadrado" no="No cuadra" />}
            <button type="button" className="btn btn-o btn-sm" onClick={exportar} disabled={!vigentes}>
              Exportar Excel
            </button>
            <button type="button" className="btn btn-o btn-sm" onClick={() => window.print()} disabled={!vigentes}>
              Imprimir
            </button>
          </div>
        </div>
        {error && (
          <div className="card-b" style={{ paddingTop: 0 }}>
            <AvisoError titulo={`No se pudo generar ${actual.t.toLowerCase()}.`} mensaje={error} onReintentar={cargar} />
          </div>
        )}
        {cargando && !vigentes && <Cargando />}
        {vigentes && <div style={{ opacity: cargando ? 0.6 : 1 }}>{contenido(vigentes)}</div>}
      </section>
    </div>
  );
}

function sinDatos(texto: string): ReactNode {
  return (
    <Vacio titulo="Sin movimientos en el período" texto={texto}>
      <Link className="btn btn-o btn-sm" to="/contabilidad/asientos">
        Ir a asientos
      </Link>
    </Vacio>
  );
}

function contenido(d: Datos): ReactNode {
  switch (d.tipo) {
    case "diario":
      return <TablaDiario r={d.r} />;
    case "mayor":
      return <TablaMayor r={d.r} />;
    case "balanza":
      return <TablaBalanza r={d.r} />;
    case "resultados":
      return <TablaResultados r={d.r} />;
    case "balance":
      return <TablaBalance r={d.r} />;
  }
}

function TablaDiario({ r }: { r: LibroDiario }) {
  if (r.asientos.length === 0) return sinDatos("No hay asientos contabilizados en estas fechas. Genere los asientos del período o registre uno manual.");
  return (
    <div className="tw">
      <table className="tbl" style={{ minWidth: 820 }}>
        <thead>
          <tr>
            <th>Fecha</th>
            <th>Asiento · Cuenta</th>
            <th>Descripción</th>
            <th className="num">Débito</th>
            <th className="num">Crédito</th>
          </tr>
        </thead>
        <tbody>
          {r.asientos.map((a) => [
            <tr key={a.id} className="ct-sub">
              <td className="nowrap">{fechaLegible(a.fecha)}</td>
              <td colSpan={2}>
                <Link to={`/contabilidad/asientos/${a.id}`} className="link mono">
                  {a.numero}
                </Link>{" "}
                · {a.concepto} <span className="chip chip-sm">{a.origen_etiqueta}</span>
              </td>
              <td className="num">{formatoMonto(a.total_debito)}</td>
              <td className="num">{formatoMonto(a.total_credito)}</td>
            </tr>,
            ...a.lineas.map((l) => (
              <tr key={l.id}>
                <td />
                <td style={{ paddingLeft: Number(l.credito) > 0 ? 44 : 16 }}>
                  <span className="mono mut">{l.cuenta_codigo}</span> {l.cuenta_nombre}
                </td>
                <td className="mut">{l.descripcion ?? ""}</td>
                <td className="num">{Number(l.debito) ? formatoMonto(l.debito) : ""}</td>
                <td className="num">{Number(l.credito) ? formatoMonto(l.credito) : ""}</td>
              </tr>
            )),
          ])}
        </tbody>
        <tfoot>
          <tr>
            <td colSpan={3}>
              Totales · {r.total_asientos} {r.total_asientos === 1 ? "asiento" : "asientos"}
            </td>
            <td className="num">{formatoMonto(r.total_debitos)}</td>
            <td className="num">{formatoMonto(r.total_creditos)}</td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

function TablaMayor({ r }: { r: Mayor }) {
  return (
    <div className="tw">
      <table className="tbl" style={{ minWidth: 900 }}>
        <thead>
          <tr>
            <th>Fecha</th>
            <th>Asiento</th>
            <th>Concepto</th>
            <th className="num">Débito</th>
            <th className="num">Crédito</th>
            <th className="num">Saldo</th>
          </tr>
        </thead>
        <tbody>
          <tr className="ct-sub">
            <td className="nowrap">{fechaLegible(r.desde)}</td>
            <td colSpan={4}>
              Saldo inicial · {etiquetaCuentaContable(r.cuenta)} ({r.cuenta.naturaleza})
            </td>
            <td className="num">{montoContable(r.saldo_inicial)}</td>
          </tr>
          {r.movimientos.map((m, i) => (
            <tr key={`${m.asiento_id}-${i}`}>
              <td className="mut nowrap">{fechaLegible(m.fecha)}</td>
              <td className="nowrap">
                <Link to={`/contabilidad/asientos/${m.asiento_id}`} className="link mono">
                  {m.numero}
                </Link>
              </td>
              <td>
                {m.concepto}
                <div className="hint">
                  {ORIGEN_LABEL[m.origen] ?? m.origen}
                  {m.cuenta_codigo !== r.cuenta.codigo ? ` · ${m.cuenta_codigo} ${m.cuenta_nombre}` : ""}
                  {m.descripcion ? ` · ${m.descripcion}` : ""}
                </div>
              </td>
              <td className="num">{Number(m.debito) ? formatoMonto(m.debito) : ""}</td>
              <td className="num">{Number(m.credito) ? formatoMonto(m.credito) : ""}</td>
              <td className="num" style={{ fontWeight: 600 }}>
                {montoContable(m.saldo)}
              </td>
            </tr>
          ))}
          {r.movimientos.length === 0 && (
            <tr>
              <td colSpan={6} className="mut" style={{ textAlign: "center", padding: "24px 14px" }}>
                Sin movimientos de esta cuenta en el período.
              </td>
            </tr>
          )}
        </tbody>
        <tfoot>
          <tr>
            <td colSpan={3}>Saldo final al {fechaLegible(r.hasta)}</td>
            <td className="num">{formatoMonto(r.total_debitos)}</td>
            <td className="num">{formatoMonto(r.total_creditos)}</td>
            <td className="num">{montoContable(r.saldo_final)}</td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

function TablaBalanza({ r }: { r: Balanza }) {
  if (r.cuentas.length === 0) return sinDatos("Ninguna cuenta tiene saldo ni movimientos en estas fechas.");
  return (
    <div className="tw">
      <table className="tbl" style={{ minWidth: 980 }}>
        <thead>
          <tr>
            <th>Código</th>
            <th>Cuenta</th>
            <th className="num">Saldo inicial</th>
            <th className="num">Débitos</th>
            <th className="num">Créditos</th>
            <th className="num">Saldo deudor</th>
            <th className="num">Saldo acreedor</th>
          </tr>
        </thead>
        <tbody>
          {r.cuentas.map((c) => (
            <tr key={c.cuenta_id} style={{ fontWeight: c.acepta_movimientos ? 400 : 600 }}>
              <td className="mono mut">{c.codigo}</td>
              <td style={sangria(c.nivel)}>{c.nombre}</td>
              <td className="num">{montoContable(c.saldo_inicial)}</td>
              <td className="num">{formatoMonto(c.debitos)}</td>
              <td className="num">{formatoMonto(c.creditos)}</td>
              <td className="num">{Number(c.saldo_final_deudor) ? formatoMonto(c.saldo_final_deudor) : ""}</td>
              <td className="num">{Number(c.saldo_final_acreedor) ? formatoMonto(c.saldo_final_acreedor) : ""}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <td colSpan={2}>Totales (cuentas de movimiento)</td>
            <td className="num">
              {formatoMonto(r.totales.saldo_inicial_deudor)} / {formatoMonto(r.totales.saldo_inicial_acreedor)}
            </td>
            <td className="num" data-total="debitos">
              {formatoMonto(r.totales.debitos)}
            </td>
            <td className="num" data-total="creditos">
              {formatoMonto(r.totales.creditos)}
            </td>
            <td className="num">{formatoMonto(r.totales.saldo_final_deudor)}</td>
            <td className="num">{formatoMonto(r.totales.saldo_final_acreedor)}</td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

function Seccion({ titulo, s, total }: { titulo: string; s: SeccionEstado; total: string }) {
  return (
    <>
      <tr className="ct-sub">
        <td colSpan={3}>{titulo}</td>
      </tr>
      {s.cuentas.map((c) => (
        <tr key={c.cuenta_id} style={{ fontWeight: c.acepta_movimientos ? 400 : 600 }}>
          <td className="mono mut">{c.codigo}</td>
          <td style={sangria(c.nivel)}>{c.nombre}</td>
          <td className="num">{montoContable(c.monto)}</td>
        </tr>
      ))}
      {s.cuentas.length === 0 && (
        <tr>
          <td />
          <td className="mut">Sin movimientos</td>
          <td className="num">0.00</td>
        </tr>
      )}
      <tr className="ct-total">
        <td />
        <td>{total}</td>
        <td className="num">{montoContable(s.total)}</td>
      </tr>
    </>
  );
}

function TablaResultados({ r }: { r: EstadoResultados }) {
  return (
    <div className="tw">
      <table className="tbl" style={{ minWidth: 640 }}>
        <thead>
          <tr>
            <th style={{ width: 120 }}>Código</th>
            <th>Cuenta</th>
            <th className="num">Monto (RD$)</th>
          </tr>
        </thead>
        <tbody>
          <Seccion titulo="Ingresos" s={r.ingresos} total="Total ingresos (netos de devoluciones)" />
          <Seccion titulo="Costos" s={r.costos} total="Total costos" />
          <tr className="ct-total">
            <td />
            <td>Utilidad bruta</td>
            <td className="num">{montoContable(r.utilidad_bruta)}</td>
          </tr>
          <Seccion titulo="Gastos" s={r.gastos} total="Total gastos" />
          <tr className="ct-total ct-res">
            <td />
            <td>Utilidad neta · margen {Number(r.margen_neto_pct).toFixed(1)} %</td>
            <td className="num">{montoContable(r.utilidad_neta)}</td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

function TablaBalance({ r }: { r: BalanceGeneral }) {
  return (
    <div className="tw">
      <table className="tbl" style={{ minWidth: 640 }}>
        <thead>
          <tr>
            <th style={{ width: 120 }}>Código</th>
            <th>Cuenta</th>
            <th className="num">Monto (RD$)</th>
          </tr>
        </thead>
        <tbody>
          <Seccion titulo="Activos" s={r.activos} total="Total activos" />
          <Seccion titulo="Pasivos" s={r.pasivos} total="Total pasivos" />
          <Seccion titulo="Patrimonio" s={r.patrimonio} total="Patrimonio registrado" />
          <tr>
            <td />
            <td>Resultado del ejercicio (desde el 1 de enero)</td>
            <td className="num">{montoContable(r.resultado_ejercicio)}</td>
          </tr>
          {Number(r.resultados_anteriores_no_cerrados) !== 0 && (
            <tr>
              <td />
              <td>Resultados de ejercicios anteriores no cerrados</td>
              <td className="num">{montoContable(r.resultados_anteriores_no_cerrados)}</td>
            </tr>
          )}
          <tr className="ct-total">
            <td />
            <td>Total patrimonio</td>
            <td className="num">{montoContable(r.total_patrimonio)}</td>
          </tr>
          <tr className="ct-total ct-res">
            <td />
            <td>Total pasivo + patrimonio</td>
            <td className="num">{montoContable(r.total_pasivo_patrimonio)}</td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

const n2 = (v: string | number | null | undefined) => (v == null || v === "" ? "" : Number(v).toFixed(2));

function filasSeccion(titulo: string, s: SeccionEstado, total: string): Fila[] {
  return [[titulo, "", ""], ...s.cuentas.map((c) => [c.codigo, c.nombre, n2(c.monto)]), ["", total, n2(s.total)]];
}

function filasCsv(d: Datos): Fila[] {
  switch (d.tipo) {
    case "diario":
      return [
        ["Fecha", "Asiento", "Concepto", "Cuenta", "Nombre de la cuenta", "Descripcion", "Debito", "Credito"],
        ...d.r.asientos.flatMap((a) =>
          a.lineas.map((l) => [fechaLegible(a.fecha), a.numero, a.concepto, l.cuenta_codigo, l.cuenta_nombre, l.descripcion, n2(l.debito), n2(l.credito)]),
        ),
        ["", "", "Totales", "", "", "", n2(d.r.total_debitos), n2(d.r.total_creditos)],
      ];
    case "mayor":
      return [
        ["Fecha", "Asiento", "Concepto", "Cuenta", "Descripcion", "Debito", "Credito", "Saldo"],
        [fechaLegible(d.r.desde), "", "Saldo inicial", d.r.cuenta.codigo, "", "", "", n2(d.r.saldo_inicial)],
        ...d.r.movimientos.map((m) => [fechaLegible(m.fecha), m.numero, m.concepto, m.cuenta_codigo, m.descripcion, n2(m.debito), n2(m.credito), n2(m.saldo)]),
        ["", "", "Saldo final", "", "", n2(d.r.total_debitos), n2(d.r.total_creditos), n2(d.r.saldo_final)],
      ];
    case "balanza":
      return [
        ["Codigo", "Cuenta", "Nivel", "Saldo inicial", "Debitos", "Creditos", "Saldo deudor", "Saldo acreedor"],
        ...d.r.cuentas.map((c) => [c.codigo, c.nombre, c.nivel, n2(c.saldo_inicial), n2(c.debitos), n2(c.creditos), n2(c.saldo_final_deudor), n2(c.saldo_final_acreedor)]),
        ["", "Totales", "", "", n2(d.r.totales.debitos), n2(d.r.totales.creditos), n2(d.r.totales.saldo_final_deudor), n2(d.r.totales.saldo_final_acreedor)],
      ];
    case "resultados":
      return [
        ["Codigo", "Cuenta", "Monto"],
        ...filasSeccion("Ingresos", d.r.ingresos, "Total ingresos"),
        ...filasSeccion("Costos", d.r.costos, "Total costos"),
        ["", "Utilidad bruta", n2(d.r.utilidad_bruta)],
        ...filasSeccion("Gastos", d.r.gastos, "Total gastos"),
        ["", "Utilidad neta", n2(d.r.utilidad_neta)],
      ];
    case "balance":
      return [
        ["Codigo", "Cuenta", "Monto"],
        ...filasSeccion("Activos", d.r.activos, "Total activos"),
        ...filasSeccion("Pasivos", d.r.pasivos, "Total pasivos"),
        ...filasSeccion("Patrimonio", d.r.patrimonio, "Patrimonio registrado"),
        ["", "Resultado del ejercicio", n2(d.r.resultado_ejercicio)],
        ["", "Resultados anteriores no cerrados", n2(d.r.resultados_anteriores_no_cerrados)],
        ["", "Total patrimonio", n2(d.r.total_patrimonio)],
        ["", "Total pasivo + patrimonio", n2(d.r.total_pasivo_patrimonio)],
      ];
  }
}
