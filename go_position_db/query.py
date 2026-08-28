from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .storage import DatabaseError


@dataclass(frozen=True)
class Token:
    kind: str
    value: str
    pos: int


def tokenize(text: str) -> list[Token]:
    tokens: list[Token] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "(":
            tokens.append(Token("LPAREN", ch, i)); i += 1; continue
        if ch == ")":
            tokens.append(Token("RPAREN", ch, i)); i += 1; continue
        if ch in {'"', "'"}:
            quote = ch
            start = i
            i += 1
            chars: list[str] = []
            while i < len(text):
                if text[i] == "\\" and i + 1 < len(text):
                    chars.append(text[i + 1])
                    i += 2
                    continue
                if text[i] == quote:
                    i += 1
                    break
                chars.append(text[i])
                i += 1
            else:
                raise DatabaseError(f"Unterminated quoted tag starting at character {start}.")
            value = "".join(chars).strip()
            if not value:
                raise DatabaseError(f"Empty quoted tag at character {start}.")
            tokens.append(Token("TAG", value, start))
            continue

        start = i
        while i < len(text) and not text[i].isspace() and text[i] not in "()":
            i += 1
        value = text[start:i]
        upper = value.upper()
        if upper in {"AND", "OR", "NOT"}:
            tokens.append(Token(upper, upper, start))
        else:
            tokens.append(Token("TAG", value, start))
    tokens.append(Token("EOF", "", len(text)))
    return tokens


class QueryParser:
    """Boolean parser with precedence NOT > AND > OR."""

    def __init__(self, text: str, lookup: Callable[[str], set[str]], universe: set[str]):
        self.tokens = tokenize(text)
        self.i = 0
        self.lookup = lookup
        self.universe = universe

    @property
    def current(self) -> Token:
        return self.tokens[self.i]

    def consume(self, kind: str) -> Token:
        tok = self.current
        if tok.kind != kind:
            raise DatabaseError(f"Expected {kind} at character {tok.pos}, found {tok.value!r}.")
        self.i += 1
        return tok

    def parse(self) -> set[str]:
        if self.current.kind == "EOF":
            raise DatabaseError("Search query cannot be empty.")
        result = self.parse_or()
        if self.current.kind != "EOF":
            raise DatabaseError(f"Unexpected token {self.current.value!r} at character {self.current.pos}.")
        return result

    def parse_or(self) -> set[str]:
        result = self.parse_and()
        while self.current.kind == "OR":
            self.consume("OR")
            result |= self.parse_and()
        return result

    def parse_and(self) -> set[str]:
        result = self.parse_not()
        while self.current.kind == "AND":
            self.consume("AND")
            result &= self.parse_not()
        return result

    def parse_not(self) -> set[str]:
        if self.current.kind == "NOT":
            self.consume("NOT")
            return self.universe - self.parse_not()
        return self.parse_primary()

    def parse_primary(self) -> set[str]:
        tok = self.current
        if tok.kind == "TAG":
            self.consume("TAG")
            return set(self.lookup(tok.value))
        if tok.kind == "LPAREN":
            self.consume("LPAREN")
            result = self.parse_or()
            self.consume("RPAREN")
            return result
        raise DatabaseError(f"Expected a tag, NOT, or '(' at character {tok.pos}; found {tok.value!r}.")
