#!/usr/bin/env python3
"""Read a PvZ2 RSB archive (PopCap Resource Bundle) without copying the whole file.

RSB keeps its catalogue at the very start, and the header gives the offset and
size of every RSG package. A few scattered megabytes are enough to reach any
single file inside a gigabyte archive.

The Reader abstracts where bytes come from: adb (OBB sitting on an Android
device), a local file, or HTTP Range. The parsing code is identical.
"""
import struct
import subprocess
import sys
import zlib

RSG_ENTRY = 204
ZSTD_MAGIC = b'\x28\xb5\x2f\xfd'


def decompress(blob):
    """Decompress RSG payload.

    Mods differ: some use zlib, some use zstd (Reflourished does). Detect by
    magic bytes rather than trusting the header flag, which has been wrong.
    """
    if blob[:4] == ZSTD_MAGIC:
        try:
            import zstandard
            return zstandard.ZstdDecompressor().decompressobj().decompress(blob)
        except ImportError:
            pass
        from compat import find_exe
        exe = find_exe('zstd')
        if not exe:
            sys.exit('This RSG uses zstd and no decompressor is available.\n'
                     '  pip install zstandard        (any platform)\n'
                     '  brew install zstd            (macOS)\n'
                     '  apt install zstd             (Linux)\n'
                     '  winget install Facebook.Zstandard   (Windows)')
        r = subprocess.run([exe, '-d', '-c', '-'], input=blob,
                           capture_output=True)
        if not r.stdout:
            raise ValueError(f'zstd failed: {r.stderr[:200]!r}')
        return r.stdout
    if blob[:1] == b'\x78':
        return zlib.decompress(blob)
    return blob                                   # not compressed


class AdbReader:
    """Read a byte range off an Android device over adb, without copying it."""

    def __init__(self, adb, serial, path):
        self.adb, self.serial, self.path = adb, serial, path

    def read(self, off, size):
        blk = 65536
        first = off // blk
        pad = off - first * blk
        count = (pad + size + blk - 1) // blk
        cmd = (f"dd if='{self.path}' bs={blk} skip={first} count={count} "
               f"2>/dev/null")
        r = subprocess.run([self.adb, '-s', self.serial, 'exec-out', cmd],
                           capture_output=True)
        return r.stdout[pad:pad + size]


class FileReader:
    def __init__(self, path):
        self.f = open(path, 'rb')

    def read(self, off, size):
        self.f.seek(off)
        return self.f.read(size)


class HttpReader:
    def __init__(self, url):
        self.url = url

    def read(self, off, size):
        from compat import http_range
        return http_range(self.url, off, size)


def read_trie(data, start, length, tail):
    """Trie-encoded name table: 4 bytes per record, one character plus a
    uint24 offset to the sibling branch.

    A zero character ends a name and is followed by `tail` bytes of payload.
    """
    names, branch, name = [], {}, ''
    pos, end = start, start + length
    while pos + 4 <= end:
        if pos in branch:
            name = branch.pop(pos)
        ch = data[pos]
        jmp = int.from_bytes(data[pos + 1:pos + 4], 'little') * 4
        pos += 4
        if jmp:
            branch[start + jmp] = name
        if ch == 0:
            names.append((name, data[pos:pos + tail]))
            pos += tail
        else:
            name += chr(ch)
    return names


class RSB:
    def __init__(self, reader):
        self.r = reader
        head = reader.read(0, 0x70)
        if head[:4] != b'1bsr':
            raise ValueError(f'not an RSB archive, magic={head[:4]!r}')
        (self.rsg_count,) = struct.unpack_from('<I', head, 0x28)
        (self.rsg_info_off,) = struct.unpack_from('<I', head, 0x2C)
        self._rsgs = None

    def rsgs(self):
        """{UPPERCASE RSG NAME: (offset, length)}."""
        if self._rsgs is None:
            blob = self.r.read(self.rsg_info_off, self.rsg_count * RSG_ENTRY)
            out = {}
            for i in range(self.rsg_count):
                e = blob[i * RSG_ENTRY:(i + 1) * RSG_ENTRY]
                if len(e) < 0x88:
                    break
                nm = e[:128].split(b'\0')[0].decode('ascii', 'replace')
                out[nm.upper()] = struct.unpack_from('<II', e, 0x80)
            self._rsgs = out
        return self._rsgs

    def rsg_files(self, rsg_name):
        """One RSG package -> {file name: bytes}."""
        off, ln = self.rsgs()[rsg_name.upper()]
        blob = self.r.read(off, ln)
        data_off = struct.unpack_from('<I', blob, 0x14)[0]
        fl_len, fl_off = struct.unpack_from('<II', blob, 0x48)
        raw = decompress(blob[data_off:])
        out = {}
        for name, tail in read_trie(blob, fl_off, fl_len, 12):
            _, o, size = struct.unpack('<III', tail)
            out[name] = raw[o:o + size]
        return out

    def rton(self, rsg_name, endswith):
        from rton import RTON
        files = self.rsg_files(rsg_name)
        blob = next((v for k, v in files.items()
                     if k.upper().endswith(endswith.upper())), None)
        if blob is None:
            raise KeyError(f'{endswith} not found in RSG {rsg_name}')
        return RTON(blob[8:]).obj()
