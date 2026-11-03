from __future__ import annotations

import math
import re
from typing import Any


class SchemaValidationError(ValueError):
    """Raised when a value does not satisfy a supported JSON Schema contract."""


_TYPE_CHECKS = {
    "object": lambda value: isinstance(value, dict),
    "array": lambda value: isinstance(value, (list, tuple)),
    "string": lambda value: isinstance(value, str),
    "number": lambda value: (
        (isinstance(value, int) and not isinstance(value, bool))
        or (isinstance(value, float) and math.isfinite(value))
    ),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "boolean": lambda value: isinstance(value, bool),
    "null": lambda value: value is None,
}


def validate_json_schema(
    value: Any,
    schema: dict[str, Any],
    *,
    subject: str = "value",
) -> None:
    """Validate the JSON Schema subset used by GeoPhysAgent public contracts.

    The validator deliberately rejects unknown schema keywords instead of silently
    pretending to enforce them. This keeps the dependency-free v0.1 runtime honest
    while supporting nested objects/arrays, composition, enums and scalar limits.
    """

    if not isinstance(schema, dict):
        raise SchemaValidationError(f"{subject}: schema must be an object")
    _validate_schema_keywords(schema, subject)
    _validate(value, schema, path="$", subject=subject)


def validate_json_schema_definition(schema: dict[str, Any], *, subject: str = "schema") -> None:
    """Reject unsupported or structurally malformed schema contracts at registration time."""

    if not isinstance(schema, dict):
        raise SchemaValidationError(f"{subject}: schema must be an object")

    def visit(current: dict[str, Any], path: str) -> None:
        _validate_schema_keywords(current, subject)
        expected = current.get("type")
        if expected is not None:
            types = [expected] if isinstance(expected, str) else expected
            if (
                not isinstance(types, list)
                or not types
                or any(not isinstance(item, str) or item not in _TYPE_CHECKS for item in types)
            ):
                raise SchemaValidationError(f"{subject}: {path} has an invalid type declaration")
        properties = current.get("properties", {})
        if not isinstance(properties, dict):
            raise SchemaValidationError(f"{subject}: {path} properties must be an object")
        for name, child in properties.items():
            if not isinstance(name, str) or not isinstance(child, dict):
                raise SchemaValidationError(
                    f"{subject}: {path} contains an invalid property schema"
                )
            visit(child, f"{path}.properties.{name}")
        required = current.get("required", [])
        if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
            raise SchemaValidationError(f"{subject}: {path} required must be a string array")
        additional = current.get("additionalProperties", True)
        if not isinstance(additional, (bool, dict)):
            raise SchemaValidationError(
                f"{subject}: {path} additionalProperties must be boolean or a schema"
            )
        if isinstance(additional, dict):
            visit(additional, f"{path}.additionalProperties")
        items = current.get("items")
        if items is not None:
            if not isinstance(items, dict):
                raise SchemaValidationError(f"{subject}: {path} items must be a schema")
            visit(items, f"{path}.items")
        for keyword in ("allOf", "anyOf", "oneOf"):
            if keyword in current:
                for index, child in enumerate(
                    _schema_array(current[keyword], subject, path, keyword)
                ):
                    visit(child, f"{path}.{keyword}[{index}]")

    visit(schema, "$")


def _validate(value: Any, schema: dict[str, Any], *, path: str, subject: str) -> None:
    if "const" in schema and value != schema["const"]:
        _fail(subject, path, f"must equal {schema['const']!r}")
    if "enum" in schema:
        enum = schema["enum"]
        if not isinstance(enum, list) or not enum:
            raise SchemaValidationError(f"{subject}: {path} enum must be a non-empty array")
        if value not in enum:
            _fail(subject, path, "is not one of the allowed values")

    if "allOf" in schema:
        for branch in _schema_array(schema["allOf"], subject, path, "allOf"):
            _validate(value, branch, path=path, subject=subject)
    if "anyOf" in schema:
        branches = _schema_array(schema["anyOf"], subject, path, "anyOf")
        if not any(_matches(value, branch, path, subject) for branch in branches):
            _fail(subject, path, "does not match any allowed schema")
    if "oneOf" in schema:
        branches = _schema_array(schema["oneOf"], subject, path, "oneOf")
        if sum(_matches(value, branch, path, subject) for branch in branches) != 1:
            _fail(subject, path, "must match exactly one allowed schema")

    expected = schema.get("type")
    if expected is not None:
        types = [expected] if isinstance(expected, str) else expected
        if (
            not isinstance(types, list)
            or not types
            or any(not isinstance(item, str) for item in types)
        ):
            raise SchemaValidationError(f"{subject}: {path} type must be a string or string array")
        unknown = [item for item in types if item not in _TYPE_CHECKS]
        if unknown:
            raise SchemaValidationError(
                f"{subject}: {path} uses unsupported JSON types: {', '.join(unknown)}"
            )
        if not any(_TYPE_CHECKS[item](value) for item in types):
            _fail(subject, path, f"must be of type {' or '.join(types)}")
            return

    if isinstance(value, dict):
        _validate_object(value, schema, path=path, subject=subject)
    elif isinstance(value, (list, tuple)):
        _validate_array(value, schema, path=path, subject=subject)
    elif isinstance(value, str):
        _validate_string(value, schema, path=path, subject=subject)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        _validate_number(value, schema, path=path, subject=subject)


def _validate_object(
    value: dict[str, Any], schema: dict[str, Any], *, path: str, subject: str
) -> None:
    required = schema.get("required", [])
    if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
        raise SchemaValidationError(f"{subject}: {path} required must be a string array")
    missing = [name for name in required if name not in value]
    if missing:
        _fail(subject, path, f"is missing required properties: {', '.join(missing)}")

    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        raise SchemaValidationError(f"{subject}: {path} properties must be an object")
    additional = schema.get("additionalProperties", True)
    for name, item in value.items():
        child_path = f"{path}.{name}"
        if name in properties:
            rule = properties[name]
            if not isinstance(rule, dict):
                raise SchemaValidationError(f"{subject}: schema for {child_path} must be an object")
            _validate_schema_keywords(rule, subject)
            _validate(item, rule, path=child_path, subject=subject)
        elif additional is False:
            _fail(subject, child_path, "is not an allowed property")
        elif isinstance(additional, dict):
            _validate_schema_keywords(additional, subject)
            _validate(item, additional, path=child_path, subject=subject)

    count = len(value)
    if "minProperties" in schema and count < schema["minProperties"]:
        _fail(subject, path, f"must contain at least {schema['minProperties']} properties")
    if "maxProperties" in schema and count > schema["maxProperties"]:
        _fail(subject, path, f"must contain at most {schema['maxProperties']} properties")


def _validate_array(
    value: list[Any] | tuple[Any, ...],
    schema: dict[str, Any],
    *,
    path: str,
    subject: str,
) -> None:
    if "minItems" in schema and len(value) < schema["minItems"]:
        _fail(subject, path, f"must contain at least {schema['minItems']} items")
    if "maxItems" in schema and len(value) > schema["maxItems"]:
        _fail(subject, path, f"must contain at most {schema['maxItems']} items")
    if schema.get("uniqueItems") is True:
        for index, item in enumerate(value):
            if item in value[:index]:
                _fail(subject, f"{path}[{index}]", "must be unique")
    items = schema.get("items")
    if items is not None:
        if not isinstance(items, dict):
            raise SchemaValidationError(f"{subject}: {path} items must be an object")
        _validate_schema_keywords(items, subject)
        for index, item in enumerate(value):
            _validate(item, items, path=f"{path}[{index}]", subject=subject)


def _validate_string(value: str, schema: dict[str, Any], *, path: str, subject: str) -> None:
    if "minLength" in schema and len(value) < schema["minLength"]:
        _fail(subject, path, f"must contain at least {schema['minLength']} characters")
    if "maxLength" in schema and len(value) > schema["maxLength"]:
        _fail(subject, path, f"must contain at most {schema['maxLength']} characters")
    if "pattern" in schema:
        try:
            matched = re.search(schema["pattern"], value)
        except (TypeError, re.error) as exc:
            raise SchemaValidationError(f"{subject}: invalid pattern at {path}: {exc}") from exc
        if matched is None:
            _fail(subject, path, f"must match pattern {schema['pattern']!r}")


def _validate_number(
    value: int | float, schema: dict[str, Any], *, path: str, subject: str
) -> None:
    if "minimum" in schema and value < schema["minimum"]:
        _fail(subject, path, f"must be >= {schema['minimum']}")
    if "maximum" in schema and value > schema["maximum"]:
        _fail(subject, path, f"must be <= {schema['maximum']}")
    if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
        _fail(subject, path, f"must be > {schema['exclusiveMinimum']}")
    if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
        _fail(subject, path, f"must be < {schema['exclusiveMaximum']}")


def _matches(value: Any, schema: dict[str, Any], path: str, subject: str) -> bool:
    try:
        _validate_schema_keywords(schema, subject)
        _validate(value, schema, path=path, subject=subject)
        return True
    except SchemaValidationError:
        return False


def _schema_array(value: Any, subject: str, path: str, keyword: str) -> list[dict[str, Any]]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, dict) for item in value)
    ):
        raise SchemaValidationError(
            f"{subject}: {path} {keyword} must be a non-empty array of schemas"
        )
    for branch in value:
        _validate_schema_keywords(branch, subject)
    return value


_SUPPORTED_KEYWORDS = {
    "$schema",
    "$id",
    "title",
    "description",
    "type",
    "const",
    "enum",
    "properties",
    "required",
    "additionalProperties",
    "items",
    "minItems",
    "maxItems",
    "uniqueItems",
    "minLength",
    "maxLength",
    "pattern",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minProperties",
    "maxProperties",
    "allOf",
    "anyOf",
    "oneOf",
}


def _validate_schema_keywords(schema: dict[str, Any], subject: str) -> None:
    unsupported = sorted(set(schema) - _SUPPORTED_KEYWORDS)
    if unsupported:
        raise SchemaValidationError(
            f"{subject}: unsupported JSON Schema keywords: {', '.join(unsupported)}"
        )


def _fail(subject: str, path: str, message: str) -> None:
    raise SchemaValidationError(f"{subject}: {path} {message}")
