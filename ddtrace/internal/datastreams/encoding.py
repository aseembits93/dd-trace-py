import struct
from typing import Tuple  # noqa:F401

from .fnv import _get_byte


MAX_VAR_LEN_64 = 9


def encode_var_int_64(v):
    # type: (int) -> bytes
    return encode_var_uint_64(v >> (64 - 1) ^ (v << 1))


def decode_var_int_64(b):
    # type: (bytes) -> Tuple[int, bytes]
    v, b = decode_var_uint_64(b)
    return (v >> 1) ^ -(v & 1), b


def encode_var_uint_64(v):
    # type: (int) -> bytes
    b = b""
    for _ in range(0, MAX_VAR_LEN_64):
        if v < 0x80:
            break
        b += struct.pack("B", (v & 255) | 0x80)
        v >>= 7
    b += struct.pack("B", v & 255)
    return b


def decode_var_uint_64(b):
    # type: (bytes) -> Tuple[int, bytes]
    x = 0
    s = 0
    b_len = len(b)
    # Avoid repeated len() calls by caching the length
    # Use a while loop to avoid range() overhead and slicing only once
    i = 0
    while i < MAX_VAR_LEN_64:
        if i >= b_len:
            raise EOFError()
        n = _get_byte(b[i])
        # Short-circuit return when decoding last byte or when continuation bit is not set
        if n < 0x80 or i == MAX_VAR_LEN_64 - 1:
            # x | n << s is equivalent to x | (n << s)
            return x | (n << s), b[i + 1 :]
        x |= (n & 0x7F) << s
        s += 7
        i += 1
    raise EOFError
