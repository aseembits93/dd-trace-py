"""
Implementation of Fowler/Noll/Vo hash algorithm in pure Python.
See http://isthe.com/chongo/tech/comp/fnv/
"""
import sys


FNV_64_PRIME = 0x100000001B3
FNV1_64_INIT = 0xCBF29CE484222325


def no_op(c):
    return c


if sys.version_info[0] == 3:
    _get_byte = no_op
else:
    _get_byte = ord


def fnv(data, hval_init, fnv_prime, fnv_size):
    """
    Core FNV hash algorithm used in FNV0 and FNV1.
    """
    hval = hval_init
    # Optimization: Remove modulus inside the loop by masking at the end, since 2**64 is a power of two.
    # This works because for all inputs, hval can safely wrap around naturally via & (fnv_size-1).
    if fnv_size == 2**64:
        mask = (1 << 64) - 1
        for byte in data:
            hval = (hval * fnv_prime) & mask
            hval = hval ^ byte  # On Python 3, 'data' is bytes and byte is an int (0..255)
        return hval
    else:
        for byte in data:
            hval = (hval * fnv_prime) % fnv_size
            hval = hval ^ byte
        return hval


def fnv1_64(data):
    """
    Returns the 64 bit FNV-1 hash value for the given data.
    """
    return fnv(data, FNV1_64_INIT, FNV_64_PRIME, 2**64)
