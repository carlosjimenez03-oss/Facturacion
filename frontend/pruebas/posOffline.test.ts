// Pruebas de las reglas del POS sin conexión (src/pages/pos/posOffline.ts):
// cola en orden, idempotencia del reintento, cortes de red y de sesión a
// mitad, errores por venta, totales de caja y existencia. Corren con el
// test runner de Node, que quita los tipos de TypeScript sin compilar
// (`npm test`; Node 22.18 o más nuevo).
import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  aplicadoPorMetodo,
  clasificarError,
  mensajeSincronizacion,
  nuevaClave,
  nuevaVentaOffline,
  ordenarCola,
  resumenPendiente,
  sincronizarCola,
  sinSincronizar,
  stockConPendientes,
} from "../src/pages/pos/posOffline.ts";
import type { DependenciasSincronizacion, VentaOffline } from "../src/pages/pos/posOffline.ts";

class HttpError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

let n = 0;
function venta(extra: Partial<VentaOffline> = {}): VentaOffline {
  n += 1;
  return {
    ...nuevaVentaOffline({
      clave: `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`,
      creadaEn: `2026-10-24T10:${String(n).padStart(2, "0")}:00.000Z`,
      sesionId: "caja-1",
      caja: "Caja principal",
      cajero: "Ana",
      tipoEcf: "32",
      datos: { Encabezado: { Totales: { MontoTotal: "118.00" } } },
      itemsInventario: [{ numero_linea: "1", producto_id: "p1", cantidad: "2", almacen_id: "alm" }],
      items: [],
      emisor: { rnc: "131897542", razonSocial: "Demo", direccion: "Calle 1", correo: "" },
      cliente: { rnc: "", nombre: "" },
      pagos: [{ metodo: "efectivo", monto: "200.00" }],
      recibido: 200,
      cambio: 82,
      total: 118,
    }),
    ...extra,
  };
}

/** Servidor simulado: facturas por clave de idempotencia, e-NCF en secuencia, caja. */
function servidor() {
  const facturas = new Map<string, { id: string; encf: string; estado: string; track_id: string | null }>();
  const caja: string[] = [];
  const guardadas = new Map<string, VentaOffline>();
  const llamadas: string[] = [];
  let siguiente = 1;
  let fallar: { paso: string; error: unknown; veces: number } | null = null;
  const quizasFalla = (paso: string) => {
    if (fallar && fallar.paso === paso && fallar.veces > 0) {
      fallar.veces -= 1;
      throw fallar.error;
    }
  };
  const deps: DependenciasSincronizacion = {
    prepararSesion: async () => "ok",
    async crearFactura(v) {
      llamadas.push(`crear ${v.clave.slice(-2)}`);
      quizasFalla("crear-antes");
      let f = facturas.get(v.clave);
      if (!f) {
        f = { id: `f-${v.clave.slice(-2)}`, encf: `E32${String(siguiente++).padStart(10, "0")}`, estado: "borrador", track_id: null };
        facturas.set(v.clave, f);
      }
      quizasFalla("crear-despues"); // creada en el servidor, la respuesta se pierde
      return { ...f };
    },
    async generarXml(id) {
      llamadas.push(`xml ${id}`);
      quizasFalla("xml");
      return { errores: [] };
    },
    async enviar(id) {
      llamadas.push(`enviar ${id}`);
      quizasFalla("enviar-antes");
      const f = [...facturas.values()].find((x) => x.id === id)!;
      f.estado = "enviado";
      f.track_id = `t-${id}`;
      quizasFalla("enviar-despues");
      return { estado: "enviado", track_id: f.track_id };
    },
    async registrarVenta(_sesion, facturaId) {
      llamadas.push(`caja ${facturaId}`);
      quizasFalla("caja");
      if (!caja.includes(facturaId)) caja.push(facturaId);
    },
    async guardar(v) {
      guardadas.set(v.clave, v);
    },
    ahora: () => new Date("2026-10-24T12:00:00Z"),
  };
  return {
    deps,
    facturas,
    caja,
    guardadas,
    llamadas,
    fallarEn: (paso: string, error: unknown, veces = 1) => {
      fallar = { paso, error, veces };
    },
    cola: () => [...guardadas.values()],
  };
}

describe("cola", () => {
  it("ordena por hora de la venta y deja fuera las sincronizadas", () => {
    const a = venta();
    const b = venta();
    const c = venta({ estado: "sincronizada" });
    assert.deepEqual(
      ordenarCola([b, c, a]).map((v) => v.clave),
      [a.clave, b.clave, c.clave],
    );
    assert.deepEqual(sinSincronizar([a, b, c]).length, 2);
    assert.deepEqual(sinSincronizar([a, venta({ sesionId: "otra" })], "caja-1"), [a]);
  });

  it("genera claves UUID v4 distintas", () => {
    const claves = new Set(Array.from({ length: 50 }, nuevaClave));
    assert.equal(claves.size, 50);
    for (const c of claves) assert.match(c, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  });
});

describe("sincronizarCola", () => {
  it("emite las ventas en orden: crear -> generar-xml -> enviar -> caja", async () => {
    const s = servidor();
    const [a, b, c] = [venta(), venta(), venta()];
    const r = await sincronizarCola([c, a, b], s.deps);
    assert.equal(r.detenida, null);
    assert.deepEqual(
      r.sincronizadas.map((v) => [v.clave, v.encf]),
      [
        [a.clave, "E320000000001"],
        [b.clave, "E320000000002"],
        [c.clave, "E320000000003"],
      ],
    );
    assert.equal(s.caja.length, 3);
    for (const v of s.cola()) {
      assert.equal(v.estado, "sincronizada");
      assert.equal(v.enviada, true);
      assert.equal(v.registradaEnCaja, true);
      assert.equal(v.sincronizadaEn, "2026-10-24T12:00:00.000Z");
    }
    assert.deepEqual(s.llamadas.slice(0, 4), [`crear ${a.clave.slice(-2)}`, `xml f-${a.clave.slice(-2)}`, `enviar f-${a.clave.slice(-2)}`, `caja f-${a.clave.slice(-2)}`]);
  });

  it("si la red se cae tras crear la factura, el reintento la reutiliza (una sola factura y un solo e-NCF)", async () => {
    const s = servidor();
    const v = venta();
    s.fallarEn("crear-despues", new TypeError("Failed to fetch"));
    const r1 = await sincronizarCola([v], s.deps);
    assert.equal(r1.detenida, "sin_red");
    const guardada = s.guardadas.get(v.clave)!;
    assert.equal(guardada.estado, "pendiente");
    assert.equal(guardada.error, null);
    const r2 = await sincronizarCola([guardada], s.deps);
    assert.equal(r2.sincronizadas.length, 1);
    assert.equal(s.facturas.size, 1);
    assert.equal(r2.sincronizadas[0].encf, "E320000000001");
    assert.equal(s.caja.length, 1);
  });

  it("si la red se cae después del envío, no vuelve a generar ni a enviar", async () => {
    const s = servidor();
    const v = venta();
    s.fallarEn("enviar-despues", new HttpError(504, "Gateway Timeout"));
    const r1 = await sincronizarCola([v], s.deps);
    assert.equal(r1.detenida, "sin_red");
    s.llamadas.length = 0;
    const r2 = await sincronizarCola([s.guardadas.get(v.clave)!], s.deps);
    assert.equal(r2.sincronizadas.length, 1);
    assert.deepEqual(s.llamadas, [`crear ${v.clave.slice(-2)}`, `caja f-${v.clave.slice(-2)}`]);
  });

  it("si la red se cae al registrar en caja, el reintento solo registra", async () => {
    const s = servidor();
    const v = venta();
    s.fallarEn("caja", new TypeError("Failed to fetch"));
    await sincronizarCola([v], s.deps);
    const guardada = s.guardadas.get(v.clave)!;
    assert.equal(guardada.enviada, true);
    assert.equal(guardada.registradaEnCaja, false);
    s.llamadas.length = 0;
    await sincronizarCola([guardada], s.deps);
    assert.deepEqual(s.llamadas, [`caja f-${v.clave.slice(-2)}`]);
    assert.equal(s.caja.length, 1);
  });

  it("sin red se detiene y deja el resto pendiente, en orden", async () => {
    const s = servidor();
    const [a, b] = [venta(), venta()];
    s.fallarEn("crear-antes", new TypeError("Failed to fetch"));
    const r = await sincronizarCola([a, b], s.deps);
    assert.equal(r.detenida, "sin_red");
    assert.equal(s.facturas.size, 0);
    assert.equal(s.guardadas.get(a.clave)!.estado, "pendiente");
    assert.equal(s.guardadas.has(b.clave), false); // ni se tocó
  });

  it("un rechazo (secuencia agotada) marca esa venta con error claro y sigue con la siguiente", async () => {
    const s = servidor();
    const [a, b] = [venta(), venta()];
    s.fallarEn("crear-antes", new HttpError(409, "La secuencia E32 está agotada (autorizada hasta E320000000010): solicite una nueva"));
    const r = await sincronizarCola([a, b], s.deps);
    assert.equal(r.detenida, null);
    assert.equal(r.conError.length, 1);
    const conError = s.guardadas.get(a.clave)!;
    assert.equal(conError.estado, "error");
    assert.match(conError.error ?? "", /^No se pudo asignar el e-NCF: La secuencia E32 está agotada/);
    assert.equal(r.sincronizadas[0].clave, b.clave);
    // Reintentar (tras registrar la secuencia nueva): se emite.
    const r2 = await sincronizarCola(s.cola(), s.deps);
    assert.equal(r2.sincronizadas.length, 1);
    assert.equal(r2.sincronizadas[0].clave, a.clave);
    assert.equal(s.guardadas.get(a.clave)!.intentos, 2);
  });

  it("errores del XSD quedan en la venta", async () => {
    const s = servidor();
    s.deps.generarXml = async () => ({ errores: ["Falta RNCComprador"] });
    const v = venta();
    const r = await sincronizarCola([v], s.deps);
    assert.equal(r.conError.length, 1);
    assert.deepEqual(s.guardadas.get(v.clave)!.erroresXml, ["Falta RNCComprador"]);
  });

  it("emitida en contingencia (la DGII no responde) cuenta como sincronizada", async () => {
    const s = servidor();
    s.deps.enviar = async () => ({ estado: "contingencia", track_id: null, contingencia: true });
    const r = await sincronizarCola([venta()], s.deps);
    assert.equal(r.sincronizadas.length, 1);
    assert.equal(r.sincronizadas[0].contingencia, true);
    assert.equal(r.sincronizadas[0].estadoDgii, "contingencia");
  });

  it("el envío sin trackId ni contingencia es un error de la venta", async () => {
    const s = servidor();
    s.deps.enviar = async () => ({ estado: "error", track_id: null, error: "La DGII rechazó el XML" });
    const v = venta();
    await sincronizarCola([v], s.deps);
    assert.equal(s.guardadas.get(v.clave)!.error, "La DGII rechazó el XML");
  });

  it("sesión vencida sin poder renovarla: no toca la cola", async () => {
    const s = servidor();
    s.deps.prepararSesion = async () => "sin_sesion";
    const v = venta();
    const r = await sincronizarCola([v], s.deps);
    assert.equal(r.detenida, "sin_sesion");
    assert.equal(s.llamadas.length, 0);
    assert.equal(s.guardadas.size, 0);
  });

  it("un 401 a mitad (sesión cerrada) deja la venta pendiente, sin error", async () => {
    const s = servidor();
    s.fallarEn("xml", new HttpError(401, "Inicie sesión para continuar"));
    const v = venta();
    const r = await sincronizarCola([v], s.deps);
    assert.equal(r.detenida, "sin_sesion");
    assert.equal(s.guardadas.get(v.clave)!.estado, "pendiente");
    assert.equal(s.guardadas.get(v.clave)!.error, null);
  });

  it("sin nada pendiente no pide la sesión", async () => {
    const s = servidor();
    let pedida = false;
    s.deps.prepararSesion = async () => {
      pedida = true;
      return "ok";
    };
    await sincronizarCola([venta({ estado: "sincronizada" })], s.deps);
    assert.equal(pedida, false);
  });
});

describe("errores", () => {
  it("clasifica red, sesión y rechazo", () => {
    assert.equal(clasificarError(new TypeError("Failed to fetch")), "red");
    for (const st of [0, 502, 503, 504]) assert.equal(clasificarError(new HttpError(st, "x")), "red");
    assert.equal(clasificarError(new HttpError(401, "x")), "sesion");
    assert.equal(clasificarError(new HttpError(409, "x")), "rechazo");
    assert.equal(clasificarError(new Error("x")), "rechazo");
  });

  it("mensajes", () => {
    assert.equal(mensajeSincronizacion(new HttpError(409, "Esta factura ya está registrada en otra caja")), "No se pudo registrar en la caja: Esta factura ya está registrada en otra caja");
    assert.equal(mensajeSincronizacion(new HttpError(422, "tipo_ecf inválido")), "tipo_ecf inválido");
  });
});

describe("caja y existencia", () => {
  it("aplicado por método: el efectivo sin el cambio", () => {
    assert.deepEqual(aplicadoPorMetodo([{ metodo: "efectivo", monto: "200.00" }], 82), { efectivo: 118, tarjeta: 0, transferencia: 0 });
    assert.deepEqual(
      aplicadoPorMetodo(
        [
          { metodo: "tarjeta", monto: "100.00" },
          { metodo: "efectivo", monto: "50.00" },
        ],
        31.5,
      ),
      { efectivo: 18.5, tarjeta: 100, transferencia: 0 },
    );
  });

  it("resumen de lo que la caja del servidor todavía no tiene", () => {
    const ventas = [
      venta(),
      venta({ pagos: [{ metodo: "tarjeta", monto: "118.00" }], cambio: 0, recibido: 118 }),
      venta({ registradaEnCaja: true, estado: "sincronizada" }),
      venta({ sesionId: "otra-caja" }),
    ];
    assert.deepEqual(resumenPendiente(ventas, "caja-1"), { cantidad: 2, total: 236, porMetodo: { efectivo: 118, tarjeta: 118, transferencia: 0 } });
  });

  it("descuenta de la existencia lo vendido que aún no llegó a la DGII", () => {
    const ventas = [venta(), venta({ enviada: true }), venta({ itemsInventario: [{ numero_linea: "1", producto_id: "p1", cantidad: "1", almacen_id: "otro" }] })];
    assert.deepEqual(stockConPendientes({ p1: 10, p2: 3 }, ventas, "alm"), { p1: 8, p2: 3 });
    // Sin almacén en la línea = el de la caja.
    assert.deepEqual(stockConPendientes({ p1: 10 }, [venta({ itemsInventario: [{ numero_linea: "1", producto_id: "p1", cantidad: "3" }] })], "alm"), { p1: 7 });
  });
});
