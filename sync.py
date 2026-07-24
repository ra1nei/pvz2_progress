#!/usr/bin/env python3
"""Keep the PvZ2 saves in step across machines without anyone remembering to.

    python3 sync.py play     pull, start the emulator, push when it closes
    python3 sync.py pull     newest saves -> device (do this before playing)
    python3 sync.py push     device -> saves/, committed and pushed
    python3 sync.py find     locate the save files on the device

The saves live in this repo under saves/, which is also where the tracker reads
them. Pushing them is what rebuilds the table: the workflow runs on any push
touching saves/, so finishing a session updates the README on its own.

`play` is the whole point. Pulling by hand is easy to forget, and forgetting is
not merely inconvenient: play on a stale save and the next push overwrites
progress made on the other machine. So `play` pulls first, and refuses to start
the emulator if that pull fails.

It then watches, pushing whatever moved every half hour and once more when the
session ends, by the emulator closing or by Ctrl-C. The mod in front is copied
each pass, so the last push has something to commit even after the device is
gone.

Do not run `pull` with the mod open. The game holds its progress in memory and
writes it out on exit, so anything pushed underneath it is overwritten the
moment you close the game. `play` sequences this correctly on its own.

Nothing here is tied to one machine: the emulator is found per OS, adb is
connected on whichever port it answers, and the save path is searched for and
then cached.
"""
import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time

from pvz.device import pick_device, devices, find_adb, list_mods, sh

HERE = os.path.dirname(os.path.abspath(__file__))
SAVES = os.path.join(HERE, 'saves')
CACHE = os.path.join(HERE, 'save_paths.json')
TMP = os.path.join(HERE, 'saves_out')

# Where PvZ2 keeps pp.dat. Ordered most to least likely; the real path is found
# by trying each and checking the RTON magic, never by trusting this list.
# No_Backup is where it actually lives, next to global_save_data and the CDN
# folder. /data/data is listed for rooted setups only: on a stock BlueStacks
# the shell user cannot read it.
SAVE_PATHS = [
    '/sdcard/Android/data/{pkg}/files/No_Backup/pp.dat',
    '/storage/emulated/0/Android/data/{pkg}/files/No_Backup/pp.dat',
    '/sdcard/Android/data/{pkg}/files/pp.dat',
    '/data/data/{pkg}/files/No_Backup/pp.dat',
    '/data/data/{pkg}/files/pp.dat',
]

# Searched when none of the above hit. Rooted at the app's own directories, not
# at /sdcard: Android 11 and up serve /sdcard/Android/data through a FUSE mount
# that `find` cannot walk, so a search from the top returns nothing at all even
# though the file is right there.
SEARCH_ROOTS = [
    '/sdcard/Android/data/{pkg}/files',
    '/storage/emulated/0/Android/data/{pkg}/files',
    '/data/data/{pkg}/files',
]

# Emulators do not appear in `adb devices` until something connects to them.
# BlueStacks answers on 5555, the second instance on 5556; the others are here
# so a different emulator works without being told which.
PORTS = ['127.0.0.1:5555', '127.0.0.1:5556', '127.0.0.1:5554',
        '127.0.0.1:62001', '127.0.0.1:21503', '127.0.0.1:7555']

# Default install locations, so --exe is only needed for an unusual setup.
EMULATORS = {
    'Darwin': ['/Applications/BlueStacks.app',
               '/Applications/BlueStacks 5.app'],
    'Windows': [r'C:\Program Files\BlueStacks_nxt\HD-Player.exe',
                r'C:\Program Files (x86)\BlueStacks_nxt\HD-Player.exe'],
    'Linux': [],
}


# ---------------------------------------------------------------- device

def connect(adb):
    """Devices, connecting to the usual emulator ports first if none show up."""
    d = devices(adb)
    if d:
        return d
    for c in PORTS:
        subprocess.run([adb, 'connect', c], capture_output=True, timeout=15)
    d = devices(adb)
    if d:
        print(f'  connected: {", ".join(d)}')
    return d


def is_save(adb, dev, path):
    """True when `path` on the device really is an RTON save."""
    r = subprocess.run([adb, '-s', dev, 'exec-out',
                        f"dd if='{path}' bs=4 count=1 2>/dev/null"],
                       capture_output=True)
    return r.stdout[:4] == b'RTON'


def find_save_path(adb, dev, pkg):
    """The save path for `pkg` on this device, or None."""
    for c in SAVE_PATHS:
        p = c.format(pkg=pkg)
        if is_save(adb, dev, p):
            return p
    for g in SEARCH_ROOTS:
        out = sh(adb, 'shell',
                 f"find {g.format(pkg=pkg)} -name 'pp*.dat' 2>/dev/null | head -10",
                 serial=dev, check=False)
        for p in out.splitlines():
            p = p.strip()
            if p and is_save(adb, dev, p):
                return p
    return None


def save_paths(adb, dev, pkgs):
    """{package: device path}, cached so the search runs once per machine."""
    cache = json.load(open(CACHE, encoding='utf-8')) if os.path.exists(CACHE) else {}
    changed = False
    out = {}
    for pkg in pkgs:
        p = cache.get(pkg)
        if p and is_save(adb, dev, p):
            out[pkg] = p
            continue
        p = find_save_path(adb, dev, pkg)
        if p:
            out[pkg] = cache[pkg] = p
            changed = True
    if changed:
        json.dump(cache, open(CACHE, 'w'), indent=1)
    return out


def cleared(path):
    """Cleared events in a save file, read straight from wmed.

    Used as the guard against overwriting a newer save with an older one. It
    needs no world data, so it works for mods the tracker has never counted.
    """
    from pvz.rton import decode
    from pvz.save import player_info
    info = player_info(decode(path)['data'])
    return sum(len([e for e in w.get('e', []) if 'i' in e])
               for w in info.get('wmed', []))


# Fields the game rewrites merely for having been opened, with nothing played.
# Deny by default: only what has been watched change on its own goes here, so
# being wrong about one costs an extra commit rather than a lost session.
#   lsc  a counter, one higher every launch: 202, 204, 205 across rfl's history
IDLE_FIELDS = {'lsc'}

# Same idea for the quest folder. _activequests.rton is a 220-byte header
# holding a slot name, a hash regenerated on every write, and the GUID of the
# machine that last opened the mod. None of it is progress, and the GUID is
# properly a property of the machine rather than of the save. Still synced, so
# a fresh install gets a complete folder; just not compared.
IDLE_QUEST_FILES = {'_activequests.rton'}


def progress_of(path):
    """A save reduced to what counts as progress, or None if unreadable.

    Buying a costume, a plant, a coin, anything at all moves a field that is
    not in IDLE_FIELDS and so still registers.
    """
    from pvz.rton import decode
    from pvz.save import player_info
    try:
        info = player_info(decode(path)['data'])
    except Exception:
        return None
    return {k: v for k, v in info.items() if k not in IDLE_FIELDS}


def same_progress(a, b):
    """True when two saves differ only in the fields that idle on their own."""
    if open(a, 'rb').read() == open(b, 'rb').read():
        return True
    pa, pb = progress_of(a), progress_of(b)
    # An unreadable save falls back to bytes, which called them different.
    return pa is not None and pa == pb


# ------------------------------------------------------- half-finished quests

def quests_path(save_path):
    """activequests/ beside the save, on the device."""
    return os.path.dirname(save_path).replace(os.sep, '/') + '/activequests'


def quests_local(sfx):
    return os.path.join(SAVES, f'quests_{sfx}')


def _tree(d):
    """{relative path: contents} for a small directory, or None if absent."""
    if not os.path.isdir(d):
        return None
    out = {}
    for root, _dirs, files in os.walk(d):
        for n in files:
            if n in IDLE_QUEST_FILES:
                continue
            p = os.path.join(root, n)
            with open(p, 'rb') as f:
                out[os.path.relpath(p, d).replace(os.sep, '/')] = f.read()
    return out


def quests_off(adb, dev, dpath, sfx, cached=False):
    """Device -> saves/quests_<sfx>. True when anything changed.

    How far into a quest chain you are is not in pp.dat: the save records only
    that a whole chain finished, so a run stopped at step 4 of 6 leaves nothing
    behind there. The game keeps that here instead, under a thousand bytes of
    it, and without carrying it the other machine starts the chain again.
    """
    stage = os.path.join(TMP, f'q_{sfx}')
    if not cached:
        shutil.rmtree(stage, ignore_errors=True)
        os.makedirs(stage, exist_ok=True)
        subprocess.run([adb, '-s', dev, 'pull', quests_path(dpath), stage],
                       capture_output=True, text=True)
    got = os.path.join(stage, 'activequests')
    if not os.path.isdir(got):
        return False                       # this mod keeps no active quests
    stored = quests_local(sfx)
    if _tree(got) == _tree(stored):
        return False
    shutil.rmtree(stored, ignore_errors=True)
    shutil.copytree(got, stored)
    return True


def quests_on(adb, dev, dpath, sfx):
    """saves/quests_<sfx> -> device, one file at a time.

    Pushed per file rather than as a directory because adb disagrees with
    itself across versions about where a pushed folder lands.
    """
    local = quests_local(sfx)
    if not os.path.isdir(local):
        return
    remote = quests_path(dpath)
    for root, _dirs, files in os.walk(local):
        for name in files:
            src = os.path.join(root, name)
            rel = os.path.relpath(src, local).replace(os.sep, '/')
            dst = f'{remote}/{rel}'
            sh(adb, 'shell', f'mkdir -p "{os.path.dirname(dst)}"',
               serial=dev, check=False)
            subprocess.run([adb, '-s', dev, 'push', src, dst],
                           capture_output=True)


# ------------------------------------------------------- the profile itself

# The files a fresh install needs before it will load pp.dat at all. pp.dat is
# the progress, but on its own it is an orphan: the game keeps the register of
# which profiles exist in local_profiles, and without that a launch starts a
# new game on top of the save sitting right there. global_save_data is the
# account-wide state beside it, .hash the checksum the game reads it against.
# None carry a machine id, so all three travel.
PROFILE_FILES = ['local_profiles', 'global_save_data', 'global_save_data.hash']


def profile_dir(save_path):
    return os.path.dirname(save_path).replace(os.sep, '/')


def profile_local(sfx):
    return os.path.join(SAVES, f'profile_{sfx}')


def profile_off(adb, dev, dpath, sfx, cached=False):
    """Device -> saves/profile_<sfx>. True when anything changed.

    Only the register and the account state, never the save/ folder beside
    them: that holds the board of a half-played level, tied to the machine
    that drew it, and syncing it is the mess this whole tool replaced.
    """
    stage = os.path.join(TMP, f'p_{sfx}')
    if not cached:
        shutil.rmtree(stage, ignore_errors=True)
        os.makedirs(stage, exist_ok=True)
        base = profile_dir(dpath)
        for name in PROFILE_FILES:
            subprocess.run([adb, '-s', dev, 'pull', f'{base}/{name}',
                            os.path.join(stage, name)], capture_output=True)
    got = {n: open(os.path.join(stage, n), 'rb').read()
           for n in PROFILE_FILES if os.path.exists(os.path.join(stage, n))}
    if not got:
        return False                       # nothing to carry for this mod yet
    stored = profile_local(sfx)
    have = {n: open(os.path.join(stored, n), 'rb').read()
            for n in PROFILE_FILES if os.path.exists(os.path.join(stored, n))}
    if got == have:
        return False
    shutil.rmtree(stored, ignore_errors=True)
    os.makedirs(stored, exist_ok=True)
    for n, body in got.items():
        with open(os.path.join(stored, n), 'wb') as f:
            f.write(body)
    return True


def profile_on(adb, dev, dpath, sfx):
    """saves/profile_<sfx> -> device, unless the device already has a register.

    Left alone when local_profiles is already there: a machine that has played
    the mod has its own, current and correct, and overwriting it with another
    machine's could strand a profile. This is for the fresh install, which has
    none.
    """
    local = profile_local(sfx)
    if not os.path.isdir(local):
        return
    base = profile_dir(dpath)
    if is_save(adb, dev, f'{base}/local_profiles') or sh(
            adb, 'shell', f'[ -f "{base}/local_profiles" ] && echo Y',
            serial=dev, check=False).strip() == 'Y':
        return
    for name in PROFILE_FILES:
        src = os.path.join(local, name)
        if os.path.exists(src):
            subprocess.run([adb, '-s', dev, 'push', src, f'{base}/{name}'],
                           capture_output=True)


# ---------------------------------------------------------------- git

def git(*args, check=True):
    r = subprocess.run(['git', '-C', HERE] + list(args),
                       capture_output=True, text=True)
    if check and r.returncode != 0:
        sys.exit(f'git {" ".join(args)} failed:\n{(r.stderr or r.stdout).strip()}')
    return r.stdout


def branch():
    return git('rev-parse', '--abbrev-ref', 'HEAD').strip() or 'main'


def refresh_saves():
    """Bring saves/ up to date from the remote, and nothing else.

    A checkout of that one path rather than a reset: this is the working repo,
    so a hard reset would take any uncommitted work along with it.
    """
    os.makedirs(SAVES, exist_ok=True)
    if git('remote', check=False).strip():
        git('fetch', '--quiet', 'origin', check=False)
        git('checkout', f'origin/{branch()}', '--', 'saves', check=False)


def commit_saves(msg):
    """Commit saves/ and push, rebasing over whatever the tracker committed.

    A failed push is reported and survived rather than fatal. This runs from
    the watch loop between mods, and letting a dropped network end the session's
    syncing would cost every mod played after it; the commit is already local,
    so the next push carries it.
    """
    git('add', '--', 'saves')
    if not git('diff', '--cached', '--name-only', '--', 'saves').strip():
        return False
    git('commit', '--quiet', '-m', msg, '--', 'saves')
    if git('remote', check=False).strip():
        git('pull', '--rebase', '--autostash', '--quiet', 'origin', branch(),
            check=False)
        r = subprocess.run(['git', '-C', HERE, 'push', '--quiet', 'origin', 'HEAD'],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(f'  [!] committed here but could not push: '
                  f'{(r.stderr or r.stdout).strip().splitlines()[-1][:90]}')
            print('      it will go up with the next one')
    return True


# ---------------------------------------------------------------- actions

def cleared_on_device(adb, dev, dpath):
    """Cleared events in the save sitting on the device, or None if unreadable.

    Unreadable covers the ordinary case of a mod installed but never opened,
    which has no save yet.
    """
    os.makedirs(TMP, exist_ok=True)
    tmp = os.path.join(TMP, 'check.dat')
    if os.path.exists(tmp):
        os.remove(tmp)
    subprocess.run([adb, '-s', dev, 'pull', dpath, tmp],
                   capture_output=True, text=True)
    if not os.path.exists(tmp) or open(tmp, 'rb').read(4) != b'RTON':
        return None
    return cleared(tmp)


def to_device(adb, dev, paths, force=False):
    """saves/ -> device, refusing any mod that would lose progress.

    The mirror of the guard in from_device, and it exists for the machine that
    played without pushing afterwards. saves/ is normally the newest copy, so
    overwriting the device is the whole point; the exception is a device
    holding progress that never went up, which nothing could recover.
    """
    refresh_saves()
    ok = True
    for pkg, dpath in sorted(paths.items()):
        sfx = pkg.rsplit('_', 1)[-1]
        src = os.path.join(SAVES, f'pp_{sfx}.dat')
        if not os.path.exists(src):
            print(f'  {sfx:<5} not in saves/ yet, leaving the device copy alone')
            continue

        # Kept rather than refused: the device copy is the newer one, so the
        # session can go ahead and the watch loop sends it up at the end. Only
        # the overwrite is skipped. Counts alone cannot tell two machines that
        # each played different levels apart, so say the number either way.
        was = cleared(src)
        now = cleared_on_device(adb, dev, dpath)
        if now is not None and now > was and not force:
            print(f'  {sfx:<5} KEPT: device has {now} cleared, saves/ has {was}. '
                  f'This machine played without pushing.')
            print(f'        Send it up with `sync.py push` before playing '
                  f'elsewhere, or overwrite it anyway with --force.')
            continue

        r = subprocess.run([adb, '-s', dev, 'push', src, dpath],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(f'  {sfx:<5} PUSH FAILED: {(r.stderr or r.stdout).strip()[:120]}')
            ok = False
        else:
            # The save and the quest state have to move together, or the game
            # reads one machine's chain progress against another's profile.
            quests_on(adb, dev, dpath, sfx)
            profile_on(adb, dev, dpath, sfx)
            print(f'  {sfx:<5} {was:>4} cleared -> device')
    return ok


def from_device(adb, dev, paths, force=False, cached=False):
    """Device -> saves/, refusing any mod that would lose progress."""
    refresh_saves()
    os.makedirs(TMP, exist_ok=True)
    changed = []
    for pkg, dpath in sorted(paths.items()):
        sfx = pkg.rsplit('_', 1)[-1]
        dest = os.path.join(TMP, f'pp_{sfx}.dat')
        if not cached:
            if os.path.exists(dest):
                os.remove(dest)
            subprocess.run([adb, '-s', dev, 'pull', dpath, dest],
                           capture_output=True, text=True)
        if not os.path.exists(dest) or open(dest, 'rb').read(4) != b'RTON':
            print(f'  {sfx:<5} ' + ('no copy was read before it closed'
                                    if cached else
                                    'could not read the save off the device'))
            continue

        now = cleared(dest)
        stored = os.path.join(SAVES, f'pp_{sfx}.dat')
        moved = True
        if os.path.exists(stored):
            was = cleared(stored)
            # Fewer cleared levels than the committed copy means this machine
            # played on a stale save. Pushing would erase whatever the other
            # machine did, which is the failure this script exists to prevent.
            if now < was and not force:
                print(f'  {sfx:<5} REFUSED: device has {now} cleared, saves/ has '
                      f'{was}. This machine played on an old save.')
                print(f'        Keep the device copy anyway with --force.')
                continue
            if now == was and same_progress(stored, dest):
                moved = False
        if moved:
            shutil.copy2(dest, stored)
        # Taken in the same breath as the save, and refused in the same breath
        # too: a quest chain half done can move on its own while the save does
        # not, so it decides whether there is anything to commit as well.
        quests = quests_off(adb, dev, dpath, sfx, cached)
        prof = profile_off(adb, dev, dpath, sfx, cached)
        if not moved and not quests and not prof:
            print(f'  {sfx:<5} {now:>4} cleared, unchanged')
            continue
        changed.append(f'{sfx} {now}')
        extra = '' if moved else (', quest progress only' if quests
                                  else ', profile only')
        print(f'  {sfx:<5} {now:>4} cleared -> saves/{extra}')

    # The commit message stays generic on purpose: this is a public repo, and
    # the per-mod detail is printed here for you rather than written into the
    # history. The count says how much moved without naming what.
    if not changed or not commit_saves(f'saves: sync progress ({len(changed)})'):
        print('  nothing to commit')
        return
    print(f'  pushed: {", ".join(changed)}')
    print('  the tracker workflow runs on this push and rebuilds the table')


def find_emulator(exe=None):
    for p in ([exe] if exe else EMULATORS.get(platform.system(), [])):
        if p and os.path.exists(p):
            return p
    return None


def launch_emulator(path):
    if platform.system() == 'Darwin' and path.endswith('.app'):
        subprocess.Popen(['open', '-a', path])
    else:
        subprocess.Popen([path])


def foreground_app(adb, dev):
    """The package in the foreground, or '' if it cannot be read."""
    out = sh(adb, 'shell',
             'dumpsys activity activities 2>/dev/null | grep -m1 topResumedActivity',
             serial=dev, check=False)
    m = re.search(r'u0 ([\w.]+)/', out)
    return m.group(1) if m else ''


def stash(adb, dev, pkg, dpath):
    """Keep a copy of a mod's save while it is still readable.

    The emulator closing is the one moment nothing can be read off it any more,
    and it is exactly when a session that never left the mod would otherwise be
    lost: play one mod, close the emulator, and there was no moment at which
    anything had been copied. So a copy is taken on every pass instead.

    Reading a save from under a running game is harmless. It is writing one
    underneath it that is not.
    """
    sfx = pkg.rsplit('_', 1)[-1]
    os.makedirs(TMP, exist_ok=True)
    dest = os.path.join(TMP, f'pp_{sfx}.dat')
    tmp = dest + '.new'
    subprocess.run([adb, '-s', dev, 'pull', dpath, tmp], capture_output=True)
    if os.path.exists(tmp) and open(tmp, 'rb').read(4) == b'RTON':
        os.replace(tmp, dest)              # only ever replace with a real save
    elif os.path.exists(tmp):
        os.remove(tmp)
    stage = os.path.join(TMP, f'q_{sfx}')
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage, exist_ok=True)
    subprocess.run([adb, '-s', dev, 'pull', quests_path(dpath), stage],
                   capture_output=True)


def final_push(adb, dev, paths, seen, force):
    """The push at the end of a session, however it ends.

    With the emulator still up, on Ctrl-C, it reads the device straight: that
    is authoritative. With the emulator gone it cannot, so it falls back to the
    copy held in hand for each mod that was open this session, and to nothing
    for mods that were not, rather than trusting a copy left over from a past
    one.
    """
    if dev in devices(adb):
        from_device(adb, dev, paths, force)
        return
    only = {pkg: paths[pkg] for pkg in seen}
    if only:
        from_device(adb, dev, only, force, cached=True)
    else:
        print('  nothing was open long enough to have a copy')


def watch(adb, dev, paths, force=False, every=8, interval=1800):
    """Push on a timer and once at the end, never once per mod left.

    Pushing every time a mod left the foreground put a commit in the log for
    every glance at a piñata. Instead one push every `interval` seconds sweeps
    whatever moved into a single commit, and a last one lands when the session
    ends, by the emulator closing or by Ctrl-C.

    The copy taken each pass is for that last one. Closing the emulator is no
    transition to notice, and by the time the device is gone there is nothing
    left to read, so the mod in front is kept in hand as you play. Watching the
    foreground beats watching processes: Android keeps a game alive long after
    you leave it, so a dead process never arrives.
    """
    mins = max(1, interval // 60)
    print(f'  watching {dev}. It pushes every {mins} min, and once more when '
          f'you close the emulator or press Ctrl-C.')
    seen = set()
    due = time.monotonic() + interval
    try:
        while True:
            time.sleep(every)
            if dev not in devices(adb):
                print('  emulator closed')
                break
            cur = foreground_app(adb, dev)
            if cur in paths:
                stash(adb, dev, cur, paths[cur])
                seen.add(cur)
            if time.monotonic() >= due:
                print(f'\n  {mins} min on, pushing what moved')
                from_device(adb, dev, paths, force)
                due = time.monotonic() + interval
    except KeyboardInterrupt:
        print('\n  stopping')
    print('\n== final push ==')
    final_push(adb, dev, paths, seen, force)


# ---------------------------------------------------------------- entry

def installed_mods(adb, dev, a):
    if a.pkg:
        return [a.pkg]
    pkgs = sorted(list_mods(adb).get(dev, ('', []))[1])
    if not pkgs:
        # A bare machine, most likely. Syncing saves needs somewhere to put
        # them, so say which command comes first rather than stopping here.
        sys.exit(f'No PvZ2 mods are installed on {dev}, so there is nothing '
                 f'to sync yet.\n'
                 f'  Put them on this machine first:  python3 install.py auto\n'
                 f'  Or try a single one:             python3 install.py install adm')
    return pkgs


def main():
    ap = argparse.ArgumentParser(description='Sync PvZ2 saves through this repo')
    ap.add_argument('action', choices=['play', 'pull', 'push', 'find'])
    ap.add_argument('--device', help='adb serial, default the first connected')
    ap.add_argument('--pkg', help='one package instead of every installed mod')
    ap.add_argument('--exe', help='emulator to launch, when it is not where it usually is')
    ap.add_argument('--force', action='store_true',
                    help='ignore the progress guard, in whichever direction it fires')
    a = ap.parse_args()

    adb = find_adb()
    devs = connect(adb)
    dev = a.device or pick_device(adb, devs)

    # `play` can start the emulator itself, so nothing being connected yet is
    # only fatal for the actions that need one right now.
    if not dev and a.action != 'play':
        sys.exit('No device. Start the emulator and try again.')

    if a.action == 'play':
        exe = find_emulator(a.exe)
        if not dev:
            if not exe:
                sys.exit('No device and no emulator found. Start it by hand, '
                         'or pass --exe.')
            print(f'== starting {exe} ==')
            launch_emulator(exe)
            for _ in range(30):
                time.sleep(5)
                devs = connect(adb)
                if devs:
                    break
            dev = devs[0] if devs else None
            if not dev:
                sys.exit('The emulator never showed up in adb.')
            # Pulling now, with the emulator already up, is safe: the mod
            # itself is not open yet, so nothing is holding the save.
            print('\n== pulling the newest saves ==')
            paths = save_paths(adb, dev, installed_mods(adb, dev, a))
            if not to_device(adb, dev, paths, a.force):
                sys.exit('Pull failed. Close the emulator without playing: '
                         'playing now would be playing on an old save.')
        else:
            paths = save_paths(adb, dev, installed_mods(adb, dev, a))
            print('== pulling the newest saves ==')
            if not to_device(adb, dev, paths, a.force):
                sys.exit('Pull failed, not starting the emulator: playing now '
                         'would be playing on an old save.')
            if exe:
                print(f'\n== {exe} is already running ==')
        watch(adb, dev, paths, a.force)
        return

    mods = installed_mods(adb, dev, a)
    paths = save_paths(adb, dev, mods)

    if a.action == 'find':
        for pkg in sorted(mods):
            print(f'  {pkg.rsplit("_", 1)[-1]:<5} {paths.get(pkg) or "NOT FOUND"}')
        if len(paths) < len(mods):
            print('\nTried:')
            for c in SAVE_PATHS:
                print(f'  {c}')
            print('  then `find pp*.dat` under ' + ', '.join(SEARCH_ROOTS))
            print('\nA mod with no save yet has simply never been opened.')
        return

    if not paths:
        sys.exit('Found no save files on the device. '
                 'Run `sync.py find` to see where it looked.')
    (to_device(adb, dev, paths, a.force) if a.action == 'pull'
     else from_device(adb, dev, paths, a.force))


if __name__ == '__main__':
    main()
