#!/usr/bin/env python3
"""Shared adb helpers."""
import subprocess
import sys

import pvz.net as compat

def find_adb(required=True):
    p = compat.find_exe('adb')
    if p:
        return p
    if not required:
        return None
    sys.exit('adb not found.\n'
             '  Install Android platform-tools and put it on PATH, or use\n'
             '  HD-Adb.exe that ships with BlueStacks on Windows.\n'
             'Reading progress from Drive does NOT need adb.')


def sh(adb, *args, serial=None, check=True):
    cmd = [adb] + (['-s', serial] if serial else []) + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode != 0:
        sys.exit(f'adb failed: {" ".join(args)}\n{r.stderr.strip()}')
    return r.stdout


def devices(adb):
    return [l.split('\t')[0] for l in sh(adb, 'devices').splitlines()[1:]
            if '\t' in l and l.split('\t')[1].strip() == 'device']


def find_device(adb, pkg):
    """The device that ACTUALLY has pkg installed, or None.

    Filtering by package avoids grabbing a real phone plugged in over USB
    when the mod only exists inside BlueStacks."""
    for d in devices(adb):
        if pkg in sh(adb, 'shell', 'pm', 'list', 'packages', pkg,
                     serial=d, check=False):
            return d
    return None


def pick_device(adb, pkg):
    d = find_device(adb, pkg)
    if d:
        return d
    devs = devices(adb)
    if not devs:
        sys.exit('No device connected. Start BlueStacks and retry,\n'
                 'or run: adb connect 127.0.0.1:5555')
    sys.exit(f'{pkg} is not installed on any connected device ({", ".join(devs)}).')


def pick_device(adb, serials):
    """Which device to work on when several answer.

    BlueStacks shows up both as an emulator- serial and as a 127.0.0.1 port,
    and on a machine running two instances those are not the same Android.
    Taking whichever adb happened to list first meant an empty instance could
    win and the whole run would report no mods installed, then work on the next
    attempt. Preferring the one that actually has mods removes the coin toss.

    Only asks when there is a choice, so the usual single-device case costs
    nothing.
    """
    if len(serials) < 2:
        return serials[0] if serials else None
    mods = list_mods(adb)
    best = max(serials, key=lambda d: len(mods.get(d, ('', []))[1]))
    # Say so out loud. With two Androids answering, which one a command landed
    # on is the first thing worth knowing when the answer looks wrong.
    print('  more than one device answered, using the one with the mods:')
    for d in serials:
        n = len(mods.get(d, ('', []))[1])
        print(f'    {"->" if d == best else "  "} {d:<18} {n} mod{"s" if n != 1 else ""}')
    return best


def list_mods(adb):
    """{serial: (model, [package pvz2...])}"""
    out = {}
    for d in devices(adb):
        model = sh(adb, 'shell', 'getprop', 'ro.product.model', serial=d).strip()
        pkgs = sorted(
            l.split(':', 1)[1].strip()
            for l in sh(adb, 'shell', 'pm', 'list', 'packages', 'pvz2',
                        serial=d, check=False).splitlines()
            if l.startswith('package:'))
        out[d] = (model, pkgs)
    return out
