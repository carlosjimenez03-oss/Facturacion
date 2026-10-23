import { IconAlerta } from "../../layout/icons";

/** Confirmación de borrado. Si el backend responde 409 (tiene cotizaciones) ofrece desactivarlo. */
export function ModalEliminar({
  nombre,
  bloqueo,
  procesando,
  onCancelar,
  onEliminar,
  onDesactivar,
}: {
  nombre: string;
  bloqueo?: string;
  procesando: boolean;
  onCancelar: () => void;
  onEliminar: () => void;
  onDesactivar: () => void;
}) {
  return (
    <div className="scrim">
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="del-t">
        <div className="modal-h">
          <h3 id="del-t">{bloqueo ? `No se puede eliminar a ${nombre}` : `¿Eliminar a ${nombre}?`}</h3>
        </div>
        <div className="modal-b">
          {bloqueo ? (
            <>
              <div className="al al-w">
                <IconAlerta />
                <span>{bloqueo}</span>
              </div>
              <span>Puede desactivarlo: dejará de aparecer al facturar y en las búsquedas, y su historial se conserva.</span>
            </>
          ) : (
            <span>Los comprobantes ya emitidos se conservan. El contacto dejará de aparecer en búsquedas y formularios.</span>
          )}
        </div>
        <div className="modal-f">
          <button type="button" className="btn btn-o" onClick={onCancelar} disabled={procesando}>
            Cancelar
          </button>
          {bloqueo ? (
            <button type="button" className="btn btn-p" onClick={onDesactivar} disabled={procesando}>
              {procesando ? "Desactivando…" : "Desactivar"}
            </button>
          ) : (
            <button type="button" className="btn btn-d" onClick={onEliminar} disabled={procesando}>
              {procesando ? "Eliminando…" : "Eliminar"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
