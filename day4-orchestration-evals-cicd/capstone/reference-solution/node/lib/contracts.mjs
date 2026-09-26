// The JSON Schema subset the Day 4 contracts use (lab5-1-handoff/contracts.py validate(), and the Java
// common Contracts): type, enum, required, additionalProperties, min/max length and items, minimum, pattern.
// Error messages are the Java reference solution's, character for character.
import fs from "node:fs";
import path from "node:path";
import { NODE_DIR } from "./repo.mjs";

export class ContractError extends Error {}

/** The contract, copied verbatim from java/core/src/main/resources/contracts/sla-proposal.json. */
export const SCHEMA = JSON.parse(fs.readFileSync(path.join(NODE_DIR, "contracts", "sla-proposal.json"), "utf8"));

const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);

function isType(v, t) {
  switch (t) {
    case "object": return isObj(v);
    case "array": return Array.isArray(v);
    case "string": return typeof v === "string";
    case "integer": return typeof v === "number" && Number.isInteger(v);
    case "number": return typeof v === "number";
    case "boolean": return typeof v === "boolean";
    case "null": return v === null;
    default: return false;
  }
}

function typeName(v) {
  if (isObj(v)) return "dict";
  if (Array.isArray(v)) return "list";
  if (typeof v === "string") return "str";
  if (typeof v === "number") return Number.isInteger(v) ? "int" : "float";
  if (typeof v === "boolean") return "bool";
  return "NoneType";
}

function deepEqual(a, b) { return JSON.stringify(a) === JSON.stringify(b); }

export function validate(value, schema, p = "$") {
  if (schema.anyOf) {
    const errors = [];
    for (const sub of schema.anyOf) {
      try { validate(value, sub, p); return value; } catch (e) { if (!(e instanceof ContractError)) throw e; errors.push(e.message); }
    }
    throw new ContractError(`${p}: matches none of anyOf (${errors.join("; ")})`);
  }
  if (schema.type !== undefined) {
    const types = Array.isArray(schema.type) ? schema.type : [schema.type];
    if (!types.some((t) => isType(value, t))) throw new ContractError(`${p}: expected ${JSON.stringify(schema.type)}, got ${typeName(value)}`);
  }
  if (schema.enum && !schema.enum.some((e) => deepEqual(e, value))) {
    throw new ContractError(`${p}: ${JSON.stringify(value)} is not one of ${JSON.stringify(schema.enum)}`);
  }
  if (typeof value === "string") {
    if (value.length < (schema.minLength ?? 0) || value.length > (schema.maxLength ?? Infinity)) {
      throw new ContractError(`${p}: length ${value.length} outside limits`);
    }
    if (schema.pattern !== undefined && !new RegExp(`^(?:${schema.pattern})$`).test(value)) {
      throw new ContractError(`${p}: '${value}' does not match ${schema.pattern}`);
    }
  }
  if (typeof value === "number" && schema.minimum !== undefined && value < schema.minimum) {
    throw new ContractError(`${p}: ${value} below minimum`);
  }
  if (Array.isArray(value)) {
    if (value.length < (schema.minItems ?? 0) || value.length > (schema.maxItems ?? Infinity)) {
      throw new ContractError(`${p}: ${value.length} items outside limits`);
    }
    if (schema.items) value.forEach((v, i) => validate(v, schema.items, `${p}[${i}]`));
  }
  if (isObj(value)) {
    for (const k of schema.required || []) if (!(k in value)) throw new ContractError(`${p}: missing '${k}'`);
    const props = schema.properties || {};
    if (schema.additionalProperties === false) {
      const extra = Object.keys(value).filter((k) => !(k in props)).sort();
      if (extra.length) throw new ContractError(`${p}: unexpected [${extra.join(", ")}]`);
    }
    for (const [k, v] of Object.entries(value)) if (k in props) validate(v, props[k], `${p}.${k}`);
  }
  return value;
}
