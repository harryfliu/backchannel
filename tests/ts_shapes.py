"""Validate JSON against the interfaces in web/src/api/types.ts.

A small parser for the subset of TypeScript used there: interfaces, type
aliases, unions, string literals, arrays, inline object types, optional
fields and references. `validate(value, "Conversation")` returns a list of
problems (empty when the value matches).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TYPES_TS = Path(__file__).resolve().parent.parent / "web" / "src" / "api" / "types.ts"

_TOKEN = re.compile(r"\s*(?:(\n)|('(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\")|([A-Za-z_$][\w$]*)|(\[\]|=>|[{}()<>\[\]|;:?,=&]))")


@dataclass
class Node:
    kind: str  # prim | lit | union | array | obj | ref | any
    value: Any = None


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


def _tokens(src: str) -> list[str]:
    out: list[str] = []
    pos = 0
    while pos < len(src):
        m = _TOKEN.match(src, pos)
        if not m or m.end() == pos:
            if src[pos:].strip() == "":
                break
            raise SyntaxError(f"can't tokenize near {src[pos:pos + 30]!r}")
        pos = m.end()
        newline, string, ident, punct = m.groups()
        if newline:
            out.append("\n")
        elif string:
            out.append(string)
        elif ident:
            out.append(ident)
        elif punct:
            out.append(punct)
    return out


class _Parser:
    def __init__(self, tokens: list[str]):
        self.t = tokens
        self.i = 0

    def peek(self, skip_nl: bool = True) -> str | None:
        j = self.i
        while skip_nl and j < len(self.t) and self.t[j] == "\n":
            j += 1
        return self.t[j] if j < len(self.t) else None

    def next(self, skip_nl: bool = True) -> str:
        while skip_nl and self.i < len(self.t) and self.t[self.i] == "\n":
            self.i += 1
        tok = self.t[self.i]
        self.i += 1
        return tok

    def expect(self, tok: str) -> None:
        got = self.next()
        if got != tok:
            raise SyntaxError(f"expected {tok!r}, got {got!r}")

    def declarations(self) -> dict[str, Node]:
        decls: dict[str, Node] = {}
        while self.peek() is not None:
            tok = self.next()
            if tok != "export":
                continue
            kind = self.next()
            name = self.next()
            if kind == "interface":
                body = self.object_body()
                if name != "Api":
                    decls[name] = body
            elif kind == "type":
                self.expect("=")
                decls[name] = self.type_expr(member=False)
        return decls

    def object_body(self) -> Node:
        self.expect("{")
        fields: dict[str, tuple[Node, bool]] = {}
        while True:
            tok = self.peek()
            if tok == "}":
                self.next()
                break
            if tok in (";", ","):
                self.next()
                continue
            name = self.next()
            if self.peek() == "(":  # method signature: skip to end of line
                while self.peek(skip_nl=False) not in ("\n", ";", "}"):
                    self.next(skip_nl=False)
                continue
            optional = False
            if self.peek() == "?":
                self.next()
                optional = True
            self.expect(":")
            fields[name] = (self.type_expr(member=True), optional)
        return Node("obj", fields)

    def type_expr(self, member: bool) -> Node:
        options = []
        if self.peek() == "|":
            self.next()
        options.append(self.postfix())
        while True:
            if member:
                # In a member, a newline ends the type unless the next line continues the union.
                j = self.i
                while j < len(self.t) and self.t[j] == "\n":
                    j += 1
                if j >= len(self.t) or self.t[j] != "|":
                    break
            elif self.peek() != "|":
                break
            self.next()
            options.append(self.postfix())
        return options[0] if len(options) == 1 else Node("union", options)

    def postfix(self) -> Node:
        node = self.primary()
        while self.peek(skip_nl=False) == "[]":
            self.next(skip_nl=False)
            node = Node("array", node)
        return node

    def primary(self) -> Node:
        tok = self.next()
        if tok == "{":
            self.i -= 1
            return self.object_body()
        if tok == "(":
            inner = self.type_expr(member=False)
            self.expect(")")
            return inner
        if tok[0] in "'\"":
            return Node("lit", tok[1:-1])
        if tok in ("string", "number", "boolean", "null", "undefined", "void", "unknown", "any"):
            return Node("prim", tok)
        if tok in ("Partial", "Promise", "Array", "Record"):
            self.expect("<")
            inner = self.type_expr(member=False)
            while self.peek() == ",":
                self.next()
                self.type_expr(member=False)
            self.expect(">")
            if tok == "Partial":
                return Node("partial", inner)
            if tok == "Array":
                return Node("array", inner)
            return Node("any")
        return Node("ref", tok)


_DECLS: dict[str, Node] | None = None


def declarations() -> dict[str, Node]:
    global _DECLS
    if _DECLS is None:
        _DECLS = _Parser(_tokens(_strip_comments(TYPES_TS.read_text()))).declarations()
    return _DECLS


def _check(value: Any, node: Node, path: str, errors: list[str], allow_extra: set[str]) -> None:
    decls = declarations()
    kind = node.kind
    if kind == "ref":
        if node.value not in decls:
            errors.append(f"{path}: unknown type {node.value}")
            return
        _check(value, decls[node.value], path, errors, allow_extra)
    elif kind == "prim":
        ok = {
            "string": isinstance(value, str),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "boolean": isinstance(value, bool),
            "null": value is None,
            "undefined": value is None,
            "void": value is None,
        }.get(node.value, True)
        if not ok:
            errors.append(f"{path}: expected {node.value}, got {type(value).__name__} {value!r:.60}")
    elif kind == "lit":
        if value != node.value:
            errors.append(f"{path}: expected '{node.value}', got {value!r:.60}")
    elif kind == "array":
        if not isinstance(value, list):
            errors.append(f"{path}: expected array, got {type(value).__name__}")
            return
        for i, item in enumerate(value):
            _check(item, node.value, f"{path}[{i}]", errors, allow_extra)
    elif kind == "union":
        attempts = []
        for option in node.value:
            sub: list[str] = []
            _check(value, option, path, sub, allow_extra)
            if not sub:
                return
            attempts.append(sub)
        best = min(attempts, key=len)
        errors.append(f"{path}: matches no union member; closest: {best[0]}")
    elif kind == "obj":
        if not isinstance(value, dict):
            errors.append(f"{path}: expected object, got {type(value).__name__}")
            return
        fields: dict[str, tuple[Node, bool]] = node.value
        for name, (sub, optional) in fields.items():
            if name not in value:
                if not optional:
                    errors.append(f"{path}.{name}: missing")
                continue
            if optional and value[name] is None and sub.kind != "union":
                continue
            _check(value[name], sub, f"{path}.{name}", errors, allow_extra)
        for name in value:
            if name not in fields and name not in allow_extra:
                errors.append(f"{path}.{name}: not in types.ts")
    elif kind == "partial":
        inner = node.value
        while inner.kind == "ref":
            inner = decls[inner.value]
        relaxed = Node("obj", {k: (v, True) for k, (v, _) in inner.value.items()})
        _check(value, relaxed, path, errors, allow_extra)


def validate(value: Any, type_name: str, allow_extra: set[str] | None = None) -> list[str]:
    errors: list[str] = []
    _check(value, Node("ref", type_name), type_name, errors, allow_extra or set())
    return errors
