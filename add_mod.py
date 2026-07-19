#!/usr/bin/env python3
"""Onboard a new mod in one command, working out its name automatically.

    python3 add_mod.py                     scan, add every mod with no counts
    python3 add_mod.py com.ea.game.pvz2_xx add just that one
    python3 add_mod.py <pkg> --name "Name" force the display name

What it does:
  1. find installed mods that have no level counts yet
  2. work out the display name by matching the package suffix against the
     Drive folder names (the suffix is a subsequence of the name: cld is
     inside Collided, rfl inside Reflourished)
  3. extract the OBB download URL from the APK into sources.json, which is
     what lets update checks run unattended later
  4. read the OBB and count the levels
  5. regenerate pvz_totals.json

What is left to you: create a Drive folder with the same display name and
drop the mod's save file in it. GitHub Actions handles everything else.
"""
import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WORLDS = os.path.join(HERE, 'worlds')
PREFIX = 'plantsvszombies2'          # shared folder-name prefix, stripped before matching


def norm(s):
    return re.sub(r'[^a-z0-9]', '', str(s).lower())


def la_day_con(sub, s):
    """Is 'cld' a subsequence of 'collided'."""
    it = iter(s)
    return all(c in it for c in sub)


def doan_ten(sfx, ten_folder):
    """Guess the display name from the Drive folder list. None if ambiguous."""
    hits = []
    for f in ten_folder:
        n = norm(f)
        if n == 'logo':
            continue
        core = n[len(PREFIX):] if n.startswith(PREFIX) else n
        if core and la_day_con(sfx, core):
            hits.append(f)
    if len(hits) != 1:
        return None, hits
    ten = re.sub(r'^plants\s*vs\.?\s*zombies\s*2\s*[:\-]\s*', '', hits[0], flags=re.I)
    return ten.strip(), hits


def ten_folder_drive():
    try:
        import drive
        return list(drive.list_folder(drive.ROOT_ID))
    except Exception as e:
        print(f'  [!] could not read the Drive folder ({e}), '
              f'you will have to pass --name')
        return []


def moi_url(pkg):
    """Extract the OBB URL from the APK into sources.json. Returns it or None.

    Mods whose app downloads its own OBB keep the URL in the dex. Mods where
    the OBB is copied in by hand have no URL to find, which is a limit of the
    mod rather than of this tool.
    """
    sp = os.path.join(HERE, 'sources.json')
    src = json.load(open(sp, encoding='utf-8')) if os.path.exists(sp) else {}
    if src.get(pkg, {}).get('obb_url'):
        return src[pkg]['obb_url']
    try:
        from adb_util import find_adb, find_device, sh
        from find_sources import apk_urls
        adb = find_adb(required=False)
        dev = find_device(adb, pkg) if adb else None
        if not dev:
            return None
        apk = sh(adb, 'shell', 'pm', 'path', pkg, serial=dev,
                 check=False).strip().replace('package:', '').split('\n')[0].strip()
        if not apk:
            return None
        urls, size = apk_urls(adb, dev, apk)
        best = [u for u in urls if '.obb' in u.lower()
                or pkg.rsplit('_', 1)[-1] in u.lower()]
        rec = src.setdefault(pkg, {})
        rec['apk_size'] = size
        rec['candidates'] = urls[:20]
        if best:
            rec['obb_url'] = best[0]
        json.dump(src, open(sp, 'w'), indent=1, ensure_ascii=False)
        return best[0] if best else None
    except Exception as e:
        print(f'      [!] URL extraction failed: {type(e).__name__}: {e}')
        return None


def them(pkg, ten_folder, ep_ten=None):
    sfx = pkg.rsplit('_', 1)[-1]
    wp = os.path.join(WORLDS, f'{pkg}.json')

    ten, hits = (ep_ten, [ep_ten]) if ep_ten else doan_ten(sfx, ten_folder)
    if not ten:
        print(f'  {sfx}: could not work out the name '
              f'({"several candidates: " + str(hits) if hits else "no Drive folder found"})')
        print(f'      rerun with:  python3 add_mod.py {pkg} --name "Display Name"')
        return False
    print(f'  {sfx}: name -> "{ten}"')

    # Get the OBB URL first. With a URL the OBB can be read over the network,
    # no Android device needed, and update checks can run unattended later.
    url = moi_url(pkg)
    if url:
        print(f'      OBB URL: {url}')
    else:
        print('      OBB URL: NONE '
              '(the OBB is copied in by hand, so the app carries no URL; '
              'this mod has to be tracked manually)')

    cmd = [sys.executable, os.path.join(HERE, 'build_worlds.py'), pkg]
    if url:
        cmd += ['--url', url]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=HERE)
    if r.returncode != 0 or not os.path.exists(wp):
        print(f'      counting FAILED: {(r.stderr or r.stdout).strip()[:200]}')
        return False

    d = json.load(open(wp, encoding='utf-8'))
    d['_display_name'] = ten                  # emit_totals.py reads this back
    json.dump(d, open(wp, 'w'), indent=1, ensure_ascii=False)
    tot = sum(w['total'] for w in d['worlds'].values() if w['counted'])
    print(f'      {tot} levels  ({"read from URL" if url else "read off the device"})')
    return True


def main():
    ap = argparse.ArgumentParser(description='Add a new PvZ2 mod to the tracker')
    ap.add_argument('pkg', nargs='?')
    ap.add_argument('--name', help='force the display name when the guess is wrong')
    a = ap.parse_args()

    os.makedirs(WORLDS, exist_ok=True)
    co_san = {f[:-5] for f in os.listdir(WORLDS) if f.endswith('.json')}

    if a.pkg:
        can_them = [a.pkg]
    else:
        from adb_util import find_adb, list_mods
        adb = find_adb(required=False)
        if not adb:
            sys.exit('No adb, so installed mods cannot be scanned.\n'
                     'Name one directly: python3 add_mod.py com.ea.game.pvz2_xx')
        cai = {p for _, (_, ps) in list_mods(adb).items() for p in ps}
        can_them = sorted(cai - co_san)
        if not can_them:
            print(f'No new mods. Tracking {len(co_san)}.')
            return
        print(f'Found {len(can_them)} mod(s) with no level counts: '
              f'{", ".join(p.rsplit("_", 1)[-1] for p in can_them)}\n')

    ten_folder = ten_folder_drive()
    ok = sum(them(p, ten_folder, a.name) for p in can_them)

    if ok:
        subprocess.run([sys.executable, os.path.join(HERE, 'emit_totals.py')], cwd=HERE)
        print('\nLeft to do:')
        print('  1. create a Drive folder named exactly as shown above')
        print('  2. put the save file in it, named exactly pp.dat')
        print('  3. commit the new worlds/*.json and pvz_totals.json')


if __name__ == '__main__':
    main()
