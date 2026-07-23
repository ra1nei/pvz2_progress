#!/usr/bin/env python3
"""Read a publicly shared Drive folder, no authentication required.

    python3 pvz/drive.py                    list mod folders and their pp.dat
    python3 pvz/drive.py --pull             download every pp.dat into saves_drive/

REQUIREMENT: the folder must be shared as "Anyone with the link". Switching it
back to private breaks this path; saves then have to come from adb instead.

It works by scraping the JSON embedded in Drive's HTML page. If Google changes
that page the regex here needs updating; the functions return empty rather than
returning something wrong.
"""
import argparse
import os
import re
import urllib.parse

import pvz.net as compat
from pvz.net import http_download, http_get

# Left blank on purpose: in a public repo a hard-coded id would make the save
# folder findable by anyone. Supply it through the DRIVE_FOLDER_ID secret.
ROOT_ID = os.environ.get('DRIVE_FOLDER_ID', "")
from pvz import ROOT as HERE
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"


def _get(url):
    return http_get(url).decode('utf-8', 'replace')


def list_folder(folder_id):
    """{name: (id, is_folder)} for every direct child."""
    h = _get(f'https://drive.google.com/drive/folders/{folder_id}')
    if 'Sign-in' in h[:4000]:
        raise SystemExit('Folder is no longer public. Re-enable "Anyone with the link".')
    # the page embeds JSON with \xNN escapes
    u = re.sub(r'\\x([0-9a-fA-F]{2})', lambda m: chr(int(m.group(1), 16)), h)
    out = {}
    for fid, name, mime in re.findall(
            r'"([0-9A-Za-z_-]{28,44})",\["' + folder_id +
            r'"\],"([^"]{1,120})","([^"]{5,80})"', u):
        out[name] = (fid, 'folder' in mime)
    return out


def download(file_id, dest):
    # Delete first. Otherwise a failed download leaves the previous file in
    # place, it still passes the RTON check, and this reports success on data
    # that is days out of date.
    if os.path.exists(dest):
        os.remove(dest)
    http_download(f'https://drive.google.com/uc?export=download&id={file_id}', dest)
    ok = os.path.exists(dest) and open(dest, 'rb').read(4) == b'RTON'
    if not ok and os.path.exists(dest):
        os.remove(dest)          # usually an HTML error page, not a save
    return ok


def download_big(file_id, dest, progress=None):
    """Download a large Drive file, streaming it to disk.

    Anything past a few megabytes gets an HTML "Download warning" interstitial
    instead of the file, because Drive will not virus-scan it. The real
    download lives behind that page's form, so parse it and post the form back.
    """
    url = f'https://drive.google.com/uc?export=download&id={file_id}'
    head = http_get(url, timeout=90)
    if head[:1] != b'<':
        n = compat.http_stream(url, dest, progress=progress)
        return n if n else 0

    page = head.decode('utf-8', 'replace')
    m = re.search(r'<form[^>]*action="([^"]+)"', page)
    if not m:
        return 0
    action = m.group(1).replace('&amp;', '&')
    args = dict(re.findall(r'<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"', page))
    if not args:
        return 0
    return compat.http_stream(f'{action}?{urllib.parse.urlencode(args)}', dest, progress=progress)


def norm(s):
    return re.sub(r'[^a-z0-9]', '', str(s).lower())


def find_saves(root_id=ROOT_ID):
    """{mod folder name: (folder_id, pp.dat id or None)}"""
    out = {}
    for name, (fid, is_dir) in list_folder(root_id).items():
        if not is_dir or norm(name) == 'logo':
            continue
        inner = list_folder(fid)
        pp = inner.get('pp.dat')
        out[name] = (fid, pp[0] if pp else None)
    return out


def main():
    ap = argparse.ArgumentParser(description='Read PvZ2 saves from a public Drive folder')
    ap.add_argument('--pull', action='store_true', help='download pp.dat into saves_drive/')
    a = ap.parse_args()

    saves = find_saves()
    d = os.path.join(HERE, 'saves_drive')
    if a.pull:
        os.makedirs(d, exist_ok=True)

    for name in sorted(saves):
        fid, pp = saves[name]
        if not pp:
            print(f'  {name:<45} no pp.dat')
            continue
        if not a.pull:
            print(f'  {name:<45} pp.dat ok')
            continue
        # Name files by package suffix to match the rest of the pipeline.
        from pvz.totals import NAME_MAP
        sfx = next((k for k, v in NAME_MAP.items()
                    if norm(v) and norm(v) in norm(name)), norm(name))
        dest = os.path.join(d, f'pp_{sfx}.dat')
        ok = download(pp, dest)
        print(f'  {name:<45} {"-> " + os.path.basename(dest) if ok else "DOWNLOAD FAILED"}')


if __name__ == '__main__':
    main()
