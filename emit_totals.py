#!/usr/bin/env python3
"""Roll every worlds/*.json up into a single pvz_totals.json.

    python3 emit_totals.py

ci.py reads this file to build the README table, so it holds display names,
per-world level ids, and whether each mod can be checked for updates
automatically.

Adding a mod normally goes through add_mod.py. Doing it by hand is two steps:
  1. python3 build_worlds.py com.ea.game.pvz2_<suffix>
  2. python3 emit_totals.py

NAME_MAP is only a fallback: add_mod.py writes the display name into the
worlds file itself. A mod missing from both gets a name guessed from its
package suffix, which is flagged in the output.
"""
import glob
import json
import os
import re

from check_updates import GH

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'pvz_totals.json')

# package suffix -> display name
NAME_MAP = {
    'rfl': 'Reflourished',
    'spi': 'Spice',
    'adm': 'Addendum',
    'auz': 'Alternate UniverZ',
    'cld': 'Collided',
    'rqm': 'Requiem',
    'fln': 'Fallen',
    'sol': 'Solstice',
}


def thong_tin_theo_doi(pkg, d):
    """Can this mod be checked for updates automatically, and at what build.

    It can when sources.json holds a GitHub Releases URL for it. Any other
    host fails this test on purpose: obb_moi_nhat() can only poll the GitHub
    API, so calling a MediaFire link "automatic" would badge the mod blue
    while nothing ever re-read it.
    """
    sp = os.path.join(HERE, 'sources.json')
    src = json.load(open(sp, encoding='utf-8')) if os.path.exists(sp) else {}
    url = src.get(pkg, {}).get('obb_url') or ''
    fp = d.get('_fingerprint', {})
    # Version, in order of preference: the fingerprint tag (present when the
    # counts came from a URL), the fingerprint source, then the sources.json
    # link. Counts built over adb carry no tag, but the link still says which
    # build is installed.
    tag = fp.get('tag') or ''
    for nguon in (str(fp.get('source', '')), url):
        if tag:
            break
        m = re.search(r'/download/([^/]+)/', nguon)
        tag = m.group(1) if m else ''
    return bool(GH.search(url)), tag


def main():
    out = {}
    for p in sorted(glob.glob(os.path.join(HERE, 'worlds', '*.json'))):
        pkg = os.path.basename(p)[:-5]
        sfx = pkg.rsplit('_', 1)[-1]
        d = json.load(open(p, encoding='utf-8'))
        name = d.get('_display_name') or NAME_MAP.get(sfx, sfx.upper())
        worlds = {k: [n[0] for n in v['nodes']]
                  for k, v in d['worlds'].items() if v['counted']}
        total = sum(len(v) for v in worlds.values())
        auto, tag = thong_tin_theo_doi(pkg, d)
        out[name] = {'pkg': pkg, 'worlds': worlds, 'total': total,
                     'auto': auto, 'tag': tag}
        star = ('' if d.get('_display_name') or sfx in NAME_MAP
                else '   <- guessed name, double-check')
        print(f'{name:<20}{total:>5} levels, {len(worlds):>2} worlds  '
              f'{"auto " + (tag or "?") if auto else "MANUAL":<18}{star}')

    json.dump(out, open(OUT, 'w'), ensure_ascii=False, separators=(',', ':'))
    print(f'\n-> {OUT}  ({os.path.getsize(OUT):,} bytes)')
    print('GitHub Actions commits this file; it is not uploaded by hand.')


if __name__ == '__main__':
    main()
