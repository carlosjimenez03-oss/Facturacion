import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { IconMas } from "../../layout/icons";
import { comprasApi } from "../../lib/comprasApi";
import type { CuentaPorPagar, PagoProveedor } from "../../lib/comprasApi";
import { formatoMonto, formatoRnc } from "../../lib/marca";
import { AvisoError, Buscador, Cargando, Estadistica, Modal, SinResultados, Vacio } from "../ingresos/IngresosShell";
import { useAviso } from "../ingresos/useAviso";
import { descargarCsv, fechaIso, fechaLegible, hoyIso, mensajeError, mesActualLegible } from "../ingresos/ingresosUtil";
import { ComprasEncabezado } from "./ComprasShell";
import { ESTADO_CXP, etiquetaBienesServicios, mensajeCompras, sePuedeEliminar, suma } from "./comprasUtil";

type Pestana = "pendientes" | "vencidas" | "pagadas" | "todas";

const PESTANAS: { id: Pestana; label: string; test: (c: CuentaPorPagar) => boolean }[] = [
  { id: "pendientes", label: "Por pagar", test: (c) => c.estado !== "pagada" },
  { id: "vencidas", label: "Vencidas", test: (c) => c.estado === "vencida" },
  { id: "pagadas", label: "Pagadas", test: (c) => c.estado === "pagada" },
  { id: "todas", label: "Todas", test: () => true },
];

export function FacturasCompraPage() {
  const navigate = useNavigate();
  const [cuentas, setCuentas] = useState<CuentaPorPagar[]>([]);
  const [pagos, setPagos] = useState<PagoProveedor[]>([]);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pestana, setPestana] = useState<Pestana>("pendientes");
  const [q, setQ] = useState("");
  const [eliminar, setEliminar] = useState<CuentaPorPagar | null>(null);
  const [eliminando, setEliminando] = useState(false);
  const [toast, mostrarToast] = useAviso();

  const cargar = useCallback(async () => {
    setCargando(true);
    const [cxp, hist] = await Promise.allSettled([comprasApi.listarCuentasPorPagar({ estado: "todas" }), comprasApi.listarPagos()]);
    if (cxp.status === "fulfilled") {
      setCuentas(cxp.value);
      setError(null);
    } else {
      setCuentas([]);
      setError(mensajeError(cxp.reason));
    }
    setPagos(hist.status === "fulfilled" ? hist.value : []);
    setCargando(false);
  }, []);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  const filas = useMemo(() => {
    const texto = q.trim().toLowerCase();
    const p = PESTANAS.find((x) => x.id === pestana)!;
    return cuentas
      .filter(p.test)
      .filter(
        (c) =>
          !texto ||
          [c.proveedor_nombre, c.proveedor_rnc, formatoRnc(c.proveedor_rnc), c.ncf, c.concepto, fechaLegible(c.vence)].join(" ").toLowerCase().includes(texto),
      )
      .sort((a, b) => fechaIso(a.vence).localeCompare(fechaIso(b.vence)));
  }, [cuentas, pestana, q]);

  const porPagar = cuentas.filter((c) => c.estado !== "pagada" && Number(c.pendiente) > 0);
  const vencidas = porPagar.filter((c) => c.estado === "vencida");
  const mes = hoyIso().slice(0, 7);
  const pagosMes = pagos.filter((p) => !p.anulado && fechaIso(p.fecha).startsWith(mes));

  async function confirmarEliminar() {
    if (!eliminar) return;
    setEliminando(true);
    try {
      await comprasApi.eliminarFactura(eliminar.registro606_id);
      mostrarToast("Factura de compra eliminada", { detalle: `${eliminar.proveedor_nombre} · ${eliminar.ncf} ya no está en el 606` });
      setEliminar(null);
      void cargar();
    } catch (err) {
      setEliminar(null);
      mostrarToast("No se pudo eliminar la factura", { detalle: mensajeCompras(err), error: true });
    } finally {
      setEliminando(false);
    }
  }

  function exportar() {
    descargarCsv(`Facturas_de_compra_${hoyIso()}.csv`, [
      ["Proveedor", "RNC/Cédula", "NCF", "Fecha", "Vence", "Tipo 606", "Total (RD$)", "Retenciones (RD$)", "Pagado (RD$)", "Pendiente (RD$)", "Estado"],
      ...filas.map((c) => [
        c.proveedor_nombre,
        c.proveedor_rnc,
        c.ncf,
        fechaLegible(c.fecha_comprobante),
        fechaLegible(c.vence),
        etiquetaBienesServicios(c.tipo_bienes_servicios),
        Number(c.total).toFixed(2),
        Number(c.retenciones).toFixed(2),
        Number(c.pagado).toFixed(2),
        Number(c.pendiente).toFixed(2),
        ESTADO_CXP[c.estado]?.label ?? c.estado,
      ]),
    ]);
  }

  return (
    <div className="content">
      <ComprasEncabezado
        titulo="Facturas de compra"
        sub="Cuentas por pagar a proveedores: e-CF recibidos, comprobantes E41 y facturas registradas a mano"
        acciones={
          <>
            <button type="button" className="btn btn-o" onClick={exportar} disabled={filas.length === 0}>
              Exportar
            </button>
            <Link className="btn btn-p" to="/compras/facturas/nueva">
              <IconMas /> Nueva factura de compra
            </Link>
          </>
        }
      />

      {error && <AvisoError titulo="No se pudieron cargar las facturas de compra." mensaje={error} onReintentar={() => void cargar()} />}

      <div className="grid-stats">
        <Estadistica
          k="Por pagar"
          v={cargando ? "—" : `RD$ ${formatoMonto(suma(porPagar, (c) => c.pendiente))}`}
          s={`${porPagar.length} ${porPagar.length === 1 ? "factura pendiente" : "facturas pendientes"}`}
        />
        <Estadistica
          k="Vencido"
          v={cargando ? "—" : `RD$ ${formatoMonto(suma(vencidas, (c) => c.pendiente))}`}
          s={`${vencidas.length} ${vencidas.length === 1 ? "factura vencida" : "facturas vencidas"}`}
        />
        <Estadistica
          k="Pagado este mes"
          v={cargando ? "—" : `RD$ ${formatoMonto(suma(pagosMes, (p) => p.monto))}`}
          s={`${mesActualLegible()} · ${pagosMes.length} ${pagosMes.length === 1 ? "pago" : "pagos"}`}
        />
      </div>

      <section className="card">
        <div className="tabs" role="tablist" aria-label="Filtrar facturas de compra">
          {PESTANAS.map((p) => (
            <button key={p.id} type="button" role="tab" aria-selected={pestana === p.id} className={pestana === p.id ? "tab on" : "tab"} onClick={() => setPestana(p.id)}>
              {p.label} <span className="chip chip-sm">{cuentas.filter(p.test).length}</span>
            </button>
          ))}
        </div>
        <div className="filters">
          <Buscador valor={q} onCambiar={setQ} placeholder="Buscar en Facturas de compra" />
        </div>

        {cargando && <Cargando />}
        {!cargando && !error && cuentas.length === 0 && (
          <Vacio titulo="No hay facturas de compra" texto="Los e-CF que reciba de sus proveedores y los comprobantes E41 que emita aparecerán aquí hasta que los pague.">
            <Link className="btn btn-p btn-sm" to="/compras/facturas/nueva">
              Nueva factura de compra
            </Link>
          </Vacio>
        )}
        {!cargando && cuentas.length > 0 && filas.length === 0 && (
          <SinResultados
            q={q}
            onLimpiar={() => {
              setQ("");
              setPestana("todas");
            }}
          />
        )}
        {!cargando && filas.length > 0 && (
          <div className="tw">
            <table className="tbl" style={{ minWidth: 1080 }}>
              <thead>
                <tr>
                  <th>Proveedor</th>
                  <th>NCF</th>
                  <th>Fecha</th>
                  <th>Vence</th>
                  <th className="num">Total (RD$)</th>
                  <th className="num">Retenciones</th>
                  <th className="num">Pendiente (RD$)</th>
                  <th>Estado</th>
                  <th style={{ textAlign: "right" }}>Acciones</th>
                </tr>
              </thead>
              <tbody>
                {filas.map((c) => {
                  const est = ESTADO_CXP[c.estado] ?? { label: c.estado, badge: "bd b-borr" };
                  const debe = c.estado !== "pagada" && Number(c.pendiente) > 0;
                  const ficha = `/compras/facturas/${c.registro606_id}`;
                  return (
                    <tr key={c.registro606_id} className="fila-link" onClick={() => navigate(ficha)}>
                      <td>
                        <div className="strong">{c.proveedor_nombre}</div>
                        <div className="hint mono">{formatoRnc(c.proveedor_rnc)}</div>
                      </td>
                      <td className="nowrap">
                        <Link to={ficha} className="link mono" onClick={(e) => e.stopPropagation()}>
                          {c.ncf}
                        </Link>
                        {c.tipo_bienes_servicios == null && <div className="hint">Sin clasificar en el 606</div>}
                      </td>
                      <td className="nowrap">{fechaLegible(c.fecha_comprobante)}</td>
                      <td className="nowrap">
                        {fechaLegible(c.vence)}
                        {c.estado === "vencida" && c.dias_vencida > 0 && (
                          <div className="err-t" style={{ fontSize: 12 }}>
                            Hace {c.dias_vencida} {c.dias_vencida === 1 ? "día" : "días"}
                          </div>
                        )}
                      </td>
                      <td className="num">{formatoMonto(c.total)}</td>
                      <td className="num">{formatoMonto(c.retenciones)}</td>
                      <td className="num" style={{ fontWeight: 600, color: debe ? "#111827" : undefined }}>
                        {formatoMonto(c.pendiente)}
                      </td>
                      <td>
                        <span className={est.badge}>{est.label}</span>
                      </td>
                      <td onClick={(e) => e.stopPropagation()}>
                        <div className="fila-acc cp-acc">
                          <Link to={ficha} className="btn btn-g btn-sm">
                            {c.origen === "compras" || c.tipo_bienes_servicios == null ? "Editar" : "Ver"}
                          </Link>
                          {debe && (
                            <Link to={`/compras/pagos?factura=${c.registro606_id}`} className="btn btn-g btn-sm">
                              Pagar
                            </Link>
                          )}
                          {sePuedeEliminar(c) && (
                            <button type="button" className="btn btn-g btn-sm" style={{ color: "#B91C1C" }} onClick={() => setEliminar(c)}>
                              Eliminar
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {eliminar && (
        <Modal
          titulo="¿Eliminar esta factura de compra?"
          onCerrar={() => setEliminar(null)}
          pie={
            <>
              <button type="button" className="btn btn-o" onClick={() => setEliminar(null)}>
                Cancelar
              </button>
              <button type="button" className="btn btn-d" onClick={confirmarEliminar} disabled={eliminando}>
                {eliminando ? "Eliminando…" : "Sí, eliminar"}
              </button>
            </>
          }
        >
          <span>
            La factura <b className="mono" style={{ color: "#111827" }}>{eliminar.ncf}</b> de {eliminar.proveedor_nombre} por{" "}
            <b style={{ color: "#111827" }}>RD$ {formatoMonto(eliminar.total)}</b> se quitará de Cuentas por pagar y del Formato 606.
          </span>
        </Modal>
      )}

      {toast}
    </div>
  );
}
