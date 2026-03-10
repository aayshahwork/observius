from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from computeruse.exceptions import ValidationError

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# All recognised leaf type tokens (case-insensitive during parsing).
_SCALAR_TYPES: frozenset[str] = frozenset({"str", "int", "float", "bool", "list", "dict"})

# Extracts the outer type name and the bracket contents from a parameterised
# type expression such as "list[str]" or "dict[str, int]".
_PARAMETERISED_RE = re.compile(r"^(list|dict)\[(.+)\]$", re.IGNORECASE)

# Locates a fenced markdown code block that contains a JSON object.
# Matches both ```json { … }``` and ``` { … }```.
_CODE_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)

# Locates the outermost JSON object in free-form text (greedy).
_BARE_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


# ---------------------------------------------------------------------------
# OutputValidator
# ---------------------------------------------------------------------------

class OutputValidator:
    """Validates and coerces LLM task output against a caller-supplied schema.

    The schema is a ``dict[str, str]`` mapping field names to type strings.
    Supported type strings:

    +--------------------------+-------------------------------------------+
    | Type string              | Python equivalent                         |
    +==========================+===========================================+
    | ``"str"``                | ``str``                                   |
    | ``"int"``                | ``int``                                   |
    | ``"float"``              | ``float``                                 |
    | ``"bool"``               | ``bool``                                  |
    | ``"list"``               | ``list`` (elements unchecked)             |
    | ``"dict"``               | ``dict`` (values unchecked)               |
    | ``"list[str]"``          | ``list[str]``                             |
    | ``"list[int]"``          | ``list[int]``                             |
    | ``"list[float]"``        | ``list[float]``                           |
    | ``"dict[str, int]"``     | ``dict[str, int]``                        |
    | ``"dict[str, float]"``   | ``dict[str, float]``                      |
    | ``"dict[str, str]"``     | ``dict[str, str]``                        |
    | ``"list[dict[str,str]]"``| ``list[dict[str, str]]``                  |
    | ``"dict[str,list[int]]"``| ``dict[str, list[int]]``                  |
    +--------------------------+-------------------------------------------+

    Quick example::

        validator = OutputValidator()

        result = validator.validate_output(
            {"price": "9.99", "tags": ["sale", "featured"], "active": "true"},
            {"price": "float", "tags": "list[str]", "active": "bool"},
        )
        # → {"price": 9.99, "tags": ["sale", "featured"], "active": True}
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate_output(
        self, output: Dict[str, Any], schema: Dict[str, str]
    ) -> Dict[str, Any]:
        """Validate and coerce *output* against *schema*.

        Every field declared in *schema* must be present in *output*.
        Values are coerced to the declared type where possible.  Extra keys
        not mentioned in the schema are passed through unchanged.

        Args:
            output: Raw output dict, typically obtained from
                    :meth:`parse_llm_json`.
            schema: Mapping of field names to type strings
                    (see class docstring for the supported grammar).

        Returns:
            A new dict with all original keys preserved and schema-declared
            values coerced to their target types.

        Raises:
            ValidationError: If *output* is not a dict, a required field is
                             absent, a type string is invalid, or a value
                             cannot be coerced.

        Examples::

            # Type coercion
            validator.validate_output({"n": "42"}, {"n": "int"})
            # → {"n": 42}

            # Parameterised list
            validator.validate_output({"scores": [1, 2, 3]}, {"scores": "list[int]"})
            # → {"scores": [1, 2, 3]}

            # Nested type
            validator.validate_output(
                {"rows": [{"k": "v"}]},
                {"rows": "list[dict[str, str]]"},
            )
            # → {"rows": [{"k": "v"}]}

            # Missing field → ValidationError
            validator.validate_output({}, {"price": "float"})
        """
        if not isinstance(output, dict):
            raise ValidationError(
                f"Expected a dict as task output, got {type(output).__name__!r}. "
                "The LLM response must be a JSON object, not an array or scalar."
            )

        validated: Dict[str, Any] = dict(output)

        for field, type_str in schema.items():
            if field not in output:
                raise ValidationError(
                    f"Required field {field!r} is missing from the task output.\n"
                    f"  Expected schema : {self.format_schema(schema)}\n"
                    f"  Fields received : {list(output.keys()) or '(none)'}"
                )

            try:
                validated[field] = self.validate_type(output[field], type_str)
            except ValidationError:
                raise
            except Exception as exc:
                raise ValidationError(
                    f"Field {field!r}: unexpected error while validating "
                    f"value {output[field]!r} as {type_str!r}: {exc}"
                ) from exc

        return validated

    def parse_llm_json(self, text: str) -> Dict[str, Any]:
        """Extract and parse a JSON object from free-form LLM output.

        Attempts extraction in this order:

        1. A fenced code block (``` json … ``` or ``` ` … ``` `).
        2. The first ``{…}`` substring found anywhere in the text.

        Args:
            text: Raw string returned by the LLM.

        Returns:
            The parsed JSON object as a Python ``dict``.

        Raises:
            ValueError: If no valid JSON object can be located or parsed.

        Examples::

            # Plain JSON
            validator.parse_llm_json('{"price": 9.99}')
            # → {"price": 9.99}

            # Markdown-fenced
            validator.parse_llm_json('```json\\n{"status": "ok"}\\n```')
            # → {"status": "ok"}

            # Embedded in prose
            validator.parse_llm_json('Here is the result: {"score": 42}')
            # → {"score": 42}
        """
        if not text or not text.strip():
            raise ValueError("Cannot parse JSON from an empty string")

        # 1. Fenced code block — highest confidence.
        code_match = _CODE_BLOCK_RE.search(text)
        if code_match:
            candidate = code_match.group(1)
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass  # fall through to bare-object search

        # 2. First bare ``{…}`` in the text.
        bare_match = _BARE_OBJECT_RE.search(text)
        if bare_match:
            candidate = bare_match.group(0)
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict):
                    return parsed
                raise ValueError(
                    f"JSON was parsed but is not an object "
                    f"(got {type(parsed).__name__!r}). "
                    "The LLM must return a JSON object, not an array or scalar."
                )
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Found a JSON-like substring but could not parse it: {exc}\n"
                    f"  Candidate: {candidate[:120]!r}"
                ) from exc

        raise ValueError(
            "No JSON object found in the LLM response.\n"
            f"  Response preview: {text[:200]!r}\n"
            "  Ensure the model is instructed to return a JSON object."
        )

    def validate_type(self, value: Any, type_str: str) -> Any:
        """Coerce *value* to the type described by *type_str*.

        The type string is parsed by :meth:`_parse_type_string` to determine
        the outer type and any parameter types.  Coercion is recursive for
        parameterised types so that every element of a ``list[T]`` or every
        value in a ``dict[str, T]`` is individually validated.

        Args:
            value:    The raw value to coerce.
            type_str: A type string from the schema (case-insensitive).

        Returns:
            The coerced value matching the requested type.

        Raises:
            ValidationError: If *type_str* is unknown or *value* cannot be
                             coerced to the requested type.

        Examples::

            validator.validate_type("42", "int")              # → 42
            validator.validate_type("9.99", "float")          # → 9.99
            validator.validate_type("true", "bool")           # → True
            validator.validate_type(1, "bool")                # → True
            validator.validate_type([1, 2], "list[int]")      # → [1, 2]
            validator.validate_type({"a": "1"}, "dict[str, int]")  # → {"a": 1}
            validator.validate_type(
                [{"k": "v"}], "list[dict[str, str]]"
            )                                                  # → [{"k": "v"}]
        """
        outer, params = self._parse_type_string(type_str)

        # --- Parameterised types ------------------------------------------
        if outer == "list" and params:
            return self._coerce_typed_list(value, item_type=params[0])

        if outer == "dict" and params:
            # params[0] = key type (always str in JSON — ignored for coercion)
            # params[1] = value type  (or params[0] when shorthand dict[T] used)
            value_type = params[1] if len(params) == 2 else params[0]
            return self._coerce_typed_dict(value, value_type=value_type)

        # --- Unparameterised / bare types ----------------------------------
        target_map: dict[str, type] = {
            "str": str, "int": int, "float": float,
            "bool": bool, "list": list, "dict": dict,
        }
        target = target_map[outer]

        if isinstance(value, target):
            # bool is a subclass of int in Python — guard against that.
            if target is int and isinstance(value, bool):
                raise ValidationError(
                    f"Cannot use bool {value!r} as int. "
                    "Use 0 or 1 explicitly if an integer is required."
                )
            return value

        try:
            if target is bool:
                return _coerce_bool(value)
            if target is int:
                return _coerce_int(value)
            if target is float:
                return _coerce_float(value)
            if target is str:
                return str(value)
            if target is list:
                return _coerce_bare_list(value)
            if target is dict:
                return _coerce_bare_dict(value)
        except (ValueError, TypeError) as exc:
            raise ValidationError(
                f"Cannot convert {value!r} ({type(value).__name__}) "
                f"to {type_str!r}: {exc}"
            ) from exc

        raise ValidationError(f"Unhandled type {type_str!r}")  # unreachable

    def _parse_type_string(self, type_str: str) -> Tuple[str, Optional[List[str]]]:
        """Parse a type string into its outer type and optional parameter list.

        Args:
            type_str: A type expression such as ``"str"``, ``"list[int]"``,
                      or ``"dict[str, list[float]]"``.

        Returns:
            A ``(outer, params)`` tuple where:

            * *outer* is the lowercase base type name (``"str"``, ``"list"``, …).
            * *params* is a list of parameter type strings when *type_str* is
              parameterised, or ``None`` for bare scalars and collections.

        Raises:
            ValidationError: If *type_str* is not a recognised type expression.

        Examples::

            _parse_type_string("str")
            # → ("str", None)

            _parse_type_string("list[int]")
            # → ("list", ["int"])

            _parse_type_string("dict[str, int]")
            # → ("dict", ["str", "int"])

            _parse_type_string("list[dict[str, str]]")
            # → ("list", ["dict[str, str]"])

            _parse_type_string("dict[str, list[float]]")
            # → ("dict", ["str", "list[float]"])
        """
        cleaned = type_str.strip().lower()

        # Bare scalar / collection
        if cleaned in _SCALAR_TYPES:
            return (cleaned, None)

        # Parameterised: list[…] or dict[…]
        match = _PARAMETERISED_RE.match(cleaned)
        if not match:
            supported = ", ".join(sorted(_SCALAR_TYPES))
            raise ValidationError(
                f"Unknown type expression {type_str!r}.\n"
                f"  Supported bare types : {supported}\n"
                f"  Parameterised forms  : list[T], dict[str, T], "
                f"list[dict[str, T]], dict[str, list[T]], …"
            )

        outer = match.group(1)
        inner = match.group(2).strip()
        params = _split_top_level(inner)

        # Validate that each parameter is itself a valid type.
        for param in params:
            self._parse_type_string(param)  # raises ValidationError if invalid

        return (outer, params)

    def format_schema(self, schema: Dict[str, str]) -> str:
        """Format *schema* as a compact string for inclusion in LLM prompts.

        Args:
            schema: Mapping of field names to type strings.

        Returns:
            A comma-separated ``"field: type"`` string, or ``"(empty schema)"``
            when *schema* is empty.

        Examples::

            validator.format_schema({"price": "float", "tags": "list[str]"})
            # → "price: float, tags: list[str]"

            validator.format_schema({})
            # → "(empty schema)"
        """
        if not schema:
            return "(empty schema)"
        return ", ".join(f"{field}: {type_str}" for field, type_str in schema.items())

    # ------------------------------------------------------------------
    # Private coercion helpers
    # ------------------------------------------------------------------

    def _coerce_typed_list(self, value: Any, item_type: str) -> List[Any]:
        """Coerce *value* to a list and validate every element as *item_type*.

        Empty lists are accepted without element validation.

        Args:
            value:     Value to coerce to a list.
            item_type: Type string that every element must satisfy.

        Returns:
            A new list with all elements coerced to *item_type*.

        Raises:
            ValidationError: If *value* is not list-like, or if any element
                             cannot be coerced (error includes the index).
        """
        raw = _coerce_bare_list(value)
        result: List[Any] = []
        for i, item in enumerate(raw):
            try:
                result.append(self.validate_type(item, item_type))
            except ValidationError as exc:
                raise ValidationError(
                    f"Element at index {i} of list[{item_type}] is invalid: {exc}"
                ) from exc
        return result

    def _coerce_typed_dict(self, value: Any, value_type: str) -> Dict[str, Any]:
        """Coerce *value* to a dict and validate every dict value as *value_type*.

        JSON object keys are always strings — key-type coercion is skipped.
        Empty dicts are accepted without value validation.

        Args:
            value:      Value to coerce to a dict.
            value_type: Type string that every dict value must satisfy.

        Returns:
            A new dict with all values coerced to *value_type*.

        Raises:
            ValidationError: If *value* is not dict-like, or if any value
                             cannot be coerced (error includes the key name).
        """
        raw = _coerce_bare_dict(value)
        result: Dict[str, Any] = {}
        for k, v in raw.items():
            try:
                result[k] = self.validate_type(v, value_type)
            except ValidationError as exc:
                raise ValidationError(
                    f"Value for key {k!r} in dict[str, {value_type}] is invalid: {exc}"
                ) from exc
        return result


# ---------------------------------------------------------------------------
# Module-level coercion helpers (kept outside the class so they can be
# unit-tested and called from models.py validation logic without
# instantiating OutputValidator)
# ---------------------------------------------------------------------------

def _coerce_bool(value: Any) -> bool:
    """Convert *value* to ``bool`` with strict literal matching.

    Accepted truthy values : ``True``, ``1``, ``"true"``, ``"1"``, ``"yes"``, ``"on"``
    Accepted falsy values  : ``False``, ``0``, ``"false"``, ``"0"``, ``"no"``, ``"off"``

    Raises:
        ValueError:  For strings that are not recognised boolean literals.
        TypeError:   For types that cannot be meaningfully converted.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        if value in (0, 1):
            return bool(value)
        raise ValueError(
            f"Integer {value!r} is ambiguous as bool. "
            "Only 0 (False) and 1 (True) are accepted."
        )
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes", "on"):
            return True
        if lowered in ("false", "0", "no", "off"):
            return False
        raise ValueError(
            f"String {value!r} is not a recognised boolean literal. "
            "Accepted: 'true'/'false', '1'/'0', 'yes'/'no', 'on'/'off'."
        )
    raise TypeError(
        f"Cannot convert {type(value).__name__!r} to bool. "
        "Provide a string or integer."
    )


def _coerce_int(value: Any) -> int:
    """Convert *value* to ``int``, rejecting lossy float conversion.

    ``1.0`` → ``1`` is allowed (lossless); ``1.5`` → ``int`` raises.

    Raises:
        ValueError: For non-numeric strings or floats with a fractional part.
    """
    if isinstance(value, bool):
        raise TypeError(
            f"Cannot use bool {value!r} as int. "
            "Use 0 or 1 explicitly."
        )
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError(
                f"Cannot losslessly convert {value!r} to int "
                "(fractional part would be discarded)."
            )
        return int(value)
    return int(value)  # raises ValueError for non-numeric strings


def _coerce_float(value: Any) -> float:
    """Convert *value* to ``float``.

    Raises:
        ValueError: For strings that are not numeric.
    """
    if isinstance(value, bool):
        raise TypeError(
            f"Cannot use bool {value!r} as float. "
            "Use 0.0 or 1.0 explicitly."
        )
    return float(value)


def _coerce_bare_list(value: Any) -> list:
    """Convert *value* to an untyped list, parsing JSON strings if needed.

    Raises:
        ValueError: If a string value is not a valid JSON array.
        TypeError:  If the value is neither a list nor a string.
    """
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass
        raise ValueError(
            f"String {value!r} cannot be parsed as a JSON array. "
            "Expected a value like '[1, 2, 3]'."
        )
    raise TypeError(
        f"Expected a list or JSON array string, got {type(value).__name__!r}."
    )


def _coerce_bare_dict(value: Any) -> dict:
    """Convert *value* to an untyped dict, parsing JSON strings if needed.

    Raises:
        ValueError: If a string value is not a valid JSON object.
        TypeError:  If the value is neither a dict nor a string.
    """
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
        raise ValueError(
            f"String {value!r} cannot be parsed as a JSON object. "
            "Expected a value like '{\"key\": \"val\"}'."
        )
    raise TypeError(
        f"Expected a dict or JSON object string, got {type(value).__name__!r}."
    )


def _split_top_level(s: str) -> List[str]:
    """Split *s* on commas that are not inside square brackets.

    Used to separate type parameters inside ``dict[…, …]`` without being
    confused by commas nested inside inner parameterised types such as
    ``dict[str, dict[str, int]]``.

    Args:
        s: The content inside the outermost ``[…]`` of a parameterised type.

    Returns:
        List of trimmed sub-strings after splitting on top-level commas.

    Examples::

        _split_top_level("str, int")              # → ["str", "int"]
        _split_top_level("str, dict[str, int]")   # → ["str", "dict[str, int]"]
        _split_top_level("str, list[int]")        # → ["str", "list[int]"]
    """
    parts: List[str] = []
    depth = 0
    current: List[str] = []
    for ch in s:
        if ch == "[":
            depth += 1
            current.append(ch)
        elif ch == "]":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current).strip())
    return [p for p in parts if p]
