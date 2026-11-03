from __future__ import annotations

import unittest

from geophysagent.schema import SchemaValidationError, validate_json_schema


class SchemaTests(unittest.TestCase):
    def test_nested_objects_and_arrays_are_validated(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "pipeline": {
                    "type": "object",
                    "properties": {
                        "steps": {
                            "type": "array",
                            "minItems": 1,
                            "items": {
                                "type": "object",
                                "properties": {"operation": {"type": "string"}},
                                "required": ["operation"],
                                "additionalProperties": False,
                            },
                        }
                    },
                    "required": ["steps"],
                    "additionalProperties": False,
                }
            },
            "required": ["pipeline"],
            "additionalProperties": False,
        }
        validate_json_schema({"pipeline": {"steps": [{"operation": "sort"}]}}, schema)
        with self.assertRaises(SchemaValidationError) as raised:
            validate_json_schema({"pipeline": {"steps": [{"operation": 7}]}}, schema)
        self.assertIn("$.pipeline.steps[0].operation", str(raised.exception))

    def test_unsupported_keyword_is_never_silently_ignored(self) -> None:
        with self.assertRaises(SchemaValidationError) as raised:
            validate_json_schema("x", {"type": "string", "format": "email"})
        self.assertIn("unsupported JSON Schema keywords", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
