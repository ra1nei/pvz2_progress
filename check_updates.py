#!/usr/bin/env python3
"""Ask whether a mod has shipped a new build, without the game installed.

    python3 check_updates.py              # compare versions only, very fast
    python3 check_updates.py --deep       # on a new build, recount the levels

Uses the OBB URLs already extracted from each APK (sources.json, written by
find_sources.py while the game was still installed), so this keeps working
after the game is uninstalled.

--deep reads the new OBB over HTTP Range: a few megabytes out of a gigabyte,
enough to recount levels and diff against the cache. Nothing is downloaded in
full and nothing is installed.
"""
import argparse
import json
import os
import re
import sys
import time

from compat import http_get

from build_worlds import build
from pvz2_progress import worlds_path
from rsb import HttpReader

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCES = os.path.join(HERE, 'sources.json')
GH = re.compile(r'github\.com/([^/]+)/([^/]+)/releases/download/([^/]+)/(.+)$')


def api(url):
    """Call the GitHub API. GITHUB_TOKEN, if set, lifts the anonymous limit
    of 60 requests/hour to 5000."""
    h = {'Accept': 'application/vnd.github+json'}
    tok = os.environ.get('GITHUB_TOKEN')
    if tok:
        h['Authorization'] = f'Bearer {tok}'
    raw = http_get(url, h)
    try:
        d = json.loads(raw.decode('utf-8', 'replace'))
    except json.JSONDecodeError:
        return None
    if isinstance(d, dict) and 'rate limit exceeded' in str(d.get('message', '')):
        raise RateLimited()
    return d


class RateLimited(Exception):
    pass


def reset_sau():
    """Minutes until GitHub resets the rate limit."""
    try:
        d = json.loads(http_get('https://api.github.com/rate_limit').decode())
        return max(0, int((d['rate']['reset'] - time.time()) // 60))
    except Exception:
        return None


def latest_release(owner, repo):
    for endpoint in (f'https://api.github.com/repos/{owner}/{repo}/releases/latest',
                     f'https://api.github.com/repos/{owner}/{repo}/releases'):
        d = api(endpoint)
        if isinstance(d, list) and d:
            d = d[0]
        if isinstance(d, dict) and d.get('tag_name'):
            return d
    return None


def diff_worlds(old, new):
    o = {k: (v['name'], v['total']) for k, v in old['worlds'].items()}
    n = {k: (v['name'], v['total']) for k, v in new['worlds'].items()}
    out = []
    for k in sorted(set(n) - set(o), key=int):
        out.append(f"NEW world {n[k][0]}: {n[k][1]} levels")
    for k in sorted(set(o) - set(n), key=int):
        out.append(f"REMOVED world {o[k][0]}: {o[k][1]} levels")
    for k in sorted(set(o) & set(n), key=int):
        if o[k][1] != n[k][1]:
            out.append(f"{n[k][0]}: {o[k][1]} -> {n[k][1]} levels "
                       f"({n[k][1] - o[k][1]:+d})")
    return out


def cached_total(pkg):
    p = worlds_path(pkg)
    if not os.path.exists(p):
        return None, None
    d = json.load(open(p, encoding='utf-8'))
    tot = sum(w['total'] for w in d['worlds'].values() if w['counted'])
    return d, tot


def main():
    ap = argparse.ArgumentParser(description='Check PvZ2 mods for new releases')
    ap.add_argument('--deep', action='store_true',
                    help='on a new build, read the OBB and recount levels '
                         '(a few MB over HTTP Range)')
    ap.add_argument('--pkg')
    a = ap.parse_args()

    if not os.path.exists(SOURCES):
        sys.exit('No sources.json yet. Run find_sources.py while the game '
                 'is still installed.')
    src = json.load(open(SOURCES, encoding='utf-8'))

    try:
        _kiem_tra(src, a)
    except RateLimited:
        m = reset_sau()
        print(f'\n[!] GitHub rate limit hit (60/hour for anonymous callers).'
              f'{f" Try again in ~{m} min." if m is not None else ""}\n'
              f'    To lift it: create a token at github.com/settings/tokens\n'
              f'    (no scopes needed) then export GITHUB_TOKEN=...')


def _kiem_tra(src, a):
    for pkg, rec in sorted(src.items()):
        if a.pkg and pkg != a.pkg:
            continue
        short = pkg.rsplit('_', 1)[-1]
        url = rec.get('obb_url')
        if not url:
            print(f'{short:<5} no URL in the APK, must be tracked by hand')
            continue
        m = GH.search(url)
        if not m:
            print(f'{short:<5} URL is not a GitHub release: {url}')
            continue
        owner, repo, have_tag, _ = m.groups()

        rel = latest_release(owner, repo)
        if not rel:
            print(f'{short:<5} could not reach GitHub ({owner}/{repo})')
            continue
        tag = rel['tag_name']
        cache, tot = cached_total(pkg)

        if tag == have_tag:
            print(f'{short:<5} {have_tag:<12} up to date'
                  f'{f"  ({tot} levels)" if tot else ""}')
            continue

        asset = next((x for x in rel.get('assets', [])
                      if x['name'].endswith('.obb')), None)
        size = f", {asset['size']/1048576:.0f}MB" if asset else ''
        print(f'{short:<5} {have_tag:<12} -> NEW BUILD {tag}'
              f'  ({rel.get("published_at","")[:10]}{size})')

        if not (a.deep and asset and cache):
            continue
        print(f'      reading the new OBB over HTTP Range...')
        try:
            new = build(HttpReader(asset['browser_download_url']),
                        {'source': asset['browser_download_url'],
                         'size': asset['size']})
        except Exception as e:
            print(f'      could not read it: {type(e).__name__}: {e}')
            continue
        ntot = sum(w['total'] for w in new['worlds'].values() if w['counted'])
        print(f'      {tot} -> {ntot} levels ({ntot - tot:+d})')
        for line in diff_worlds(cache, new):
            print(f'        {line}')


if __name__ == '__main__':
    main()
