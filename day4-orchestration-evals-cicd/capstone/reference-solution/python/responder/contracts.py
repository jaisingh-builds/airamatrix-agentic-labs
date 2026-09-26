"""The proposal contract: sla-proposal.json (the same file as Java's, verbatim) and a validator for it.

The schema is BOTH the model's submit_proposal tool schema and what its output is checked against.
Enough of JSON Schema for this contract, with the same messages as the Java/Node validators
(Lab 5.1's contracts.validate, messages aligned to day4 Java common Contracts).
"""
import json, re
from pathlib import Path

from .util import dumps, java_list

SCHEMA = json.loads((Path(__file__).resolve().parent / "contracts" / "sla-proposal.json").read_text(encoding="utf-8"))


class ContractError(ValueError):
    pass


def _is(v, t):
    if t == "object":
        return isinstance(v, dict)
    if t == "array":
        return isinstance(v, list)
    if t == "string":
        return isinstance(v, str)
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if t == "boolean":
        return isinstance(v, bool)
    if t == "null":
        return v is None
    return False


def _type_name(v):
    for t, n in ((dict, "dict"), (list, "list"), (str, "str"), (bool, "bool"), (int, "int"), (float, "float")):
        if isinstance(v, t):
            return n
    return "NoneType"


def validate(value, schema, path="$"):
    if "anyOf" in schema:
        errors = []
        for sub in schema["anyOf"]:
            try:
                return validate(value, sub, path)
            except ContractError as e:
                errors.append(str(e))
        raise ContractError(f"{path}: matches none of anyOf ({'; '.join(errors)})")
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_is(value, t) for t in types):
            raise ContractError(f"{path}: expected {dumps(schema['type'])}, got {_type_name(value)}")
    if "enum" in schema and not any(value == e and type(value) is type(e) for e in schema["enum"]):
        raise ContractError(f"{path}: {dumps(value)} is not one of {dumps(schema['enum'])}")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", 2**31 - 1):
            raise ContractError(f"{path}: length {len(value)} outside limits")
        if "pattern" in schema and not re.fullmatch(schema["pattern"], value, re.ASCII):
            raise ContractError(f"{path}: '{value}' does not match {schema['pattern']}")
    if _is(value, "number") and "minimum" in schema and value < schema["minimum"]:
        raise ContractError(f"{path}: {dumps(value)} below minimum")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", 2**31 - 1):
            raise ContractError(f"{path}: {len(value)} items outside limits")
        if "items" in schema:
            for i, v in enumerate(value):
                validate(v, schema["items"], f"{path}[{i}]")
    if isinstance(value, dict):
        for k in schema.get("required", []):
            if k not in value:
                raise ContractError(f"{path}: missing '{k}'")
        props = schema.get("properties", {})
        if schema.get("additionalProperties", True) is False:
            extra = sorted(set(value) - set(props))
            if extra:
                raise ContractError(f"{path}: unexpected {java_list(extra)}")
        for k, v in value.items():
            if k in props:
                validate(v, props[k], f"{path}.{k}")
    return value
