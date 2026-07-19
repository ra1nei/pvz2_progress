#!/usr/bin/env python3
"""Extract each mod's OBB download URL from its APK into sources.json.

    python3 find_sources.py

RUN THIS WHILE THE GAME IS STILL INSTALLED. Once it is gone, check_updates.py
can still use the saved URL to ask whether a newer release added levels, with
nothing installed at all.

The APK is streamed over adb straight into memory, never written to disk.
"""
import io
import json
import os
import re
import subprocess
import zipfile

from adb_util import find_adb, list_mods, sh

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'sources.json')

URL_RE = re.compile(rb'https?://[A-Za-z0-9./_~:%-]{12,160}')
NOISE = re.compile(
    rb'google|android|firebase|crashlytics|facebook|schemas|apache|w3\.org|'
    rb'gstatic|doubleclick|admob|unity3d|applovin|ironsrc|supersonic|vungle|'
    rb'adcolony|mopub|applvn|mobileapptracking|exoplayer|goo\.gl|live\.com|'
    rb'yahoo|example|localhost|cloudflare|adobe|eamobile|app-measurement',
    re.I)


def apk_urls(adb, dev, apk_path):
    raw = subprocess.run([adb, '-s', dev, 'exec-out', f"cat '{apk_path}'"],
                         capture_output=True).stdout
    if not raw[:2] == b'PK':
        return [], len(raw)
    hits = set()
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        for n in z.namelist():
            if not n.endswith('.dex'):
                continue
            for m in URL_RE.finditer(z.read(n)):
                u = m.group(0)
                if not NOISE.search(u):
                    hits.add(u.decode('ascii', 'replace'))
    return sorted(hits), len(raw)


def main():
    adb = find_adb()
    old = json.load(open(OUT, encoding='utf-8')) if os.path.exists(OUT) else {}

    for dev, (model, pkgs) in list_mods(adb).items():
        for pkg in pkgs:
            apk = sh(adb, 'shell', 'pm', 'path', pkg, serial=dev,
                     check=False).strip().replace('package:', '').split('\n')[0]
            if not apk:
                continue
            urls, size = apk_urls(adb, dev, apk.strip())
            # The data URL usually points at that package's own .obb file.
            best = [u for u in urls if u.lower().endswith('.obb')
                    or '.obb' in u.lower() or pkg.rsplit('_', 1)[-1] in u.lower()]
            rec = old.setdefault(pkg, {})
            rec['apk_size'] = size
            rec['candidates'] = urls[:20]
            if best:
                rec['obb_url'] = best[0]
            print(f"{pkg.rsplit('_',1)[-1]:<5} apk {size/1048576:>5.1f}MB  "
                  f"{len(urls):>2} urls  ->  {best[0] if best else '(no obb URL found)'}")

    json.dump(old, open(OUT, 'w'), indent=1, ensure_ascii=False)
    print(f'\n-> {OUT}')


if __name__ == '__main__':
    main()
