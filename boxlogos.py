#!/usr/bin/env python3
"""Fit every mod logo into one identical box, so the table stops looking ragged.

    python3 boxlogos.py            rebuild every boxed logo
    python3 boxlogos.py cld pen    just those

Logos come in whatever shape their author drew them, from a 1.3:1 square-ish
badge to a 3.2:1 banner. Scaled to fit a box they keep their own shape, so they
end up between 104 and 150 px wide, and a column of them reads as ragged even
though the table column itself is one width.

The fix is to give each one the same footprint: the logo is scaled to fit and
then centred on a transparent canvas of exactly LOGO_BOX. Every <img> is then
the same size, whatever shape the art is, with nothing stretched or cropped.
The results are committed, so nothing here runs in CI: the table is drawn from
whatever is already in assets/logo/box.

Needs Pillow, which the rest of the repo does not. Run it when a logo is added
or changed; a mod with no boxed logo still renders, just at its own width.
Under a Python that refuses to install packages system-wide (Homebrew's, on
macOS), make a throwaway environment for it rather than forcing the issue:

    python3 -m venv /tmp/venv && /tmp/venv/bin/pip install Pillow
    /tmp/venv/bin/python boxlogos.py
"""
import os
import sys

from track import HERE, LOGO_BOX

SRC = os.path.join(HERE, 'assets', 'logo')
OUT = os.path.join(SRC, 'box')


def box_one(sfx):
    """Write assets/logo/box/<sfx>.png at exactly LOGO_BOX. True if written."""
    from PIL import Image
    src = next((os.path.join(SRC, f'{sfx}.{e}') for e in ('png', 'webp', 'jpg')
                if os.path.exists(os.path.join(SRC, f'{sfx}.{e}'))), None)
    if not src:
        print(f'  {sfx}: no logo in assets/logo')
        return False

    bw, bh = LOGO_BOX
    im = Image.open(src).convert('RGBA')

    # Trim the transparent margin the art was saved with, first. Centring the
    # file rather than the drawing inside it is what left the logos looking
    # off: the margins are not even, so one logo carries 5 px of nothing above
    # and 20 below, another 75 to the left and 57 to the right, and each ends
    # up sitting a little differently in its box. Trimming to where the pixels
    # actually start means what gets centred is the drawing, and it also stops
    # a logo saved with a wide margin from rendering smaller than the rest.
    bb = im.getbbox()
    if bb:
        im = im.crop(bb)

    # Never enlarge. Every logo here is far bigger than the box, but blowing a
    # small one up to fill it would only make a blurry logo the same size as
    # the sharp ones.
    k = min(bw / im.width, bh / im.height, 1)
    im = im.resize((max(1, round(im.width * k)), max(1, round(im.height * k))),
                   Image.LANCZOS)

    canvas = Image.new('RGBA', (bw, bh), (0, 0, 0, 0))
    canvas.paste(im, ((bw - im.width) // 2, (bh - im.height) // 2), im)
    os.makedirs(OUT, exist_ok=True)
    canvas.save(os.path.join(OUT, f'{sfx}.png'), optimize=True)
    print(f'  {sfx}: {os.path.basename(src)} -> box/{sfx}.png  ({bw}x{bh})')
    return True


def main():
    want = sys.argv[1:]
    if not want:
        want = sorted({os.path.splitext(f)[0] for f in os.listdir(SRC)
                       if os.path.isfile(os.path.join(SRC, f))
                       and f.rsplit('.', 1)[-1].lower() in ('png', 'webp', 'jpg')})
    print(f'boxing {len(want)} logo(s) to {LOGO_BOX[0]}x{LOGO_BOX[1]}')
    n = sum(box_one(s) for s in want)
    print(f'\n{n} written to {OUT}')


if __name__ == '__main__':
    main()
