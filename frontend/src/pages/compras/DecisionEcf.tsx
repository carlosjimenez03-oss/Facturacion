// Aprobación / rechazo comercial (ACECF) de un e-CF recibido y su registro como factura de compra.
// Lo usan el panel lateral de la Bandeja (Bandeja.dc.html) y la ficha del e-CF recibido.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { IconAlerta } from "../../layout/icons";
import { comprasApi } from "../../lib/comprasApi";
import type { EcfRecibido, FacturaCompra } from "../../lib/comprasApi";
import { ErpApiError } from "../../lib/erpApi";
import { inventarioApi } from "../../lib/inventarioApi";
import { formatoMonto } from "../../lib/marca";
import type { Compra, Proveedor } from "../../lib/types";
import { Modal } from "../ingresos/IngresosShell";
import { fechaLegible } from "../ingresos/ingresosUtil";
import { IconoOk } from "./ComprasShell";
import { etiquetaBienesServicios, mensajeCompras, OPCIONES_BIENES_SERVICIOS } from "./comprasUtil";

type Avisar = (texto: string, opciones?: { detalle?: string; error?: boolean }) => void;

const MAX_MOTIVO = 250;

export function DecisionEcf({
  ecf,
  onCambio,
  avisar,
  enlaceDetalle,
}: {
  ecf: EcfRecibido;
  /** El e-CF actualizado tras aprobar/rechazar/vincular; `null` para volver a pedirlo al servidor. */
  onCambio: (ecf: EcfRecibido | null) => void;
  avisar: Avisar;
  /** Muestra "Ver detalle y XML" (en el panel de la bandeja). */
  enlaceDetalle?: boolean;
}) {
  const [rechazando, setRechazando] = useState(false);
  const [motivo, setMotivo] = useState("");
  const [enviando, setEnviando] = useState<"" | "aprobar" | "rechazar">("");
  const [factura, setFactura] = useState<FacturaCompra | null>(null);
  const [registrar, setRegistrar] = useState(false);

  useEffect(() => {
    setRechazando(false);
    setMotivo("");
  }, [ecf.id]);

  // La factura de compra (renglón del 606) que se creó al recibir el e-CF: muestra si ya está clasificada.
  useEffect(() => {
    let vigente = true;
    setFactura(null);
    if (ecf.compra606_id && ecf.estado_aprobacion === "aprobado") {
      comprasApi
        .obtenerFactura(ecf.compra606_id)
        .then((f) => vigente && setFactura(f))
        .catch(() => vigente && setFactura(null));
    }
    return () => {
      vigente = false;
    };
  }, [ecf.compra606_id, ecf.estado_aprobacion]);

  const quien = `${ecf.razon_social_emisor ?? ecf.rnc_emisor} · ${ecf.encf}`;

  async function decidir(aprobar: boolean) {
    setEnviando(aprobar ? "aprobar" : "rechazar");
    try {
      const actualizado = aprobar ? await comprasApi.aprobar(ecf.id) : await comprasApi.rechazar(ecf.id, motivo.trim());
      avisar(aprobar ? "Aprobación comercial enviada" : "Rechazo comercial enviado", { detalle: quien, error: !aprobar });
      setRechazando(false);
      setMotivo("");
      onCambio(actualizado);
    } catch (err) {
      const fallaDgii = err instanceof ErpApiError && err.status === 502;
      avisar(fallaDgii ? "La DGII no recibió la respuesta comercial" : aprobar ? "No se pudo aprobar el e-CF" : "No se pudo rechazar el e-CF", {
        detalle: fallaDgii ? `${mensajeCompras(err)} El comprobante sigue pendiente; puede intentarlo de nuevo.` : mensajeCompras(err),
        error: true,
      });
      onCambio(null);
    } finally {
      setEnviando("");
    }
  }

  const pendiente = ecf.estado_aprobacion === "pendiente";
  const clasificada = factura != null && factura.tipo_bienes_servicios != null;

  return (
    <>
      {pendiente && ecf.error_aprobacion && (
        <div className="al al-w" role="status">
          <IconAlerta />
          <span>
            <b>El último envío a la DGII falló.</b> {ecf.error_aprobacion}
          </span>
        </div>
      )}

      {pendiente && rechazando && (
        <>
          <div className="fld">
            <label className="lbl" htmlFor="motivo">
              Motivo del rechazo <span className="req">*</span>
            </label>
            <textarea
              id="motivo"
              className="inp"
              rows={3}
              maxLength={MAX_MOTIVO}
              placeholder="Ej.: Cantidad facturada no coincide con la orden de compra OC-2026-0418"
              value={motivo}
              onChange={(e) => setMotivo(e.target.value)}
              autoFocus
            />
            <span className="hint">
              El proveedor recibirá este motivo. Deberá emitir una nota de crédito si corresponde. {motivo.length}/{MAX_MOTIVO}
            </span>
          </div>
          <div className="row" style={{ justifyContent: "flex-end" }}>
            <button type="button" className="btn btn-o" onClick={() => setRechazando(false)} disabled={enviando !== ""}>
              Cancelar
            </button>
            <button type="button" className="btn btn-d" onClick={() => decidir(false)} disabled={!motivo.trim() || enviando !== ""}>
              {enviando === "rechazar" ? "Enviando…" : "Enviar rechazo"}
            </button>
          </div>
        </>
      )}

      {pendiente && !rechazando && (
        <div className="row">
          <button type="button" className="btn btn-p" style={{ flex: 1 }} onClick={() => decidir(true)} disabled={enviando !== ""}>
            <IconoOk />
            {enviando === "aprobar" ? "Enviando a la DGII…" : "Aprobar comercialmente"}
          </button>
          <button type="button" className="btn btn-do" style={{ flex: 1 }} onClick={() => setRechazando(true)} disabled={enviando !== ""}>
            Rechazar
          </button>
        </div>
      )}

      {ecf.estado_aprobacion === "aprobado" && (
        <>
          <div className="al al-s">
            <IconoOk />
            <span>
              Aprobada comercialmente{ecf.aprobado_en ? ` el ${fechaLegible(ecf.aprobado_en)}` : ""}. El proveedor fue notificado y el comprobante está disponible
              para el Formato 606.
            </span>
          </div>
          {clasificada ? (
            <div className="al al-i">
              <IconoOk />
              <span>
                <b>Registrada como factura de compra</b> · {etiquetaBienesServicios(factura.tipo_bienes_servicios)}
                {Number(factura.pendiente) > 0 ? ` · pendiente RD$ ${formatoMonto(factura.pendiente)}` : " · pagada"}
              </span>
            </div>
          ) : (
            <button type="button" className="btn btn-o" onClick={() => setRegistrar(true)}>
              Registrar como factura de compra
            </button>
          )}
          {clasificada && (
            <div className="row">
              <Link className="btn btn-o" style={{ flex: 1 }} to={`/compras/facturas/${factura.registro606_id}`}>
                Ver factura de compra
              </Link>
              {Number(factura.pendiente) > 0 && (
                <Link className="btn btn-p" style={{ flex: 1 }} to={`/compras/pagos?factura=${factura.registro606_id}`}>
                  Registrar pago
                </Link>
              )}
            </div>
          )}
        </>
      )}

      {ecf.estado_aprobacion === "rechazado" && (
        <div className="al al-d">
          <IconoOk />
          <span>Rechazada comercialmente. Motivo enviado: {ecf.motivo_rechazo || "—"}</span>
        </div>
      )}

      {(ecf.compra606_id || enlaceDetalle) && (
        <div className="row" style={{ justifyContent: "space-between" }}>
          {enlaceDetalle && (
            <Link className="link" to={`/bandeja/${ecf.id}`}>
              Ver detalle y XML
            </Link>
          )}
          {ecf.compra606_id && (
            <Link className="link" to={`/formato-606/registros/${ecf.compra606_id}/editar`}>
              Ver renglón en el Formato 606
            </Link>
          )}
        </div>
      )}

      {registrar && (
        <ModalRegistrarCompra
          ecf={ecf}
          factura={factura}
          onCerrar={() => setRegistrar(false)}
          onListo={(e, f) => {
            setRegistrar(false);
            setFactura(f);
            onCambio(e);
            avisar("Factura de compra registrada", { detalle: `${quien} · ${etiquetaBienesServicios(f.tipo_bienes_servicios)}` });
          }}
        />
      )}
    </>
  );
}

/** "Registrar como factura de compra": asegura su renglón del 606 (vincular) y lo clasifica (actualizarFactura). */
function ModalRegistrarCompra({
  ecf,
  factura,
  onCerrar,
  onListo,
}: {
  ecf: EcfRecibido;
  factura: FacturaCompra | null;
  onCerrar: () => void;
  onListo: (ecf: EcfRecibido, factura: FacturaCompra) => void;
}) {
  const soloBienes = ecf.items.length > 0 && ecf.items.every((i) => i.bien_o_servicio === "bien");
  const [tipo, setTipo] = useState(String(factura?.tipo_bienes_servicios ?? (soloBienes ? 9 : 2)));
  const [concepto, setConcepto] = useState(factura?.concepto ?? ecf.items[0]?.descripcion ?? "");
  const [vence, setVence] = useState(factura?.vence ?? ecf.fecha_limite_pago ?? "");
  const [compraId, setCompraId] = useState(ecf.compra_inventario_id ?? "");
  const [compras, setCompras] = useState<Compra[]>([]);
  const [proveedores, setProveedores] = useState<Proveedor[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [guardando, setGuardando] = useState(false);

  useEffect(() => {
    void Promise.allSettled([inventarioApi.listarCompras(), inventarioApi.listarProveedores()]).then(([c, p]) => {
      setCompras(c.status === "fulfilled" ? c.value : []);
      setProveedores(p.status === "fulfilled" ? p.value : []);
    });
  }, []);

  const nombreProveedor = (id: string | null) => proveedores.find((p) => p.id === id)?.nombre;

  async function guardar() {
    if (!tipo) return setError("Elija el tipo de bien o servicio del 606.");
    setGuardando(true);
    setError(null);
    try {
      const cambioCompra = (compraId || null) !== (ecf.compra_inventario_id ?? null);
      const vinculado = await comprasApi.vincular(ecf.id, cambioCompra ? { compra_inventario_id: compraId || null } : {});
      if (!vinculado.compra606_id) throw new Error("El e-CF no tiene renglón en el 606.");
      const f = await comprasApi.actualizarFactura(vinculado.compra606_id, {
        tipo_bienes_servicios: Number(tipo),
        concepto: concepto.trim() || null,
        ...(vence && vence !== factura?.vence ? { vence } : {}),
      });
      onListo(vinculado, f);
    } catch (err) {
      setError(mensajeCompras(err));
      setGuardando(false);
    }
  }

  return (
    <Modal
      titulo="Registrar como factura de compra"
      onCerrar={onCerrar}
      ancho={560}
      pie={
        <>
          <button type="button" className="btn btn-o" onClick={onCerrar}>
            Cancelar
          </button>
          <button type="button" className="btn btn-p" onClick={guardar} disabled={guardando}>
            {guardando ? "Registrando…" : "Registrar factura de compra"}
          </button>
        </>
      }
    >
      <span>
        {ecf.razon_social_emisor ?? ecf.rnc_emisor} · <span className="mono">{ecf.encf}</span> · RD$ {formatoMonto(ecf.monto_total)}. Quedará en Cuentas por pagar y en
        el Formato 606 con esta clasificación.
      </span>
      <div className="fld">
        <label className="lbl" htmlFor="rc-tipo">
          Tipo de bien o servicio (606) <span className="req">*</span>
        </label>
        <select id="rc-tipo" className="inp" value={tipo} onChange={(e) => setTipo(e.target.value)}>
          {OPCIONES_BIENES_SERVICIOS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </div>
      <div className="fld">
        <label className="lbl" htmlFor="rc-con">Concepto</label>
        <input id="rc-con" className="inp" value={concepto} onChange={(e) => setConcepto(e.target.value)} />
      </div>
      <div className="fld">
        <label className="lbl" htmlFor="rc-vence">Vence</label>
        <input id="rc-vence" type="date" className="inp" value={vence} onChange={(e) => setVence(e.target.value)} />
        <span className="hint">Por defecto, la fecha límite de pago del e-CF o los días de crédito del proveedor.</span>
      </div>
      <div className="fld">
        <label className="lbl" htmlFor="rc-compra">Entrada de mercancía (inventario)</label>
        <select id="rc-compra" className="inp" value={compraId} onChange={(e) => setCompraId(e.target.value)}>
          <option value="">Sin vincular</option>
          {compraId && !compras.some((c) => c.id === compraId) && <option value={compraId}>Compra vinculada</option>}
          {compras.map((c) => (
            <option key={c.id} value={c.id}>
              {[c.numero_documento || "Sin documento", nombreProveedor(c.proveedor_id), fechaLegible(c.creado_en), c.estado === "borrador" ? "borrador" : null]
                .filter(Boolean)
                .join(" · ")}
            </option>
          ))}
        </select>
        <span className="hint">Opcional: la recepción de la mercancía de esta factura en Inventario.</span>
      </div>
      {error && (
        <div className="al al-d" role="alert">
          <IconAlerta />
          <span>
            <b>{error}</b>
          </span>
        </div>
      )}
    </Modal>
  );
}
