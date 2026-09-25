"""
The contract between stages: what each stage must hand the next.

Agents hand over STRUCTURED output, validated here, never free text parsed by
hope. A stage whose output fails validation fails the stage - it is not passed
on "mostly right". The same schemas are given to the model (Agent SDK
output_format), so the model is steered by them AND checked against them.
"""

# The only changes this pipeline can ever make. Anything else is refused at the
# gate, whatever an agent proposes: least privilege for the pipeline itself.
ALLOWED_ACTIONS = ("update_config", "add_ticket_comment", "none")
CONFIG_KEYS = ("ingest.max_concurrent_jobs", "ingest.rush_slide_limit",
               "alerts.ingest_latency_minutes", "viewer.overlay_calibration_um")

PROPOSAL = {
    "type": "object",
    "additionalProperties": False,
    "required": ["diagnosis", "evidence", "confidence", "proposed_change"],
    "properties": {
        "diagnosis": {"type": "string", "minLength": 20, "maxLength": 2000,
                      "description": "At most 5 sentences."},
        "evidence": {"type": "array", "minItems": 1, "maxItems": 12,
                     "description": "Required. 3-6 items, each naming its source (ticket id, or config key and value).",
                     "items": {"type": "string", "maxLength": 400}},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "risks": {"type": "array", "maxItems": 6, "description": "At most 6 short items.",
                  "items": {"type": "string", "maxLength": 300}},
        "proposed_change": {
            "type": "object",
            "additionalProperties": False,
            "required": ["action"],
            "properties": {
                "action": {"type": "string", "enum": list(ALLOWED_ACTIONS)},
                "key": {"type": "string", "enum": list(CONFIG_KEYS)},
                "value": {"anyOf": [{"type": "number"}, {"type": "string"}, {"type": "boolean"}]},
                "expected_version": {"type": "integer", "minimum": 1},
                "ticket_id": {"type": "string", "pattern": "^T-\\d{4}$"},
                "comment": {"type": "string", "maxLength": 1000},
            },
        },
    },
}

VERDICT = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "checks", "reasons"],
    "properties": {
        "verdict": {"type": "string", "enum": ["approve", "revise", "block"]},
        "checks": {"type": "array", "minItems": 1, "maxItems": 12,
                   "description": "One item per claim you re-checked. verified and source belong inside each item.",
                   "items": {
            "type": "object", "additionalProperties": False, "required": ["claim", "verified"],
            "properties": {"claim": {"type": "string", "maxLength": 300},
                           "verified": {"type": "boolean"},
                           "source": {"type": "string", "maxLength": 200}}}},
        "reasons": {"type": "array", "minItems": 1, "maxItems": 6, "items": {"type": "string", "maxLength": 400}},
        "safer_alternative": {"type": "string", "maxLength": 400},
    },
}

class ContractError(ValueError):
    pass

_TYPES = {"object": dict, "array": list, "string": str, "integer": int, "number": (int, float), "boolean": bool}

def validate(value, schema, path="$"):
    """Enough of JSON Schema for these contracts: type, enum, required,
    additionalProperties, min/max length and items, minimum, pattern."""
    import re
    if "anyOf" in schema:
        errors = []
        for sub in schema["anyOf"]:
            try:
                return validate(value, sub, path)
            except ContractError as e:
                errors.append(str(e))
        raise ContractError(f"{path}: matches none of anyOf ({'; '.join(errors)})")
    t = schema.get("type")
    if t:
        types = t if isinstance(t, list) else [t]
        ok = any(isinstance(value, _TYPES[x]) and not (x in ("integer", "number") and isinstance(value, bool))
                 for x in types)
        if not ok:
            raise ContractError(f"{path}: expected {t}, got {type(value).__name__}")
    if "enum" in schema and value not in schema["enum"]:
        raise ContractError(f"{path}: {value!r} is not one of {schema['enum']}")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", 10**9):
            raise ContractError(f"{path}: length {len(value)} outside limits")
        if "pattern" in schema and not re.fullmatch(schema["pattern"], value):
            raise ContractError(f"{path}: {value!r} does not match {schema['pattern']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value < schema.get("minimum", float("-inf")):
        raise ContractError(f"{path}: {value} below minimum")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", 10**9):
            raise ContractError(f"{path}: {len(value)} items outside limits")
        for i, v in enumerate(value):
            validate(v, schema.get("items", {}), f"{path}[{i}]")
    if isinstance(value, dict):
        for k in schema.get("required", []):
            if k not in value:
                raise ContractError(f"{path}: missing {k!r}")
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extra = set(value) - set(props)
            if extra:
                raise ContractError(f"{path}: unexpected {sorted(extra)}")
        for k, v in value.items():
            if k in props:
                validate(v, props[k], f"{path}.{k}")
    return value

def check_change(change):
    """Semantic checks the schema can't express: each action's required fields."""
    a = change["action"]
    need = {"update_config": ("key", "value", "expected_version"),
            "add_ticket_comment": ("ticket_id", "comment"), "none": ()}[a]
    missing = [f for f in need if f not in change]
    if missing:
        raise ContractError(f"$.proposed_change: {a} needs {missing}")
    return change
