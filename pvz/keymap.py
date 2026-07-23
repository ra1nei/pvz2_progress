#!/usr/bin/env python3
"""Give every mod the same keyboard layout.

The mods all draw the same UI, so one keymap fits all of them. BlueStacks
files these per package, which means a mod you just installed starts with no
keys at all until you set them up again. This copies keymap.cfg into place
under the new package's name.

BLUESTACKS ONLY. The file says so itself: every entry is typed
`"$type": "Tap, Bluestacks"`. LDPlayer, Nox and MEmu each invented their own
format, and the Android Studio emulator has no key mapping at all, so on
anything else this reports that it found nowhere to write and moves on.

Coordinates are percentages of the window rather than pixels, so the same file
works at any resolution and on any machine.
"""
import os
import platform
import shutil

from pvz import ROOT

SOURCE = os.path.join(ROOT, 'keymap.cfg')

# Where BlueStacks keeps the layouts you edited yourself. The folder beside
# this one holds the ones it ships with, which are replaced on update.
DIRS = {
    'Darwin': [
        '/Users/Shared/Library/Application Support/BlueStacks/Engine/UserData/InputMapper/UserFiles',
        '/Users/Shared/Library/Application Support/BlueStacks_nxt/Engine/UserData/InputMapper/UserFiles',
    ],
    'Windows': [
        r'C:\ProgramData\BlueStacks_nxt\Engine\UserData\InputMapper\UserFiles',
        r'C:\ProgramData\BlueStacks\Engine\UserData\InputMapper\UserFiles',
    ],
    'Linux': [],
}


def folder():
    """The keymap folder on this machine, or None if BlueStacks is not here."""
    for d in DIRS.get(platform.system(), []):
        if os.path.isdir(d):
            return d
    return None


def apply(pkg, force=False, quiet=False):
    """Put the shared layout in place for `pkg`. Returns what it did.

    An existing file is left alone unless forced: it may be one you tuned for
    that mod, and silently replacing it would be worse than doing nothing.
    """
    if not os.path.exists(SOURCE):
        return 'no keymap.cfg in the repo'
    d = folder()
    if not d:
        return 'no BlueStacks keymap folder on this machine'

    dest = os.path.join(d, f'{pkg}.cfg')
    if os.path.exists(dest):
        same = (open(dest, 'rb').read() == open(SOURCE, 'rb').read())
        if same:
            return 'already set'
        if not force:
            return 'left alone, this mod has its own (use --force to replace)'
    shutil.copy(SOURCE, dest)
    try:
        os.chmod(dest, 0o666)          # BlueStacks runs as a different user
    except OSError:
        pass
    if not quiet:
        print(f'      keyboard layout -> {os.path.basename(dest)}')
    return 'written'
