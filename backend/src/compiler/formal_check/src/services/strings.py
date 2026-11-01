"""Lossless enum string transport through TLC's ASCII string syntax."""

import json

PREFIX = "__DPA_STRING_"


def encode_string(value: str) -> str:
    # TLA+'s lexer accepts neither arbitrary Unicode nor JSON \u escapes.
    # The DSL has equality/membership only, so an injective token encoding
    # preserves its string semantics. Escape prefix literals to avoid collisions.
    if value.startswith(PREFIX) or any(
        ord(char) > 126 or (ord(char) < 32 and char not in "\n\r\t\f") for char in value
    ):
        value = PREFIX + value.encode("utf-8").hex()
    return json.dumps(value, ensure_ascii=True)


def decode_string(value: str) -> str:
    return (
        bytes.fromhex(value[len(PREFIX) :]).decode("utf-8") if value.startswith(PREFIX) else value
    )
