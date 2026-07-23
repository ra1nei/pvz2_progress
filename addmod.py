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


def _record_obb_url(pkg, url):
    """Put a hand-supplied OBB URL into sources.json, where the rest reads it."""
    sp = os.path.join(HERE, 'sources.json')
    src = json.load(open(sp, encoding='utf-8')) if os.path.exists(sp) else {}
    src.setdefault(pkg, {})['obb_url'] = url
    with open(sp, 'w', encoding='utf-8') as f:
        json.dump(src, f, indent=1, ensure_ascii=False)
        f.write('\n')


def add_one(pkg, forced=None, obb_url=None):
    sfx = pkg.rsplit('_', 1)[-1]
    wp = os.path.join(WORLDS, f'{pkg}.json')

    # The app names itself, so nothing has to be typed. A mod that is already
    # known keeps the name it was filed under.
    from pvz.totals import NAME_MAP
    name = forced or NAME_MAP.get(sfx)
    if not name:
        from pvz.device import find_adb, find_device
        adb = find_adb(required=False)
        dev = find_device(adb, pkg) if adb else None
        if dev:
            name = name_from_apk(adb, dev, pkg)
            if name:
                print(f'  {sfx}: name from the APK itself')
    if not name:
        print(f'  {sfx}: could not read a name off the APK. It needs aapt2, '
              f'and the mod installed on a connected device.')
        print(f'      rerun with:  python3 addmod.py {pkg} --name "Display Name"')
        return False
    print(f'  {sfx}: name -> "{name}"')

    # Get the OBB URL first. With a URL the OBB can be read over the network,
    # no Android device needed, and update checks can run unattended later.
    # Passing one in covers the mod that is not on this machine at all: some
    # authors publish the link in a text file beside the APK rather than
    # building it into the app, and then nothing can extract it.
    url = obb_url or extract_obb_url(pkg)
    if obb_url:
        _record_obb_url(pkg, obb_url)
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
        # Show the child's last few lines rather than its first 200 characters.
        # The reason a fetch failed is printed as it happens, so truncating
        # from the front threw away exactly the part worth reading.
        out = (r.stdout or '') + (r.stderr or '')
        print('      counting FAILED:')
        for line in out.strip().splitlines()[-6:]:
            print(f'        {line}')
        return False

    d = json.load(open(wp, encoding='utf-8'))
    d['_display_name'] = name                  # pvz/totals.py reads this back
    json.dump(d, open(wp, 'w'), indent=1, ensure_ascii=False)
    total = sum(w['total'] for w in d['worlds'].values() if w['counted'])
    print(f'      {total} levels  ({"read from URL" if url else "read off the device"})')
    return True


def set_link(sfx, url):
    """Record where a mod is published, keyed by package suffix.

    The one thing here that cannot be worked out: nothing on the device says
    where its mod came from, and the APK only carries an OBB URL when the mod
    ships a downloader. Taking it as a flag at least saves editing JSON.
    """
    p = os.path.join(HERE, 'links.json')
    d = json.load(open(p, encoding='utf-8'))
    d[sfx] = url
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(d, f, indent=1, ensure_ascii=False)
        f.write('\n')          # the file is hand-edited too, keep it tidy


def main():
    ap = argparse.ArgumentParser(description='Add a new PvZ2 mod to the tracker')
    ap.add_argument('pkg', nargs='?')
    ap.add_argument('--name', help='force the display name when the guess is wrong')
    ap.add_argument('--link', help='the mod\'s download page, written into links.json')
    ap.add_argument('--obb-url', dest='obb_url',
                    help='read the OBB from here, for a mod not installed on '
                         'this machine or one that publishes the link separately')
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

    ok = sum(add_one(p, a.name, a.obb_url) for p in todo)

    if not ok:
        return

    # Only with one mod in hand: a link belongs to a single mod, and applying
    # it to a batch would file every one of them under the same page.
    linked = bool(a.link) and len(todo) == 1
    if linked:
        set_link(todo[0].rsplit('_', 1)[-1], a.link)
        print(f'      links.json -> {a.link}')
    elif a.link:
        print('      [!] --link needs one mod; name the package to use it')

    totals.main()
    steps = ([] if linked else
             ["add the mod's download page to links.json, keyed by suffix"])
    steps += ['python3 install.py scan     finds its APK and OBB',
              'python3 sync.py push       sends your save up',
              'commit worlds/, sources.json, links.json, install.json']
    print('\nLeft to do:')
    for i, s in enumerate(steps, 1):
        print(f'  {i}. {s}')


if __name__ == '__main__':
    main()
