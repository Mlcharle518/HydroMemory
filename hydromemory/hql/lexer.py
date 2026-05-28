"""Hand-written tokenizer for the Hydro Query Language (PRD §13).

HQL is a small, line-oriented DSL. The lexer turns source text into a flat list
of :class:`Token` values; the parser (``parser.py``) consumes them via
recursive descent. No external lexer/parser dependency is used.

Token kinds: ``IDENT`` (bare words, incl. keywords -- the parser distinguishes),
``STRING`` (double-quoted), ``NUMBER`` (int/float), ``OP`` (``= > < >= <= !=``),
``DOT`` (``.``), ``LPAREN``/``RPAREN``, and ``EOF``.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TokenKind(str, Enum):
    IDENT = "ident"
    STRING = "string"
    NUMBER = "number"
    OP = "op"
    DOT = "dot"
    LPAREN = "lparen"
    RPAREN = "rparen"
    EOF = "eof"


@dataclass(frozen=True)
class Token:
    kind: TokenKind
    value: str
    pos: int


class HQLSyntaxError(ValueError):
    """Raised on a malformed HQL query (lexing or parsing)."""


_OPERATOR_CHARS = set("=<>!")
_IDENT_START = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_"
_IDENT_CONT = _IDENT_START + "0123456789"


def tokenize(text: str) -> list[Token]:
    """Tokenize ``text`` into a list of tokens terminated by an ``EOF`` token."""
    tokens: list[Token] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]

        # Whitespace (incl. newlines -- HQL is whitespace-insensitive between tokens).
        if ch.isspace():
            i += 1
            continue

        # String literal: double-quoted, supports backslash escapes.
        if ch == '"':
            j = i + 1
            buf: list[str] = []
            while j < n and text[j] != '"':
                if text[j] == "\\" and j + 1 < n:
                    buf.append(text[j + 1])
                    j += 2
                    continue
                buf.append(text[j])
                j += 1
            if j >= n:
                raise HQLSyntaxError(f"unterminated string literal at position {i}")
            tokens.append(Token(TokenKind.STRING, "".join(buf), i))
            i = j + 1
            continue

        # Number literal (int or float).
        if ch.isdigit() or (ch == "." and i + 1 < n and text[i + 1].isdigit()):
            j = i
            seen_dot = False
            while j < n and (text[j].isdigit() or (text[j] == "." and not seen_dot)):
                if text[j] == ".":
                    seen_dot = True
                j += 1
            tokens.append(Token(TokenKind.NUMBER, text[i:j], i))
            i = j
            continue

        # Operators: = , >=, <=, !=, >, <
        if ch in _OPERATOR_CHARS:
            two = text[i : i + 2]
            if two in (">=", "<=", "!=", "=="):
                tokens.append(Token(TokenKind.OP, "=" if two == "==" else two, i))
                i += 2
                continue
            if ch in ("=", ">", "<"):
                tokens.append(Token(TokenKind.OP, ch, i))
                i += 1
                continue
            raise HQLSyntaxError(f"unexpected character {ch!r} at position {i}")

        if ch == ".":
            tokens.append(Token(TokenKind.DOT, ".", i))
            i += 1
            continue
        if ch == "(":
            tokens.append(Token(TokenKind.LPAREN, "(", i))
            i += 1
            continue
        if ch == ")":
            tokens.append(Token(TokenKind.RPAREN, ")", i))
            i += 1
            continue

        # Identifier / keyword.
        if ch in _IDENT_START:
            j = i
            while j < n and text[j] in _IDENT_CONT:
                j += 1
            tokens.append(Token(TokenKind.IDENT, text[i:j], i))
            i = j
            continue

        raise HQLSyntaxError(f"unexpected character {ch!r} at position {i}")

    tokens.append(Token(TokenKind.EOF, "", n))
    return tokens
