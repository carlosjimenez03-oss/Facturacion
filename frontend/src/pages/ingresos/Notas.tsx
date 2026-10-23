import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { IconMas } from "../../layout/icons";
import { api } from "../../lib/api";
import { CODIGOS_MODIFICACION } from "../../lib/catalogos";
import { erpApi } from "../../lib/erpApi";
import type { DocumentoVenta } from "../../lib/erpApi";
import { ESTADO_BADGE, ESTADO_LABEL } from "../../lib/estados";
import type { EstadoFactura } from "../../lib/types";
import { formatoMonto, formatoRnc } from "../../lib/marca";
import { AvisoError, Buscador, Cargando, Estadistica, IngresosEncabezado, SinResultados, Vacio } from "./IngresosShell";
import { descargarCsv, fechaIso, fechaLegible, hoyIso, mensajeError, mesActualLegible } from "./ingresosUtil";

const CONFIG = {
  34: {
    titulo: "Notas de crédito",
    sub: "e-CF tipo 34 que anulan o reducen un comprobante emitido",
    nueva: "Nueva nota de crédito",
    columnaMotivo: "Motivo",
    singular: "nota de crédito",
    archivo: "Notas_de_credito",
  },
  33: {
    titulo: "Notas de débito",
    sub: "e-CF tipo 33 que aumentan el monto de un comprobante emitido",
    nueva: "Nueva nota de débito",
    columnaMotivo: "Concepto",
    singular: "nota de débito",
    archivo: "Notas_de_debito",
  },
} as const;

function motivo(codigo: string | null): string {
  if (!codigo) return "—";
  return CODIGOS_MODIFICACION.find((c) => c.value === String(codigo))?.label ?? `Código ${codigo}`;
}

function estadoLabel(estado: string): string {
  return ESTADO_LABEL[estado as EstadoFactura] ?? estado;
}

function tienePdf(d: DocumentoVenta): boolean {
  return d.estado === "enviado" || d.estado === "aceptado";
}

function NotasPage({ tipo }: { tipo: 33 | 34 }) {
  const cfg = CONFIG[tipo];
  const navigate = useNavigate();
  const [notas, setNotas] = useState<DocumentoVenta[]>([]);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");

  const cargar = useCallback(() => {
    setCargando(true);
    setError(null);
    erpApi
      .listarDocumentosVenta({ tipo_ecf: tipo })
      .then(setNotas)
      .catch((err) => {
        setNotas([]);
        setError(mensajeError(err));
      })
      .finally(() => setCargando(false));
  }, [tipo]);

  useEffect(() => {
    setQ("");
    cargar();
  }, [cargar]);

  const filas = useMemo(() => {
    const texto = q.trim().toLowerCase();
    return [...notas]
      .sort((a, b) => (b.encf ?? b.creado_en).localeCompare(a.encf ?? a.creado_en))
      .filter(
        (n) =>
          !texto ||
          [n.encf, n.cliente_nombre, n.cliente_rnc, n.encf_afectado, motivo(n.codigo_modificacion), estadoLabel(n.estado), fechaLegible(n.fecha_emision)]
            .join(" ")
            .toLowerCase()
            .includes(texto),
      );
  }, [notas, q]);

  const total = notas.reduce((s, n) => s + (Number(n.total) || 0), 0);
  const mes = hoyIso().slice(0, 7);
  const delMes = notas.filter((n) => fechaIso(n.fecha_emision ?? n.creado_en).startsWith(mes));
  const totalMes = delMes.reduce((s, n) => s + (Number(n.total) || 0), 0);

  function exportar() {
    descargarCsv(`${cfg.archivo}_${hoyIso()}.csv`, [
      ["e-NCF", "Cliente", "RNC", "e-NCF afectado", "Fecha", cfg.columnaMotivo, "Total (RD$)", "Estado DGII"],
      ...filas.map((n) => [
        n.encf,
        n.cliente_nombre,
        n.cliente_rnc,
        n.encf_afectado,
        fechaLegible(n.fecha_emision),
        motivo(n.codigo_modificacion),
        Number(n.total).toFixed(2),
        estadoLabel(n.estado),
      ]),
    ]);
  }

  const nuevaUrl = `/facturas/nueva?tipo=${tipo}`;

  return (
    <div className="content">
      <IngresosEncabezado
        titulo={cfg.titulo}
        sub={cfg.sub}
        acciones={
          <>
            <button type="button" className="btn btn-o" onClick={exportar} disabled={filas.length === 0}>
              Exportar
            </button>
            <Link className="btn btn-p" to={nuevaUrl}>
              <IconMas /> {cfg.nueva}
            </Link>
          </>
        }
      />

      {error && <AvisoError titulo={`No se pudieron cargar las ${cfg.titulo.toLowerCase()}.`} mensaje={error} onReintentar={cargar} />}

      <div className="grid-stats">
        <Estadistica k="Registros" v={cargando ? "—" : String(notas.length)} />
        <Estadistica k="Total (RD$) · total" v={cargando ? "—" : `RD$ ${formatoMonto(total)}`} />
        <Estadistica k={`Período · ${mesActualLegible()}`} v={cargando ? "—" : `RD$ ${formatoMonto(totalMes)}`} s={`${delMes.length} ${delMes.length === 1 ? cfg.singular : cfg.titulo.toLowerCase()} este mes`} />
      </div>

      <section className="card">
        <div className="filters">
          <Buscador valor={q} onCambiar={setQ} placeholder={`Buscar en ${cfg.titulo}`} />
        </div>

        {cargando && <Cargando />}
        {!cargando && !error && notas.length === 0 && (
          <Vacio titulo={`No hay ${cfg.titulo.toLowerCase()} todavía`} texto={`Emita una ${cfg.singular} para corregir un comprobante ya enviado a la DGII.`}>
            <Link className="btn btn-p btn-sm" to={nuevaUrl}>
              {cfg.nueva}
            </Link>
          </Vacio>
        )}
        {!cargando && notas.length > 0 && filas.length === 0 && <SinResultados q={q} onLimpiar={() => setQ("")} />}
        {!cargando && filas.length > 0 && (
          <div className="tw">
            <table className="tbl" style={{ minWidth: 960 }}>
              <thead>
                <tr>
                  <th>e-NCF</th>
                  <th>Cliente</th>
                  <th>e-NCF afectado</th>
                  <th>Fecha</th>
                  <th>{cfg.columnaMotivo}</th>
                  <th className="num">Total (RD$)</th>
                  <th>Estado DGII</th>
                  <th style={{ textAlign: "right" }}>Acciones</th>
                </tr>
              </thead>
              <tbody>
                {filas.map((n) => (
                  <tr key={n.id} className="fila-link" onClick={() => navigate(`/facturas/${n.id}`)}>
                    <td className="nowrap">
                      <Link to={`/facturas/${n.id}`} className="link mono" onClick={(e) => e.stopPropagation()}>
                        {n.encf || "(sin e-NCF)"}
                      </Link>
                    </td>
                    <td>
                      <div className="strong">{n.cliente_nombre || "Consumidor final"}</div>
                      {n.cliente_rnc && <div className="hint mono">{formatoRnc(n.cliente_rnc)}</div>}
                    </td>
                    <td className="mono nowrap">{n.encf_afectado || "—"}</td>
                    <td className="nowrap">{fechaLegible(n.fecha_emision)}</td>
                    <td>{motivo(n.codigo_modificacion)}</td>
                    <td className="num">{formatoMonto(n.total)}</td>
                    <td>
                      <span className={ESTADO_BADGE[n.estado as EstadoFactura] ?? "bd b-borr"}>{estadoLabel(n.estado)}</span>
                      {(n.estado === "rechazado" || n.estado === "error") && n.error && <div className="row-motivo">{n.error}</div>}
                    </td>
                    <td onClick={(e) => e.stopPropagation()}>
                      <div className="fila-acc">
                        <Link to={`/facturas/${n.id}`} className="btn btn-g btn-sm">
                          Ver
                        </Link>
                        {tienePdf(n) && (
                          <a href={api.representacionImpresaUrl(n.id)} target="_blank" rel="noreferrer" className="btn btn-g btn-sm">
                            PDF
                          </a>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

export function NotasCreditoPage() {
  return <NotasPage key={34} tipo={34} />;
}

export function NotasDebitoPage() {
  return <NotasPage key={33} tipo={33} />;
}
