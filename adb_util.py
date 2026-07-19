#!/usr/bin/env python3
"""Shared adb helpers."""
import subprocess
import sys

import compat

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
