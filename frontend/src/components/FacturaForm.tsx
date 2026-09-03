import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "../lib/api";
import { CODIGOS_MODIFICACION, TIPOS_ECF, TIPOS_INGRESOS, TIPOS_PAGO } from "../lib/catalogos";
import { calcularTotales, decimal2 } from "../lib/format";
import { configParaTipo } from "../lib/tipoConfig";
import { construirDatos, estadoInicial } from "../lib/buildDatos";
import { ItemsEditor } from "./ItemsEditor";

export function FacturaForm() {
  const navigate = useNavigate();
  const [tipoEcf, setTipoEcf] = useState("31");
  const [form, setForm] = useState(estadoInicial("31"));
  const [guardando, setGuardando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const config = useMemo(() => configParaTipo(tipoEcf), [tipoEcf]);
  const totales = useMemo(() => calcularTotales(form.items), [form.items]);

  function cambiarTipo(nuevoTipo: string) {
    setTipoEcf(nuevoTipo);
    setForm((prev) => ({ ...prev, tipoEcf: nuevoTipo }));
  }

  function set<K extends keyof typeof form>(campo: K, valor: (typeof form)[K]) {
    setForm((prev) => ({ ...prev, [campo]: valor }));
  }

  async function guardar(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setGuardando(true);
    try {
      const datos = construirDatos(form, config);
      const creada = await api.crear({ tipo_ecf: Number(tipoEcf), encf: form.encf || null, datos });
      navigate(`/facturas/${creada.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setGuardando(false);
    }
  }

  return (
    <form className="factura-form" onSubmit={guardar}>
      <section>
        <h2>Tipo de comprobante</h2>
        <div className="tipo-picker">
          {TIPOS_ECF.map((t) => (
            <button
              key={t.value}
              type="button"
              className={`tipo-pill${t.value === tipoEcf ? " active" : ""}`}
              onClick={() => cambiarTipo(t.value)}
            >
              {t.label}
            </button>
          ))}
        </div>
        <label>
          e-NCF
          <input
            value={form.encf}
            onChange={(e) => set("encf", e.target.value)}
            placeholder="Ej. E310000000001"
            maxLength={13}
            required
          />
        </label>
        {config.requiereFechaVencimientoSecuencia && (
          <label>
            Fecha vencimiento de secuencia
            <input
              type="date"
              value={form.fechaVencimientoSecuencia}
              onChange={(e) => set("fechaVencimientoSecuencia", e.target.value)}
              required
            />
          </label>
        )}
        {config.requiereTipoIngresos && (
          <label>
            Tipo de ingresos
            <select value={form.tipoIngresos} onChange={(e) => set("tipoIngresos", e.target.value)}>
              {TIPOS_INGRESOS.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>
        )}
        <label>
          Tipo de pago
          <select value={form.tipoPago} onChange={(e) => set("tipoPago", e.target.value)}>
            {TIPOS_PAGO.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
        </label>
      </section>

      <section>
        <h2>Emisor</h2>
        <label>
          RNC emisor
          <input
            value={form.rncEmisor}
            onChange={(e) => set("rncEmisor", e.target.value)}
            placeholder="9 u 11 dígitos"
            required
          />
        </label>
        <label>
          Razón social
          <input
            value={form.razonSocialEmisor}
            onChange={(e) => set("razonSocialEmisor", e.target.value)}
            required
          />
        </label>
        <label>
          Dirección
          <input
            value={form.direccionEmisor}
            onChange={(e) => set("direccionEmisor", e.target.value)}
            required
          />
        </label>
        <label>
          Fecha de emisión
          <input
            type="date"
            value={form.fechaEmision}
            onChange={(e) => set("fechaEmision", e.target.value)}
            required
          />
        </label>
        <label>
          Correo (opcional)
          <input
            type="email"
            value={form.correoEmisor}
            onChange={(e) => set("correoEmisor", e.target.value)}
          />
        </label>
      </section>

      {config.compradorVisible && (
        <section>
          <h2>Comprador{config.compradorRncRequerido ? "" : " (opcional)"}</h2>
          <label>
            RNC comprador{config.compradorRncRequerido ? "" : " / cédula"}
            <input
              value={form.compradorRnc}
              onChange={(e) => set("compradorRnc", e.target.value)}
              required={config.compradorRncRequerido}
            />
          </label>
          {!config.compradorRncRequerido && (
            <label>
              Identificador extranjero (si no tiene RNC)
              <input
                value={form.compradorIdentificadorExtranjero}
                onChange={(e) => set("compradorIdentificadorExtranjero", e.target.value)}
              />
            </label>
          )}
          <label>
            Razón social / nombre
            <input
              value={form.razonSocialComprador}
              onChange={(e) => set("razonSocialComprador", e.target.value)}
              required={config.compradorRncRequerido}
            />
          </label>
          <label>
            Correo (opcional)
            <input
              type="email"
              value={form.correoComprador}
              onChange={(e) => set("correoComprador", e.target.value)}
            />
          </label>
          <label>
            Dirección (opcional)
            <input
              value={form.direccionComprador}
              onChange={(e) => set("direccionComprador", e.target.value)}
            />
          </label>
        </section>
      )}

      {(config.informacionReferenciaRequerida || form.ncfModificado) && (
        <section>
          <h2>Comprobante que modifica{config.informacionReferenciaRequerida ? "" : " (opcional)"}</h2>
          <label>
            NCF modificado
            <input
              value={form.ncfModificado}
              onChange={(e) => set("ncfModificado", e.target.value)}
              required={config.informacionReferenciaRequerida}
            />
          </label>
          <label>
            Fecha del NCF modificado
            <input
              type="date"
              value={form.fechaNcfModificado}
              onChange={(e) => set("fechaNcfModificado", e.target.value)}
              required={config.informacionReferenciaRequerida}
            />
          </label>
          <label>
            Motivo de la modificación
            <select value={form.codigoModificacion} onChange={(e) => set("codigoModificacion", e.target.value)}>
              {CODIGOS_MODIFICACION.map((c) => (
                <option key={c.value} value={c.value}>
                  {c.label}
                </option>
              ))}
            </select>
          </label>
          {config.requiereIndicadorNotaCredito && (
            <label>
              ¿Emitida más de 30 días después de la factura original?
              <select
                value={form.indicadorNotaCredito}
                onChange={(e) => set("indicadorNotaCredito", e.target.value)}
              >
                <option value="0">No (≤ 30 días)</option>
                <option value="1">Sí (&gt; 30 días)</option>
              </select>
            </label>
          )}
        </section>
      )}

      <section>
        <h2>Detalle</h2>
        <ItemsEditor items={form.items} onChange={(items) => set("items", items)} />
      </section>

      <section className="totales-preview">
        <h2>Totales (calculados)</h2>
        <dl>
          <dt>Monto gravado</dt>
          <dd>{decimal2(totales.montoGravadoTotal)}</dd>
          <dt>ITBIS</dt>
          <dd>{decimal2(totales.totalITBIS)}</dd>
          <dt>Exento</dt>
          <dd>{decimal2(totales.montoExento)}</dd>
          <dt>
            <strong>Total</strong>
          </dt>
          <dd>
            <strong>{decimal2(totales.montoTotal)}</strong>
          </dd>
        </dl>
      </section>

      {error && <p className="error">{error}</p>}

      <div className="acciones">
        <button type="submit" className="btn-primario" disabled={guardando}>
          {guardando ? "Guardando…" : "Guardar borrador"}
        </button>
      </div>
    </form>
  );
}
