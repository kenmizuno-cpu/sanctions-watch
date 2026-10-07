"""Reject malformed remote values; preserve arbitrary integer precision in JSON/Sheets."""
import re

def integer(value):
    if type(value) is not int or not 0<=value<2**256: raise ValueError('invalid nonnegative integer')
    return str(value)

def quantity(value):
    if not isinstance(value,str) or not re.fullmatch(r'0x[0-9a-fA-F]+',value):
        raise ValueError('invalid RPC hex integer')
    if len(value)>66: raise ValueError('RPC integer too large')
    return str(int(value,16))

def hash32(value):
    if not isinstance(value,str) or not re.fullmatch(r'0x[0-9a-fA-F]{64}',value):
        raise ValueError('invalid block/transaction hash')
    return value.lower()
