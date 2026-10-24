import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { configuracionApi, publicarEmpresa, type Empresa, type EmpresaPayload } from "../../lib/configuracionApi";
import { useAviso } from "../ingresos/useAviso";
import {
  SeccionAmbiente,
  SeccionCertificado,
  SeccionEmpresa,
  SeccionEnvio,
  SeccionInventario,
  SeccionSecuencias,
  SeccionUsuarios,
  SeccionVendedores,
} from "./ConfiguracionSecciones";
import { mensajeError } from "./configuracionUtil";
import { SeccionPagos } from "./SeccionPagos";
import "./plataforma.css";

const SECCIONES = [
  { id: "empresa", label: "Datos de la empresa" },
  { id: "certificado", label: "Certificado digital" },
  { id: "ambiente", label: "Ambiente DGII" },
  { id: "secuencias", label: "Secuencias autorizadas" },
  { id: "inventario", label: "Método de inventario" },
  { id: "envio", label: "WhatsApp y correo" },
  { id: "pagos", label: "Pagos en línea" },
  { id: "vendedores", label: "Vendedores" },
  { id: "usuarios", label: "Usuarios" },
] as const;

const VACIA: EmpresaPayload = {
  razon_social: "",
  nombre_comercial: "",
  rnc: "",
  actividad_economica: "",
  direccion: "",
  provincia: "",
  municipio: "",
  telefono: "",
  correo: "",
  logo: null,
};

/** /configuracion[/:seccion]: secciones del prototipo Configuracion.dc.html. */
export function ConfiguracionPage() {
  const { seccion: elegida } = useParams();
  const seccion = elegida ?? "empresa";
  const navigate = useNavigate();
  const [aviso, mostrarAviso] = useAviso();
  const [empresa, setEmpresa] = useState<Empresa | null>(null);
  const [form, setForm] = useState<EmpresaPayload>(VACIA);
  const [metodo, setMetodo] = useState<"promedio" | "peps">("promedio");
  const [metodoGuardado, setMetodoGuardado] = useState<"promedio" | "peps">("promedio");
  const [error, setError] = useState<string | null>(null);
  const [guardando, setGuardando] = useState(false);
  /** Hasta que llegan los datos guardados no se edita (si no, la respuesta pisaría lo escrito). */
  const [cargada, setCargada] = useState(false);
  const refs = useRef<Record<string, HTMLElement | null>>({});

  useEffect(() => {
    configuracionApi
      .empresa()
      .then((e) => {
        setEmpresa(e);
        setForm({
          razon_social: e.razon_social ?? "",
          nombre_comercial: e.nombre_comercial ?? "",
          rnc: e.rnc ?? "",
          actividad_economica: e.actividad_economica ?? "",
          direccion: e.direccion ?? "",
          provincia: e.provincia ?? "",
          municipio: e.municipio ?? "",
          telefono: e.telefono ?? "",
          correo: e.correo ?? "",
          logo: e.logo,
        });
        setCargada(true);
      })
      .catch((err) => setError(mensajeError(err)));
    configuracionApi
      .inventario()
      .then((r) => {
        setMetodo(r.metodo);
        setMetodoGuardado(r.metodo);
      })
      .catch(() => undefined);
  }, []);

  // Sin sección en la ruta (/configuracion) la página abre arriba, con su título.
  useEffect(() => {
    if (elegida) refs.current[elegida]?.scrollIntoView?.({ behavior: "smooth", block: "start" });
  }, [elegida]);

  async function guardar() {
    setError(null);
    if (!form.razon_social.trim()) {
      setError("Escriba la razón social de la empresa.");
      navigate("/configuracion/empresa");
      return;
    }
    setGuardando(true);
    try {
      const e = await configuracionApi.guardarEmpresa(form);
      setEmpresa(e);
      publicarEmpresa(e);
      if (metodo !== metodoGuardado) {
        await configuracionApi.guardarInventario(metodo);
        setMetodoGuardado(metodo);
      }
      mostrarAviso("Configuración guardada");
    } catch (err) {
      setError(mensajeError(err));
      mostrarAviso("No se pudo guardar", { detalle: mensajeError(err), error: true });
    } finally {
      setGuardando(false);
    }
  }

  const ref = (id: string) => (el: HTMLElement | null) => {
    refs.current[id] = el;
  };

  return (
    <div className="content">
      <div className="ph">
        <div>
          <h1>Configuración</h1>
          <p>Empresa, firma digital, conexión con la DGII y envío automático de comprobantes</p>
        </div>
        <button type="button" className="btn btn-p" onClick={() => void guardar()} disabled={guardando || !cargada}>
          {guardando ? "Guardando…" : "Guardar cambios"}
        </button>
      </div>
      {error && (
        <div className="al al-d" role="alert">
          <span>{error}</span>
        </div>
      )}

      <div className="cfg">
        <nav className="cfg-nav" aria-label="Secciones de configuración">
          {SECCIONES.map((s) => (
            <button key={s.id} type="button" className={s.id === seccion ? "sv on" : "sv"} onClick={() => navigate(`/configuracion/${s.id}`)}>
              {s.label}
            </button>
          ))}
        </nav>

        <div className="cfg-body">
          <SeccionEmpresa refSeccion={ref("empresa")} form={form} setForm={setForm} empresa={empresa} cargada={cargada} />
          <SeccionCertificado refSeccion={ref("certificado")} avisar={mostrarAviso} />
          <SeccionAmbiente refSeccion={ref("ambiente")} empresa={empresa} />
          <SeccionSecuencias refSeccion={ref("secuencias")} avisar={mostrarAviso} />
          <SeccionInventario refSeccion={ref("inventario")} metodo={metodo} setMetodo={setMetodo} />
          <SeccionEnvio refSeccion={ref("envio")} avisar={mostrarAviso} empresa={empresa} />
          <SeccionPagos refSeccion={ref("pagos")} avisar={mostrarAviso} />
          <SeccionVendedores refSeccion={ref("vendedores")} avisar={mostrarAviso} />
          <SeccionUsuarios refSeccion={ref("usuarios")} avisar={mostrarAviso} />
        </div>
      </div>
      {aviso}
    </div>
  );
}
