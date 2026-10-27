// Configuración → Padrón de RNC de la DGII (docs/PADRON_RNC.md): la última
// importación del listado público de contribuyentes (DGII_RNC.zip), con
// «Actualizar ahora» y «Subir DGII_RNC.zip» para servidores que no llegan a
// dgii.gov.do. Es el mismo para todas las empresas: solo lo actualiza un
// administrador de la instalación.
import { useEffect, useRef, useState } from "react";
import { padronApi, type EstadoPadron } from "../../lib/padronApi";
import { Tarjeta, type RefSeccion } from "./ConfiguracionSecciones";
import { mensajeError, numero, type Avisar } from "./configuracionUtil";

function fechaHora(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString("es-DO", { dateStyle: "medium", timeStyle: "short" }) : "—";
}

export function SeccionPadron({ refSeccion, avisar }: { refSeccion: RefSeccion; avisar: Avisar }) {
  const [estado, setEstado] = useState<EstadoPadron | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);
  const archivo = useRef<HTMLInputElement | null>(null);
  const enCurso = Boolean(estado?.en_curso);

  useEffect(() => {
    padronApi.estado().then(setEstado, (err) => setError(mensajeError(err)));
  }, []);

  // Mientras importa, se pregunta cada 3 s hasta que termina.
  useEffect(() => {
    if (!enCurso) return;
    const t = window.setInterval(() => {
      padronApi
        .estado()
        .then((e) => {
          setEstado(e);
          if (!e.en_curso) avisar(e.error ? "No se pudo actualizar el padrón" : "Padrón de RNC actualizado", e.error ? { detalle: e.error, error: true } : undefined);
        })
        .catch(() => undefined);
    }, 3000);
    return () => window.clearInterval(t);
  }, [enCurso, avisar]);

  async function ejecutar(accion: () => Promise<EstadoPadron>) {
    setEnviando(true);
    setError(null);
    try {
      setEstado(await accion());
    } catch (err) {
      setError(mensajeError(err));
    } finally {
      setEnviando(false);
    }
  }

  function subir(f: File | undefined) {
    if (archivo.current) archivo.current.value = "";
    if (!f) return;
    if (estado && f.size > estado.max_mb * 1024 * 1024) {
      setError(`El archivo pasa de ${estado.max_mb} MB.`);
      return;
    }
    void ejecutar(() => padronApi.subir(f));
  }

  const puede = Boolean(estado?.puede_actualizar) && !enviando && !enCurso;
  const ok = Boolean(estado?.importado_en);

  return (
    <Tarjeta
      id="padron"
      refSeccion={refSeccion}
      titulo="Padrón de RNC de la DGII"
      extra={
        estado && (
          <span className={enCurso ? "bd b-pend" : estado.error ? "bd b-rech" : ok ? "bd b-acep" : "bd b-borr"} data-padron-estado>
            {enCurso ? "Actualizando…" : estado.error ? "Con error" : ok ? "Al día" : "Sin importar"}
          </span>
        )
      }
    >
      <div className="card-b" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        <p className="hint" style={{ margin: 0 }}>
          El listado público de contribuyentes de la DGII llena la razón social, el nombre comercial, la actividad, el estado y el régimen al verificar
          un RNC o una cédula (Agregar empresa, Contactos y la factura). Es el mismo para todas las empresas de la instalación.
          {estado?.automatico ? ` Se descarga solo cada ${estado.cada_horas} horas.` : " La descarga automática está apagada (PADRON_ACTIVO)."}
        </p>
        {error && (
          <div className="al al-d" role="alert">
            <span>{error}</span>
          </div>
        )}
        {estado && (
          <dl className="kv" style={{ margin: 0 }}>
            <dt>Última importación</dt>
            <dd data-padron-fecha>{fechaHora(estado.importado_en)}</dd>
            <dt>Contribuyentes</dt>
            <dd data-padron-filas>{estado.filas != null ? numero(estado.filas) : "—"}</dd>
            {estado.importado_en && (estado.nuevas != null || estado.eliminadas != null) && (
              <>
                <dt>Cambios</dt>
                <dd>
                  {numero(estado.nuevas)} nuevos · {numero(estado.cambiadas)} cambiados · {numero(estado.eliminadas)} quitados
                </dd>
              </>
            )}
            <dt>Origen</dt>
            <dd style={{ wordBreak: "break-all" }}>{estado.origen ?? estado.url}</dd>
          </dl>
        )}
        {estado?.error && !enCurso && (
          <div className="al al-w" data-padron-error>
            <span>
              <b>Último intento ({fechaHora(estado.error_en)}):</b> {estado.error}
              {ok && " Se sigue usando el padrón anterior."}
            </span>
          </div>
        )}
        {estado && !estado.puede_actualizar && (
          <p className="hint" style={{ margin: 0 }}>
            Solo un administrador de la instalación puede actualizarlo.
          </p>
        )}
        {estado?.puede_actualizar && (
          <div className="row" style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            <button type="button" className="btn btn-p btn-sm" disabled={!puede} onClick={() => void ejecutar(padronApi.actualizar)}>
              {enCurso ? "Actualizando…" : "Actualizar ahora"}
            </button>
            <label className={`btn btn-o btn-sm${puede ? "" : " dis"}`} aria-disabled={!puede} style={puede ? undefined : { opacity: 0.55, pointerEvents: "none" }}>
              Subir DGII_RNC.zip
              <input
                ref={archivo}
                type="file"
                accept=".zip,.txt,application/zip"
                hidden
                disabled={!puede}
                onChange={(e) => subir(e.target.files?.[0])}
                aria-label="Subir el archivo DGII_RNC.zip"
              />
            </label>
            <span className="hint" style={{ alignSelf: "center" }}>
              Si este servidor no llega a dgii.gov.do: descárguelo en Consultas → RNC de la DGII y súbalo aquí (máx. {estado.max_mb} MB).
            </span>
          </div>
        )}
      </div>
    </Tarjeta>
  );
}
