"""Reading PvZ2 mods: their archives, their level counts, and a save file.

The scripts at the top of the repo are the things you run. This package is
what they are built out of, grouped by what it touches:

    rton, rsb        PopCap's binary JSON and archive formats
    worlds, quests   the two places a mod keeps its levels
    save             a save file, and what it says has been finished
    totals           rolling every mod's counts into one file
    github, drive    where mods are published, and downloading from there
    device, apk      talking to an emulator, and reading an installed APK
    net              HTTP and the differences between operating systems
"""

import os
import re

# Data lives at the top of the repo, one level above this package.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def norm(s):
    """Squash a name to letters and digits, for matching one against another.

    Mods are named inconsistently across an APK label, a folder and a file, so
    everything that has to line those up compares normalised forms.
    """
    return re.sub(r'[^a-z0-9]', '', str(s).lower())
