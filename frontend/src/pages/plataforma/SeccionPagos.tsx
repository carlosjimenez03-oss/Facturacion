// Configuración → Pagos en línea (fase 7): pago con tarjeta desde el portal de
// clientes por la Página de Pagos de Azul (o el proveedor simulado fuera de
// producción). El AuthKey se escribe aquí pero nunca vuelve del servidor:
// solo se ve «configurada». Guardar lo puede solo un administrador.
import { useEffect, useState } from "react";
import { bancosApi, type CuentaBancaria } from "../../lib/bancosApi";
import { pagosApi, type ConfigPagos, type ConfigPagosPayload } from "../../lib/pagosApi";
import { Interruptor, Tarjeta, type RefSeccion } from "./ConfiguracionSecciones";
import { mensajeError, type Avisar } from "./configuracionUtil";

type Form = Omit<ConfigPagosPayload, "borrar_auth_key"> & { auth_key: string };

function formDe(c: ConfigPagos): Form {
  return {
    activo: c.activo,
    proveedor: c.proveedor,
    ambiente: c.ambiente,
    merchant_id: c.merchant_id,
    merchant_name: c.merchant_name,
    merchant_type: c.merchant_type,
    permitir_parcial: c.permitir_parcial,
    cuenta_bancaria_id: c.cuenta_bancaria_id,
    auth_key: "",
  };
}

export function SeccionPagos({ refSeccion, avisar }: { refSeccion: RefSeccion; avisar: Avisar }) {
  const [cfg, setCfg] = useState<ConfigPagos | null>(null);
  const [form, setForm] = useState<Form | null>(null);
  const [cuentas, setCuentas] = useState<CuentaBancaria[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [guardando, setGuardando] = useState(false);

  function aplicar(c: ConfigPagos) {
    setCfg(c);
    setForm(formDe(c));
  }

  useEffect(() => {
    pagosApi.config().then(aplicar, (err) => setError(mensajeError(err)));
    bancosApi.listarCuentas({ activa: true }).then((cs) => setCuentas(cs.filter((c) => c.moneda === "DOP")), () => undefined);
  }, []);

  async function guardar(cambios: Partial<Form> = {}, borrarLlave = false) {
    if (!form) return;
    const datos = { ...form, ...cambios };
    setGuardando(true);
    try {
      const c = await pagosApi.guardarConfig({ ...datos, auth_key: datos.auth_key || null, borrar_auth_key: borrarLlave });
      aplicar(c);
      avisar(c.activo ? "Pagos en línea activos" : "Pagos en línea guardados");
    } catch (err) {
      avisar("No se pudo guardar", { detalle: mensajeError(err), error: true });
    } finally {
      setGuardando(false);
    }
  }

  const set = <K extends keyof Form>(campo: K, valor: Form[K]) => setForm((f) => (f ? { ...f, [campo]: valor } : f));
  const editable = Boolean(cfg?.puede_editar) && !guardando;
  const azul = form?.proveedor === "azul";

  return (
    <Tarjeta
      id="pagos"
      refSeccion={refSeccion}
      titulo="Pagos en línea"
      extra={
        cfg && (
          <span className={cfg.activo ? "bd b-acep" : "bd b-borr"} data-pagos-estado>
            {cfg.activo ? "Activos" : "Inactivos"}
          </span>
        )
      }
    >
      {error && (
        <div className="al al-d" style={{ margin: 16 }}>
          <span>{error}</span>
        </div>
      )}
      {cfg && form && (
        <div className="card-b" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <span className="hint" style={{ lineHeight: 1.5 }}>
            Sus clientes pagan con tarjeta las facturas pendientes desde el portal de clientes. El pago se hace en la página segura del banco: Kontara
            nunca recibe los datos de la tarjeta. Cada pago aprobado se registra solo como un cobro (método tarjeta) con su asiento contable.
          </span>
          {!cfg.puede_editar && (
            <div className="al al-w">
              <span>Solo un administrador puede cambiar esta configuración.</span>
            </div>
          )}
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
            <span style={{ fontSize: 13.5 }}>
              <b>Aceptar pagos con tarjeta en el portal</b>
              {!cfg.listo && <span className="hint"> · faltan: {cfg.faltantes.join(", ")}</span>}
            </span>
            <Interruptor on={form.activo} onChange={(v) => void guardar({ activo: v })} label="Activar pagos en línea" disabled={!editable} />
          </div>
          <div className="cfg-grid">
            <div className="fld">
              <label className="lbl" htmlFor="pg-prov">Proveedor</label>
              <select id="pg-prov" className="inp" value={form.proveedor} disabled={!editable} onChange={(e) => set("proveedor", e.target.value as Form["proveedor"])}>
                {cfg.proveedores.map((p) => (
                  <option key={p.clave} value={p.clave}>
                    {p.nombre}
                  </option>
                ))}
              </select>
            </div>
            {azul && (
              <div className="fld">
                <label className="lbl" htmlFor="pg-amb">Ambiente de Azul</label>
                <select id="pg-amb" className="inp" value={form.ambiente} disabled={!editable} onChange={(e) => set("ambiente", e.target.value as Form["ambiente"])}>
                  <option value="pruebas">Pruebas (pruebas.azul.com.do)</option>
                  <option value="produccion">Producción (pagos.azul.com.do)</option>
                </select>
              </div>
            )}
            <div className="fld">
              <label className="lbl" htmlFor="pg-cta">Cuenta donde Azul deposita</label>
              <select id="pg-cta" className="inp" value={form.cuenta_bancaria_id ?? ""} disabled={!editable} onChange={(e) => set("cuenta_bancaria_id", e.target.value || null)}>
                <option value="">Sin cuenta (asiento a la cuenta por defecto «Banco»)</option>
                {cuentas.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.nombre}
                    {c.numero_enmascarado ? ` ${c.numero_enmascarado}` : ""}
                  </option>
                ))}
              </select>
            </div>
          </div>
          {azul && (
            <div className="cfg-grid">
              <div className="fld">
                <label className="lbl" htmlFor="pg-mid">MerchantId</label>
                <input id="pg-mid" className="inp mono" inputMode="numeric" value={form.merchant_id} disabled={!editable} onChange={(e) => set("merchant_id", e.target.value.replace(/\D/g, ""))} />
              </div>
              <div className="fld">
                <label className="lbl" htmlFor="pg-mname">MerchantName</label>
                <input id="pg-mname" className="inp" value={form.merchant_name} disabled={!editable} onChange={(e) => set("merchant_name", e.target.value)} placeholder="Nombre que ve el cliente en Azul" />
              </div>
              <div className="fld">
                <label className="lbl" htmlFor="pg-mtype">MerchantType</label>
                <input id="pg-mtype" className="inp" value={form.merchant_type} disabled={!editable} onChange={(e) => set("merchant_type", e.target.value)} placeholder="ECommerce" />
              </div>
              <div className="fld">
                <label className="lbl" htmlFor="pg-key">AuthKey</label>
                {cfg.auth_key_origen === "variable" ? (
                  <span className="hint" style={{ paddingTop: 8 }}>
                    Configurada por la variable <code>AZUL_AUTH_KEY</code>
                  </span>
                ) : (
                  <input
                    id="pg-key"
                    type="password"
                    className="inp"
                    autoComplete="new-password"
                    value={form.auth_key}
                    disabled={!editable || !cfg.cifrado_disponible}
                    onChange={(e) => set("auth_key", e.target.value)}
                    placeholder={cfg.auth_key_configurada ? "Configurada (escriba para cambiarla)" : ""}
                  />
                )}
                {cfg.auth_key_origen === "guardada" && editable && (
                  <button type="button" className="btn btn-g btn-sm" style={{ alignSelf: "flex-start" }} onClick={() => void guardar({ activo: false }, true)}>
                    Borrar AuthKey
                  </button>
                )}
              </div>
            </div>
          )}
          {azul && !cfg.cifrado_disponible && cfg.auth_key_origen !== "variable" && (
            <div className="al al-w">
              <span>
                Para guardar el AuthKey configure <code>CERTIFICADOS_CLAVE</code> (32 caracteres o más) en Dokploy, o páselo en la variable{" "}
                <code>AZUL_AUTH_KEY</code> de erp-api.
              </span>
            </div>
          )}
          {form.proveedor === "simulado" && (
            <div className="al al-w">
              <span>Proveedor simulado: una página de pago de prueba que no cobra dinero. No está disponible en producción.</span>
            </div>
          )}
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
            <span style={{ fontSize: 13.5 }}>Permitir pagos parciales (el cliente elige el monto; por defecto, todo el saldo)</span>
            <Interruptor on={form.permitir_parcial} onChange={(v) => set("permitir_parcial", v)} label="Permitir pagos parciales" disabled={!editable} />
          </div>
          {cfg.url_retorno && azul && (
            <span className="hint">
              URL de retorno que verá Azul: <code>{cfg.url_retorno}</code>
            </span>
          )}
          <div className="cfg-acciones">
            <button type="button" className="btn btn-p btn-sm" disabled={!editable} onClick={() => void guardar()}>
              {guardando ? "Guardando…" : "Guardar pagos en línea"}
            </button>
          </div>
        </div>
      )}
    </Tarjeta>
  );
}
