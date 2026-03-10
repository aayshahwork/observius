"""Unit tests for computeruse.validator.OutputValidator."""

from __future__ import annotations

import pytest

from computeruse.exceptions import ValidationError
from computeruse.validator import OutputValidator


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def validator() -> OutputValidator:
    return OutputValidator()


# ---------------------------------------------------------------------------
# validate_output — simple scalar types
# ---------------------------------------------------------------------------

class TestValidateSimpleTypes:
    def test_str_passthrough(self, validator: OutputValidator) -> None:
        result = validator.validate_output({"name": "Alice"}, {"name": "str"})
        assert result["name"] == "Alice"

    def test_int_passthrough(self, validator: OutputValidator) -> None:
        result = validator.validate_output({"count": 7}, {"count": "int"})
        assert result["count"] == 7
        assert isinstance(result["count"], int)

    def test_float_passthrough(self, validator: OutputValidator) -> None:
        result = validator.validate_output({"price": 9.99}, {"price": "float"})
        assert result["price"] == pytest.approx(9.99)

    def test_bool_passthrough(self, validator: OutputValidator) -> None:
        result = validator.validate_output({"active": True}, {"active": "bool"})
        assert result["active"] is True

    def test_list_passthrough(self, validator: OutputValidator) -> None:
        result = validator.validate_output({"items": [1, 2, 3]}, {"items": "list"})
        assert result["items"] == [1, 2, 3]

    def test_dict_passthrough(self, validator: OutputValidator) -> None:
        result = validator.validate_output({"meta": {"k": "v"}}, {"meta": "dict"})
        assert result["meta"] == {"k": "v"}

    def test_extra_keys_preserved(self, validator: OutputValidator) -> None:
        """Fields not in the schema pass through unchanged."""
        result = validator.validate_output(
            {"name": "Alice", "extra": "bonus"},
            {"name": "str"},
        )
        assert result["extra"] == "bonus"


# ---------------------------------------------------------------------------
# validate_output — type conversion
# ---------------------------------------------------------------------------

class TestTypeConversion:
    def test_string_to_int(self, validator: OutputValidator) -> None:
        result = validator.validate_output({"count": "42"}, {"count": "int"})
        assert result["count"] == 42
        assert isinstance(result["count"], int)

    def test_string_to_float(self, validator: OutputValidator) -> None:
        result = validator.validate_output({"price": "9.99"}, {"price": "float"})
        assert result["price"] == pytest.approx(9.99)

    def test_int_to_float(self, validator: OutputValidator) -> None:
        result = validator.validate_output({"rate": 3}, {"rate": "float"})
        assert result["rate"] == pytest.approx(3.0)

    def test_lossless_float_to_int(self, validator: OutputValidator) -> None:
        result = validator.validate_output({"n": 5.0}, {"n": "int"})
        assert result["n"] == 5

    def test_lossy_float_to_int_raises(self, validator: OutputValidator) -> None:
        with pytest.raises(ValidationError):
            validator.validate_output({"n": 3.7}, {"n": "int"})

    def test_string_true_to_bool(self, validator: OutputValidator) -> None:
        for truthy in ("true", "True", "TRUE", "1", "yes", "on"):
            result = validator.validate_output({"flag": truthy}, {"flag": "bool"})
            assert result["flag"] is True, f"Expected True for {truthy!r}"

    def test_string_false_to_bool(self, validator: OutputValidator) -> None:
        for falsy in ("false", "False", "FALSE", "0", "no", "off"):
            result = validator.validate_output({"flag": falsy}, {"flag": "bool"})
            assert result["flag"] is False, f"Expected False for {falsy!r}"

    def test_int_to_str(self, validator: OutputValidator) -> None:
        result = validator.validate_output({"label": 123}, {"label": "str"})
        assert result["label"] == "123"

    def test_json_string_to_list(self, validator: OutputValidator) -> None:
        result = validator.validate_output(
            {"tags": '["a", "b", "c"]'},
            {"tags": "list"},
        )
        assert result["tags"] == ["a", "b", "c"]

    def test_json_string_to_dict(self, validator: OutputValidator) -> None:
        result = validator.validate_output(
            {"meta": '{"key": "val"}'},
            {"meta": "dict"},
        )
        assert result["meta"] == {"key": "val"}


# ---------------------------------------------------------------------------
# validate_output — missing fields and bad types
# ---------------------------------------------------------------------------

class TestValidationErrors:
    def test_missing_required_field(self, validator: OutputValidator) -> None:
        with pytest.raises(ValidationError, match="missing"):
            validator.validate_output({}, {"name": "str"})

    def test_missing_field_message_includes_schema(self, validator: OutputValidator) -> None:
        with pytest.raises(ValidationError) as exc_info:
            validator.validate_output({"x": 1}, {"x": "int", "y": "str"})
        assert "y" in str(exc_info.value)

    def test_unknown_type_raises(self, validator: OutputValidator) -> None:
        with pytest.raises(ValidationError, match="Unknown type"):
            validator.validate_output({"x": 1}, {"x": "uuid"})

    def test_non_dict_output_raises(self, validator: OutputValidator) -> None:
        with pytest.raises(ValidationError):
            validator.validate_output("not a dict", {"field": "str"})  # type: ignore

    def test_ambiguous_int_bool_raises(self, validator: OutputValidator) -> None:
        """Integers other than 0/1 must not be silently coerced to bool."""
        with pytest.raises(ValidationError):
            validator.validate_output({"flag": 42}, {"flag": "bool"})


# ---------------------------------------------------------------------------
# validate_type — nested / parameterised types
# ---------------------------------------------------------------------------

class TestValidateType:
    def test_list_of_str(self, validator: OutputValidator) -> None:
        result = validator.validate_type([1, 2, 3], "list[str]")
        assert result == ["1", "2", "3"]

    def test_list_of_int(self, validator: OutputValidator) -> None:
        result = validator.validate_type(["10", "20"], "list[int]")
        assert result == [10, 20]

    def test_dict_str_int(self, validator: OutputValidator) -> None:
        result = validator.validate_type({"a": "1", "b": "2"}, "dict[str, int]")
        assert result == {"a": 1, "b": 2}

    def test_dict_value_type_only(self, validator: OutputValidator) -> None:
        """dict[float] is accepted as a shorthand for dict[str, float]."""
        result = validator.validate_type({"x": "3.14"}, "dict[float]")
        assert result == {"x": pytest.approx(3.14)}

    def test_nested_type_conversion_failure(self, validator: OutputValidator) -> None:
        with pytest.raises(ValidationError):
            validator.validate_type(["a", "b"], "list[int]")

    def test_case_insensitive_type_strings(self, validator: OutputValidator) -> None:
        assert validator.validate_type("hello", "STR") == "hello"
        assert validator.validate_type("42", "INT") == 42


# ---------------------------------------------------------------------------
# parse_llm_json
# ---------------------------------------------------------------------------

class TestParseLLMJson:
    def test_plain_json_object(self, validator: OutputValidator) -> None:
        result = validator.parse_llm_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_markdown_json_block(self, validator: OutputValidator) -> None:
        text = '```json\n{"price": 9.99, "currency": "USD"}\n```'
        result = validator.parse_llm_json(text)
        assert result == {"price": 9.99, "currency": "USD"}

    def test_markdown_block_without_language_hint(self, validator: OutputValidator) -> None:
        text = '```\n{"status": "ok"}\n```'
        result = validator.parse_llm_json(text)
        assert result == {"status": "ok"}

    def test_json_embedded_in_prose(self, validator: OutputValidator) -> None:
        text = 'Here is the result: {"score": 42} Hope that helps!'
        result = validator.parse_llm_json(text)
        assert result == {"score": 42}

    def test_empty_string_raises(self, validator: OutputValidator) -> None:
        with pytest.raises(ValueError, match="empty"):
            validator.parse_llm_json("")

    def test_no_json_in_text_raises(self, validator: OutputValidator) -> None:
        with pytest.raises(ValueError):
            validator.parse_llm_json("There is no JSON here at all.")

    def test_malformed_json_raises(self, validator: OutputValidator) -> None:
        with pytest.raises(ValueError):
            validator.parse_llm_json("{broken json: true,}")

    def test_returns_dict_not_list(self, validator: OutputValidator) -> None:
        """A JSON array at the top level should not be returned."""
        with pytest.raises(ValueError):
            validator.parse_llm_json('```json\n[1, 2, 3]\n```')

    def test_nested_object(self, validator: OutputValidator) -> None:
        text = '{"user": {"name": "Alice", "age": 30}, "active": true}'
        result = validator.parse_llm_json(text)
        assert result["user"]["name"] == "Alice"
        assert result["active"] is True


# ---------------------------------------------------------------------------
# format_schema
# ---------------------------------------------------------------------------

class TestFormatSchema:
    def test_single_field(self, validator: OutputValidator) -> None:
        assert validator.format_schema({"price": "float"}) == "price: float"

    def test_multiple_fields_preserves_order(self, validator: OutputValidator) -> None:
        schema = {"balance": "float", "status": "str", "active": "bool"}
        formatted = validator.format_schema(schema)
        assert formatted == "balance: float, status: str, active: bool"

    def test_empty_schema(self, validator: OutputValidator) -> None:
        assert validator.format_schema({}) == "(empty schema)"

    def test_nested_type_rendered_verbatim(self, validator: OutputValidator) -> None:
        formatted = validator.format_schema({"tags": "list[str]"})
        assert formatted == "tags: list[str]"
