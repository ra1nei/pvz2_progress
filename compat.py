#!/usr/bin/env python3
"""Windows / macOS / Linux compatibility layer. Standard library only.

Every OS-specific detail lives here so the other modules never have to know
which platform they are running on.
"""
import os
import shutil
import urllib.error
import urllib.request

IS_WIN = os.name == 'nt'
UA = 'Mozilla/5.0 (compatible; pvz2-tracker)'


# ---------------------------------------------------------------- HTTP

def http_get(url, headers=None, timeout=90):
    """GET returning bytes. On an HTTP error returns the error body; on a
    network failure returns b''."""
    req = urllib.request.Request(url, headers={'User-Agent': UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        return e.read() or b''
    except Exception:
        return b''


def http_range(url, start, length):
    """Fetch exactly one byte range.

    If the server ignores Range this returns b'' rather than silently pulling
    the whole file: an OBB is over a gigabyte and would hang the run."""
    req = urllib.request.Request(url, headers={
        'User-Agent': UA, 'Range': f'bytes={start}-{start + length - 1}'})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            if r.status != 206:
                return b''
            return r.read()
    except Exception:
        return b''


def http_download(url, dest, timeout=180):
    data = http_get(url, timeout=timeout)
    if not data:
        return False
    with open(dest, 'wb') as f:
        f.write(data)
    return True


# ---------------------------------------------------------------- locating binaries

_HINTS = {
    'adb': [
        '~/Library/Android/sdk/platform-tools/adb',            # macOS
        '~/Android/Sdk/platform-tools/adb',                    # Linux
        '~/AppData/Local/Android/Sdk/platform-tools/adb.exe',  # Windows
        'C:/Program Files/BlueStacks_nxt/HD-Adb.exe',
        'C:/Program Files (x86)/BlueStacks_nxt/HD-Adb.exe',
        '/opt/homebrew/bin/adb', '/usr/local/bin/adb', '/usr/bin/adb',
    ],
    'zstd': ['/opt/homebrew/bin/zstd', '/usr/local/bin/zstd', '/usr/bin/zstd',
             'C:/Program Files/zstd/zstd.exe'],
}


def find_exe(name):
    """Search PATH first, then the usual per-OS install locations."""
    p = shutil.which(name) or (shutil.which(name + '.exe') if IS_WIN else None)
    if p:
        return p
    for h in _HINTS.get(name, []):
        h = os.path.expanduser(h)
        if os.path.exists(h):
            return h
    return None

