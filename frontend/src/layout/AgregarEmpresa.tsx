// "Agregar empresa" (Mis empresas, multiempresa): el RNC se verifica en el
// registro de la instalación y en el padrón de RNC de la DGII (erp-api,
// docs/PADRON_RNC.md), que llena razón social, nombre comercial y actividad
// y dice su estado y régimen; con certificado digital, además, si es emisor
// electrónico (consulta_directorio, best-effort). Quien la agrega queda como
// su administrador y puede cambiarse a ella en ese momento.
import { useRef, useState, type FormEvent } from "react";
import { cambiarEmpresa, cargarMisEmpresas, empresasApi, type ConsultaRnc, type EmpresaResumen } from "../lib/empresasApi";
import { formatoRnc } from "../lib/marca";
import { estadoPadron, fechaPadron, NO_EN_PADRON, textoRegimen } from "../lib/padronUtil";
import { IconCerrar } from "./icons";

const CAMPOS: { k: "nombre_comercial" | "actividad_economica" | "direccion" | "provincia" | "municipio" | "telefono" | "correo"; t: string; ancho?: boolean }[] = [
  { k: "nombre_comercial", t: "Nombre comercial" },
  { k: "actividad_economica", t: "Actividad económica" },
  { k: "direccion", t: "Dirección", ancho: true },
  { k: "provincia", t: "Provincia" },
  { k: "municipio", t: "Municipio" },
  { k: "telefono", t: "Teléfono" },
  { k: "correo", t: "Correo" },
];

function mensaje(err: unknown): string {
  return err instanceof Error ? err.message : "No se pudo completar. Intente de nuevo.";
}

export function AgregarEmpresa({ onCerrar }: { onCerrar: () => void }) {
  const [rnc, setRnc] = useState("");
  const [consulta, setConsulta] = useState<ConsultaRnc | null>(null);
  const [razon, setRazon] = useState("");
  const [datos, setDatos] = useState<Record<string, string>>({});
  const [ocupado, setOcupado] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [creada, setCreada] = useState<EmpresaResumen | null>(null);
  /** Lo último que llenó el padrón: se reemplaza al verificar otro RNC, lo escrito a mano no. */
  const autollenado = useRef<Record<string, string>>({});
  const digitos = rnc.replace(/\D/g, "");
  const rncValido = digitos.length === 9 || digitos.length === 11;

  async function verificar(e?: FormEvent) {
    e?.preventDefault();
    if (!rncValido) return setError("El RNC debe tener 9 dígitos (o 11 si es una cédula).");
    setError(null);
    setOcupado(true);
    try {
      const c = await empresasApi.consultarRnc(digitos);
      setConsulta(c);
      const previo = autollenado.current;
      const libre = (actual: string | undefined, k: string) => !actual?.trim() || actual === previo[k];
      const nuevos: Record<string, string> = {};
      if (c.padron) {
        const p = c.padron;
        if (libre(razon, "razon")) {
          setRazon(p.razon_social);
          nuevos.razon = p.razon_social;
        }
        const llenar: Record<string, string | null> = { nombre_comercial: p.nombre_comercial, actividad_economica: p.actividad };
        setDatos((d) => {
          const sig = { ...d };
          for (const [k, v] of Object.entries(llenar)) {
            if (v && libre(d[k], k)) {
              sig[k] = v;
              nuevos[k] = v;
            }
          }
          return sig;
        });
      } else if (c.nombre && libre(razon, "razon")) {
        setRazon(c.nombre);
        nuevos.razon = c.nombre;
      }
      autollenado.current = nuevos;
    } catch (err) {
      setError(mensaje(err));
    } finally {
      setOcupado(false);
    }
  }

  async function agregar(e: FormEvent) {
    e.preventDefault();
    if (!razon.trim()) return setError("La razón social es obligatoria.");
    setError(null);
    setOcupado(true);
    try {
      const nueva = await empresasApi.agregar({
        rnc: digitos,
        razon_social: razon.trim(),
        ...Object.fromEntries(Object.entries(datos).map(([k, v]) => [k, v.trim() || null])),
      });
      await cargarMisEmpresas(true);
      setCreada(nueva);
    } catch (err) {
      setError(mensaje(err));
    } finally {
      setOcupado(false);
    }
  }

  const paso = creada ? 3 : consulta && !consulta.registrada ? 2 : 1;

  return (
    <div className="scrim" role="presentation" onClick={onCerrar}>
      <div className="modal ae-modal" role="dialog" aria-modal="true" aria-labelledby="agregar-empresa-t" onClick={(e) => e.stopPropagation()}>
        <div className="modal-h ae-h">
          <h3 id="agregar-empresa-t">{creada ? "Empresa agregada" : "Agregar empresa"}</h3>
          <button type="button" className="ib" onClick={onCerrar} aria-label="Cerrar">
            <IconCerrar />
          </button>
        </div>
        {paso === 3 && creada ? (
          <>
            <div className="modal-b">
              <div className="al al-s">
                <span>
                  <b>{creada.razon_social}</b> (RNC {formatoRnc(creada.rnc)}) ya es una de sus empresas. Usted es su administrador.
                </span>
              </div>
              <p style={{ margin: 0 }}>
                Dentro de ella cargue su <b>certificado digital</b>, sus <b>secuencias autorizadas</b> y su <b>ambiente DGII</b> en Configuración, e
                invite a sus usuarios. Cada empresa tiene sus propios clientes, productos, facturas, compras, bancos, contabilidad y nómina.
              </p>
            </div>
            <div className="modal-f">
              <button type="button" className="btn btn-g" onClick={onCerrar}>
                Seguir en la actual
              </button>
              <button type="button" className="btn btn-p" onClick={() => void cambiarEmpresa(creada.id)}>
                Cambiar a {creada.nombre_comercial || creada.razon_social}
              </button>
            </div>
          </>
        ) : (
          <form onSubmit={paso === 1 ? verificar : agregar}>
            <div className="modal-b">
              <div className="ae-rnc">
                <div className="fld" style={{ flex: 1 }}>
                  <label className="lbl" htmlFor="ae-rnc">
                    RNC de la nueva empresa <span className="req">*</span>
                  </label>
                  <input
                    id="ae-rnc"
                    className="inp mono"
                    inputMode="numeric"
                    placeholder="1-00-00000-0"
                    value={rnc}
                    autoFocus
                    onChange={(e) => {
                      setRnc(e.target.value);
                      setConsulta(null);
                    }}
                  />
                </div>
                <button type={paso === 1 ? "submit" : "button"} className="btn btn-o" disabled={ocupado || !rncValido} onClick={paso === 1 ? undefined : () => void verificar()}>
                  {ocupado && paso === 1 ? "Verificando…" : "Verificar RNC"}
                </button>
              </div>
              {consulta?.registrada && (
                <div className="al al-w">
                  <span>El RNC {formatoRnc(consulta.rnc)} ya es una empresa de esta instalación: búsquela en Mis empresas.</span>
                </div>
              )}
              {consulta && !consulta.registrada && <ResultadoDgii consulta={consulta} />}
              {paso === 2 && (
                <div className="ae-grid">
                  <div className="fld ae-ancho">
                    <label className="lbl" htmlFor="ae-razon">
                      Razón social <span className="req">*</span>
                    </label>
                    <input id="ae-razon" className="inp" value={razon} onChange={(e) => setRazon(e.target.value)} />
                  </div>
                  {CAMPOS.map((c) => (
                    <div key={c.k} className={`fld${c.ancho ? " ae-ancho" : ""}`}>
                      <label className="lbl" htmlFor={`ae-${c.k}`}>
                        {c.t}
                      </label>
                      <input
                        id={`ae-${c.k}`}
                        className="inp"
                        type={c.k === "correo" ? "email" : "text"}
                        value={datos[c.k] ?? ""}
                        onChange={(e) => setDatos((d) => ({ ...d, [c.k]: e.target.value }))}
                      />
                    </div>
                  ))}
                </div>
              )}
              {error && (
                <div className="al al-d" role="alert">
                  <span>{error}</span>
                </div>
              )}
            </div>
            <div className="modal-f">
              <button type="button" className="btn btn-g" onClick={onCerrar}>
                Cancelar
              </button>
              {paso === 2 && (
                <button type="submit" className="btn btn-p" disabled={ocupado || !razon.trim()}>
                  {ocupado ? "Agregando…" : "Agregar empresa"}
                </button>
              )}
            </div>
          </form>
        )}
      </div>
    </div>
  );
}

/** Lo que dijo la DGII: el padrón (datos, estado, régimen) y el directorio de emisores electrónicos. */
function ResultadoDgii({ consulta }: { consulta: ConsultaRnc }) {
  const p = consulta.padron;
  const estado = p ? estadoPadron(p.estado) : null;
  const regimen = p ? textoRegimen(p.regimen) : null;
  const actualizado = fechaPadron(p?.padron_actualizado_en);
  return (
    <div className="ae-dgii" data-padron={p ? "encontrado" : "sin-datos"}>
      {p && estado ? (
        <div className={`al ${estado.aviso ? "al-w" : "al-s"}`}>
          <span>
            <b>Padrón de la DGII:</b> {p.razon_social}{" "}
            <span className={estado.cls} data-estado-dgii>
              {estado.label}
            </span>
            {regimen && <> · Régimen {regimen}</>}
            {p.fecha_inicio && <> · Inicio {fechaPadron(p.fecha_inicio)}</>}
            {estado.aviso && <span style={{ display: "block", marginTop: 4 }}>{estado.aviso}</span>}
            <span className="hint" style={{ display: "block", marginTop: 4 }}>
              {NO_EN_PADRON}
              {actualizado && ` Padrón del ${actualizado}.`}
            </span>
          </span>
        </div>
      ) : (
        <div className="al al-i">
          <span>{consulta.padron_detalle ?? "No se encontró en el padrón de la DGII: complete los datos a mano."}</span>
        </div>
      )}
      <p className="hint" style={{ margin: "6px 2px 0" }} data-directorio>
        {consulta.consultado
          ? consulta.emisor_electronico
            ? "Emisor electrónico: sí (directorio de e-CF de la DGII)."
            : "Emisor electrónico: no aparece en el directorio de e-CF de la DGII."
          : consulta.detalle}
      </p>
    </div>
  );
}
