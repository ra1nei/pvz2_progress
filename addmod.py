#!/usr/bin/env python3
"""Onboard a new mod in one command, working out its name automatically.

    python3 addmod.py                     scan, add every mod with no counts
    python3 addmod.py com.ea.game.pvz2_xx add just that one
    python3 addmod.py <pkg> --name "Name" force the display name

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

from pvz import totals

HERE = os.path.dirname(os.path.abspath(__file__))
WORLDS = os.path.join(HERE, 'worlds')
FOLDER_PREFIX = 'plantsvszombies2'          # shared folder-name prefix, stripped before matching


def norm(s):
    return re.sub(r'[^a-z0-9]', '', str(s).lower())


def is_subsequence(sub, s):
    """Is 'cld' a subsequence of 'collided'."""
    it = iter(s)
    return all(c in it for c in sub)


def guess_name(sfx, names):
    """Guess the display name from the Drive folder list. None if ambiguous."""
    hits = []
    for f in names:
        n = norm(f)
        if n == 'logo':
            continue
        core = n[len(FOLDER_PREFIX):] if n.startswith(FOLDER_PREFIX) else n
        if core and is_subsequence(sfx, core):
            hits.append(f)
    if len(hits) != 1:
        return None, hits
    name = re.sub(r'^plants\s*vs\.?\s*zombies\s*2\s*[:\-]\s*', '', hits[0], flags=re.I)
    return name.strip(), hits


def find_aapt():
    """aapt2 from the Android SDK, which is where the app's own name is read."""
    import glob
    from pvz.net import find_exe
    p = find_exe('aapt2')
    if p:
        return p
    for root in ('~/Library/Android/sdk', '~/Android/Sdk',
                 '~/AppData/Local/Android/Sdk'):
        hit = sorted(glob.glob(os.path.expanduser(root + '/build-tools/*/aapt2*')))
        if hit:
            return hit[-1]
    return None


def name_from_apk(adb, dev, pkg):
    """The mod's own display name, straight out of its APK.

    A new mod is by definition not in NAME_MAP, and matching its suffix against
    the names already there is what nearly filed Resonance as Reflourished. The
    app knows what it is called, so ask it: Addendum answers 'PvZ2 Addendum'.

    Needs aapt2, which ships with the Android SDK alongside adb. Without it the
    name has to be given by hand.
    """
    aapt = find_aapt()
    if not aapt:
        return None
    from pvz.device import sh
    apk = sh(adb, 'shell', 'pm', 'path', pkg, serial=dev,
             check=False).strip().replace('package:', '').split('\n')[0].strip()
    if not apk:
        return None
    import tempfile
    tmp = os.path.join(tempfile.gettempdir(), f'{pkg}.apk')
    try:
        raw = subprocess.run([adb, '-s', dev, 'exec-out', f"cat '{apk}'"],
                             capture_output=True).stdout
        if raw[:2] != b'PK':
            return None
        open(tmp, 'wb').write(raw)
        out = subprocess.run([aapt, 'dump', 'badging', tmp],
                             capture_output=True, text=True).stdout
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    m = re.search(r"^application-label:'(.+)'", out, re.M)
    if not m:
        return None
    # Trim the franchise prefix the mods all share, keeping what tells them
    # apart: 'PvZ2 Addendum' is filed as Addendum.
    return re.sub(r'^(pvz\s*2|plants\s*vs\.?\s*zombies\s*2)\s*[:\-]?\s*', '',
                  m.group(1), flags=re.I).strip() or m.group(1)


def candidate_names(sfx):
    """Names `sfx` might go by, with names another mod already owns removed.

    A suffix matches loosely, as a subsequence, so leaving the known names in
    lets a new mod take one of them: Resonance's `res` is a subsequence of
    Reflourished, and would have been filed under that name without a word.
    Anything NAME_MAP hands to a different suffix is therefore dropped, which
    leaves only names that are genuinely unclaimed.
    """
    from pvz.totals import NAME_MAP
    if sfx in NAME_MAP:
        return [NAME_MAP[sfx]]
    taken = {norm(v) for k, v in NAME_MAP.items() if k != sfx}
    out = []
    try:
        import pvz.drive as drive
        if drive.ROOT_ID:
            out = list(drive.list_folder(drive.ROOT_ID))
    except Exception:
        pass
    return [t for t in out if norm(t) not in taken]


def extract_obb_url(pkg):
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
        from pvz.device import find_adb, find_device, sh
        from pvz.apk import apk_urls
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


def add_one(pkg, names, forced=None):
    sfx = pkg.rsplit('_', 1)[-1]
    wp = os.path.join(WORLDS, f'{pkg}.json')

    name, hits = (forced, [forced]) if forced else guess_name(sfx, names)
    if not name:
        from pvz.device import find_adb, find_device
        adb = find_adb(required=False)
        dev = find_device(adb, pkg) if adb else None
        if dev:
            name = name_from_apk(adb, dev, pkg)
            if name:
                print(f'  {sfx}: name from the APK itself')
    if not name:
        print(f'  {sfx}: could not work out the name '
              f'({"several candidates: " + str(hits) if hits else "nothing to guess from"})')
        print(f'      rerun with:  python3 addmod.py {pkg} --name "Display Name"')
        return False
    print(f'  {sfx}: name -> "{name}"')

    # Get the OBB URL first. With a URL the OBB can be read over the network,
    # no Android device needed, and update checks can run unattended later.
    url = extract_obb_url(pkg)
    if url:
        print(f'      OBB URL: {url}')
    else:
        print('      OBB URL: NONE '
              '(the OBB is copied in by hand, so the app carries no URL; '
              'this mod has to be tracked manually)')

    cmd = [sys.executable, '-m', 'pvz.worlds', pkg]
    if url:
        cmd += ['--url', url]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=HERE)
    if r.returncode != 0 or not os.path.exists(wp):
        print(f'      counting FAILED: {(r.stderr or r.stdout).strip()[:200]}')
        return False

    d = json.load(open(wp, encoding='utf-8'))
    d['_display_name'] = name                  # pvz/totals.py reads this back
    json.dump(d, open(wp, 'w'), indent=1, ensure_ascii=False)
    total = sum(w['total'] for w in d['worlds'].values() if w['counted'])
    print(f'      {total} levels  ({"read from URL" if url else "read off the device"})')
    return True


def main():
    ap = argparse.ArgumentParser(description='Add a new PvZ2 mod to the tracker')
    ap.add_argument('pkg', nargs='?')
    ap.add_argument('--name', help='force the display name when the guess is wrong')
    a = ap.parse_args()

    os.makedirs(WORLDS, exist_ok=True)
    known = {f[:-5] for f in os.listdir(WORLDS) if f.endswith('.json')}

    if a.pkg:
        todo = [a.pkg]
    else:
        from pvz.device import find_adb, list_mods
        adb = find_adb(required=False)
        if not adb:
            sys.exit('No adb, so installed mods cannot be scanned.\n'
                     'Name one directly: python3 addmod.py com.ea.game.pvz2_xx')
        install_one = {p for _, (_, ps) in list_mods(adb).items() for p in ps}
        todo = sorted(install_one - known)
        if not todo:
            print(f'No new mods. Tracking {len(known)}.')
            return
        print(f'Found {len(todo)} mod(s) with no level counts: '
              f'{", ".join(p.rsplit("_", 1)[-1] for p in todo)}\n')

    ok = sum(add_one(p, candidate_names(p.rsplit('_', 1)[-1]), a.name)
             for p in todo)

    if ok:
        totals.main()
        print('\nLeft to do:')
        print('  1. add the mod\'s download page to links.json, keyed by suffix')
        print('  2. python3 install.py scan     finds its APK and OBB')
        print('  3. python3 sync.py push       sends your save up')
        print('  4. commit worlds/, sources.json, links.json, install.json')


if __name__ == '__main__':
    main()
