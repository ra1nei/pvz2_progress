#!/usr/bin/env python3
"""Put the mods you are playing onto a machine that has none of them.

    python3 install.py add <url>   take on a mod from its download page
    python3 install.py remove cld  take one off THIS machine, repo untouched
    python3 install.py clean       drop every cached download
    python3 install.py forget xx   stop tracking a mod in the repo entirely
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
import shutil
import subprocess
import sys
import time

import pvz.drive as drive
from pvz.device import pick_device, find_adb, sh
from pvz.github import GH, latest_release
from pvz import keymap
from sync import (SAVE_PATHS, SAVES, cleared, is_running, save_paths,
                       refresh_saves, connect)
# Under its own name: this file already has a progress(), for download bars.
from sync import progress as save_progress

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

# Finding the builds in a folder listing now lives in pvz/drive.py, since
# addmod.py reads the same listing when it onboards a mod.
drive_files = drive.files_of_type


def scan_one(sfx, url, cfg, src):
    """Read one mod's Drive folder into its install.json entry.

    Ambiguous cases are written out as choices rather than guessed at: Collided
    ships a 30 and a 60 FPS build, Fallen a 32 and a 64 bit one, and picking
    for you would install something you did not ask for.
    """
    rec = cfg.setdefault(sfx, {})
    rec['obb_url'] = src.get(PKG.format(sfx), {}).get('obb_url', '')

    m = re.search(r'/folders/([\w-]+)', str(url))
    if not m:
        print(f'{sfx:<5} {url} is not a Drive folder, APK must be set by hand')
        return
    try:
        items = drive.list_folder(m.group(1))
    except Exception as e:
        print(f'{sfx:<5} cannot read the Drive folder: {e}')
        return
    if not items:
        # Empty is not the same as "no APK there". A machine whose HTTPS is
        # broken gets an empty listing for every mod, and treating that as
        # fact used to wipe the ids that were already known good.
        print(f'{sfx:<5} the folder listing came back empty, leaving what '
              f'is already known alone')
        return
    # An OBB in the folder is only worth looking for when GitHub has none.
    if not rec['obb_url']:
        obbs = drive_files(items, '.obb')
        if len(obbs) == 1:
            rec['obb_name'], rec['obb_id'] = next(iter(obbs.items()))
            print(f'{sfx:<5} OBB in Drive: {rec["obb_name"]}')
        elif obbs:
            print(f'{sfx:<5} {len(obbs)} OBBs in Drive, none chosen: {sorted(obbs)}')

    apks = drive_files(items, '.apk')
    if not apks:
        # The folder read fine and genuinely holds no APK. Keep the id
        # anyway: it worked before, and a file that moved is far more
        # likely than one that is gone for good.
        print(f'{sfx:<5} no APK in the folder'
              + (', keeping the one already recorded' if rec.get('apk_id') else ''))
    elif len(apks) == 1:
        n, i = next(iter(apks.items()))
        rec['apk_name'], rec['apk_id'] = n, i
        rec.pop('apk_choices', None)
        print(f'{sfx:<5} {n}')
    else:
        rec['apk_choices'] = apks
        keep = rec.get('apk_name')
        # A build already picked keeps its name but not necessarily its file. A
        # mod that re-uploads to fix a crash leaves the name and the version
        # alone, so only the id moves, and the recorded one becomes a 404 that
        # an install would fetch. Following the name is what keeps the choice
        # meaning what it meant.
        #
        # Only apk_id follows, which is where an install fetches from.
        # apk_installed is what went on the device, written by the install and
        # by nothing else. They were one field, and looking was enough to make
        # the update signal disappear: this printed once, recorded the new id,
        # and from then on the folder and the record agreed while the device
        # still had the old build.
        if keep in apks and rec.get('apk_id') != apks[keep]:
            rec['apk_id'] = apks[keep]
            print(f'{sfx:<5} {keep} was re-uploaded, now pointing at the new file')
        print(f'{sfx:<5} {len(apks)} APKs, pick one with '
              f'`install.py pick {sfx} "<name>"`:')
        for n in sorted(apks):
            print(f'        {"* " if n == keep else "  "}{n}')


def scan():
    """Find every known mod's APK in its Drive folder, and its OBB source."""
    links = json.load(open(os.path.join(HERE, 'links.json'), encoding='utf-8'))
    src = json.load(open(os.path.join(HERE, 'sources.json'), encoding='utf-8'))
    cfg = read_config()
    for sfx, url in sorted(links.items()):
        if sfx.startswith('_'):
            continue
        scan_one(sfx, url, cfg, src)
        write_config(cfg)
    print(f'\n-> {CONFIG}')


def add(url):
    """Take a mod nothing here has seen from its download page, by URL alone.

    Which package a folder holds is not something to be told: the OBB beside
    the APK is named after it, `main.675.com.ea.game.pvz2_xx.obb`, so the name
    is read off it rather than asked for. That is the one fact that had to be
    known in advance to get started, and it was already sitting in the folder.

    Only the entry is written, not the mod. Installing means a gigabyte over
    the wire and a choice of build to make first, so that stays a command of
    its own.
    """
    m = re.search(r'/folders/([\w-]+)', str(url))
    if not m:
        sys.exit(f'Not a Drive folder link: {url}\n'
                 f'A mod published elsewhere has to be added by hand: put it '
                 f'in links.json, then set apk_url in install.json.')
    try:
        items = drive.list_folder(m.group(1))
    except Exception as e:
        sys.exit(f'Cannot read that folder: {e}')
    obbs = drive_files(items, '.obb')
    pkg = next((mm.group(1) for n in obbs
                for mm in [re.match(r'main\.\d+\.(com\.ea\.game\.pvz2_\w+)\.obb',
                                    n, re.I)] if mm), None)
    if not pkg:
        sys.exit(f'No OBB named after a package in that folder, so there is '
                 f'nothing to read the package name off.\n'
                 f'Found: {sorted(items) if items else "nothing"}')
    sfx = pkg.rsplit('_', 1)[-1]
    print(f'{sfx:<5} {pkg}, read off {next(iter(obbs))}')

    lp = os.path.join(HERE, 'links.json')
    links = json.load(open(lp, encoding='utf-8'))
    if links.get(sfx) and links[sfx] != url:
        print(f'{sfx:<5} NOTE: links.json already points somewhere else, '
              f'replacing it\n        was {links[sfx]}')
    links[sfx] = url
    with open(lp, 'w', encoding='utf-8') as f:
        json.dump(links, f, indent=1, ensure_ascii=False)
        f.write('\n')

    src = json.load(open(os.path.join(HERE, 'sources.json'), encoding='utf-8'))
    cfg = read_config()
    scan_one(sfx, url, cfg, src)
    write_config(cfg)
    print(f'\n-> links.json, {CONFIG}')
    nxt = []
    if cfg.get(sfx, {}).get('apk_choices'):
        nxt.append(f'python3 install.py pick {sfx} "<name from the list above>"')
    nxt += [f'python3 install.py install {sfx}     downloads and installs it',
            f'python3 addmod.py                  counts its levels and names it']
    print('\nNext:')
    for i, s in enumerate(nxt, 1):
        print(f'  {i}. {s}')


def cached_files(sfx):
    """What downloads/ is holding for one mod: the APK, the OBB, the kept save."""
    import glob
    out = []
    for p in glob.glob(os.path.join(DOWNLOADS, '*')):
        n = os.path.basename(p)
        if n in (f'{sfx}.apk', f'pp_{sfx}.keep') or f'pvz2_{sfx}.obb' in n:
            out.append(p)
    return out


def remove(adb, dev, sfx, force=False):
    """Take a mod off THIS machine. The repo, and every other machine, keep it.

    The counterpart of install. A finished mod is a gigabyte of OBB on the
    emulator and often another gigabyte cached in downloads/, and none of that
    is a last copy: the save and the level counts live in the repo, and the
    build itself is a download away. So this frees the disk and leaves the row
    in the table exactly as it was, still showing the progress, still watched
    for new builds, because none of that is read off this machine.

    The one thing the emulator holds alone is the save, and uninstalling
    deletes it. So the repo is checked first, by the same comparison the sync
    guard makes: if the device is further along, this stops and says to push.

    Nothing here is platform-specific. adb is found the same way on Windows,
    macOS and Linux, and the rest is deleting files.
    """
    pkg = PKG.format(sfx)
    co, ver = installed(adb, dev, pkg)
    _obb_name, obb_size = obb_on_device(adb, dev, pkg)
    files = cached_files(sfx)
    cached = sum(os.path.getsize(p) for p in files)

    if not co and not obb_size and not files:
        print(f'{sfx} is not on this machine, and nothing is cached for it.')
        return
    print(f'Removing {sfx} from this machine:')
    if co:
        print(f'  the app, version {ver or "?"}')
    if obb_size:
        print(f'  its OBB on the emulator{"":<22}{obb_size / 1048576:>8.0f} MB')
    for p in files:
        print(f'  downloads/{os.path.basename(p):<34}{os.path.getsize(p) / 1048576:>8.0f} MB')
    print(f'  {"":<44}{(obb_size + cached) / 1073741824:>8.2f} GB in total')

    # The save is the one thing that is not a copy of something else.
    behind = None
    if co:
        from sync import progress, progress_on_device
        dpath = save_paths(adb, dev, [pkg]).get(pkg)
        stored = os.path.join(SAVES, f'pp_{sfx}.dat')
        if dpath:
            here = progress_on_device(adb, dev, dpath, pkg)
            there = progress(stored, pkg) if os.path.exists(stored) else -1
            if here > there:
                behind = (here, there)
    if behind:
        print(f'\nSTOP: the emulator is further along than saves/ '
              f'({behind[0]} against {behind[1]}), and uninstalling deletes '
              f'the save.\n      Run `python3 sync.py push` first. '
              f'--force removes it anyway.')
        if not force:
            return
    if not force:
        print('\nNothing done. Add --force to go ahead.')
        return

    if co:
        r = subprocess.run([adb, '-s', dev, 'uninstall', pkg],
                           capture_output=True, text=True)
        print(f'  app: {"uninstalled" if "Success" in (r.stdout or "") else (r.stdout + r.stderr).strip()[:70]}')
    # Uninstalling does not always take the OBB folder with it, and that is
    # where the weight is, so it goes explicitly.
    sh(adb, 'shell', f'rm -rf /sdcard/Android/obb/{pkg}', serial=dev, check=False)
    left, _ = obb_on_device(adb, dev, pkg)
    print(f'  OBB: {"gone" if not left else "still there, remove it by hand"}')
    for p in files:
        os.remove(p)
    if files:
        print(f'  downloads: {len(files)} file(s) deleted')
    print(f'\nFreed about {(obb_size + cached) / 1073741824:.2f} GB. The repo '
          f'still has the save and the counts, so the table is unchanged.\n'
          f'`python3 install.py install {sfx}` puts it back.')


def clean(force=False):
    """Delete every cached download. Nothing else on the machine is touched.

    downloads/ is a staging area, not a store: an APK is kept so a re-run does
    not fetch it again, an OBB so a reinstall does not pull a gigabyte twice,
    and a .keep is the save held aside during an install. All of it is
    re-fetchable, and it is usually the largest thing in the folder.
    """
    import glob
    files = [p for p in glob.glob(os.path.join(DOWNLOADS, '*')) if os.path.isfile(p)]
    if not files:
        print('downloads/ is already empty.')
        return
    total = sum(os.path.getsize(p) for p in files)
    print(f'downloads/ holds {len(files)} file(s), {total / 1073741824:.2f} GB:')
    for p in sorted(files, key=os.path.getsize, reverse=True)[:12]:
        print(f'  {os.path.basename(p):<45}{os.path.getsize(p) / 1048576:>8.0f} MB')
    if len(files) > 12:
        print(f'  ... and {len(files) - 12} smaller')
    if not force:
        print('\nNothing done. Add --force to go ahead.')
        return
    for p in files:
        os.remove(p)
    print(f'\nfreed {total / 1073741824:.2f} GB')


def forget(sfx, force=False):
    """Take a mod out of the repo: its save, its counts, its entries, its art.

    Everything for one mod goes at once, which is the point of having this as a
    command. Left half-done by hand, the leftovers are worse than either state:
    a save with no counts puts an empty row in the table, and counts with no
    save leave a mod being watched that nobody plays.

    What this costs, and it is worth being plain about it: the memory of where
    that mod stood. Updates are spotted by comparison, a GitHub release against
    the tag in its counts file, a Drive OBB against the size recorded in
    state.json, and both of those are among the things removed. Take a mod out
    and add it back and the first reading becomes the new baseline: it cannot
    tell you the build changed while it was gone, because nothing here saw the
    old one. Nothing is lost beyond that, since git keeps every version of all
    of it, and `git log -- saves/pp_<sfx>.dat` still reads back the progress.

    Prints what would go and stops; --force is what actually deletes.
    """
    pkg = PKG.format(sfx)
    files = [os.path.join(HERE, 'saves', f'pp_{sfx}.dat'),
             os.path.join(HERE, 'saves', f'profile_{sfx}'),
             os.path.join(HERE, 'worlds', f'{pkg}.json'),
             os.path.join(HERE, 'assets', 'logo', 'box', f'{sfx}.png'),
             os.path.join(HERE, 'assets', 'bar', f'{sfx}.svg'),
             os.path.join(HERE, 'assets', 'tag', f'{sfx}.svg'),
             os.path.join(DOWNLOADS, f'{sfx}.apk')]
    files += [os.path.join(HERE, 'assets', 'logo', f'{sfx}.{e}')
              for e in ('png', 'webp', 'jpg')]
    files = [p for p in files if os.path.exists(p)]

    edits = []           # (file, key it holds for this mod)
    for name, key in (('links.json', sfx), ('install.json', sfx),
                      ('sources.json', pkg)):
        p = os.path.join(HERE, name)
        if os.path.exists(p) and key in json.load(open(p, encoding='utf-8')):
            edits.append((name, key))
    sp = os.path.join(HERE, 'state.json')
    state = json.load(open(sp, encoding='utf-8')) if os.path.exists(sp) else {}
    in_state = [k for k, d in (('mods', state.get('mods', {})),
                               ('releases', state.get('releases', {})),
                               ('watch', state.get('watch', {})))
                if pkg in d or sfx in d]

    if not files and not edits and not in_state:
        sys.exit(f'Nothing here for {sfx}. Tracking: '
                 f'{", ".join(sorted(k for k in read_config() if not k.startswith("_")))}')

    print(f'Forgetting {sfx} ({pkg}) would delete:')
    for p in files:
        print(f'  {os.path.relpath(p, HERE)}')
    for name, key in edits:
        print(f'  the {key} entry in {name}')
    for k in in_state:
        print(f'  its {k} entry in state.json')

    if not force:
        print(f'\nNothing done. Run it again with --force to go ahead.')
        print(f'The mod stays installed on the emulator either way; '
              f'`adb uninstall {pkg}` is what removes it there.')
        return

    import shutil
    for p in files:
        shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
    for name, key in edits:
        p = os.path.join(HERE, name)
        d = json.load(open(p, encoding='utf-8'))
        d.pop(key, None)
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(d, f, indent=1, ensure_ascii=False)
            f.write('\n')
    for k in in_state:
        state[k].pop(pkg, None)
        state[k].pop(sfx, None)
    if in_state:
        json.dump(state, open(sp, 'w'), indent=1, ensure_ascii=False)
    print(f'\n{sfx} is out. Run `python3 track.py` to redraw the table, then '
          f'commit.\nIt is still installed on the emulator; '
          f'`adb uninstall {pkg}` removes it there.')


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
            size = next((int(x) for x in p if x.isdigit() and int(x) > 1000), 0)
            return p[-1], size
    return None, 0


# ---------------------------------------------------------------- install

def drop(path):
    """Delete a bad download, and survive not being allowed to.

    Windows hands out WinError 32 whenever anything still holds the file, and
    an antivirus scanning what was just written counts. Leaving a junk file
    behind is a nuisance; dying halfway through ten mods is worse.
    """
    try:
        os.remove(path)
    except OSError as e:
        print(f'      (could not delete {os.path.basename(path)}: {e.strerror})')


def fetch_apk(sfx, rec, fresh=False):
    """Fetch the APK. `apk_url` wins over the Drive id when both are set.

    apk_url exists for the mods that publish somewhere this cannot scrape:
    Requiem hands out MediaFire links inside a text file, Spice ships from
    itch.io. Paste a direct link into install.json and they install like the
    rest; guessing at those hosts would break the first time they redesign.

    `fresh` re-downloads even when a matching APK is already cached. A mod
    rebuilds its APK in place, same Drive id and same filename, so the only way
    to know the build changed is to fetch it and hash it: the cache otherwise
    hands back the last one forever and no update is ever seen. Set for an
    explicit `install <mod>`, where the point is to get whatever is current;
    left off for `auto`, where re-downloading every mod each run would burn
    bandwidth and Drive's daily quota for nothing.
    """
    os.makedirs(DOWNLOADS, exist_ok=True)
    dest = os.path.join(DOWNLOADS, f'{sfx}.apk')
    ghi = rec.get('apk_sha256')
    if not fresh and os.path.exists(dest) and ghi and sha256(dest) == ghi:
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
    # Every check below closes the file before deleting it. Windows refuses to
    # unlink a file anything still holds open, so removing it from inside the
    # `with` that opened it crashed the whole run there while passing on macOS.
    with open(dest, 'rb') as f:
        head = f.read(2)
    if head != b'PK':
        n = os.path.getsize(dest)
        print(f'      not an APK: {n:,} bytes starting {head!r}, refusing to '
              f'install it')
        if n < 4000:
            with open(dest, 'rb') as f:
                snippet = f.read(300).decode('utf-8', 'replace').strip()
            print(f'      what came back instead: {snippet[:200]}')
        drop(dest)
        return None

    import zipfile
    try:
        with zipfile.ZipFile(dest) as z:
            ok = 'AndroidManifest.xml' in z.namelist()
        if not ok:
            print('      zip without AndroidManifest.xml, not an APK')
            drop(dest)
            return None
    except zipfile.BadZipFile:
        print('      corrupt download')
        drop(dest)
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


def obb_wanted(rec):
    """(name, size) the device should end up with, without downloading it.

    GitHub lists its assets, so both are known for a couple of KB of API. Drive
    tells you nothing without fetching, so size comes back 0 there and the
    caller falls back to comparing the name.
    """
    m = GH.search(rec.get('obb_url') or '')
    if m:
        rel = latest_release(m.group(1), m.group(2))
        asset = next((a for a in (rel or {}).get('assets', [])
                      if a['name'].endswith('.obb')), None)
        return (asset['name'], asset['size']) if asset else (None, 0)
    if rec.get('obb_id'):
        return rec.get('obb_name') or '', 0
    return None, 0


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


def wake_folder(adb, dev, pkg, wait=60):
    """Have the mod make its own save folder, by opening it and closing it.

    The folder has to belong to the game, not to adb. One made over adb belongs
    to `shell`, and then the files the game writes inside land in a group adb
    cannot read, so the save quietly stops syncing; made by the game, the folder
    carries setgid and everything inside stays readable both ways. That is the
    whole difference between a mod that syncs for months and one that does not.

    It costs a launch, and no more than that. The folder appears within seconds
    of the app starting, long before the terms screen and nowhere near the
    prologue, so nothing has to be played and nothing has to be tapped. Then the
    app is stopped again and the save goes in.

    Returns the folder, or None if it never appeared.
    """
    base = os.path.dirname(SAVE_PATHS[0].format(pkg=pkg))
    subprocess.run([adb, '-s', dev, 'shell', 'monkey', '-p', pkg,
                    '-c', 'android.intent.category.LAUNCHER', '1'],
                   capture_output=True)
    found = False
    for _ in range(max(1, wait // 5)):
        time.sleep(5)
        if sh(adb, 'shell', f'[ -d "{base}" ] && echo Y',
              serial=dev, check=False).strip() == 'Y':
            found = True
            break
    sh(adb, 'shell', f'am force-stop {pkg}', serial=dev, check=False)
    time.sleep(2)
    return base if found else None


def pick_save(kept, tu_repo, pkg):
    """Which save goes back after an install, the device's or the repo's.

    It used to be the repo's whenever there was one, on the reasoning that the
    repo holds what the other machine last played. That only holds while every
    session gets pushed. Play here, install before pushing, and the older repo
    save goes back over the newer one, which is a loss that shows no sign: the
    file lands, the game opens, and the only trace is a level count that used
    to be higher. So compare the two, the way sync.py has compared both ends
    from the beginning, and put back whichever is further on.

    Returns (path or None, lines to print).
    """
    on_repo = tu_repo and os.path.exists(tu_repo)
    if not (kept and on_repo):
        return (tu_repo if on_repo else kept), []
    try:
        here, there = save_progress(kept, pkg), save_progress(tu_repo, pkg)
    except (Exception, SystemExit):
        # SystemExit as well: rton.decode raises it on a file that is not a
        # save, and a half-pulled file is exactly that. Reading one must not
        # take the install down with it.
        # Unreadable either side, so nothing to compare on. The repo copy is
        # the shared one, which is the safer thing to land.
        return tu_repo, ['could not read both saves to compare, using the repo one']
    if here > there:
        return kept, [f'the save on the emulator is further on than the repo, '
                      f'{here} against {there}, putting that one back instead',
                      'push it once the game is closed: python3 sync.py pull']
    if there > here:
        return tu_repo, [f'repo save is further on, {there} against {here}']
    return tu_repo, []


def install_one(adb, dev, sfx, cfg, force=False, fresh=False, apk_path=None):
    pkg = PKG.format(sfx)
    rec = cfg.setdefault(sfx, {})
    co, ver = installed(adb, dev, pkg)
    print(f'\n== {sfx} ==')
    print(f'   installed: {ver or "no"}')

    if co and is_running(adb, dev, pkg):
        print(f'   {sfx} is open on the emulator right now.')
        print('   Close it from inside the game first, back to the world map or')
        print('   out to the Android home screen, and give it a few seconds to')
        print('   write. Anything it has not written yet is lost when the')
        print('   installer stops it, and no copy of it exists to put back.')
        print(f'   Then run the same command again, or --force to install anyway.')
        if not force:
            return False

    if apk_path:
        # A build handed over by hand, for when the folder will not serve it.
        # Drive stops serving a popular file once it has been fetched too often
        # that day, and both of Reflourished's copies hit that at once, so the
        # only way through was a copy from elsewhere. Checked like any other:
        # what arrives from a quota page is an HTML file with an APK's name.
        import zipfile
        apk = apk_path
        try:
            with zipfile.ZipFile(apk) as z:
                ok = 'AndroidManifest.xml' in z.namelist()
        except Exception:
            ok = False
        if not ok:
            print(f'   {apk} is not an APK, refusing to install it')
            return False
        rec['apk_sha256'] = sha256(apk)
        print(f'   using {os.path.basename(apk)} '
              f'({os.path.getsize(apk) / 1048576:.0f}MB, given by hand)')
        # Take a note of what the folder is offering while we are here. Without
        # it the recorded id stays at whatever it was before, so every later
        # check reports the same re-upload for ever and starts being ignored,
        # which is worse than not checking. This records that the folder was at
        # this file when the build went on; it does not claim the two are the
        # same bytes, which is unknowable while Drive refuses to serve it.
        try:
            lp = os.path.join(HERE, 'links.json')
            links = json.load(open(lp, encoding='utf-8'))
            m2 = re.search(r'/folders/([\w-]+)', str(links.get(sfx) or ''))
            if m2:
                apks = drive.files_of_type(drive.list_folder(m2.group(1)), '.apk')
                name = rec.get('apk_name')
                if name in apks and apks[name] != rec.get('apk_id'):
                    rec['apk_id'] = apks[name]
                    if 'apk_choices' in rec:
                        rec['apk_choices'] = apks
                    print(f'   the folder has moved on too, noting its file')
        except Exception:
            pass
    else:
        apk = fetch_apk(sfx, rec, fresh=fresh)
    write_config(cfg)
    if not apk:
        print('   no APK available, skipping')
        return False

    # Save first, always. An uninstall takes the save with it, and a mod that
    # changed signing key can only be updated by uninstalling.
    kept = None
    if co:
        paths = save_paths(adb, dev, [pkg], quiet=True)
        if paths.get(pkg):
            os.makedirs(DOWNLOADS, exist_ok=True)
            kept = os.path.join(DOWNLOADS, f'pp_{sfx}.keep')
            subprocess.run([adb, '-s', dev, 'pull', paths[pkg], kept],
                           capture_output=True)
            if os.path.exists(kept) and open(kept, 'rb').read(4) == b'RTON':
                print(f'   save held aside: {cleared(kept)} cleared')
                # And kept for good, under its own name. The one file used to
                # be overwritten by the next install, so by the time anyone
                # noticed a save had gone, the copy that could have brought it
                # back had been written over twice. Copies are a few kilobytes.
                import shutil
                stamp = time.strftime('%Y%m%d_%H%M%S')
                shutil.copy(kept, os.path.join(DOWNLOADS, f'pp_{sfx}.{stamp}.keep'))
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
    # Now, and only here, does the record of what is on the device move. Reading
    # the folder must never write this: that is what let a re-upload be reported
    # once and then look settled while the device still had the old build.
    if rec.get('apk_id'):
        rec['apk_installed'] = rec['apk_id']
        write_config(cfg)
    # A new package starts with no key mapping at all, so hand it the shared
    # one. Host side, nothing to do with the device.
    keymap.apply(pkg, force)

    # Ask what the device already has BEFORE fetching anything. An OBB runs to
    # 1.3 GB, and a machine that is merely being re-run would otherwise
    # download every one of them only to find it had them already.
    want, want_size = obb_wanted(rec)
    have, have_size = obb_on_device(adb, dev, pkg)
    if not want:
        print('   no OBB source; the mod will download its own on first run')
    elif have == want and (have_size == want_size or not want_size):
        # Drive gives no size in advance, so there the name is all there is to
        # go on. A rebuild published under the same name looks identical; that
        # is the same blind spot the amber badge already stands for.
        print(f'   OBB already there ({have_size / 1048576:.0f}MB), not downloading')
    else:
        obb, size = fetch_obb(sfx, rec)
        write_config(cfg)
        if not obb:
            print('   OBB download failed, leaving the one on the device alone')
        else:
            sh(adb, 'shell', f'mkdir -p /sdcard/Android/obb/{pkg}',
               serial=dev, check=False)
            print(f'   pushing OBB, {size / 1048576:.0f}MB, this takes a while')
            subprocess.run([adb, '-s', dev, 'push', obb,
                            f'/sdcard/Android/obb/{pkg}/{os.path.basename(obb)}'],
                           capture_output=True)
            print('   OBB in place')
    write_config(cfg)

    refresh_saves()
    tu_repo = os.path.join(SAVES, f'pp_{sfx}.dat')
    save, said = pick_save(kept, tu_repo, pkg)
    for line in said:
        print(f'   {line}')
    if save:
        paths = save_paths(adb, dev, [pkg], quiet=True)
        dest = paths.get(pkg)
        if not dest:
            # The save folder is the game's to make, not ours: see wake_folder.
            # So the mod is opened for a few seconds and closed again, which is
            # all it takes, rather than adb making the folder itself.
            print('   no save folder yet, opening the mod so it makes its own')
            base = wake_folder(adb, dev, pkg)
            if not base:
                print(f'   it did not appear. Open {sfx} once by hand, then: '
                      f'python3 sync.py pull')
                return True
            dest = f'{base}/pp.dat'
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


def apk_behind(sfx, rec):
    """Whether the Drive folder now offers a different APK file, or None.

    Told by the file id, not by any version. A mod fixing a crash re-uploads
    the APK under the same name and the same version number, leaves the OBB
    alone, and says nothing anywhere: the name matches, the versionName matches,
    the OBB matches, and the only thing that moved is which file the folder
    points at. Reflourished did exactly that and every check here was blind to
    it, which is what this is for.

    The recorded id going 404 says the same thing from the other side, and is
    worth catching because that is what an install would hit.
    """
    p = os.path.join(HERE, 'links.json')
    links = json.load(open(p, encoding='utf-8')) if os.path.exists(p) else {}
    m = re.search(r'/folders/([\w-]+)', str(links.get(sfx) or ''))
    # What went on the device, not what the folder was last seen at. Falls back
    # for a mod recorded before the two were told apart.
    have = rec.get('apk_installed') or rec.get('apk_id')
    if not m or not have:
        return None
    try:
        apks = drive.files_of_type(drive.list_folder(m.group(1)), '.apk')
    except Exception:
        return None
    if not apks or have in apks.values():
        return None                      # the folder still points at ours
    name = rec.get('apk_name')
    if name in apks:
        return f'APK re-uploaded as {name}'
    return f'APK replaced, the folder now has: {", ".join(sorted(apks))}'


def behind(adb, dev, sfx, rec):
    """Whether the device's OBB is older than the published one, as a phrase.

    Device against the repo's pinned build, by the OBB alone, told by name and
    size against the published GitHub asset. Needs a GitHub call, so it is
    best-effort and stays silent when the network or the rate limit does not
    allow it.

    The APK is deliberately not compared. Its versionName lives in a different
    space from the release tag: a mod tagged v1.3.1b can still stamp its APK
    1.3.0 and leave it there for a month, so reading the tag as the APK's due
    version flags an update that reinstalling can never clear. There is no APK
    metadata to compare without downloading it, so the honest check is to
    re-fetch on an explicit `install` and let fetch_apk report whether the
    bytes changed.
    """
    pkg = PKG.format(sfx)
    co, _ = installed(adb, dev, pkg)
    if not co:
        return None
    news = []
    try:
        want_name, want_size = obb_wanted(rec)
        have_name, have_size = obb_on_device(adb, dev, pkg)
        if want_name and want_size and (have_name != want_name or have_size != want_size):
            news.append(f'OBB {have_size // 1048576}MB -> {want_size // 1048576}MB')
    except Exception:
        pass
    a = apk_behind(sfx, rec)
    if a:
        news.append(a)
    return '; '.join(news) or None


def status(adb, dev, cfg):
    print(f'{"mod":<6}{"on device":<14}{"release":<12}{"OBB":<9}{"update"}')
    print('-' * 74)
    for sfx in sorted(cfg):
        if sfx.startswith('_'):
            continue
        rec = cfg[sfx]
        co, ver = installed(adb, dev, PKG.format(sfx))
        _n, size = obb_on_device(adb, dev, PKG.format(sfx))
        m = GH.search(rec.get('obb_url') or '')
        rel = m.group(3).lstrip('v') if m else '-'
        upd = behind(adb, dev, sfx, rec) if co else ''
        print(f'{sfx:<6}{(ver or "-") if co else "not installed":<14}{rel:<12}'
              f'{f"{size / 1048576:.0f}MB" if size else "-":<9}{upd or "OBB current" if co else "-"}')
    print('\n"on device" is the APK versionName; "release" is the OBB tag, a '
          'separate number, so a mismatch is not itself an update. What is '
          'compared is the OBB, by size, and the APK, by which file the mod\'s\n'
          'folder points at: a rebuild is re-uploaded under the same name and '
          'the same version, so the file id is the only thing that moves.\n'
          'After an APK change run `install.py scan` first, which picks up the '
          'new file, then `install.py install <mod>`.')


def keymaps(only, force=False):
    """Give every mod you play the shared keyboard layout.

    Runs without a device: the files live on this computer, not on the
    emulator. Which is also why it can be run before anything is installed.
    """
    d = keymap.folder()
    if not d:
        print('No BlueStacks keymap folder here. Key mapping is a BlueStacks '
              'feature and its file format is its own, so there is nothing to '
              'write on another emulator.')
        return
    print(f'{d}\n')
    known = set(played_mods()) | {k for k in read_config() if not k.startswith('_')}
    for sfx in (only or sorted(known)):
        if sfx not in known:
            # A typo would otherwise leave a layout filed under a package that
            # does not exist, which nothing would ever read or clean up.
            print(f'  {sfx:<5} not a mod here: {", ".join(sorted(known))}')
            continue
        print(f'  {sfx:<5} {keymap.apply(PKG.format(sfx), force, quiet=True)}')


def main():
    ap = argparse.ArgumentParser(description='Install the PvZ2 mods you play onto this machine')
    ap.add_argument('action',
                    choices=['add', 'install', 'remove', 'clean', 'forget',
                             'scan', 'pick', 'status', 'auto', 'keymap'])
    ap.add_argument('args', nargs='*')
    ap.add_argument('--device')
    ap.add_argument('--force', action='store_true',
                    help='uninstall and reinstall when the signature changed')
    ap.add_argument('--apk', metavar='PATH',
                    help='install this APK file instead of fetching one, for '
                         'when the mod\'s folder has stopped serving it')
    a = ap.parse_args()

    if a.action == 'add':
        if len(a.args) != 1:
            sys.exit('usage: install.py add "<the mod\'s Drive folder link>"')
        return add(a.args[0])
    if a.action == 'clean':
        return clean(a.force)
    if a.action == 'forget':
        if len(a.args) != 1:
            sys.exit('usage: install.py forget <suffix> [--force]')
        return forget(a.args[0], a.force)
    if a.action == 'scan':
        return scan()
    if a.action == 'keymap':
        return keymaps(a.args, a.force)
    if a.action == 'pick':
        if len(a.args) != 2:
            sys.exit('usage: install.py pick <suffix> "<apk name>"')
        return pick(*a.args)

    cfg = read_config()
    if not cfg:
        sys.exit('No install.json yet. Run: python3 install.py scan')

    adb = find_adb()
    devs = connect(adb)
    dev = a.device or pick_device(adb, devs)
    if not dev:
        sys.exit('No device. Start the emulator and try again.')

    if a.action == 'status':
        return status(adb, dev, cfg)
    if a.action == 'remove':
        if not a.args:
            sys.exit('usage: install.py remove <suffix> [--force]')
        for sfx in a.args:
            remove(adb, dev, sfx, a.force)
        return
    if a.action == 'install':
        if not a.args:
            sys.exit('usage: install.py install <suffix>')
        if a.apk and len(a.args) != 1:
            sys.exit('--apk installs one mod: python3 install.py install rfl '
                     '--apk "<file>"')
        for sfx in a.args:
            # Explicit install means "get me whatever is current", so the APK
            # is re-fetched rather than served from cache. auto below keeps the
            # cache, since it re-runs across every mod.
            install_one(adb, dev, sfx, cfg, a.force, fresh=True, apk_path=a.apk)
        return

    want = played_mods()
    if not want:
        sys.exit('saves/ is empty, so there is nothing to install.')
    print(f'mods you have a save for: {", ".join(want)}')
    for sfx in want:
        install_one(adb, dev, sfx, cfg, a.force)


if __name__ == '__main__':
    main()
