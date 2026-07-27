#!/usr/bin/env python3
"""Read and download from a publicly shared Drive folder.

Most mods publish their APK and OBB on Drive, so install.py comes through
here. Nothing else does: saves live in saves/ in this repo, and logos are
committed under assets/logo.

REQUIREMENT: the folder must be shared as "Anyone with the link".

It works by scraping the JSON embedded in Drive's HTML page. If Google changes
that page the regex here needs updating; the functions return empty rather than
returning something wrong.
"""
import re
import urllib.parse

import pvz.net as compat
from pvz import norm
from pvz.net import http_get

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"


def list_folder(folder_id):
    """{name: (id, is_folder)} for every direct child."""
    h = http_get(f'https://drive.google.com/drive/folders/{folder_id}'
                 ).decode('utf-8', 'replace')
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


def files_of_type(items, ext):
    """Files ending in `ext` in a folder listing, and one level below.

    Mods do not agree on where the builds go. Some leave them at the top of the
    folder, others sort them into APKs and OBB, so looking only at the top
    reports a mod as shipping neither. A subfolder is only descended into when
    its name says it holds this kind of file, which is what keeps a folder of
    screenshots or old builds out of the answer.

    Lives here rather than in install.py because onboarding a mod reads the
    same listing for the same reason: to find the OBB to watch it by.
    """
    out = {n: i for n, (i, is_dir) in items.items()
           if not is_dir and n.lower().endswith(ext)}
    for n, (i, is_dir) in items.items():
        if is_dir and ext.lstrip('.') in norm(n):
            try:
                out.update({x: y for x, (y, sub) in list_folder(i).items()
                            if not sub and x.lower().endswith(ext)})
            except Exception:
                pass
    return out


def file_size(file_id):
    """Size in bytes of a shared Drive file, without downloading it, or 0.

    Anything past a few megabytes answers the download URL with an HTML
    "cannot scan for viruses" interstitial rather than the file, exactly as
    download_big has to handle; the real file sits behind that page's form.
    Either way the size comes from a one-byte Range probe, never the body, so
    watching a gigabyte OBB for a new release costs one small request.
    """
    url = f'https://drive.google.com/uc?export=download&id={file_id}'
    head = http_get(url, timeout=90)
    if not head:
        return 0
    if head[:1] != b'<':
        return compat.content_length(url)
    page = head.decode('utf-8', 'replace')
    m = re.search(r'<form[^>]*action="([^"]+)"', page)
    if not m:
        return 0
    action = m.group(1).replace('&amp;', '&')
    args = dict(re.findall(r'<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"', page))
    if not args:
        return 0
    return compat.content_length(f'{action}?{urllib.parse.urlencode(args)}')


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
    return compat.http_stream(f'{action}?{urllib.parse.urlencode(args)}', dest,
                              progress=progress)
