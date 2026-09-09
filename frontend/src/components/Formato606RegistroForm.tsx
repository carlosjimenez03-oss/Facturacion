import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  FORMAS_PAGO,
  Formato606ApiError,
  TIPOS_BIENES_SERVICIOS,
  TIPOS_RETENCION_ISR,
  formato606Api,
} from "../lib/formato606Api";

function aaaammddADateInput(valor: string | null | undefined): string {
  if (!valor || !/^\d{8}$/.test(valor)) return "";
  return `${valor.slice(0, 4)}-${valor.slice(4, 6)}-${valor.slice(6, 8)}`;
}

function dateInputAAaaammdd(valor: string): string | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(valor)) return null;
  return valor.replaceAll("-", "");
}

export function Formato606RegistroForm() {
  const navigate = useNavigate();
  const { id, periodo } = useParams<{ id?: string; periodo?: string }>();
  const modoManual = !id;

  // Campos base (solo se editan al crear un registro manual).
  const [rncCedula, setRncCedula] = useState("");
  const [ncf, setNcf] = useState("");
  const [ncfModificado, setNcfModificado] = useState("");
  const [fechaComprobante, setFechaComprobante] = useState("");
  const [montoServicios, setMontoServicios] = useState("0");
  const [montoBienes, setMontoBienes] = useState("0");
  const [itbisFacturado, setItbisFacturado] = useState("0");

  // Campos de clasificación (siempre editables).
  const [tipoBienesServicios, setTipoBienesServicios] = useState("");
  const [fechaPago, setFechaPago] = useState("");
  const [itbisRetenido, setItbisRetenido] = useState("0");
  const [itbisProporcionalidad, setItbisProporcionalidad] = useState("0");
  const [itbisCosto, setItbisCosto] = useState("0");
  const [itbisPercibido, setItbisPercibido] = useState("0");
  const [tipoRetencionIsr, setTipoRetencionIsr] = useState("");
  const [montoRetencionRenta, setMontoRetencionRenta] = useState("0");
  const [isrPercibido, setIsrPercibido] = useState("0");
  const [impuestoSelectivoConsumo, setImpuestoSelectivoConsumo] = useState("0");
  const [otrosImpuestos, setOtrosImpuestos] = useState("0");
  const [montoPropinaLegal, setMontoPropinaLegal] = useState("0");
  const [formaPago, setFormaPago] = useState("");

  const [periodoRegistro, setPeriodoRegistro] = useState(periodo ?? "");
  const [cargando, setCargando] = useState(!modoManual);
  const [guardando, setGuardando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    formato606Api
      .obtenerRegistro(id)
      .then((r) => {
        setPeriodoRegistro(r.periodo);
        setRncCedula(r.rnc_cedula);
        setNcf(r.ncf);
        setNcfModificado(r.ncf_modificado ?? "");
        setFechaComprobante(r.fecha_comprobante);
        setMontoServicios(r.monto_servicios);
        setMontoBienes(r.monto_bienes);
        setItbisFacturado(r.itbis_facturado);
        setTipoBienesServicios(r.tipo_bienes_servicios != null ? String(r.tipo_bienes_servicios) : "");
        setFechaPago(aaaammddADateInput(r.fecha_pago));
        setItbisRetenido(r.itbis_retenido);
        setItbisProporcionalidad(r.itbis_proporcionalidad);
        setItbisCosto(r.itbis_costo);
        setItbisPercibido(r.itbis_percibido);
        setTipoRetencionIsr(r.tipo_retencion_isr != null ? String(r.tipo_retencion_isr) : "");
        setMontoRetencionRenta(r.monto_retencion_renta);
        setIsrPercibido(r.isr_percibido);
        setImpuestoSelectivoConsumo(r.impuesto_selectivo_consumo);
        setOtrosImpuestos(r.otros_impuestos);
        setMontoPropinaLegal(r.monto_propina_legal);
        setFormaPago(r.forma_pago != null ? String(r.forma_pago) : "");
      })
      .catch((err) => setError(err instanceof Formato606ApiError ? err.message : String(err)))
      .finally(() => setCargando(false));
  }, [id]);

  async function guardar(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setGuardando(true);
    try {
      const clasificacion = {
        tipo_bienes_servicios: tipoBienesServicios ? Number(tipoBienesServicios) : null,
        fecha_pago: dateInputAAaaammdd(fechaPago),
        itbis_retenido: itbisRetenido,
        itbis_proporcionalidad: itbisProporcionalidad,
        itbis_costo: itbisCosto,
        itbis_percibido: itbisPercibido,
        tipo_retencion_isr: tipoRetencionIsr ? Number(tipoRetencionIsr) : null,
        monto_retencion_renta: montoRetencionRenta,
        isr_percibido: isrPercibido,
        impuesto_selectivo_consumo: impuestoSelectivoConsumo,
        otros_impuestos: otrosImpuestos,
        monto_propina_legal: montoPropinaLegal,
        forma_pago: formaPago ? Number(formaPago) : null,
      };

      let periodoDestino = periodoRegistro;
      if (modoManual) {
        const creado = await formato606Api.crearRegistroManual({
          rnc_cedula: rncCedula,
          ncf,
          ncf_modificado: ncfModificado || null,
          fecha_comprobante: fechaComprobante,
          monto_servicios: montoServicios,
          monto_bienes: montoBienes,
          itbis_facturado: itbisFacturado,
          ...clasificacion,
        });
        periodoDestino = creado.periodo;
      } else if (id) {
        const actualizado = await formato606Api.clasificarRegistro(id, clasificacion);
        periodoDestino = actualizado.periodo;
      }
      navigate(`/formato-606/periodos/${periodoDestino}`);
    } catch (err) {
      setError(err instanceof Formato606ApiError ? err.message : String(err));
    } finally {
      setGuardando(false);
    }
  }

  if (cargando) return <p>Cargando…</p>;

  return (
    <form className="factura-form" onSubmit={guardar}>
      {modoManual && (
        <section>
          <h2>Comprobante</h2>
          <label>
            RNC/Cédula del proveedor
            <input value={rncCedula} onChange={(e) => setRncCedula(e.target.value)} required />
          </label>
          <label>
            NCF
            <input value={ncf} onChange={(e) => setNcf(e.target.value)} required />
          </label>
          <label>
            NCF o documento modificado (opcional)
            <input value={ncfModificado} onChange={(e) => setNcfModificado(e.target.value)} />
          </label>
          <label>
            Fecha del comprobante
            <input
              type="date"
              value={aaaammddADateInput(fechaComprobante)}
              onChange={(e) => setFechaComprobante(dateInputAAaaammdd(e.target.value) ?? "")}
              required
            />
          </label>
          <label>
            Monto facturado en servicios
            <input
              type="number"
              min="0"
              step="0.01"
              value={montoServicios}
              onChange={(e) => setMontoServicios(e.target.value)}
            />
          </label>
          <label>
            Monto facturado en bienes
            <input
              type="number"
              min="0"
              step="0.01"
              value={montoBienes}
              onChange={(e) => setMontoBienes(e.target.value)}
            />
          </label>
          <label>
            ITBIS facturado
            <input
              type="number"
              min="0"
              step="0.01"
              value={itbisFacturado}
              onChange={(e) => setItbisFacturado(e.target.value)}
            />
          </label>
        </section>
      )}

      {!modoManual && (
        <section>
          <h2>Comprobante</h2>
          <p className="nota-acciones">
            NCF {ncf} · RNC/Cédula {rncCedula} · periodo {periodoRegistro}. Estos campos vienen del
            e-CF de origen y no se editan aquí.
          </p>
        </section>
      )}

      <section>
        <h2>Clasificación (campos obligatorios)</h2>
        <label>
          Tipo de Bienes y Servicios Comprados
          <select value={tipoBienesServicios} onChange={(e) => setTipoBienesServicios(e.target.value)} required>
            <option value="">Selecciona…</option>
            {Object.entries(TIPOS_BIENES_SERVICIOS).map(([valor, etiqueta]) => (
              <option key={valor} value={valor}>
                {etiqueta}
              </option>
            ))}
          </select>
        </label>
        <label>
          Forma de Pago
          <select value={formaPago} onChange={(e) => setFormaPago(e.target.value)} required>
            <option value="">Selecciona…</option>
            {Object.entries(FORMAS_PAGO).map(([valor, etiqueta]) => (
              <option key={valor} value={valor}>
                {etiqueta}
              </option>
            ))}
          </select>
        </label>
      </section>

      <section>
        <h2>Retenciones y otros impuestos (opcionales)</h2>
        <label>
          Fecha de pago (requerida si hay ITBIS retenido o retención ISR)
          <input type="date" value={fechaPago} onChange={(e) => setFechaPago(e.target.value)} />
        </label>
        <label>
          ITBIS Retenido
          <input
            type="number"
            min="0"
            step="0.01"
            value={itbisRetenido}
            onChange={(e) => setItbisRetenido(e.target.value)}
          />
        </label>
        <label>
          ITBIS sujeto a Proporcionalidad (Art. 349)
          <input
            type="number"
            min="0"
            step="0.01"
            value={itbisProporcionalidad}
            onChange={(e) => setItbisProporcionalidad(e.target.value)}
          />
        </label>
        <label>
          ITBIS llevado al Costo
          <input
            type="number"
            min="0"
            step="0.01"
            value={itbisCosto}
            onChange={(e) => setItbisCosto(e.target.value)}
          />
        </label>
        <label>
          ITBIS percibido en compras
          <input
            type="number"
            min="0"
            step="0.01"
            value={itbisPercibido}
            onChange={(e) => setItbisPercibido(e.target.value)}
          />
        </label>
        <label>
          Tipo de Retención en ISR
          <select value={tipoRetencionIsr} onChange={(e) => setTipoRetencionIsr(e.target.value)}>
            <option value="">Ninguna</option>
            {Object.entries(TIPOS_RETENCION_ISR).map(([valor, etiqueta]) => (
              <option key={valor} value={valor}>
                {etiqueta}
              </option>
            ))}
          </select>
        </label>
        <label>
          Monto Retención Renta
          <input
            type="number"
            min="0"
            step="0.01"
            value={montoRetencionRenta}
            onChange={(e) => setMontoRetencionRenta(e.target.value)}
          />
        </label>
        <label>
          ISR Percibido en compras
          <input
            type="number"
            min="0"
            step="0.01"
            value={isrPercibido}
            onChange={(e) => setIsrPercibido(e.target.value)}
          />
        </label>
        <label>
          Impuesto Selectivo al Consumo
          <input
            type="number"
            min="0"
            step="0.01"
            value={impuestoSelectivoConsumo}
            onChange={(e) => setImpuestoSelectivoConsumo(e.target.value)}
          />
        </label>
        <label>
          Otros Impuestos/Tasas
          <input
            type="number"
            min="0"
            step="0.01"
            value={otrosImpuestos}
            onChange={(e) => setOtrosImpuestos(e.target.value)}
          />
        </label>
        <label>
          Monto Propina Legal
          <input
            type="number"
            min="0"
            step="0.01"
            value={montoPropinaLegal}
            onChange={(e) => setMontoPropinaLegal(e.target.value)}
          />
        </label>
      </section>

      {error && <p className="error">{error}</p>}

      <div className="acciones">
        <button type="submit" className="btn-primario" disabled={guardando}>
          {guardando ? "Guardando…" : "Guardar"}
        </button>
      </div>
    </form>
  );
}
