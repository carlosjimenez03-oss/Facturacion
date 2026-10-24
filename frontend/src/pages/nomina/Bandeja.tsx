// Nómina › Bandeja de entrada (vista "bandeja" de Nomina.dc.html): avisos de la TSS, la DGII y del
// equipo (nóminas por aprobar o pagar). Los avisos se calculan en el backend; el "leído" se guarda en
// este navegador.
import { Link } from "react-router-dom";
import { AvisoError, Cargando, Vacio } from "../ingresos/IngresosShell";
import { mensajeError } from "../ingresos/ingresosUtil";
import { Encabezado, NominaShell } from "./NominaShell";
import { useAvisosNomina } from "./nominaUtil";

export function BandejaNominaPage() {
  const { avisos, error, leidos, marcar, marcarTodos, recargar } = useAvisosNomina();
  return (
    <NominaShell>
      <Encabezado
        titulo="Bandeja de entrada"
        sub="Avisos de la TSS, la DGII y tu equipo"
        acciones={
          <button type="button" className="btn btn-o" disabled={!avisos?.length} onClick={() => marcarTodos((avisos ?? []).map((a) => a.id))}>
            Marcar todo como leído
          </button>
        }
      />
      {error != null && <AvisoError titulo="No se pudieron cargar los avisos." mensaje={mensajeError(error)} onReintentar={recargar} />}
      <section className="card">
        {avisos === null ? (
          <Cargando />
        ) : avisos.length === 0 ? (
          <Vacio titulo="Bandeja vacía" texto="Aquí llegarán los vencimientos de la TSS y la DGII y las nóminas por aprobar o pagar." />
        ) : (
          avisos.map((a) => {
            const leido = leidos.has(a.id);
            return (
              <div key={a.id} className={`nm-msg${leido ? "" : " nuevo"}`}>
                <span className="nm-dot" aria-hidden="true" />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <b>{a.titulo}</b>
                  <div className="hint" style={{ fontSize: 13, marginTop: 2 }}>{a.detalle}</div>
                </div>
                <div className="row">
                  {a.enlace && (
                    <Link to={a.enlace} className="btn btn-g btn-sm" onClick={() => marcar(a.id, true)}>
                      Ver
                    </Link>
                  )}
                  <button type="button" className="btn btn-g btn-sm" onClick={() => marcar(a.id, !leido)}>
                    {leido ? "Marcar no leído" : "Marcar leído"}
                  </button>
                </div>
              </div>
            );
          })
        )}
      </section>
    </NominaShell>
  );
}
