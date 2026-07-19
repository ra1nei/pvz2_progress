#!/usr/bin/env python3
"""The GitHub Actions entrypoint. No Android device, no local machine.

    python3 ci.py

Where the data comes from:
    save files  <- a PUBLIC Drive folder, read through its share link
    level count <- the OBB on GitHub Releases, a few MB over HTTP Range
    result      -> README.md and pvz_totals.json, committed by the workflow

Nothing in this loop authenticates against anything.

Environment (all optional):
    DRIVE_FOLDER_ID  Drive folder holding the per-mod folders; falls back to drive.py
    LOGO_FOLDER_ID   Drive folder holding the mod logos
    GITHUB_TOKEN     raises the GitHub API limit from 60 to 5000 requests/hour
"""
import glob
import json
import os
import subprocess
import sys

import compat
import drive
from build_worlds import build
from check_updates import GH, RateLimited, latest_release
from pvz2_progress import extract, worlds_path
from rsb import HttpReader

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, 'state.json')
# Mods pinned to the top of the table regardless of progress
GHIM = ['Reflourished']
# From a repo secret. Logos are already committed under assets/logo, so a
# missing value is harmless; it only matters for mods added later.
LOGO_FOLDER_ID = os.environ.get('LOGO_FOLDER_ID', '')
SOURCES = os.path.join(HERE, 'sources.json')


def summary(lines):
    """Write the run summary for Actions; outside CI just print it."""
    text = '\n'.join(lines)
    print(text)
    p = os.environ.get('GITHUB_STEP_SUMMARY')
    if p:
        with open(p, 'a') as f:
            f.write(text + '\n')


def _tag(rec):
    import re as _re
    m = _re.search(r'/download/([^/]+)/', str(rec.get('obb_url', '')))
    return m.group(1) if m else ''


def obb_moi_nhat(rec):
    """(url, size, tag) of the newest OBB, or None if it cannot be resolved."""
    url = rec.get('obb_url')
    m = GH.search(url) if url else None
    if not m:
        return None
    rel = latest_release(m.group(1), m.group(2))
    if not rel:
        return None
    asset = next((x for x in rel.get('assets', [])
                  if x['name'].endswith('.obb')), None)
    return (asset['browser_download_url'], asset['size'],
            rel['tag_name']) if asset else None


def ten_to_sfx():
    """[(normalised display name, package suffix)], longest name first.

    add_mod.py writes _display_name into worlds/*.json, so a mod onboarded
    there is recognised here without anyone editing NAME_MAP by hand. Longest
    first because a folder matching both "Spice" and "Spice Re:Seasoned" has
    to resolve by the more specific one.
    """
    from emit_totals import NAME_MAP
    out = {drive.norm(v): k for k, v in NAME_MAP.items() if drive.norm(v)}
    for p in glob.glob(os.path.join(HERE, 'worlds', '*.json')):
        sfx = os.path.basename(p)[:-5].rsplit('_', 1)[-1]
        try:
            nm = json.load(open(p, encoding='utf-8')).get('_display_name')
        except (OSError, ValueError):
            continue
        if nm and drive.norm(nm):
            out[drive.norm(nm)] = sfx
    return sorted(out.items(), key=lambda kv: -len(kv[0]))


def keo_save(la=None):
    """Fetch every mod's save file from the public Drive folder.

    Returns {package: (path, folder id)}. Folders whose package cannot be
    resolved are collected into `la` and reported in the run summary; that is
    the signal that a new mod was added without running add_mod.py.
    """
    ten2sfx = ten_to_sfx()
    root = os.environ.get('DRIVE_FOLDER_ID') or drive.ROOT_ID
    d = os.path.join(HERE, 'saves_drive')
    os.makedirs(d, exist_ok=True)
    out = {}
    for folder, (fid, ppid) in drive.find_saves(root).items():
        if not ppid:
            print(f'  [!] "{folder}" has no save file')
            continue
        sfx = next((s for n, s in ten2sfx if n in drive.norm(folder)), None)
        if not sfx:
            print(f'  [!] cannot resolve a package from "{folder}"')
            if la is not None:
                la.append(folder)
            continue
        dest = os.path.join(d, f'pp_{sfx}.dat')
        if drive.download(ppid, dest):
            out[f'com.ea.game.pvz2_{sfx}'] = (dest, fid)
            print(f'  {folder:<45} -> pp_{sfx}.dat')
        else:
            print(f'  [!] download failed: {folder}')
    return out


def mau_thanh(pt):
    """Red at 0%, amber at 50%, green at 100%."""
    def tron(a, b, t):
        x = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
        y = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
        return '#%02x%02x%02x' % tuple(round(x[i] + (y[i] - x[i]) * t) for i in range(3))
    pt = max(0.0, min(1.0, pt))
    return (tron('#d93025', '#f9ab00', pt * 2) if pt < 0.5
            else tron('#f9ab00', '#34a853', (pt - 0.5) * 2))


def svg_thanh(pt, w=140, h=12):
    """Progress bar as SVG. GitHub renders SVG committed to the repo, so this
    is a real coloured bar."""
    r = h // 2
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
            f'viewBox="0 0 {w} {h}">'
            f'<rect width="{w}" height="{h}" rx="{r}" fill="#e8eaed"/>'
            f'<rect width="{max(r * 2, round(w * pt))}" height="{h}" rx="{r}" '
            f'fill="{mau_thanh(pt)}"/></svg>')


def svg_badge(text, auto):
    """Pill badge for the Updates column: blue carries a version this run
    verified against GitHub, amber means nobody is watching that mod.

    textLength pins the glyphs to the box, so the badge cannot overflow on a
    viewer whose monospace font is wider than the one used to size it.
    """
    text = text.replace('&', '&amp;').replace('<', '&lt;')
    w = round(len(text) * 6.62) + 18
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="20" '
            f'viewBox="0 0 {w} 20">'
            f'<rect width="{w}" height="20" rx="10" '
            f'fill="{"#1a73e8" if auto else "#b06000"}"/>'
            f'<text x="{w // 2}" y="14" fill="#ffffff" text-anchor="middle" '
            f'font-family="ui-monospace,SFMono-Regular,Menlo,Consolas,monospace" '
            f'font-size="11" textLength="{w - 18}" '
            f'lengthAdjust="spacingAndGlyphs">{text}</text></svg>')


def tai_logo(ten_hien, sfx):
    """Skipped when the logo is already committed, so this costs nothing on a
    normal run."""
    d = os.path.join(HERE, 'assets', 'logo')
    os.makedirs(d, exist_ok=True)
    for ext in ('png', 'webp', 'jpg'):
        if os.path.exists(os.path.join(d, f'{sfx}.{ext}')):
            return f'assets/logo/{sfx}.{ext}'
    try:
        items = drive.list_folder(LOGO_FOLDER_ID)
    except Exception:
        return None
    goc = drive.norm(ten_hien)
    for name, (fid, is_dir) in items.items():
        if is_dir:
            continue
        core = drive.norm(os.path.splitext(name)[0].replace('_logo', ''))
        if not core or (core not in goc and goc not in core):
            continue
        ext = os.path.splitext(name)[1].lstrip('.').lower() or 'png'
        dest = os.path.join(d, f'{sfx}.{ext}')
        raw = compat.http_get(
            f'https://drive.google.com/uc?export=download&id={fid}')
        if raw[:1] in (b'\x89', b'R', b'\xff'):     # png / webp / jpg
            open(dest, 'wb').write(raw)
            return f'assets/logo/{sfx}.{ext}'
    return None


def doc_links():
    """{package suffix: source url} from links.json.

    Hand-maintained and never written back, so the links survive every
    regeneration. Keys starting with _ are notes for whoever opens the file.
    """
    p = os.path.join(HERE, 'links.json')
    if not os.path.exists(p):
        return {}
    try:
        d = json.load(open(p, encoding='utf-8'))
    except ValueError:
        return {}
    return {k: v for k, v in d.items() if not k.startswith('_') and v}


def viet_readme(rows, gio):
    bar_dir = os.path.join(HERE, 'assets', 'bar')
    tag_dir = os.path.join(HERE, 'assets', 'tag')
    os.makedirs(bar_dir, exist_ok=True)
    os.makedirs(tag_dir, exist_ok=True)
    links = doc_links()

    ghim = {drive.norm(x) for x in GHIM}
    xong = [r for r in rows if r[1] is not None]
    chua = [r for r in rows if r[1] is None]
    xong.sort(key=lambda r: (0 if drive.norm(r[6]) in ghim else 1, -r[1] / r[2]))

    # Raw HTML, not a markdown table: markdown has no colspan, and "Progress"
    # has to sit across both the numbers and the bar.
    L = ['# PvZ2 mod progress', '',
         f'Updated {gio} UTC, refreshed every 6 hours.', '',
         '<table>',
         '<tr><th></th><th>Mod</th><th colspan="2">Progress</th>'
         '<th>Left</th><th>Done</th><th>Updates</th></tr>']

    def o_ten(ten, short):
        # The link is the mod's own source page, from links.json. Never a Drive
        # folder link: this repo is public, and pasting one in would expose
        # where the saves live, undoing the point of hiding the folder id in a
        # secret.
        sao = '⭐ ' if drive.norm(ten) in ghim else ''
        # Non-breaking spaces: the column is narrow enough that a name like
        # "Spice Re:Seasoned" would otherwise wrap.
        ten = ten.replace(' ', '&nbsp;')
        url = links.get(short)
        return sao + (f'<a href="{url}">{ten}</a>' if url else ten)

    def dong(r):
        short, done, total, note, auto, tag, ten, logo, _fid = r
        pt = done / total
        open(os.path.join(bar_dir, f'{short}.svg'), 'w').write(svg_thanh(pt))
        open(os.path.join(tag_dir, f'{short}.svg'), 'w').write(
            svg_badge(tag or 'auto', True) if auto else svg_badge('manual', False))
        anh = f'<img src="{logo}" height="56">' if logo else ''
        return ('<tr>'
                f'<td align="center">{anh}</td>'
                f'<td align="center">{o_ten(ten, short)}</td>'
                f'<td align="center">{done}&nbsp;/&nbsp;{total}<br>{pt*100:.0f}%</td>'
                f'<td align="center"><img src="assets/bar/{short}.svg" width="140"></td>'
                f'<td align="right">{total - done}</td>'
                f'<td align="center">{"✅" if done >= total else ""}</td>'
                f'<td align="center"><img src="assets/tag/{short}.svg" height="20"></td>'
                '</tr>')

    for r in xong:
        L.append(dong(r))
    for short, _d, _t, note, _a, _tg, ten, logo, _f in chua:
        anh = f'<img src="{logo}" height="56">' if logo else ''
        L.append(f'<tr><td align="center">{anh}</td>'
                 f'<td align="center">{o_ten(ten, short)}</td>'
                 f'<td colspan="5">no level count yet</td></tr>')
    L += ['</table>', '',
          'Mod names link to where the build came from. Blue means the level '
          'count is re-checked against GitHub Releases every run, and the '
          'badge shows the version it was last read from. Amber means the mod '
          'ships its OBB outside GitHub, so nothing can watch it: if that mod '
          'adds levels, the total here stays wrong until the count is rebuilt '
          'by hand.']
    return '\n'.join(L) + '\n'


def main():
    out = ['## PvZ2 progress', '']
    state = (json.load(open(STATE, encoding='utf-8'))
             if os.path.exists(STATE) else {'mods': {}, 'releases': {}})

    print('== pulling saves from Drive ==')
    folder_la = []
    saves = keo_save(folder_la)
    if not saves:
        sys.exit('No saves fetched. Is the Drive folder still public?')

    src = json.load(open(SOURCES, encoding='utf-8')) if os.path.exists(SOURCES) else {}

    print('\n== checking level counts ==')
    bi_chan = False
    for pkg in sorted(saves):
        if bi_chan:
            break
        try:
            info = obb_moi_nhat(src.get(pkg, {}))
        except RateLimited:
            bi_chan = True
            out.append('> GitHub rate limit hit, update checks skipped this '
                       'run. Progress numbers are unaffected.')
            info = None
        if not info:
            continue
        url, size, tag = info
        wp = worlds_path(pkg)
        old = json.load(open(wp, encoding='utf-8')) if os.path.exists(wp) else None
        fp = (old or {}).get('_fingerprint', {})
        if fp.get('source') == url and fp.get('size') == size:
            continue                                   # unchanged
        print(f'  {pkg}: reading OBB {tag} ({size:,} bytes)')
        data = build(HttpReader(url), {'source': url, 'size': size, 'tag': tag})
        if old and old.get('_display_name'):
            data['_display_name'] = old['_display_name']   # keep the resolved name
        os.makedirs(os.path.dirname(wp), exist_ok=True)
        json.dump(data, open(wp, 'w'), indent=1, ensure_ascii=False)
        tot = sum(w['total'] for w in data['worlds'].values() if w['counted'])
        out.append(f'- **{pkg.rsplit("_", 1)[-1]}** moved to `{tag}`, '
                   f'now **{tot}** levels')
        state['releases'][pkg.rsplit('_', 1)[-1]] = tag

    print('\n== computing progress ==')
    rows, doi = [], []
    for pkg, (path, fid) in sorted(saves.items()):
        short = pkg.rsplit('_', 1)[-1]
        if not os.path.exists(worlds_path(pkg)):
            rows.append((short, None, None, 'no level count yet', False, '',
                         short, None, fid))
            continue
        try:
            d = extract(path, pkg)
        except Exception as e:
            rows.append((short, None, None, f'error: {type(e).__name__}', False, '',
                         short, None, fid))
            continue
        cur = {'done': d['done_total'], 'total': d['grand_total']}
        cu = state['mods'].get(pkg)
        note = ''
        if cu and cu.get('done') != cur['done']:
            note = f"+{cur['done'] - cu['done']}"
            doi.append(f"{short} {cu['done']}->{cur['done']}")
        state['mods'][pkg] = cur
        sp = (json.load(open(worlds_path(pkg), encoding='utf-8'))
              if os.path.exists(worlds_path(pkg)) else {})
        rec = src.get(pkg, {})
        # Counts built over adb carry no _display_name. Borrow one from
        # NAME_MAP and write it back, so later runs need not guess again.
        from emit_totals import NAME_MAP
        ten = sp.get('_display_name') or NAME_MAP.get(short) or short
        if sp and not sp.get('_display_name') and ten != short:
            sp['_display_name'] = ten
            json.dump(sp, open(worlds_path(pkg), 'w'), indent=1, ensure_ascii=False)
        rows.append((short, cur['done'], cur['total'], note,
                     bool(GH.search(rec.get('obb_url') or '')), _tag(rec), ten,
                     tai_logo(ten, short), fid))

    import datetime as _dt
    open(os.path.join(HERE, 'README.md'), 'w', encoding='utf-8').write(
        viet_readme(rows, _dt.datetime.utcnow().strftime('%Y-%m-%d %H:%M')))

    D = sum(r[1] for r in rows if r[1] is not None)
    T = sum(r[2] for r in rows if r[2] is not None)
    out += ['', '| Mod | Cleared | Total | % | |', '|---|---:|---:|---:|---|']
    for short, done, total, note, _a, _t, _n, _l, _f in rows:
        out.append(f'| {short} | | | | {note} |' if done is None else
                   f'| {short} | {done} | {total} | {done * 100 / total:.1f}% | {note} |')
    if T:
        out.append(f'| **TOTAL** | **{D}** | **{T}** | **{D * 100 / T:.1f}%** | |')

    if folder_la:
        out += ['', '> **Unrecognised Drive folders:** '
                    + ', '.join(f'`{x}`' for x in folder_la)
                    + '. These are new mods not in the system yet. Run'
                    + ' `python3 add_mod.py` once on a machine with the mod'
                    + ' installed, then commit the result.']

    # Mods whose OBB is not on GitHub Releases cannot be re-read from the
    # cloud, so their level count is frozen. Say so rather than quietly
    # serving a stale number.
    mu = sorted(p.rsplit('_', 1)[-1] for p in saves
                if not GH.search(src.get(p, {}).get('obb_url') or ''))
    if mu:
        out += ['', f'> Cannot be checked automatically: **{", ".join(mu)}** '
                    f'(no GitHub Releases URL for their OBB). When these '
                    f'update, the level count has to be rebuilt locally with '
                    f'`build_worlds.py`.']

    subprocess.run([sys.executable, os.path.join(HERE, 'emit_totals.py')],
                   capture_output=True, cwd=HERE)
    json.dump(state, open(STATE, 'w'), indent=1, ensure_ascii=False)
    summary(out)
    print('\nCHANGED: ' + ', '.join(doi) if doi else '\nnothing new')


if __name__ == '__main__':
    main()
