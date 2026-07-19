#!/usr/bin/env python3
"""RTON (Riot Object Notation) to JSON decoder, enough for PvZ2 saves."""
import json
import struct
import sys


class Reader:
    def __init__(self, buf):
        self.b = buf
        self.i = 0

    def u8(self):
        v = self.b[self.i]
        self.i += 1
        return v

    def take(self, n):
        v = self.b[self.i:self.i + n]
        self.i += n
        return v

    def varint(self):
        r = 0
        s = 0
        while True:
            c = self.u8()
            r |= (c & 0x7F) << s
            if not (c & 0x80):
                return r
            s += 7

    def unpack(self, fmt):
        n = struct.calcsize(fmt)
        return struct.unpack(fmt, self.take(n))[0]


def zigzag(n):
    return (n >> 1) ^ -(n & 1)


class RTON:
    def __init__(self, buf):
        self.r = Reader(buf)
        self.pool_ascii = []
        self.pool_utf8 = []

    def string(self, t):
        r = self.r
        if t == 0x81:                       # ascii, not cached
            return r.take(r.varint()).decode('utf-8', 'replace')
        if t == 0x82:                       # utf8, not cached
            r.varint()                      # character count, unused
            return r.take(r.varint()).decode('utf-8', 'replace')
        if t == 0x90:                       # ascii + cache
            s = r.take(r.varint()).decode('utf-8', 'replace')
            self.pool_ascii.append(s)
            return s
        if t == 0x91:                       # ref cache ascii
            return self.pool_ascii[r.varint()]
        if t == 0x92:                       # utf8 + cache
            r.varint()
            s = r.take(r.varint()).decode('utf-8', 'replace')
            self.pool_utf8.append(s)
            return s
        if t == 0x93:                       # ref cache utf8
            return self.pool_utf8[r.varint()]
        raise ValueError(f'not a string type: 0x{t:02x} at {r.i}')

    def value(self, t):
        r = self.r
        if t == 0x00:
            return False
        if t == 0x01:
            return True
        if t == 0x08:
            return r.unpack('<b')
        if t == 0x09:
            return 0
        if t == 0x0A:
            return r.unpack('<B')
        if t == 0x0B:
            return 0
        if t == 0x10:
            return r.unpack('<h')
        if t == 0x11:
            return 0
        if t == 0x12:
            return r.unpack('<H')
        if t == 0x13:
            return 0
        if t == 0x20:
            return r.unpack('<i')
        if t == 0x21:
            return 0
        if t == 0x22:
            return r.unpack('<f')
        if t == 0x23:
            return 0.0
        if t == 0x24:
            return r.varint()
        if t == 0x25:
            return zigzag(r.varint())
        if t == 0x26:
            return r.unpack('<I')
        if t == 0x27:
            return 0
        if t == 0x28:
            return r.varint()
        if t == 0x29:
            return zigzag(r.varint())
        if t == 0x40:
            return r.unpack('<q')
        if t == 0x41:
            return 0
        if t == 0x42:
            return r.unpack('<d')
        if t == 0x43:
            return 0.0
        if t == 0x44:
            return r.varint()
        if t == 0x45:
            return zigzag(r.varint())
        if t == 0x46:
            return r.unpack('<Q')
        if t == 0x47:
            return 0
        if t == 0x48:
            return r.varint()
        if t == 0x49:
            return zigzag(r.varint())
        if t in (0x81, 0x82, 0x90, 0x91, 0x92, 0x93):
            return self.string(t)
        if t == 0x83:                       # RTID
            sub = r.u8()
            if sub == 0x00:
                return 'RTID(0)'
            if sub == 0x02:
                i1 = r.varint()
                i2 = r.varint()
                name = self.string(r.u8())
                return f'RTID({i2}.{i1}.{name}@?)'
            if sub == 0x03:
                i1 = r.varint()
                i2 = r.varint()
                h = r.take(4)[::-1].hex()
                return f'RTID({i2}.{i1}.{h})'
            raise ValueError(f'RTID sub 0x{sub:02x} @ {r.i}')
        if t == 0x84:
            return None
        if t == 0x85:
            return self.obj()
        if t == 0x86:
            return self.arr()
        raise ValueError(f'unknown type 0x{t:02x} at {r.i}')

    def obj(self):
        out = {}
        while True:
            t = self.r.u8()
            if t == 0xFF:
                return out
            k = self.string(t)
            out[k] = self.value(self.r.u8())

    def arr(self):
        r = self.r
        assert r.u8() == 0xFD, 'array must start with 0xFD'
        n = r.varint()
        out = [self.value(r.u8()) for _ in range(n)]
        assert r.u8() == 0xFE, 'array must end with 0xFE'
        return out


def decode(path):
    buf = open(path, 'rb').read()
    if buf[:4] != b'RTON':
        raise SystemExit(f'not an RTON file, magic = {buf[:8]!r}')
    ver = struct.unpack('<I', buf[4:8])[0]
    p = RTON(buf[8:])
    data = p.obj()
    rest = buf[8 + p.r.i:]
    return {'_version': ver, '_trailer': rest.decode('latin1'), 'data': data}


if __name__ == '__main__':
    res = decode(sys.argv[1])
    print(json.dumps(res['data'], indent=2, ensure_ascii=False))
    print(f"\n// rton version={res['_version']} trailer={res['_trailer']!r}",
          file=sys.stderr)
