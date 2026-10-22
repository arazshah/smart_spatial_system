"""
field_calculator.py

GeoChat SDK Plugin
==================

Plugin ID:
    field_calculator

Capability:
    - calculate_field:
        Add or overwrite a property on every feature from an expression such as
        ``population / geom_area * 1000000`` or ``upper(name)``.

Security:
    The expression is parsed with ``ast`` and evaluated by a small interpreter
    that only knows arithmetic, comparisons, boolean logic, ``a if c else b``,
    literals, feature property names and a fixed list of functions. There is no
    attribute access, subscripting, comprehension, lambda or import, and size
    guards stop pathological power/string expressions. It never uses eval().

Names available in an expression:
    - any property name that exists on at least one feature (missing values are null);
      use ``prop("name with spaces")`` for names that are not identifiers;
    - ``geom_area``, ``geom_length``, ``geom_x``, ``geom_y`` (centroid), derived from the
      geometry in planar coordinate units.

Null handling:
    Arithmetic and ordering comparisons with null give null. ``==``/``!=`` compare
    normally; ``coalesce`` and ``is_null`` handle nulls explicitly.
"""

from __future__ import annotations

import ast
import math
from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.plugin import auto_collect
from geochat_sdk.types.vector import VectorOut

from plugins._shared.vector_common import (
    check_limit,
    extract_features,
    get_shapely,
    load_config,
    make_vector_out,
    to_int,
    validate_choice,
)

PLUGIN_ID = "field_calculator"

MAX_EXPRESSION_CHARS = 1000
MAX_NODES = 200
MAX_STRING = 10_000
MAX_POW_EXPONENT = 100
DEFAULT_MAX_FEATURES = 200_000
GEOMETRY_NAMES = {"geom_area", "geom_length", "geom_x", "geom_y"}


class ExpressionError(ValueError):
    """Invalid or disallowed expression."""


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _fn_round(x, n=0):
    return None if x is None else round(x, int(n))


def _fn_coalesce(*values):
    for value in values:
        if value is not None:
            return value
    return None


def _null_safe(fn):
    def wrapped(*args):
        return None if any(a is None for a in args) else fn(*args)

    return wrapped


FUNCTIONS: dict[str, Any] = {
    "abs": _null_safe(abs),
    "round": _fn_round,
    "min": _null_safe(min),
    "max": _null_safe(max),
    "sqrt": _null_safe(math.sqrt),
    "log": _null_safe(math.log),
    "log10": _null_safe(math.log10),
    "exp": _null_safe(math.exp),
    "floor": _null_safe(math.floor),
    "ceil": _null_safe(math.ceil),
    "int": _null_safe(int),
    "float": _null_safe(float),
    "str": _null_safe(str),
    "len": _null_safe(len),
    "upper": _null_safe(lambda s: str(s).upper()),
    "lower": _null_safe(lambda s: str(s).lower()),
    "contains": _null_safe(lambda s, sub: str(sub) in str(s)),
    "coalesce": _fn_coalesce,
    "is_null": lambda x: x is None,
}

_BIN_OPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)
_CMP_OPS = (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn)


def compile_expression(expression: Any, known_names: set[str]) -> ast.Expression:
    """Parse and validate an expression; raises ExpressionError with a clear reason."""
    if not isinstance(expression, str) or not expression.strip():
        raise ExpressionError("expression must be a non-empty string.")
    if len(expression) > MAX_EXPRESSION_CHARS:
        raise ExpressionError(f"expression is longer than {MAX_EXPRESSION_CHARS} characters.")
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"expression syntax error: {exc.msg}.") from exc

    count = 0
    for node in ast.walk(tree):
        count += 1
        if count > MAX_NODES:
            raise ExpressionError(f"expression is too complex (more than {MAX_NODES} nodes).")
        if isinstance(node, (ast.Expression, ast.Load, ast.And, ast.Or, ast.Not, ast.UAdd,
                             ast.USub, ast.boolop, ast.operator, ast.cmpop, ast.unaryop)):
            if isinstance(node, ast.operator) and not isinstance(node, _BIN_OPS):
                raise ExpressionError(f"operator {type(node).__name__} is not allowed.")
            if isinstance(node, ast.cmpop) and not isinstance(node, _CMP_OPS):
                raise ExpressionError(f"operator {type(node).__name__} is not allowed.")
        elif isinstance(node, (ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp)):
            continue
        elif isinstance(node, (ast.List, ast.Tuple)):
            continue
        elif isinstance(node, ast.Constant):
            if not isinstance(node.value, (int, float, str, bool, type(None))):
                raise ExpressionError("only number, string, boolean and null literals are allowed.")
        elif isinstance(node, ast.Name):
            if node.id in FUNCTIONS or node.id == "prop":
                continue
            if node.id not in known_names and node.id not in GEOMETRY_NAMES:
                raise ExpressionError(
                    f"unknown field '{node.id}'. Available: {sorted(known_names | GEOMETRY_NAMES)[:30]}"
                )
        elif isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.keywords:
                raise ExpressionError("only plain calls like round(x, 2) are allowed.")
            if node.func.id not in FUNCTIONS and node.func.id != "prop":
                raise ExpressionError(f"function '{node.func.id}' is not allowed.")
        else:
            raise ExpressionError(f"{type(node).__name__} is not allowed in expressions.")
    return tree


class _Evaluator:
    def __init__(self, properties: dict[str, Any], geometry_values: Any) -> None:
        self.properties = properties
        self._geometry_values = geometry_values
        self._geom_cache: dict[str, Any] | None = None

    def geom(self, name: str) -> Any:
        if self._geom_cache is None:
            self._geom_cache = self._geometry_values()
        return self._geom_cache.get(name)

    def visit(self, node: ast.AST) -> Any:  # noqa: C901 - flat dispatch is clearest here
        if isinstance(node, ast.Expression):
            return self.visit(node.body)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id in GEOMETRY_NAMES:
                return self.geom(node.id)
            return self.properties.get(node.id)
        if isinstance(node, (ast.List, ast.Tuple)):
            return [self.visit(e) for e in node.elts]
        if isinstance(node, ast.UnaryOp):
            operand = self.visit(node.operand)
            if isinstance(node.op, ast.Not):
                return not operand
            if operand is None:
                return None
            if not _number(operand):
                raise TypeError("unary +/- needs a number")
            return -operand if isinstance(node.op, ast.USub) else +operand
        if isinstance(node, ast.BoolOp):
            if isinstance(node.op, ast.And):
                result: Any = True
                for value in node.values:
                    result = self.visit(value)
                    if not result:
                        return result
                return result
            result = False
            for value in node.values:
                result = self.visit(value)
                if result:
                    return result
            return result
        if isinstance(node, ast.IfExp):
            return self.visit(node.body) if self.visit(node.test) else self.visit(node.orelse)
        if isinstance(node, ast.BinOp):
            return self._binop(node.op, self.visit(node.left), self.visit(node.right))
        if isinstance(node, ast.Compare):
            return self._compare(node)
        if isinstance(node, ast.Call):
            args = [self.visit(a) for a in node.args]
            if node.func.id == "prop":
                if len(args) != 1 or not isinstance(args[0], str):
                    raise TypeError("prop() takes one field-name string")
                return self.properties.get(args[0])
            return FUNCTIONS[node.func.id](*args)
        raise ExpressionError(f"{type(node).__name__} is not allowed in expressions.")

    @staticmethod
    def _binop(op: ast.AST, left: Any, right: Any) -> Any:
        if left is None or right is None:
            return None
        if isinstance(op, ast.Add):
            if isinstance(left, str) and isinstance(right, str):
                if len(left) + len(right) > MAX_STRING:
                    raise OverflowError("string too long")
                return left + right
            if _number(left) and _number(right):
                return left + right
            raise TypeError("+ needs two numbers or two strings")
        if not (_number(left) and _number(right)):
            if isinstance(op, ast.Mult) and isinstance(left, str) and isinstance(right, int) \
                    and not isinstance(right, bool):
                if right < 0 or len(left) * right > MAX_STRING:
                    raise OverflowError("string too long")
                return left * right
            raise TypeError("arithmetic needs numbers")
        if isinstance(op, ast.Sub):
            return left - right
        if isinstance(op, ast.Mult):
            return left * right
        if isinstance(op, ast.Div):
            return left / right
        if isinstance(op, ast.FloorDiv):
            return left // right
        if isinstance(op, ast.Mod):
            return left % right
        if isinstance(op, ast.Pow):
            if abs(right) > MAX_POW_EXPONENT:
                raise OverflowError("exponent too large")
            if isinstance(left, int) and isinstance(right, int) and right > 0 \
                    and abs(left).bit_length() * right > 256:
                raise OverflowError("result too large")
            return left**right
        raise ExpressionError(f"operator {type(op).__name__} is not allowed.")

    def _compare(self, node: ast.Compare) -> Any:
        left = self.visit(node.left)
        result: Any = True
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            right = self.visit(comparator)
            if isinstance(op, ast.Eq):
                outcome: Any = left == right
            elif isinstance(op, ast.NotEq):
                outcome = left != right
            elif isinstance(op, (ast.In, ast.NotIn)):
                if not isinstance(right, (list, str)):
                    raise TypeError("'in' needs a list or a string on the right")
                contained = (left in right) if left is not None else False
                outcome = contained if isinstance(op, ast.In) else not contained
            elif left is None or right is None:
                return None
            else:
                if not ((_number(left) and _number(right))
                        or (isinstance(left, str) and isinstance(right, str))):
                    raise TypeError("ordering needs two numbers or two strings")
                outcome = {ast.Lt: left < right, ast.LtE: left <= right,
                           ast.Gt: left > right, ast.GtE: left >= right}[type(op)]
            if not outcome:
                return False
            result = outcome
            left = right
        return result


def _geometry_values_for(geometry: dict[str, Any] | None):
    def compute() -> dict[str, Any]:
        if not geometry:
            return {name: None for name in GEOMETRY_NAMES}
        shape, _, _ = get_shapely(PLUGIN_ID)
        geom = shape(geometry)
        centroid = geom.centroid
        return {
            "geom_area": geom.area,
            "geom_length": geom.length,
            "geom_x": None if centroid.is_empty else centroid.x,
            "geom_y": None if centroid.is_empty else centroid.y,
        }

    return compute


@capability(
    name="calculate_field",
    keywords=[
        "field calculator", "calculate field", "computed column", "new attribute", "expression",
        "add column", "derived field", "density", "ratio",
        "محاسبه فیلد", "ستون جدید", "فیلد محاسباتی", "عبارت", "افزودن ستون", "تراکم", "نسبت",
    ],
    description=(
        "Add or overwrite a property on every feature from a safe expression over its "
        "properties and geometry measures (arithmetic, comparisons, if/else, round, "
        "min/max, sqrt, coalesce, upper/lower...)."
    ),
    required_inputs=["features", "field", "expression"],
    optional_inputs=["on_error", "overwrite", "metadata"],
    output_kind="vector",
    permissions=[],
    metadata={
        "category": "analysis",
        "data_type": "vector",
        "operation": "calculate_field",
        "returns": "VectorOut",
        "artifact_kind": "features",
        "access_scope": "attribute_analysis",
        "config_aware": True,
        "routable": True,
    },
)
def calculate_field(
    features: Any,
    field: str,
    expression: str,
    on_error: str = "null",
    overwrite: bool = True,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Compute ``field`` for every feature.

    Args:
        features: Input features.
        field: Name of the property to create or overwrite.
        expression: Expression, e.g. ``round(population / geom_area * 1e6, 1)``.
        on_error: ``null`` stores null where the expression fails for a feature (for
            example division by zero); ``raise`` aborts on the first failure.
        overwrite: If False, an existing property of that name is an error.
        metadata: Extra output metadata.

    Returns:
        VectorOut with the same geometries and the new property.
    """
    config = load_config(PLUGIN_ID)
    if not isinstance(field, str) or not field.strip():
        raise ValueError("field must be a non-empty string.")
    mode = validate_choice(on_error, {"null", "raise"}, "on_error")
    input_features, source_info = extract_features(features)
    check_limit(
        len(input_features),
        to_int(config.get("max_features", DEFAULT_MAX_FEATURES), "max_features"),
        "input feature count",
    )

    known: set[str] = set()
    for feature in input_features:
        known.update(str(k) for k in (feature.get("properties") or {}))
    tree = compile_expression(expression, known)

    output: list[dict[str, Any]] = []
    errors = 0
    for index, feature in enumerate(input_features):
        properties = dict(feature.get("properties") or {})
        if field in properties and not overwrite:
            raise ValueError(f"Property '{field}' already exists and overwrite is False.")
        evaluator = _Evaluator(properties, _geometry_values_for(feature.get("geometry")))
        try:
            value = evaluator.visit(tree)
            if isinstance(value, float) and not math.isfinite(value):
                value = None
        except (ArithmeticError, TypeError, ValueError, OverflowError) as exc:
            if mode == "raise":
                raise ValueError(f"Feature {index}: {type(exc).__name__}: {exc}") from exc
            errors += 1
            value = None
        properties[field] = value
        output.append({"type": "Feature", "geometry": feature.get("geometry"),
                       "properties": properties})

    return make_vector_out(
        output,
        plugin_id=PLUGIN_ID,
        operation="calculate_field",
        source_info=source_info,
        extra={"field": field, "expression": expression, "error_count": errors},
        user_metadata=metadata,
    )


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="Field Calculator",
    description="Computes a new attribute from a safe expression over properties and geometry.",
    author="GeoChat Platform Team",
    permissions=[],
)
