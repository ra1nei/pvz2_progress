#!/usr/bin/env python3
"""Put the mods you are playing onto a machine that has none of them.

    python3 install.py scan        find the APK and OBB for each mod
    python3 install.py status      installed here vs available
    python3 install.py auto        install or update everything you play
    python3 install.py install rfl just that one

`auto` is the point: on a fresh machine, run it and you get the mods you have a
save for, each with your latest save already in place and playable at once, no
second step.

Where the files come from:
    APK  <- the mod's own Drive folder, listed in links.json
    OBB  <- sources.json when it is on GitHub Releases, otherwise Drive
    save <- saves/ in this repo, the same one sync.py keeps up to date

Updating a sideloaded mod is the part that bites. A rebuilt APK is often signed
with a different key, `adb install -r` refuses it, and the usual fix is to
uninstall first, which deletes the save with it. So the save is pulled out
before any uninstall and pushed back after, always.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

import pvz.drive as drive
from pvz.device import find_adb, sh
from pvz.github import GH, latest_release
from sync import (SAVE_PATHS, SAVES, cleared, save_paths, refresh_saves,
                       connect)

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, 'install.json')
DOWNLOADS = os.path.join(HERE, 'downloads')
PKG = 'com.ea.game.pvz2_{}'


def read_config():
    return json.load(open(CONFIG, encoding='utf-8')) if os.path.exists(CONFIG) else {}


def write_config(d):
    json.dump(d, open(CONFIG, 'w'), indent=1, ensure_ascii=False)


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def progress(name):
    def f(x, total):
        if total and x % (50 << 20) < (1 << 20):
            print(f'      {x / 1048576:>6.0f} / {total / 1048576:.0f} MB  {name}', flush=True)
    return f


# ---------------------------------------------------------------- discovery

def scan():
    """Find each mod's APK in its Drive folder and its OBB source.

    Ambiguous cases are written out as choices rather than guessed at: Collided
    ships a 30 and a 60 FPS build, Fallen a 32 and a 64 bit one, and picking
    for you would install something you did not ask for.
    """
    links = json.load(open(os.path.join(HERE, 'links.json'), encoding='utf-8'))
    src = json.load(open(os.path.join(HERE, 'sources.json'), encoding='utf-8'))
    cfg = read_config()

    for sfx, url in sorted(links.items()):
        if sfx.startswith('_'):
            continue
        rec = cfg.setdefault(sfx, {})
        rec['obb_url'] = src.get(PKG.format(sfx), {}).get('obb_url', '')

        m = re.search(r'/folders/([\w-]+)', str(url))
        if not m:
            print(f'{sfx:<5} {url} is not a Drive folder, APK must be set by hand')
            continue
        try:
            items = drive.list_folder(m.group(1))
        except Exception as e:
            print(f'{sfx:<5} cannot read the Drive folder: {e}')
            continue
        # An OBB in the folder is only worth looking for when GitHub has none.
        # Several mods keep theirs one level down, in a folder called OBB.
        if not rec['obb_url']:
            obbs = {n: i for n, (i, is_dir) in items.items()
                    if not is_dir and n.lower().endswith('.obb')}
            for n, (i, is_dir) in items.items():
                if is_dir and 'obb' in drive.norm(n):
                    try:
                        obbs.update({x: y for x, (y, d2) in drive.list_folder(i).items()
                                     if not d2 and x.lower().endswith('.obb')})
                    except Exception:
                        pass
            if len(obbs) == 1:
                rec['obb_name'], rec['obb_id'] = next(iter(obbs.items()))
                print(f'{sfx:<5} OBB in Drive: {rec["obb_name"]}')
            elif obbs:
                print(f'{sfx:<5} {len(obbs)} OBBs in Drive, none chosen: {sorted(obbs)}')

        apks = {n: i for n, (i, is_dir) in items.items()
                if not is_dir and n.lower().endswith('.apk')}
        if not apks:
            print(f'{sfx:<5} no APK in the folder')
            rec.pop('apk_id', None)
        elif len(apks) == 1:
            n, i = next(iter(apks.items()))
            rec['apk_name'], rec['apk_id'] = n, i
            rec.pop('apk_choices', None)
            print(f'{sfx:<5} {n}')
        else:
            rec['apk_choices'] = apks
            keep = rec.get('apk_name')
            print(f'{sfx:<5} {len(apks)} APKs, pick one with '
                  f'`install.py pick {sfx} "<name>"`:')
            for n in sorted(apks):
                print(f'        {"* " if n == keep else "  "}{n}')
        write_config(cfg)
    print(f'\n-> {CONFIG}')


def pick(sfx, name):
    cfg = read_config()
    rec = cfg.get(sfx) or {}
    choices = rec.get('apk_choices') or ({rec['apk_name']: rec['apk_id']}
                                     if rec.get('apk_name') else {})
    hit = [n for n in choices if name.lower() in n.lower()]
    if len(hit) != 1:
        sys.exit(f'{name!r} matches {len(hit)} of: {sorted(choices)}')
    rec['apk_name'], rec['apk_id'] = hit[0], choices[hit[0]]
    cfg[sfx] = rec
    write_config(cfg)
    print(f'{sfx}: will install {hit[0]}')


# ---------------------------------------------------------------- device

def installed(adb, dev, pkg):
    """(installed, versionName). versionName is '' when it cannot be read."""
    out = sh(adb, 'shell', 'pm', 'list', 'packages', pkg, serial=dev, check=False)
    if pkg not in out:
        return False, ''
    d = sh(adb, 'shell', 'dumpsys', 'package', pkg, serial=dev, check=False)
    m = re.search(r'versionName=(\S+)', d)
    return True, m.group(1) if m else ''


def obb_on_device(adb, dev, pkg):
    """(name, size) of the OBB already on the device, or (None, 0)."""
    out = sh(adb, 'shell', f"ls -l /sdcard/Android/obb/{pkg}/ 2>/dev/null",
             serial=dev, check=False)
    for line in out.splitlines():
        if '.obb' in line:
            p = line.split()
            kich = next((int(x) for x in p if x.isdigit() and int(x) > 1000), 0)
            return p[-1], kich
    return None, 0


# ---------------------------------------------------------------- install

def fetch_apk(sfx, rec):
    """Fetch the APK. `apk_url` wins over the Drive id when both are set.

    apk_url exists for the mods that publish somewhere this cannot scrape:
    Requiem hands out MediaFire links inside a text file, Spice ships from
    itch.io. Paste a direct link into install.json and they install like the
    rest; guessing at those hosts would break the first time they redesign.
    """
    os.makedirs(DOWNLOADS, exist_ok=True)
    dest = os.path.join(DOWNLOADS, f'{sfx}.apk')
    ghi = rec.get('apk_sha256')
    if os.path.exists(dest) and ghi and sha256(dest) == ghi:
        return dest

    if rec.get('apk_url'):
        from pvz.net import http_stream
        print(f'      downloading {rec["apk_url"]}')
        if not http_stream(rec['apk_url'], dest, progress=progress('apk')):
            print('      APK download failed')
            return None
    elif rec.get('apk_id'):
        print(f'      downloading {rec["apk_name"]}')
        if not drive.download_big(rec['apk_id'], dest, progress('apk')):
            print('      APK download failed')
            return None
    else:
        return None
    with open(dest, 'rb') as f:
        if f.read(2) != b'PK':
            print('      not an APK (no zip header), refusing to install it')
            os.remove(dest)
            return None
    import zipfile
    try:
        if 'AndroidManifest.xml' not in zipfile.ZipFile(dest).namelist():
            print('      zip without AndroidManifest.xml, not an APK')
            os.remove(dest)
            return None
    except zipfile.BadZipFile:
        print('      corrupt download')
        os.remove(dest)
        return None

    now = sha256(dest)
    # The same Drive file changing content is worth stopping for. It usually
    # means a new build, but it is also what a swapped file looks like, and
    # this installs with no further questions asked.
    if ghi and ghi != now:
        print(f'      NOTE: this APK changed since last time.')
        print(f'        was {ghi[:16]}...  now {now[:16]}...')
    rec['apk_sha256'] = now
    return dest


def fetch_obb(sfx, rec):
    """Fetch the OBB, from GitHub when possible, otherwise from Drive."""
    os.makedirs(DOWNLOADS, exist_ok=True)
    url = rec.get('obb_url') or ''
    m = GH.search(url)
    if m:
        rel = latest_release(m.group(1), m.group(2))
        asset = next((a for a in (rel or {}).get('assets', [])
                      if a['name'].endswith('.obb')), None)
        if not asset:
            return None, 0
        dest = os.path.join(DOWNLOADS, asset['name'])
        if os.path.exists(dest) and os.path.getsize(dest) == asset['size']:
            return dest, asset['size']
        print(f'      downloading {asset["name"]} ({asset["size"] / 1048576:.0f}MB)')
        from pvz.net import http_stream
        n = http_stream(asset['browser_download_url'], dest, progress=progress('obb'))
        return (dest, n) if n else (None, 0)

    if rec.get('obb_id'):
        dest = os.path.join(DOWNLOADS, rec.get('obb_name') or f'main.{sfx}.obb')
        print(f'      downloading {os.path.basename(dest)} from Drive')
        n = drive.download_big(rec['obb_id'], dest, progress('obb'))
        return (dest, n) if n else (None, 0)
    return None, 0


def install_one(adb, dev, sfx, cfg, force=False):
    pkg = PKG.format(sfx)
    rec = cfg.setdefault(sfx, {})
    co, ver = installed(adb, dev, pkg)
    print(f'\n== {sfx} ==')
    print(f'   installed: {ver or "no"}')

    apk = fetch_apk(sfx, rec)
    write_config(cfg)
    if not apk:
        print('   no APK available, skipping')
        return False

    # Save first, always. An uninstall takes the save with it, and a mod that
    # changed signing key can only be updated by uninstalling.
    kept = None
    if co:
        paths = save_paths(adb, dev, [pkg])
        if paths.get(pkg):
            os.makedirs(DOWNLOADS, exist_ok=True)
            kept = os.path.join(DOWNLOADS, f'pp_{sfx}.keep')
            subprocess.run([adb, '-s', dev, 'pull', paths[pkg], kept],
                           capture_output=True)
            if os.path.exists(kept) and open(kept, 'rb').read(4) == b'RTON':
                print(f'   save held aside: {cleared(kept)} cleared')
            else:
                kept = None

    r = subprocess.run([adb, '-s', dev, 'install', '-r', apk],
                       capture_output=True, text=True)
    if 'Success' not in (r.stdout or ''):
        loi = (r.stdout + r.stderr).strip()[:160]
        print(f'   install -r refused: {loi}')
        if not co or not force:
            print('   rerun with --force to uninstall and install clean '
                  '(the save is already held aside)')
            return False
        subprocess.run([adb, '-s', dev, 'uninstall', pkg], capture_output=True)
        r = subprocess.run([adb, '-s', dev, 'install', apk],
                           capture_output=True, text=True)
        if 'Success' not in (r.stdout or ''):
            print(f'   clean install failed too: {(r.stdout + r.stderr).strip()[:160]}')
            return False
    print('   APK installed')

    obb, size = fetch_obb(sfx, rec)
    write_config(cfg)
    if obb:
        name, was = obb_on_device(adb, dev, pkg)
        if was == size and name == os.path.basename(obb):
            print(f'   OBB already current ({size / 1048576:.0f}MB)')
        else:
            sh(adb, 'shell', f'mkdir -p /sdcard/Android/obb/{pkg}',
               serial=dev, check=False)
            print(f'   pushing OBB, {size / 1048576:.0f}MB, this takes a while')
            subprocess.run([adb, '-s', dev, 'push', obb,
                            f'/sdcard/Android/obb/{pkg}/{os.path.basename(obb)}'],
                           capture_output=True)
            print('   OBB in place')
    else:
        print('   no OBB source; the mod will download its own on first run')

    # Prefer the repo save: it is the one the other machine just played.
    refresh_saves()
    tu_repo = os.path.join(SAVES, f'pp_{sfx}.dat')
    save = tu_repo if os.path.exists(tu_repo) else kept
    if save:
        paths = save_paths(adb, dev, [pkg])
        dest = paths.get(pkg)
        if not dest:
            # A freshly installed mod has not made its save folder yet, so
            # there is nowhere to put the save and the search finds nothing.
            # Make the folder and drop the save in: the game reads it on first
            # launch, which saves having to start the mod and sync again.
            dest = SAVE_PATHS[0].format(pkg=pkg)
            sh(adb, 'shell', f'mkdir -p {os.path.dirname(dest)}',
               serial=dev, check=False)
        r = subprocess.run([adb, '-s', dev, 'push', save, dest],
                           capture_output=True, text=True)
        if r.returncode == 0:
            print(f'   save in place: {cleared(save)} cleared '
                  f'({"from saves/" if save == tu_repo else "the one held aside"})')
        else:
            print(f'   could not place the save: {(r.stderr or r.stdout).strip()[:110]}')
            print('   start the mod once, then run: python3 sync.py pull')
    return True


# ---------------------------------------------------------------- entry

def played_mods():
    """Package suffixes that have a save in saves/, most progress first."""
    refresh_saves()
    out = []
    for f in sorted(os.listdir(SAVES)):
        m = re.fullmatch(r'pp_(\w+)\.dat', f)
        if m:
            out.append((cleared(os.path.join(SAVES, f)), m.group(1)))
    return [s for _, s in sorted(out, reverse=True)]


def status(adb, dev, cfg):
    print(f'{"mod":<6}{"on device":<14}{"APK available":<34}{"OBB"}')
    print('-' * 74)
    for sfx in sorted(cfg):
        if sfx.startswith('_'):
            continue
        co, ver = installed(adb, dev, PKG.format(sfx))
        _n, size = obb_on_device(adb, dev, PKG.format(sfx))
        rec = cfg[sfx]
        apk = rec.get('apk_name') or (f'{len(rec["apk_choices"])} to pick from'
                                      if rec.get('apk_choices') else 'none')
        print(f'{sfx:<6}{(ver or "-") if co else "not installed":<14}{apk[:33]:<34}'
              f'{f"{size / 1048576:.0f}MB" if size else "-"}')


def main():
    ap = argparse.ArgumentParser(description='Install the PvZ2 mods you play onto this machine')
    ap.add_argument('action', choices=['scan', 'pick', 'status', 'auto', 'install'])
    ap.add_argument('args', nargs='*')
    ap.add_argument('--device')
    ap.add_argument('--force', action='store_true',
                    help='uninstall and reinstall when the signature changed')
    a = ap.parse_args()

    if a.action == 'scan':
        return scan()
    if a.action == 'pick':
        if len(a.args) != 2:
            sys.exit('usage: install.py pick <suffix> "<apk name>"')
        return pick(*a.args)

    cfg = read_config()
    if not cfg:
        sys.exit('No install.json yet. Run: python3 install.py scan')

    adb = find_adb()
    devs = connect(adb)
    dev = a.device or (devs[0] if devs else None)
    if not dev:
        sys.exit('No device. Start the emulator and try again.')

    if a.action == 'status':
        return status(adb, dev, cfg)
    if a.action == 'install':
        if not a.args:
            sys.exit('usage: install.py install <suffix>')
        for sfx in a.args:
            install_one(adb, dev, sfx, cfg, a.force)
        return

    want = played_mods()
    if not want:
        sys.exit('saves/ is empty, so there is nothing to install.')
    print(f'mods you have a save for: {", ".join(want)}')
    for sfx in want:
        install_one(adb, dev, sfx, cfg, a.force)


if __name__ == '__main__':
    main()
