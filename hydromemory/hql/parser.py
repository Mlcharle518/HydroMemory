"""Recursive-descent parser for the Hydro Query Language (PRD §13).

Grammar (conjunction-only)::

    query      := verb target [where] [group_by] [output]
    verb       := GET | PRECIPITATE | FILTER | DISTILL
    target     := IDENT
    where      := WHERE predicate (AND predicate)*
    predicate  := field_path ( op value | "(" value ")" )
    field_path := IDENT ("." IDENT)*
    op         := "=" | ">" | "<" | ">=" | "<=" | "!="
    value      := STRING | NUMBER | IDENT
    group_by   := GROUP BY IDENT
    output     := OUTPUT IDENT

The function-call predicate form ``permission.allows("assistant")`` is parsed
into ``Predicate(field="permission.allows", op="call", value="assistant")``.
"""
from __future__ import annotations

from hydromemory.hql.ast import OutputSpec, Predicate, Query
from hydromemory.hql.lexer import HQLSyntaxError, Token, TokenKind, tokenize

_VERBS = {"GET", "PRECIPITATE", "FILTER", "DISTILL"}
_KEYWORDS = _VERBS | {"WHERE", "AND", "GROUP", "BY", "OUTPUT"}
_OPS = {"=", ">", "<", ">=", "<=", "!="}


class _Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self._tokens = tokens
        self._i = 0

    # --- token cursor helpers ----------------------------------------------
    def _peek(self) -> Token:
        return self._tokens[self._i]

    def _advance(self) -> Token:
        tok = self._tokens[self._i]
        if tok.kind is not TokenKind.EOF:
            self._i += 1
        return tok

    def _at_eof(self) -> bool:
        return self._peek().kind is TokenKind.EOF

    def _expect(self, kind: TokenKind) -> Token:
        tok = self._peek()
        if tok.kind is not kind:
            raise HQLSyntaxError(f"expected {kind.value} but found {tok.value!r} at {tok.pos}")
        return self._advance()

    def _keyword_upper(self, tok: Token) -> str | None:
        """Return the uppercase keyword for an IDENT token, else None."""
        if tok.kind is TokenKind.IDENT and tok.value.upper() in _KEYWORDS:
            return tok.value.upper()
        return None

    # --- grammar productions -----------------------------------------------
    def parse(self) -> Query:
        verb_tok = self._expect(TokenKind.IDENT)
        verb = verb_tok.value.upper()
        if verb not in _VERBS:
            raise HQLSyntaxError(f"unknown verb {verb_tok.value!r} (expected one of {sorted(_VERBS)})")

        target_tok = self._expect(TokenKind.IDENT)
        if self._keyword_upper(target_tok) is not None:
            raise HQLSyntaxError(f"expected a target noun but found keyword {target_tok.value!r}")
        query = Query(verb=verb, target=target_tok.value)

        if self._keyword_upper(self._peek()) == "WHERE":
            self._advance()
            query.predicates = self._parse_predicates()

        if self._keyword_upper(self._peek()) == "GROUP":
            self._advance()
            by = self._advance()
            if self._keyword_upper(by) != "BY":
                raise HQLSyntaxError(f"expected BY after GROUP but found {by.value!r}")
            query.group_by = self._expect(TokenKind.IDENT).value

        if self._keyword_upper(self._peek()) == "OUTPUT":
            self._advance()
            query.output = OutputSpec(self._expect(TokenKind.IDENT).value)

        if not self._at_eof():
            tok = self._peek()
            raise HQLSyntaxError(f"unexpected trailing token {tok.value!r} at {tok.pos}")
        return query

    def _parse_predicates(self) -> list[Predicate]:
        predicates = [self._parse_predicate()]
        while self._keyword_upper(self._peek()) == "AND":
            self._advance()
            predicates.append(self._parse_predicate())
        return predicates

    def _parse_predicate(self) -> Predicate:
        field = self._parse_field_path()

        # Function-call form: permission.allows("assistant")
        if self._peek().kind is TokenKind.LPAREN:
            self._advance()
            value = self._parse_value()
            self._expect(TokenKind.RPAREN)
            return Predicate(field=field, op="call", value=value)

        op_tok = self._peek()
        if op_tok.kind is not TokenKind.OP or op_tok.value not in _OPS:
            raise HQLSyntaxError(f"expected an operator after {field!r} but found {op_tok.value!r}")
        self._advance()
        value = self._parse_value()
        return Predicate(field=field, op=op_tok.value, value=value)

    def _parse_field_path(self) -> str:
        first = self._expect(TokenKind.IDENT)
        parts = [first.value]
        while self._peek().kind is TokenKind.DOT:
            self._advance()
            parts.append(self._expect(TokenKind.IDENT).value)
        return ".".join(parts)

    def _parse_value(self) -> object:
        tok = self._peek()
        if tok.kind is TokenKind.STRING:
            self._advance()
            return tok.value
        if tok.kind is TokenKind.NUMBER:
            self._advance()
            return float(tok.value) if ("." in tok.value) else int(tok.value)
        if tok.kind is TokenKind.IDENT:
            self._advance()
            low = tok.value.lower()
            if low == "true":
                return True
            if low == "false":
                return False
            return tok.value
        raise HQLSyntaxError(f"expected a value but found {tok.value!r} at {tok.pos}")


def parse(text: str) -> Query:
    """Parse HQL ``text`` into a :class:`~hydromemory.hql.ast.Query`."""
    if not text or not text.strip():
        raise HQLSyntaxError("empty query")
    return _Parser(tokenize(text)).parse()
