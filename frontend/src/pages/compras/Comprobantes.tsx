import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { IconMas } from "../../layout/icons";
import { api } from "../../lib/api";
import { comprasApi } from "../../lib/comprasApi";
import type { ComprobanteCompra } from "../../lib/comprasApi";
import { ESTADO_BADGE, ESTADO_LABEL } from "../../lib/estados";
import type { EstadoFactura } from "../../lib/types";
import { formatoMonto, formatoRnc } from "../../lib/marca";
import { AvisoError, Buscador, Cargando, Estadistica, SinResultados, Vacio } from "../ingresos/IngresosShell";
import { descargarCsv, fechaIso, fechaLegible, hoyIso, mensajeError, mesActualLegible } from "../ingresos/ingresosUtil";
import { ComprasEncabezado } from "./ComprasShell";
import { suma } from "./comprasUtil";

const CONFIG = {
  41: {
    titulo: "Comprobantes de compras E41",
    sub: "Emitidos por usted al comprar a proveedores informales (personas físicas no emisoras)",
    nuevo: "Nuevo comprobante E41",
    singular: "comprobante",
    plural: "comprobantes E41",
    archivo: "Comprobantes_de_compras_E41",
    vacio: "Emita un E41 cuando compre a una persona física que no emite comprobantes: se envía a la DGII con las retenciones.",
  },
  43: {
    titulo: "Gastos menores E43",
    sub: "Gastos pequeños sin comprobante del proveedor: peajes, parqueos, transporte",
    nuevo: "Nuevo gasto menor",
    singular: "gasto menor",
    plural: "gastos menores",
    archivo: "Gastos_menores_E43",
    vacio: "Registre peajes, parqueos y otros gastos pequeños con un comprobante de gastos menores E43.",
  },
} as const;

function estadoLabel(estado: string): string {
  return ESTADO_LABEL[estado as EstadoFactura] ?? estado;
}

function tienePdf(c: ComprobanteCompra): boolean {
  return c.estado === "enviado" || c.estado === "aceptado";
}

/** E41: el monto antes de ITBIS (como el prototipo); E43: lo gastado. */
function montoDe(c: ComprobanteCompra): string {
  return c.tipo_ecf === 41 ? c.monto : c.total;
}

function ComprobantesPage({ tipo }: { tipo: 41 | 43 }) {
  const cfg = CONFIG[tipo];
  const navigate = useNavigate();
  const [lista, setLista] = useState<ComprobanteCompra[]>([]);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");

  const cargar = useCallback(() => {
    setCargando(true);
    setError(null);
    comprasApi
      .listarComprobantes({ tipo_ecf: tipo })
      .then(setLista)
      .catch((err) => {
        setLista([]);
        setError(mensajeError(err));
      })
      .finally(() => setCargando(false));
  }, [tipo]);

  useEffect(() => {
    setQ("");
    cargar();
  }, [cargar]);

  const filas = useMemo(() => {
    const texto = q.trim().toLowerCase();
    return [...lista]
      .sort((a, b) => (b.encf ?? b.creado_en).localeCompare(a.encf ?? a.creado_en))
      .filter(
        (c) =>
          !texto ||
          [c.encf, c.proveedor_nombre, c.proveedor_rnc, c.proveedor_rnc && formatoRnc(c.proveedor_rnc), c.concepto, estadoLabel(c.estado), fechaLegible(c.fecha_emision)]
            .join(" ")
            .toLowerCase()
            .includes(texto),
      );
  }, [lista, q]);

  const total = suma(lista, montoDe);
  const mes = hoyIso().slice(0, 7);
  const delMes = lista.filter((c) => fechaIso(c.fecha_emision ?? c.creado_en).startsWith(mes));

  function exportar() {
    descargarCsv(`${cfg.archivo}_${hoyIso()}.csv`, [
      tipo === 41
        ? ["e-NCF", "Proveedor", "Cédula", "Fecha", "Monto (RD$)", "ITBIS retenido", "ISR retenido", "Estado DGII"]
        : ["e-NCF", "Concepto", "Fecha", "Monto (RD$)", "Estado DGII"],
      ...filas.map((c) =>
        tipo === 41
          ? [c.encf, c.proveedor_nombre, c.proveedor_rnc, fechaLegible(c.fecha_emision), Number(c.monto).toFixed(2), Number(c.itbis_retenido).toFixed(2), Number(c.isr_retenido).toFixed(2), estadoLabel(c.estado)]
          : [c.encf, c.concepto, fechaLegible(c.fecha_emision), Number(c.total).toFixed(2), estadoLabel(c.estado)],
      ),
    ]);
  }

  const nuevoUrl = `/facturas/nueva?tipo=${tipo}`;

  return (
    <div className="content">
      <ComprasEncabezado
        titulo={cfg.titulo}
        sub={cfg.sub}
        acciones={
          <>
            <button type="button" className="btn btn-o" onClick={exportar} disabled={filas.length === 0}>
              Exportar
            </button>
            <Link className="btn btn-p" to={nuevoUrl}>
              <IconMas /> {cfg.nuevo}
            </Link>
          </>
        }
      />

      {error && <AvisoError titulo={`No se pudieron cargar los ${cfg.plural}.`} mensaje={error} onReintentar={cargar} />}

      <div className="grid-stats">
        <Estadistica k="Registros" v={cargando ? "—" : String(lista.length)} />
        <Estadistica k="Monto (RD$) · total" v={cargando ? "—" : `RD$ ${formatoMonto(total)}`} />
        <Estadistica
          k={`Período · ${mesActualLegible()}`}
          v={cargando ? "—" : `RD$ ${formatoMonto(suma(delMes, montoDe))}`}
          s={`${delMes.length} ${delMes.length === 1 ? cfg.singular : cfg.plural} este mes`}
        />
      </div>

      <section className="card">
        <div className="filters">
          <Buscador valor={q} onCambiar={setQ} placeholder={`Buscar en ${cfg.titulo}`} />
        </div>

        {cargando && <Cargando />}
        {!cargando && !error && lista.length === 0 && (
          <Vacio titulo={`No hay ${cfg.plural} todavía`} texto={cfg.vacio}>
            <Link className="btn btn-p btn-sm" to={nuevoUrl}>
              {cfg.nuevo}
            </Link>
          </Vacio>
        )}
        {!cargando && lista.length > 0 && filas.length === 0 && <SinResultados q={q} onLimpiar={() => setQ("")} />}
        {!cargando && filas.length > 0 && (
          <div className="tw">
            <table className="tbl" style={{ minWidth: 960 }}>
              <thead>
                <tr>
                  <th>e-NCF</th>
                  {tipo === 41 ? (
                    <>
                      <th>Proveedor</th>
                      <th>Cédula</th>
                    </>
                  ) : (
                    <th>Concepto</th>
                  )}
                  <th>Fecha</th>
                  <th className="num">Monto (RD$)</th>
                  {tipo === 41 && (
                    <>
                      <th className="num">ITBIS retenido</th>
                      <th className="num">ISR retenido</th>
                    </>
                  )}
                  <th>Estado DGII</th>
                  <th style={{ textAlign: "right" }}>Acciones</th>
                </tr>
              </thead>
              <tbody>
                {filas.map((c) => (
                  <tr key={c.id} className="fila-link" onClick={() => navigate(`/facturas/${c.id}`)}>
                    <td className="nowrap">
                      <Link to={`/facturas/${c.id}`} className="link mono" onClick={(e) => e.stopPropagation()}>
                        {c.encf || "(sin e-NCF)"}
                      </Link>
                    </td>
                    {tipo === 41 ? (
                      <>
                        <td>
                          <div className="strong">{c.proveedor_nombre || "—"}</div>
                          {c.concepto && <div className="hint">{c.concepto}</div>}
                        </td>
                        <td className="mono nowrap">{c.proveedor_rnc ? formatoRnc(c.proveedor_rnc) : "—"}</td>
                      </>
                    ) : (
                      <td>
                        <div className="strong">{c.concepto || "—"}</div>
                        {c.lineas > 1 && <div className="hint">{c.lineas} líneas</div>}
                      </td>
                    )}
                    <td className="nowrap">{fechaLegible(c.fecha_emision)}</td>
                    <td className="num">{formatoMonto(montoDe(c))}</td>
                    {tipo === 41 && (
                      <>
                        <td className="num">{formatoMonto(c.itbis_retenido)}</td>
                        <td className="num">{formatoMonto(c.isr_retenido)}</td>
                      </>
                    )}
                    <td>
                      <span className={ESTADO_BADGE[c.estado as EstadoFactura] ?? "bd b-borr"}>{estadoLabel(c.estado)}</span>
                      {(c.estado === "rechazado" || c.estado === "error") && c.error && <div className="row-motivo">{c.error}</div>}
                    </td>
                    <td onClick={(e) => e.stopPropagation()}>
                      <div className="fila-acc cp-acc">
                        <Link to={`/facturas/${c.id}`} className="btn btn-g btn-sm">
                          Ver detalle
                        </Link>
                        {tienePdf(c) && (
                          <a href={api.representacionImpresaUrl(c.id)} target="_blank" rel="noreferrer" className="btn btn-g btn-sm">
                            PDF
                          </a>
                        )}
                        {tipo === 41 && c.registro606_id && !c.fecha_pago_606 && (
                          <Link to={`/compras/pagos?factura=${c.registro606_id}`} className="btn btn-g btn-sm">
                            Pagar
                          </Link>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

export function ComprobantesE41Page() {
  return <ComprobantesPage key={41} tipo={41} />;
}

export function ComprobantesE43Page() {
  return <ComprobantesPage key={43} tipo={43} />;
}
