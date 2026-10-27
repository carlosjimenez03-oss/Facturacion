// Presentación del padrón de RNC de la DGII (src/lib/padronUtil.ts): badge
// y aviso por estado, régimen, fechas y números válidos para consultar.
import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { estadoPadron, fechaPadron, NO_EN_PADRON, rncParaPadron, textoRegimen } from "../src/lib/padronUtil.ts";

describe("estadoPadron", () => {
  it("ACTIVO es verde y sin aviso", () => {
    assert.deepEqual(estadoPadron("ACTIVO"), { label: "Activo", cls: "bd b-acep", aviso: null });
    assert.equal(estadoPadron(" activo ").aviso, null);
  });

  it("suspendido y cese temporal avisan en ámbar", () => {
    for (const e of ["SUSPENDIDO", "CESE TEMPORAL"]) {
      const v = estadoPadron(e);
      assert.equal(v.cls, "bd b-pend");
      assert.match(v.aviso ?? "", new RegExp(`está ${e}`));
    }
    assert.equal(estadoPadron("CESE TEMPORAL").label, "Cese temporal");
  });

  it("dado de baja, anulado y rechazado en rojo", () => {
    for (const e of ["DADO DE BAJA", "ANULADO", "RECHAZADO"]) assert.equal(estadoPadron(e).cls, "bd b-rech");
    assert.match(estadoPadron("dado de baja").aviso ?? "", /DADO DE BAJA: confirme/);
  });

  it("desconocido o vacío: gris, con aviso", () => {
    assert.equal(estadoPadron("EN REVISION").cls, "bd b-borr");
    assert.ok(estadoPadron("EN REVISION").aviso);
    assert.equal(estadoPadron(null).label, "Sin estado");
  });
});

describe("textoRegimen y fechas", () => {
  it("régimen", () => {
    assert.equal(textoRegimen("NORMAL"), "Normal");
    assert.equal(textoRegimen("rst"), "RST (Régimen Simplificado de Tributación)");
    assert.equal(textoRegimen(""), null);
  });

  it("fecha del padrón", () => {
    assert.equal(fechaPadron("2026-10-27T05:00:00+00:00"), "27/10/2026");
    assert.equal(fechaPadron("1998-03-15"), "15/03/1998");
    assert.equal(fechaPadron(null), null);
  });
});

describe("rncParaPadron", () => {
  it("acepta RNC de 9 y cédula de 11, con o sin guiones", () => {
    assert.equal(rncParaPadron("1-01-00000-1"), "101000001");
    assert.equal(rncParaPadron("001-0000000-1"), "00100000001");
    assert.equal(rncParaPadron("12345"), null);
    assert.equal(rncParaPadron("1234567890"), null);
  });

  it("dice qué datos no trae el padrón", () => {
    assert.match(NO_EN_PADRON, /dirección, teléfono, correo ni provincia/);
  });
});
