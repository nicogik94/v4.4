"""A small evaluator for the GitHub Actions expression subset used by `evals.yml`.

The authorization tests must prove what the workflow *actually says*, not what a
restatement of it says.  So they parse the real `if:` expression out of the YAML
and evaluate it against a synthetic event context.  If someone later loosens a
guard in the workflow, the matrix fails -- which a hand-written mirror of the
same logic could never catch.

Only the constructs `evals.yml` uses are supported, and anything unsupported
raises rather than being guessed at: a silently mis-parsed guard would be worse
than no test at all.
"""

from __future__ import annotations

import re
from typing import Any


class ExpressionError(Exception):
    pass


_TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<string>'(?:[^']|'')*')
  | (?P<op>==|!=|&&|\|\||!|\(|\)|,)
  | (?P<path>[A-Za-z_][A-Za-z0-9_-]*(?:\.(?:\*|[A-Za-z_][A-Za-z0-9_-]*))*)
  | (?P<number>-?\d+(?:\.\d+)?)
    """,
    re.VERBOSE,
)


def _tokenize(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    position = 0
    while position < len(text):
        match = _TOKEN_RE.match(text, position)
        if not match:
            raise ExpressionError(f"unparsable at offset {position}: {text[position:position + 30]!r}")
        position = match.end()
        kind = match.lastgroup
        if kind == "ws":
            continue
        tokens.append((kind, match.group()))
    return tokens


_MISSING = object()


class _Parser:
    def __init__(self, tokens: list[tuple[str, str]], context: dict):
        self.tokens = tokens
        self.index = 0
        self.context = context

    def peek(self) -> tuple[str, str] | None:
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def take(self) -> tuple[str, str]:
        token = self.peek()
        if token is None:
            raise ExpressionError("unexpected end of expression")
        self.index += 1
        return token

    def expect(self, value: str) -> None:
        kind, text = self.take()
        if text != value:
            raise ExpressionError(f"expected {value!r}, got {text!r}")

    # or := and ('||' and)*
    def parse_or(self) -> Any:
        value = self.parse_and()
        while (token := self.peek()) and token[1] == "||":
            self.take()
            right = self.parse_and()
            value = _truthy(value) or _truthy(right)
        return value

    # and := unary ('&&' unary)*
    def parse_and(self) -> Any:
        value = self.parse_comparison()
        while (token := self.peek()) and token[1] == "&&":
            self.take()
            right = self.parse_comparison()
            value = _truthy(value) and _truthy(right)
        return value

    # comparison := unary (('=='|'!=') unary)?
    def parse_comparison(self) -> Any:
        left = self.parse_unary()
        token = self.peek()
        if token and token[1] in ("==", "!="):
            self.take()
            right = self.parse_unary()
            equal = _equals(left, right)
            return equal if token[1] == "==" else not equal
        return left

    def parse_unary(self) -> Any:
        token = self.peek()
        if token and token[1] == "!":
            self.take()
            return not _truthy(self.parse_unary())
        return self.parse_primary()

    def parse_primary(self) -> Any:
        kind, text = self.take()
        if text == "(":
            value = self.parse_or()
            self.expect(")")
            return value
        if kind == "string":
            return text[1:-1].replace("''", "'")
        if kind == "number":
            return float(text) if "." in text else int(text)
        if kind == "path":
            token = self.peek()
            if token and token[1] == "(":
                return self.parse_call(text)
            if text == "true":
                return True
            if text == "false":
                return False
            if text == "null":
                return None
            return self.lookup(text)
        raise ExpressionError(f"unexpected token {text!r}")

    def parse_call(self, name: str) -> Any:
        self.expect("(")
        args: list[Any] = []
        if (token := self.peek()) and token[1] != ")":
            args.append(self.parse_or())
            while (token := self.peek()) and token[1] == ",":
                self.take()
                args.append(self.parse_or())
        self.expect(")")
        if name == "contains":
            if len(args) != 2:
                raise ExpressionError("contains() takes 2 arguments")
            haystack, needle = args
            if isinstance(haystack, (list, tuple)):
                return any(_equals(item, needle) for item in haystack)
            return str(needle) in str(haystack)
        if name == "always":
            return True
        if name == "success":
            return bool(self.context.get("__success__", True))
        raise ExpressionError(f"unsupported function {name!r}")

    def lookup(self, path: str) -> Any:
        current: Any = self.context
        for part in path.split("."):
            if part == "*":
                if not isinstance(current, (list, tuple)):
                    return _MISSING
                current = _Star(current)
                continue
            if isinstance(current, _Star):
                current = [_get(item, part) for item in current.items]
                continue
            current = _get(current, part)
            if current is _MISSING:
                return _MISSING
        if isinstance(current, _Star):
            return list(current.items)
        return current


class _Star:
    def __init__(self, items):
        self.items = items


def _get(container: Any, name: str) -> Any:
    if isinstance(container, dict):
        return container.get(name, _MISSING)
    return _MISSING


def _truthy(value: Any) -> bool:
    if value is _MISSING or value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value != ""
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, (list, tuple, dict)):
        return True
    return bool(value)


def _equals(left: Any, right: Any) -> bool:
    """GitHub compares loosely; the only coercion needed here is missing/null.

    A missing context value never equals a string, which is what makes
    `github.event.inputs.provider_gate == 'gate_a_anthropic_primary'` false on a
    `pull_request` event where `inputs` does not exist at all.
    """

    if left is _MISSING:
        left = None
    if right is _MISSING:
        right = None
    if isinstance(left, bool) or isinstance(right, bool):
        return left is right or left == right
    return left == right


def evaluate(expression: str, context: dict) -> bool:
    """Evaluate a `${{ ... }}` expression (wrapper optional) to a boolean."""

    text = expression.strip()
    if text.startswith("${{") and text.endswith("}}"):
        text = text[3:-2].strip()
    parser = _Parser(_tokenize(text), context)
    value = parser.parse_or()
    if parser.peek() is not None:
        raise ExpressionError(f"trailing tokens from {parser.peek()!r}")
    return _truthy(value)


def pull_request_context(
    *,
    draft: bool,
    labels: list[str],
    event_name: str = "pull_request",
    action: str = "labeled",
) -> dict:
    """A pull_request event context.

    `action` defaults to `labeled` -- the moment a human explicitly authorizes
    spend. Tests that care about ordinary pushes pass `synchronize`.
    """

    return {
        "github": {
            "event_name": event_name,
            "event": {
                "action": action,
                "pull_request": {
                    "draft": draft,
                    "labels": [{"name": name} for name in labels],
                },
            },
        },
    }


def dispatch_context(*, provider_gate: str | None, confirm: str | None) -> dict:
    inputs: dict[str, Any] = {}
    if provider_gate is not None:
        inputs["provider_gate"] = provider_gate
    if confirm is not None:
        inputs["confirm_paid_execution"] = confirm
    return {
        "github": {
            "event_name": "workflow_dispatch",
            "event": {"inputs": inputs},
            "inputs": inputs,
        },
    }


def with_needs(context: dict, **results: str) -> dict:
    merged = dict(context)
    merged["needs"] = {
        name.replace("_", "-"): {"result": result} for name, result in results.items()
    }
    return merged
