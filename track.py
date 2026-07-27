#!/usr/bin/env python3
"""The GitHub Actions entrypoint. No Android device, no local machine.

    python3 track.py

Where the data comes from:
    save files  <- saves/ in this repo
    level count <- the OBB on GitHub Releases, a few MB over HTTP Range
    result      -> README.md and pvz_totals.json, committed by the workflow

Environment (all optional):
    SAVES_DIR        somewhere other than saves/ to read them from
    GITHUB_TOKEN     raises the GitHub API limit from 60 to 5000 requests/hour
"""
import datetime
import glob
import json
import os
import sys

import pvz.net as compat
from pvz import norm, totals
from pvz.worlds import build
from pvz.github import GH, RateLimited, latest_release
from pvz.save import extract, worlds_path
from pvz.rsb import HttpReader

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, 'state.json')
# Mods pinned to the top of the table regardless of progress
PINNED = ['Reflourished']
SOURCES = os.path.join(HERE, 'sources.json')
# Hours ahead of UTC to show alongside it in the header. The Actions UI prints
# run times in the reader's own zone while this file is written in UTC, and one
# page saying 15:46 while the other says 08:46 reads as a stale README.
TZ_OFFSET = 7


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


def link_release(rec, tag):
    """The GitHub release page for `tag`, or '' when the OBB is not on GitHub."""
    m = GH.search(str(rec.get('obb_url', '')))
    return f'https://github.com/{m.group(1)}/{m.group(2)}/releases/tag/{tag}' \
        if m and tag else ''


def latest_obb(rec):
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


def name_to_suffix():
    """[(normalised display name, package suffix)], longest name first.

    addmod.py writes _display_name into worlds/*.json, so a mod onboarded
    there is recognised here without anyone editing NAME_MAP by hand. Longest
    first because a folder matching both "Spice" and "Spice Re:Seasoned" has
    to resolve by the more specific one.
    """
    from pvz.totals import NAME_MAP
    out = {norm(v): k for k, v in NAME_MAP.items() if norm(v)}
    for p in glob.glob(os.path.join(HERE, 'worlds', '*.json')):
        sfx = os.path.basename(p)[:-5].rsplit('_', 1)[-1]
        try:
            nm = json.load(open(p, encoding='utf-8')).get('_display_name')
        except (OSError, ValueError):
            continue
        if nm and norm(nm):
            out[norm(nm)] = sfx
    return sorted(out.items(), key=lambda kv: -len(kv[0]))


def fetch_saves():
    """Every mod's save file, as {package: path}.

    They are committed here by sync.py, so this is a directory listing and
    nothing more. Files that are not RTON are reported rather than passed on
    to be misread as a save.
    """
    d = os.environ.get('SAVES_DIR') or os.path.join(HERE, 'saves')
    out = {}
    for f in sorted(glob.glob(os.path.join(d, 'pp_*.dat'))):
        sfx = os.path.basename(f)[3:-4]
        if open(f, 'rb').read(4) != b'RTON':
            print(f'  [!] {os.path.basename(f)} is not an RTON save')
            continue
        out[f'com.ea.game.pvz2_{sfx}'] = f
        print(f'  {os.path.basename(f):<20} <- {d}')
    return out


def bar_colour(pt):
    """Red at 0%, amber at 50%, green at 100%."""
    def blend(a, b, t):
        x = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
        y = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
        return '#%02x%02x%02x' % tuple(round(x[i] + (y[i] - x[i]) * t) for i in range(3))
    pt = max(0.0, min(1.0, pt))
    return (blend('#d93025', '#f9ab00', pt * 2) if pt < 0.5
            else blend('#f9ab00', '#34a853', (pt - 0.5) * 2))


def svg_bar(pt, w=140, h=12):
    """Progress bar as SVG. GitHub renders SVG committed to the repo, so this
    is a real coloured bar."""
    r = h // 2
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
            f'viewBox="0 0 {w} {h}">'
            f'<rect width="{w}" height="{h}" rx="{r}" fill="#e8eaed"/>'
            f'<rect width="{max(r * 2, round(w * pt))}" height="{h}" rx="{r}" '
            f'fill="{bar_colour(pt)}"/></svg>')


def svg_badge(text, auto):
    """Pill badge for the Updates column: blue is watched (a version verified
    against GitHub, or `drive` for a Drive OBB polled by size), amber `manual`
    is a mod nothing can watch.

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


# The box every logo is fitted inside. They range from 1.3:1 to 4:1, so one
# fixed height makes the column as wide as the widest banner and leaves the
# squarer ones looking tiny in it, while one fixed width shrinks the banners
# instead. Fitting each inside a box makes every logo as large as it can be
# without any of them setting the column's size for the rest.
LOGO_BOX = (120, 72)


def _img_size(path):
    """(width, height) of a PNG or WebP, or None. Header bytes only."""
    import struct
    with open(path, 'rb') as f:
        b = f.read(64)
    try:
        if b[:8] == b'\x89PNG\r\n\x1a\n':
            return struct.unpack('>II', b[16:24])
        if b[:4] == b'RIFF' and b[8:12] == b'WEBP':
            if b[12:16] == b'VP8X':
                return (int.from_bytes(b[24:27], 'little') + 1,
                        int.from_bytes(b[27:30], 'little') + 1)
            if b[12:16] == b'VP8 ':
                w, h = struct.unpack('<HH', b[26:30])
                return (w & 0x3fff, h & 0x3fff)
            if b[12:16] == b'VP8L':
                n = int.from_bytes(b[21:25], 'little')
                return ((n & 0x3fff) + 1, ((n >> 14) & 0x3fff) + 1)
    except Exception:
        pass
    return None


# A transparent 1px PNG stretched to the box height, for the logo cells that
# hold an unboxed image. Those fit the box at their own aspect, so a banner is
# far shallower than a square badge, and a row is as tall as its tallest cell:
# without this their rows come out visibly shallower than the rest. GitHub
# strips inline CSS, so a spacer image is the only way left to pin a row's
# height. A boxed logo is already exactly the box and needs none.
SPACER = f'<img src="assets/spacer.png" width="1" height="{LOGO_BOX[1]}">'


def logo_img(pair):
    """<img> for a logo, fit within LOGO_BOX, linked to the full-size art.

    `pair` is (what to show, what to link to) from logo(). The table shows the
    boxed copy, which is scaled down to line the column up, so the link goes to
    the original: clicking a logo should get you the art, not the thumbnail cut
    from it.

    The spacer goes in when there is no logo yet, or when one has not been
    boxed, so a mod still being onboarded keeps the same row height as the rest
    instead of collapsing.
    """
    show, full = pair if isinstance(pair, tuple) else (pair, pair)
    if not show:
        return SPACER
    d = _img_size(os.path.join(HERE, show))
    if not d or not d[1]:
        return SPACER + f'<img src="{show}" width="{LOGO_BOX[0]}">'
    bw, bh = LOGO_BOX
    if d == LOGO_BOX:
        # Already boxed, so it is the full height on its own and needs no
        # spacer propping the row up beside it.
        img = f'<img src="{show}" width="{bw}" height="{bh}">'
        return f'<a href="{full}">{img}</a>' if full and full != show else img
    k = min(bw / d[0], bh / d[1])
    return SPACER + f'<img src="{show}" width="{round(d[0] * k)}" height="{round(d[1] * k)}">'


def logo(sfx):
    """(what the table shows, what it links to), or (None, None).

    Two files, and the difference matters. Logos are drawn in whatever shape
    their author chose, so scaling them to fit leaves each a different width;
    boxlogos.py centres them on a canvas of one size, and showing that is what
    makes the column line up. But it is a thumbnail, a fifth of the art's width,
    so it is only what is shown: the link stays on the original, which is the
    logo as its author drew it.

    A logo that has not been boxed is shown and linked as itself, so one just
    dropped in still appears, at its own width, until boxlogos.py runs.

    Committed files only. A new mod shows a blank first column until one is
    dropped in by hand, named after the package suffix.
    """
    orig = None
    for ext in ('png', 'webp', 'jpg'):
        if os.path.exists(os.path.join(HERE, 'assets', 'logo', f'{sfx}.{ext}')):
            orig = f'assets/logo/{sfx}.{ext}'
            break
    boxed = f'assets/logo/box/{sfx}.png'
    if os.path.exists(os.path.join(HERE, boxed)):
        return boxed, orig or boxed
    return orig, orig


def timestamp_line(now=None):
    """The 'Updated ...' header line.

    Carries both zones, and links to the run that wrote the file when there is
    one, so the timestamp can be checked against the Actions UI instead of
    guessed at.
    """
    now = now or datetime.datetime.utcnow()
    loc = now + datetime.timedelta(hours=TZ_OFFSET)
    s = (f'Updated {loc:%Y-%m-%d %H:%M} UTC+{TZ_OFFSET} '
         f'({now:%H:%M} UTC), refreshed every 6 hours.')
    run = os.environ.get('GITHUB_RUN_ID')
    if run:
        srv = os.environ.get('GITHUB_SERVER_URL', 'https://github.com')
        repo = os.environ.get('GITHUB_REPOSITORY', '')
        s += f' [Run log]({srv}/{repo}/actions/runs/{run}).'
    return s


def read_links():
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


def write_readme(rows, gio):
    bar_dir = os.path.join(HERE, 'assets', 'bar')
    tag_dir = os.path.join(HERE, 'assets', 'tag')
    os.makedirs(bar_dir, exist_ok=True)
    os.makedirs(tag_dir, exist_ok=True)
    links = read_links()

    pinned = {norm(x) for x in PINNED}
    done = [r for r in rows if r[1] is not None]
    pending = [r for r in rows if r[1] is None]
    # Pinned first, then the mods that are actually watched, and within each
    # group by completion. Manual mods sink to the bottom because their totals
    # are the ones that can quietly go stale.
    # Sorted on the same figure the bar draws, both columns together, so the
    # order and the bars agree.
    done.sort(key=lambda r: (0 if norm(r[6]) in pinned else 1,
                             0 if r[4] else 1,
                             -(r[1] + r[9]) / (r[2] + r[10])))

    # Raw HTML, not a markdown table: markdown has no colspan, and "Progress"
    # has to sit across both the numbers and the bar.
    L = ['# PvZ2 mod progress', '',
         'How far through each Plants vs. Zombies 2 mod I have got. The numbers '
         'are read out of my save files and out of each mod\'s own data, by a '
         'GitHub Action that keeps this page current on its own.', '',
         gio, '',
         '<table>',
         '<tr><th></th><th>Mod</th><th>World</th><th>Quest</th>'
         '<th>Progress</th><th>Done</th>'
         '<th>Updates</th></tr>']

    def name_cell(name, short):
        # The link is the mod's own source page, from links.json. Never a Drive
        # folder link: this repo is public, and pasting one in would expose
        # where the saves live, undoing the point of hiding the folder id in a
        # secret.
        star = '⭐ ' if norm(name) in pinned else ''
        # Non-breaking spaces: the column is narrow enough that a name like
        # "Spice Re:Seasoned" would otherwise wrap.
        name = name.replace(' ', '&nbsp;')
        url = links.get(short)
        return star + (f'<a href="{url}">{name}</a>' if url else name)

    def quest_cell(qd, qt):
        # Blank rather than 0/0 when a mod has no quest registry: Requiem ships
        # none, and a zero there would read as nothing done rather than
        # nothing to do. No percentage: the World and Quest cells carry only
        # their counts, and the one figure that covers both sits under the bar.
        if not qt:
            return '<td align="center">-</td>'
        return f'<td align="center">{qd}&nbsp;/&nbsp;{qt}</td>'

    def row(r):
        short, done, total, note, auto, tag, name, logo, rel, qd, qt = r
        # The bar sits after both number columns, so it reports both: a mod
        # with its worlds finished and its quests barely started is not the
        # 100% the world column alone would draw.
        pt = (done + qd) / (total + qt)
        open(os.path.join(bar_dir, f'{short}.svg'), 'w').write(svg_bar(pt, w=110))
        open(os.path.join(tag_dir, f'{short}.svg'), 'w').write(
            svg_badge(tag or 'auto', True) if auto else svg_badge('manual', False))
        # The badge links to the GitHub release it was read from, so the
        # version is checkable rather than something to take on faith.
        badge = f'<img src="assets/tag/{short}.svg" height="20">'
        if rel:
            badge = f'<a href="{rel}">{badge}</a>'
        # By width, not height. These range from 1.3:1 to 4:1, so sizing by
        # height made the column as wide as the widest banner and left the
        # squarer ones looking tiny inside it.
        img = logo_img(logo)
        return ('<tr>'
                f'<td align="center">{img}</td>'
                f'<td align="center">{name_cell(name, short)}</td>'
                f'<td align="center">{done}&nbsp;/&nbsp;{total}</td>'
                + quest_cell(qd, qt) +
                f'<td align="center"><img src="assets/bar/{short}.svg" width="110">'
                f'<br>{round(pt * 100)}%</td>'
                f'<td align="center">{"✅" if done >= total and qd >= qt else ""}</td>'
                f'<td align="center">{badge}</td>'
                '</tr>')

    for r in done:
        L.append(row(r))
    for short, _d, _t, note, _a, _tg, name, logo, _rel, _qd, _qt in pending:
        img = logo_img(logo)
        L.append(f'<tr><td align="center">{img}</td>'
                 f'<td align="center">{name_cell(name, short)}</td>'
                 f'<td colspan="5">no level count yet</td></tr>')
    L += ['</table>', '',
          'World is the levels the game shows on its world maps. Quest is the '
          'levels reachable only through the quest system, which is where the '
          'Epic chains live; a chain counts as done all at once, because that '
          'is the only granularity the save records. A dash means there is '
          'nothing to count: Requiem ships no registry at all, and Alternate '
          "UniverZ's quests are either switched off, repeating events, or "
          'levels already on its maps. '
          'The bar and the tick both count World and Quest together, so a '
          'mod is only finished once its quests are too. '
          'Mod names link to where the build came from. A blue version badge '
          'links to the GitHub release the level count was read from, '
          're-checked every run. A blue <code>drive</code> badge means the OBB '
          'is on Drive: its size is watched for a new build, though the count '
          'is then rebuilt by hand. Amber <code>manual</code> means nothing can '
          'watch it (an itch.io page behind a Cloudflare check), so both the '
          'update and the recount are spotted by hand.']

    # The guide lives in its own file. Everything above this line is rewritten
    # on every run, so prose typed straight into README.md would not survive
    # the next one.
    p = os.path.join(HERE, 'usage.md')
    if os.path.exists(p):
        L += ['', open(p, encoding='utf-8').read().rstrip()]
    return '\n'.join(L) + '\n'


def main():
    out = ['## PvZ2 progress', '']
    state = (json.load(open(STATE, encoding='utf-8'))
             if os.path.exists(STATE) else {'mods': {}, 'releases': {}})

    print('== reading saves ==')
    saves = fetch_saves()
    if not saves:
        sys.exit('No saves found. saves/ holds no pp_*.dat, so either nothing '
                 'has been pushed yet or SAVES_DIR points somewhere empty.')

    src = json.load(open(SOURCES, encoding='utf-8')) if os.path.exists(SOURCES) else {}

    print('\n== checking level counts ==')
    rate_limited = False
    for pkg in sorted(saves):
        if rate_limited:
            break
        try:
            info = latest_obb(src.get(pkg, {}))
        except RateLimited:
            rate_limited = True
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
        total = sum(w['total'] for w in data['worlds'].values() if w['counted'])
        out.append(f'- **{pkg.rsplit("_", 1)[-1]}** moved to `{tag}`, '
                   f'now **{total}** levels')
        state['releases'][pkg.rsplit('_', 1)[-1]] = tag

    # Mods whose OBB is on Drive, not GitHub, cannot be re-read over HTTP Range
    # to recount, but their size can be watched: when the modder ships a build
    # the OBB changes size, and that alone says a rebuild is due. The id comes
    # from install.json, where install.py already recorded it. Spice is left out
    # on purpose, its itch.io page sits behind a Cloudflare check a plain
    # request cannot pass, so there is nothing to poll.
    import pvz.drive as drive
    icfg = (json.load(open(os.path.join(HERE, 'install.json'), encoding='utf-8'))
            if os.path.exists(os.path.join(HERE, 'install.json')) else {})
    watched = set()
    print('\n== watching Drive OBBs ==')
    for pkg in sorted(saves):
        short = pkg.rsplit('_', 1)[-1]
        if GH.search(src.get(pkg, {}).get('obb_url') or ''):
            continue
        did = (icfg.get(short) or {}).get('obb_id')
        if not did:
            continue
        prev = state.setdefault('watch', {}).get(pkg)
        size = drive.file_size(did)
        if not size:
            # A read can fail transiently (Drive interstitial changing shape, a
            # timeout). Once a size is known the mod stays watched on the last
            # one, so its badge does not flicker to manual and back; only a mod
            # never read at all is left unwatched.
            if prev:
                watched.add(short)
                print(f'  {short}: Drive read failed, keeping last known size')
            else:
                print(f'  {short}: could not read the Drive OBB size, skipping')
            continue
        watched.add(short)
        state['watch'][pkg] = size
        print(f'  {short}: {size:,} bytes' + (' (unchanged)' if prev == size else ''))
        if prev and prev != size:
            out.append(f'- **{short}**: the Drive OBB changed '
                       f'({prev / 1048576:.0f} -> {size / 1048576:.0f} MB), so a '
                       f'new build is out. Rebuild its count on a machine that '
                       f'has it: `python3 addmod.py`.')

    print('\n== computing progress ==')
    from pvz.totals import NAME_MAP
    rows, changed, uncounted = [], [], []
    for pkg, path in sorted(saves.items()):
        short = pkg.rsplit('_', 1)[-1]
        if not os.path.exists(worlds_path(pkg)):
            # A save with no level count: someone played a mod on a machine
            # that has it installed, and the save reached here on its own, but
            # counting the levels needs the OBB and so has to be done there.
            uncounted.append(short)
            name = NAME_MAP.get(short) or short
            rows.append((short, None, None, 'no level count yet', False, '',
                         name, logo(short), '', 0, 0))
            continue
        try:
            d = extract(path, pkg)
        except Exception as e:
            rows.append((short, None, None, f'error: {type(e).__name__}', False, '',
                         short, None, '', 0, 0))
            continue
        cur = {'done': d['done_total'], 'total': d['grand_total']}
        was = state['mods'].get(pkg)
        note = ''
        if was and was.get('done') != cur['done']:
            note = f"{cur['done'] - was['done']:+d}"
            changed.append(f"{short} {was['done']}->{cur['done']}")
        state['mods'][pkg] = cur
        sp = (json.load(open(worlds_path(pkg), encoding='utf-8'))
              if os.path.exists(worlds_path(pkg)) else {})
        rec = src.get(pkg, {})
        # Counts built over adb carry no _display_name. Borrow one from
        # NAME_MAP and write it back, so later runs need not guess again.
        name = sp.get('_display_name') or NAME_MAP.get(short) or short
        if sp and not sp.get('_display_name') and name != short:
            sp['_display_name'] = name
            json.dump(sp, open(worlds_path(pkg), 'w'), indent=1, ensure_ascii=False)
        is_gh = bool(GH.search(rec.get('obb_url') or ''))
        rows.append((short, cur['done'], cur['total'], note,
                     is_gh or short in watched,
                     _tag(rec) if is_gh else ('drive' if short in watched else ''),
                     name, logo(short), link_release(rec, _tag(rec)),
                     d.get('quest_done') or 0, d.get('quest_total') or 0))

    open(os.path.join(HERE, 'README.md'), 'w', encoding='utf-8').write(
        write_readme(rows, timestamp_line()))

    D = sum(r[1] for r in rows if r[1] is not None)
    T = sum(r[2] for r in rows if r[2] is not None)
    out += ['', '| Mod | World | Quest | % | |', '|---|---:|---:|---:|---|']
    for short, done, total, note, _a, _t, _n, _l, _f, qd, qt in rows:
        q = f'{qd}/{qt}' if qt else '-'
        out.append(f'| {short} | | | | {note} |' if done is None else
                   f'| {short} | {done}/{total} | {q} | '
                   f'{done * 100 / total:.1f}% | {note} |')
    QD = sum(r[9] for r in rows)
    QT = sum(r[10] for r in rows)
    if T:
        out.append(f'| **TOTAL** | **{D}/{T}** | **{QD}/{QT}** | '
                   f'**{D * 100 / T:.1f}%** | |')

    # A hub's gates open sub-worlds that ship no world map of their own, so
    # their levels are in no total. Reflourished's Travel Log hides several
    # hundred that way. Reported every run so the omission stays known.
    # Unpackaged worlds no gate points at stay unmentioned: those are dead
    # WORLDMAPLIST entries, not reachable content.
    for pkg in sorted(saves):
        wp = worlds_path(pkg)
        if not os.path.exists(wp):
            continue
        for w in json.load(open(wp, encoding='utf-8'))['worlds'].values():
            if w.get('opens'):
                out += ['', f'> **{pkg.rsplit("_", 1)[-1]}**: the '
                            f'`{w["name"]}` hub opens {len(w["opens"])} '
                            f'sub-worlds that ship no world map, so their '
                            f'levels are not in the total above.']

    # A save arrived for a mod nothing here has counted yet. Saves travel by
    # themselves; level counts cannot, because reading the OBB needs the mod
    # installed. Say exactly what to run rather than leaving a blank row.
    if uncounted:
        out += ['', f'> **Played but not counted: {", ".join(uncounted)}.** '
                    f'The save arrived on its own, but the level count has to '
                    f'be built where the mod is installed. On that machine '
                    f'run `python3 addmod.py`, then commit the new '
                    f'`worlds/` file and `sources.json`.']

    # Non-GitHub mods, split by whether anything can watch them. A Drive OBB
    # has its size polled above, so a new build is at least noticed; the count
    # still has to be rebuilt by hand, since a Drive OBB cannot be read over
    # Range the way a GitHub one can. What is left has nothing to poll at all.
    if watched:
        out += ['', f'> Watched by their Drive OBB size: '
                    f'**{", ".join(sorted(watched))}**. A size change flags a '
                    f'new build; the count is then rebuilt on a machine that '
                    f'has the mod with `python3 addmod.py` (a Drive OBB cannot '
                    f'be re-read from the cloud like a GitHub one).']
    blind = sorted(p.rsplit('_', 1)[-1] for p in saves
                if p.rsplit('_', 1)[-1] not in uncounted
                and p.rsplit('_', 1)[-1] not in watched
                and not GH.search(src.get(p, {}).get('obb_url') or ''))
    if blind:
        out += ['', f'> Cannot be watched at all: **{", ".join(blind)}** '
                    f'(no GitHub release and no pollable Drive OBB; itch.io '
                    f'sits behind a Cloudflare check). Their updates have to be '
                    f'spotted by hand, then the count rebuilt with `python3 '
                    f'addmod.py`.']

    totals.main()
    json.dump(state, open(STATE, 'w'), indent=1, ensure_ascii=False)
    summary(out)
    print('\nCHANGED: ' + ', '.join(changed) if changed else '\nnothing new')


if __name__ == '__main__':
    main()
