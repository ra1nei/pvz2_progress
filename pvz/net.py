#!/usr/bin/env python3
"""Windows / macOS / Linux compatibility layer. Standard library only.

Every OS-specific detail lives here so the other modules never have to know
which platform they are running on.
"""
import os
import shutil
import ssl
import sys
import urllib.error
import urllib.request

IS_WIN = os.name == 'nt'
UA = 'Mozilla/5.0 (compatible; pvz2-tracker)'

_ctx = None


def ssl_context():
    """The certificate store HTTPS is verified against.

    Python normally reads the operating system's store, and on Windows that
    sometimes holds nothing it can use: a fresh install Windows Update has not
    filled in yet, or antivirus terminating TLS with a root of its own. The
    symptom is CERTIFICATE_VERIFY_FAILED on every host at once, while git and
    the browser on the same machine are fine, because they carry their own
    roots.

    certifi is Mozilla's root list as a pip package, so it is added when it is
    installed. This only ever adds roots to the system ones, never replaces
    them, and verification itself is never turned off: these downloads become
    APKs that get installed, so an unverified one is the last thing you want.
    """
    global _ctx
    if _ctx is None:
        _ctx = ssl.create_default_context()
        try:
            import certifi
            _ctx.load_verify_locations(cafile=certifi.where())
        except Exception:
            pass
    return _ctx


# Every download here returns empty on failure rather than raising, because a
# caller looping over eight mods should not die on one of them. The cost is
# that a machine where HTTPS is broken outright looks exactly like a machine
# where a folder went private, so the reason is printed once and then kept
# quiet: eight identical certificate errors help nobody.
_reported = set()


def _blame(url, e):
    """Say why a request failed, once per kind of failure."""
    kind = type(e).__name__
    msg = str(e)
    if kind in _reported:
        return
    _reported.add(kind)
    print(f'      [!] {kind}: {msg[:150]}')
    print(f'          while fetching {url[:90]}')
    if 'CERTIFICATE' in msg.upper() or 'SSL' in kind.upper():
        try:
            import certifi
            where = certifi.where()
        except Exception:
            where = None
        print('          Python has no root certificate it can verify HTTPS '
              'with. git and your browser carry their own, which is why only '
              'this is affected.')
        print(f'          running: {sys.executable}')
        if where:
            print(f'          certifi is here ({where}) and was already tried, '
                  f'so something else is breaking TLS: a proxy, or antivirus '
                  f'terminating connections to inspect them.')
        else:
            # Naming the interpreter matters more than the command. A bare
            # `pip install` on a machine with several Pythons installs into
            # whichever one owns pip, which is often not the one running this.
            print('          certifi is NOT importable from that interpreter. '
                  'Install it into that one specifically:')
            print(f'          "{sys.executable}" -m pip install --upgrade certifi')


# ---------------------------------------------------------------- HTTP

def http_get(url, headers=None, timeout=90):
    """GET returning bytes. On an HTTP error returns the error body; on a
    network failure returns b'' and prints why."""
    req = urllib.request.Request(url, headers={'User-Agent': UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout,
                                   context=ssl_context()) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        return e.read() or b''
    except Exception as e:
        _blame(url, e)
        return b''


def http_range(url, start, length):
    """Fetch exactly one byte range.

    If the server ignores Range this returns b'' rather than silently pulling
    the whole file: an OBB is over a gigabyte and would hang the run."""
    req = urllib.request.Request(url, headers={
        'User-Agent': UA, 'Range': f'bytes={start}-{start + length - 1}'})
    try:
        with urllib.request.urlopen(req, timeout=90,
                                   context=ssl_context()) as r:
            if r.status != 206:
                return b''
            return r.read()
    except Exception as e:
        _blame(url, e)
        return b''


def http_download(url, dest, timeout=180):
    data = http_get(url, timeout=timeout)
    if not data:
        return False
    with open(dest, 'wb') as f:
        f.write(data)
    return True


def http_stream(url, dest, headers=None, timeout=300, progress=None):
    """Download to a file in chunks. Returns bytes written, 0 on failure.

    Separate from http_download because an OBB runs past a gigabyte and
    http_get holds the whole body in memory first. Writes to <dest>.part and
    renames at the end, so an interrupted download never looks complete.
    """
    req = urllib.request.Request(url, headers={'User-Agent': UA, **(headers or {})})
    tmp = dest + '.part'
    try:
        with urllib.request.urlopen(req, timeout=timeout,
                                    context=ssl_context()) as r, open(tmp, 'wb') as f:
            tong = int(r.headers.get('Content-Length') or 0)
            done = 0
            while True:
                buf = r.read(1 << 20)
                if not buf:
                    break
                f.write(buf)
                done += len(buf)
                if progress:
                    progress(done, tong)
        os.replace(tmp, dest)
        return done
    except Exception as e:
        _blame(url, e)
        if os.path.exists(tmp):
            os.remove(tmp)
        return 0


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

