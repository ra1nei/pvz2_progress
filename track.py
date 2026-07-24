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


# The box every logo is fitted inside. They range from 1.3:1 to 4:1, so one
# fixed height makes the column as wide as the widest banner and leaves the
# squarer ones looking tiny in it, while one fixed width shrinks the banners
# instead. Fitting each inside a box makes every logo as large as it can be
# without any of them setting the column's size for the rest.
LOGO_BOX = (150, 80)


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


def logo_img(rel):
    """<img> for a logo, scaled to fit LOGO_BOX. Falls back to a plain width."""
    if not rel:
        return ''
    d = _img_size(os.path.join(HERE, rel))
    if not d or not d[1]:
        return f'<img src="{rel}" width="{LOGO_BOX[0]}">'
    bw, bh = LOGO_BOX
    k = min(bw / d[0], bh / d[1])
    return f'<img src="{rel}" width="{round(d[0] * k)}" height="{round(d[1] * k)}">'


def logo(sfx):
    """The mod's logo under assets/logo, or None when it has none.

    Committed files only. A new mod shows a blank first column until one is
    dropped in by hand, named after the package suffix.
    """
    for ext in ('png', 'webp', 'jpg'):
        if os.path.exists(os.path.join(HERE, 'assets', 'logo', f'{sfx}.{ext}')):
            return f'assets/logo/{sfx}.{ext}'
    return None


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
         '<th>Collected</th><th>Progress</th><th>Done</th>'
         '<th>Updates</th></tr>']

    def collected(short):
        """The Collected cell: plants and costumes, folded into a <details>.

        Closed, the summary is the two owned counts on their own lines, which
        is narrow enough that the disclosure triangle stays beside the first
        one. Open, each becomes a fraction with a bar. No completion tick: it
        was what pushed the widest row's triangle onto a line of its own, and
        the fraction reads as complete without it once opened.
        """
        wp = os.path.join(HERE, 'worlds', f'com.ea.game.pvz2_{short}.json')
        sp = os.path.join(HERE, 'saves', f'pp_{short}.dat')
        if not (os.path.exists(wp) and os.path.exists(sp)):
            return '<td></td>'
        try:
            col = json.load(open(wp, encoding='utf-8')).get('_collection') or {}
            info = extract(sp, f'com.ea.game.pvz2_{short}')
        except Exception:
            return '<td></td>'
        if not col.get('plants') and not col.get('costumes'):
            return '<td></td>'

        bar_d = os.path.join(HERE, 'assets', 'bar')
        os.makedirs(bar_d, exist_ok=True)

        def line(label, n, tot, kind):
            if not tot:
                return (f'<tr><td>{label}</td><td align="right">{n}</td>'
                        f'<td></td></tr>')
            pt = n / tot
            open(os.path.join(bar_d, f'{short}_{kind}.svg'), 'w').write(
                svg_bar(pt, w=90))
            return (f'<tr><td>{label}</td>'
                    f'<td align="right">{n}&nbsp;/&nbsp;{tot}</td>'
                    f'<td><img src="assets/bar/{short}_{kind}.svg" width="90">'
                    f'<br><sub>{round(pt * 100)}%</sub></td></tr>')

        pl, plt = info.get('plants_unlocked') or 0, col.get('plants') or 0
        co, cot = info.get('costumes') or 0, col.get('costumes') or 0
        return ('<td align="center"><details>'
                f'<summary>{pl}&nbsp;🌱<br>{co}&nbsp;🎩</summary>'
                '<table>'
                + line('Plants', pl, plt, 'p')
                + line('Costumes', co, cot, 'c')
                + '</table></details></td>')

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
        # nothing to do.
        if not qt:
            return '<td align="center">-</td>'
        return (f'<td align="center">{qd}&nbsp;/&nbsp;{qt}'
                f'<br>{round(qd * 100 / qt)}%</td>')

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
                f'<td align="center">{done}&nbsp;/&nbsp;{total}'
                f'<br>{round(done * 100 / total)}%</td>'
                + quest_cell(qd, qt) + collected(short) +
                f'<td align="center"><img src="assets/bar/{short}.svg" width="110"></td>'
                f'<td align="center">{"✅" if done >= total and qd >= qt else ""}</td>'
                f'<td align="center">{badge}</td>'
                '</tr>')

    for r in done:
        L.append(row(r))
    for short, _d, _t, note, _a, _tg, name, logo, _rel, _qd, _qt in pending:
        img = logo_img(logo)
        L.append(f'<tr><td align="center">{img}</td>'
                 f'<td align="center">{name_cell(name, short)}</td>'
                 f'<td colspan="6">no level count yet</td></tr>')
    L += ['</table>', '',
          'World is the levels the game shows on its world maps. Quest is the '
          'levels reachable only through the quest system, which is where the '
          'Epic chains live; a chain counts as done all at once, because that '
          'is the only granularity the save records. A dash means there is '
          'nothing to count: Requiem ships no registry at all, and Alternate '
          "UniverZ's quests are either switched off, repeating events, or "
          'levels already on its maps. Collected opens where it sits, for the '
          'plants and costumes that save holds against what the mod offers. '
          'The bar and the tick both count World and Quest together, so a '
          'mod is only finished once its quests are too. '
          'Mod names link to where the build came from. A blue badge links '
          'to the GitHub release the level count was read from, and is '
          're-checked every run. Amber means the mod ships its OBB outside '
          'GitHub, so nothing can watch it: if that mod adds levels, the total '
          'here stays wrong until the count is rebuilt by hand.']

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
        rows.append((short, cur['done'], cur['total'], note,
                     bool(GH.search(rec.get('obb_url') or '')), _tag(rec), name,
                     logo(short), link_release(rec, _tag(rec)),
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

    # Mods whose OBB is not on GitHub Releases cannot be re-read from the
    # cloud, so their level count is frozen. Say so rather than quietly
    # serving a stale number. Mods with no count at all are reported above
    # instead: telling you an absent number cannot be refreshed helps nobody.
    blind = sorted(p.rsplit('_', 1)[-1] for p in saves
                if p.rsplit('_', 1)[-1] not in uncounted
                and not GH.search(src.get(p, {}).get('obb_url') or ''))
    if blind:
        out += ['', f'> Cannot be checked automatically: **{", ".join(blind)}** '
                    f'(no GitHub Releases URL for their OBB). When these '
                    f'update, the level count has to be rebuilt locally with '
                    f'`pvz/worlds.py`.']

    totals.main()
    json.dump(state, open(STATE, 'w'), indent=1, ensure_ascii=False)
    summary(out)
    print('\nCHANGED: ' + ', '.join(changed) if changed else '\nnothing new')


if __name__ == '__main__':
    main()
