#!/usr/bin/env python3
"""Draw the diagrams in usage.md.

    python3 mkflow.py            redraw all of them
    python3 mkflow.py play       just one

Each diagram is a function at the bottom of this file, and each is a list of
boxes and the lines between them, at the coordinates they sit at. The layout is
placed by hand rather than computed: these are explanations, and where a box
goes is part of the explanation, so nothing here tries to be a layout engine.
What it does take care of is everything that is the same every time, and that
is most of the file: the stylesheet with its dark-mode half, the arrowhead
marker, escaping, and centring a box's lines inside it.

It exists because the diagrams outlive the behaviour they describe. One of them
spent a while claiming the tracker pushed every half hour after it had stopped
doing so, and correcting that meant editing SVG path coordinates by hand, which
is no way to keep a drawing honest. Now the drawing is source, and changing it
means changing a line here and running this.

Standard library only, like everything else here, and it writes nothing but
assets/diagram/*.svg.
"""
import os
import sys
import xml.sax.saxutils as xu

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'assets', 'diagram')

# GitHub renders these on a white page or a dark one and gives no say in which,
# so every colour appears twice: the light value, then the dark override under
# a prefers-color-scheme block. Two families of box come out of this. The
# flowcharts use the semantic ones, where the colour carries the meaning:
# blue for the command you type, purple for a decision, green for the good
# outcome, amber for a refusal that is not an error, red for a stop. The two
# summary diagrams use their own headers and panels, which is why hd1 to hd4
# and boxa to boxc are here as well.
LIGHT = {
    'bg':    'fill: #ffffff;',
    'box':   'fill: #f6f8fa; stroke: #d0d7de; stroke-width: 1;',
    'cmdb':  'fill: #ddf4ff; stroke: #54aeff; stroke-width: 1.4;',
    'okb':   'fill: #dafbe1; stroke: #4ac26b; stroke-width: 1;',
    'warnb': 'fill: #fff8c5; stroke: #d4a72c; stroke-width: 1;',
    # The one red box in the summary sheet, the do-not-do-this note.
    'warn':  'fill: #ffebe9; stroke: #ff8182; stroke-width: 1;',
    'stopb': 'fill: #ffebe9; stroke: #ff8182; stroke-width: 1;',
    'noteb': 'fill: #f6f8fa; stroke: #d0d7de; stroke-width: 1; stroke-dasharray: 4 3;',
    'deci':  'fill: #fbefff; stroke: #c297ff; stroke-width: 1.2;',
    'hd1':   'fill: #ddf4ff; stroke: #54aeff; stroke-width: 1;',
    'hd2':   'fill: #dafbe1; stroke: #4ac26b; stroke-width: 1;',
    'hd3':   'fill: #fff8c5; stroke: #d4a72c; stroke-width: 1;',
    'hd4':   'fill: #fbefff; stroke: #c297ff; stroke-width: 1;',
    'boxa':  'fill: #ddf4ff; stroke: #54aeff; stroke-width: 1;',
    'boxb':  'fill: #fff8c5; stroke: #d4a72c; stroke-width: 1;',
    'boxc':  'fill: #dafbe1; stroke: #4ac26b; stroke-width: 1;',
}
DARK = {
    'bg': 'fill: #0d1117;',
    'box': 'fill: #161b22; stroke: #30363d;',
    'cmdb': 'fill: #121d2f; stroke: #1f6feb;',
    'okb': 'fill: #12261e; stroke: #238636;',
    'warnb': 'fill: #272115; stroke: #9e6a03;',
    'warn': 'fill: #25171c; stroke: #f85149;',
    'stopb': 'fill: #25171c; stroke: #f85149;',
    'noteb': 'fill: #161b22; stroke: #30363d;',
    'deci': 'fill: #21162d; stroke: #8957e5;',
    'hd1': 'fill: #121d2f; stroke: #1f6feb;',
    'hd2': 'fill: #12261e; stroke: #238636;',
    'hd3': 'fill: #272115; stroke: #9e6a03;',
    'hd4': 'fill: #21162d; stroke: #8957e5;',
    'boxa': 'fill: #121d2f; stroke: #1f6feb;',
    'boxb': 'fill: #272115; stroke: #9e6a03;',
    'boxc': 'fill: #12261e; stroke: #238636;',
    'h': 'fill: #e6edf3;', 's': 'fill: #8b949e;', 'cmd': 'fill: #e6edf3;',
    'mono': 'fill: #8b949e;', 'lbl': 'fill: #e6edf3;', 'm': 'fill: #8b949e;',
    'edge': 'stroke: #6e7681;', 'arr': 'stroke: #6e7681;', 'ah': 'stroke: #6e7681;',
    'elbl': 'fill: #e6edf3;', 'sect': 'fill: #e6edf3;', 'cap': 'fill: #8b949e;',
}
UI = '-apple-system, "Segoe UI", Roboto, sans-serif'
UI_SM = '-apple-system, "Segoe UI", sans-serif'      # the small labels and captions
MONO = 'ui-monospace, SFMono-Regular, Menlo, monospace'


def _n(v):
    """A number as SVG wants it: no trailing .0 on whole ones."""
    f = float(v)
    return str(int(f)) if f == int(f) else str(round(f, 2))


class Sheet:
    """One diagram. Everything is added in the order it should be drawn."""

    def __init__(self, name, w, h, title, desc, fonts=None, lead=15.5, dy=4,
                 weights=None, arrw=1.5):
        self.name, self.w, self.h = name, w, h
        self.title, self.desc = title, desc
        # Point sizes differ between the flowcharts and the two wider summary
        # diagrams, which are read at a glance rather than followed.
        self.f = {'h': 13, 's': 11, 'cmd': 12.5, 'mono': 10.5, 'elbl': 10.5,
                  'sect': 15, 'cap': 11, 'lbl': 11.5, 'm': 10.5, **(fonts or {})}
        self.wt = {'cmd': 600, 'lbl': 600, **(weights or {})}
        self.lead, self.dy, self.arrw = lead, dy, arrw
        self.body = []

    def text(self, cls, x, y, s, anchor='start'):
        # start is the default, so it is left off rather than written out.
        at = f' text-anchor="{anchor}"' if anchor != 'start' else ''
        self.body.append(f'<text class="{cls}" x="{_n(x)}" y="{_n(y)}"{at}>'
                         f'{xu.escape(s)}</text>')

    def node(self, cls, x, y, w, h, lines, rx=6, lead=None, dy=None, ys=None,
             dashed=False):
        """A box with its lines centred inside it.

        `lines` is (class, text) pairs, so a box can mix a heading with the
        small print under it. lead and dy shift the whole block for a box that
        was nudged by eye; ys gives the baselines outright, for the few boxes
        whose lines are not evenly spaced and should stay as they were set.
        `dashed` outlines the box rather than drawing it solid, which marks an
        aside: a step only taken the first time, or one that may not happen.
        """
        lead = self.lead if lead is None else lead
        dy = self.dy if dy is None else dy
        dash = ' stroke-dasharray="4 3"' if dashed else ''
        self.body.append(f'<rect class="{cls}" x="{_n(x)}" y="{_n(y)}" '
                         f'width="{_n(w)}" height="{_n(h)}" rx="{_n(rx)}"{dash}/>')
        cx, cy = x + w / 2, y + h / 2
        for i, (lc, s) in enumerate(lines):
            base = (ys[i] if ys else
                    cy + dy + (i - (len(lines) - 1) / 2) * lead)
            self.text(lc, cx, base, s, anchor='middle')

    def edge(self, points, label=None, dashed=False, arrow=True, cls='edge'):
        """A line from box to box. `points` are the corners it turns at."""
        d = 'M' + ' L'.join(f'{_n(x)} {_n(y)}' for x, y in points)
        bits = f'<path class="{cls}" d="{d}"'
        if arrow:
            bits += ' marker-end="url(#a)"'
        if dashed:
            bits += ' stroke-dasharray="4 3"'
        self.body.append(bits + '/>')
        if label:
            s, x, y, anchor = label
            self.text('elbl', x, y, s, anchor=anchor)

    def style(self):
        out = []
        for k, v in LIGHT.items():
            out.append(f'  .{k:<5} {{ {v} }}')
        out.append(f'  .h    {{ fill: #1f2328; font: 600 {self.f["h"]}px {UI}; }}')
        out.append(f'  .s    {{ fill: #59636e; font: 400 {self.f["s"]}px {UI}; }}')
        out.append(f'  .cmd  {{ fill: #1f2328; font: {self.wt["cmd"]} '
                   f'{self.f["cmd"]}px {MONO}; }}')
        out.append(f'  .mono {{ fill: #59636e; font: 400 {self.f["mono"]}px {MONO}; }}')
        out.append(f'  .lbl  {{ fill: #1f2328; font: {self.wt["lbl"]} '
                   f'{self.f["lbl"]}px {MONO}; }}')
        out.append(f'  .m    {{ fill: #59636e; font: 400 {self.f["m"]}px {MONO}; }}')
        out.append('  .edge { stroke: #8c959f; stroke-width: 1.5; fill: none; }')
        out.append(f'  .arr  {{ stroke: #8c959f; stroke-width: {self.arrw}; fill: none; }}')
        out.append('  .ah   { stroke: #8c959f; fill: none; }')
        out.append(f'  .elbl {{ fill: #1f2328; font: 500 {self.f["elbl"]}px {UI_SM}; }}')
        out.append(f'  .sect {{ fill: #1f2328; font: 700 {self.f["sect"]}px {UI}; }}')
        out.append(f'  .cap  {{ fill: #59636e; font: italic 400 {self.f["cap"]}px {UI_SM}; }}')
        out.append('  @media (prefers-color-scheme: dark) {')
        for k, v in DARK.items():
            out.append(f'    .{k:<5} {{ {v} }}')
        out.append('  }')
        return '\n'.join(out)

    def render(self):
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" '
            f'height="{self.h}" viewBox="0 0 {self.w} {self.h}" role="img" '
            f'aria-labelledby="t d">\n'
            f'<title id="t">{xu.escape(self.title)}</title>\n'
            f'<desc id="d">{xu.escape(self.desc)}</desc>\n'
            f'<style>\n{self.style()}\n</style>\n'
            '<defs>\n'
            '  <marker id="a" viewBox="0 0 10 10" refX="8" refY="5" '
            'markerWidth="6" markerHeight="6" orient="auto-start-reverse">\n'
            '    <path class="ah" d="M2 1L8 5L2 9" stroke-width="1.6" '
            'stroke-linecap="round" stroke-linejoin="round"/>\n'
            '  </marker>\n'
            '</defs>\n'
            f'<rect class="bg" width="{self.w}" height="{self.h}"/>\n'
            + '\n'.join(self.body) + '\n</svg>\n')

    def write(self):
        os.makedirs(OUT, exist_ok=True)
        p = os.path.join(OUT, f'{self.name}.svg')
        open(p, 'w', encoding='utf-8').write(self.render())
        print(f'  {self.name}.svg  {self.w}x{self.h}  '
              f'{len(self.body)} shapes  {os.path.getsize(p):,} bytes')


# ---------------------------------------------------------------- diagrams


def play():
    s = Sheet('play', 1040, 1220,
        'What sync.py play does, branch by branch',
        'A flowchart of sync.py play: it connects to the emulator, copies the '
        'newest saves onto it, then watches the foreground and prints each '
        "mod's progress as it happens, uploading once at the end. Every "
        'refusal notes on the left explaining why each guard exists.')
    s.text('sect', 20, 34, 'python3 sync.py play')
    s.text('cap', 20, 54, 'The daily one. Follow the spine down the middle; every branch peels off to the right.')
    s.node('cmdb', 325, 72, 250, 44, [('cmd', 'sync.py play')])
    s.node('deci', 305, 148, 290, 52, [('h', 'is the emulator up?'), ('s', 'adb devices, then the usual ports')], rx=14)
    s.node('box', 655, 148, 265, 52, [('s', 'starts it and waits, up to'), ('s', '150 seconds, then gives up')])
    s.node('stopb', 655, 232, 265, 46, [('h', 'STOP'), ('mono', 'never showed up in adb')])
    s.node('box', 325, 330, 250, 52, [('h', 'pull: saves/ onto the device'), ('s', 'one mod at a time')])
    s.node('deci', 305, 422, 290, 52, [('h', 'for this mod, which case?'), ('s', 'compares cleared levels both ways')], rx=14)
    s.node('box', 655, 377, 265, 46, [('mono', 'not in saves/ yet'), ('s', 'leaves the device copy alone')])
    s.node('warnb', 655, 434, 265, 56, [('mono', 'KEPT'), ('s', 'the device holds MORE progress,'), ('s', 'so it is not overwritten')])
    s.node('stopb', 655, 509, 265, 50, [('h', 'STOP: adb push failed'), ('s', 'playing now would use an old save')])
    s.node('okb', 325, 524, 250, 42, [('s', 'N cleared -> device')])
    s.node('box', 305, 600, 290, 56, [('h', 'watch the foreground'), ('s', 'copy the open mod every 8 seconds')])
    s.node('deci', 305, 688, 290, 48, [('h', 'emulator still up?')], rx=14)
    s.node('box', 655, 660, 265, 56, [('h', 'closed, or Ctrl-C'), ('s', 'the session is ending, so'), ('s', 'this is the last push')])
    s.node('deci', 305, 776, 290, 48, [('h', 'did the save move?')], rx=14)
    s.node('box', 305, 864, 290, 56, [('h', 'print where it stands'), ('s', 'levels, plants, costumes, coins and'), ('s', 'gems, against where the mod started')])
    s.node('box', 655, 1000, 265, 44, [('mono', 'unchanged'), ('s', 'nothing moved, no commit')])
    s.node('stopb', 655, 1054, 265, 56, [('mono', 'REFUSED'), ('s', 'the device holds LESS than saves/,'), ('s', 'so this machine is on an old save')])
    s.node('box', 325, 956, 250, 46, [('h', 'commit and push saves/'), ('s', 'a failed push is survived')])
    s.node('okb', 305, 1038, 290, 50, [('h', 'the workflow rebuilds the table'), ('s', 'a minute or two later')])
    s.edge([(450, 116), (450, 146)])
    s.edge([(595, 174), (653, 174)], label=('no', 625, 167, 'middle'))
    s.edge([(787.5, 200), (787.5, 230)], label=('still nothing', 795.5, 212, 'middle'))
    s.edge([(450, 200), (450, 328)], label=('yes', 458, 236.5, 'middle'))
    s.edge([(920, 174), (972, 174), (972, 306), (450, 306), (450, 328)], label=('once it appears', 960, 300, 'end'))
    s.edge([(450, 382), (450, 420)])
    s.edge([(595, 448), (653, 400)])
    s.edge([(595, 448), (625, 448), (625, 462), (653, 462)])
    s.edge([(625, 448), (625, 534), (653, 534)])
    s.edge([(450, 474), (450, 522)], label=('saves/ is newer, the normal case', 458, 490.5, 'middle'))
    s.edge([(450, 566), (450, 598)])
    s.edge([(450, 656), (450, 686)])
    s.edge([(595, 712), (653, 688)], label=('no', 625, 705, 'middle'))
    s.edge([(450, 736), (450, 774)], label=('yes', 458, 750, 'middle'))
    s.edge([(450, 824), (450, 862)], label=('yes', 458, 838, 'middle'))
    s.edge([(920, 688), (972, 688), (972, 978), (577, 978)], label=('push once more', 966, 780, 'end'))
    s.edge([(575, 978), (653, 1022)])
    s.edge([(575, 978), (625, 978), (625, 1082), (653, 1082)])
    s.edge([(305, 892), (258, 892), (258, 612), (305, 612)], label=('then keep watching', 266, 770, 'start'), dashed=True)
    s.edge([(450, 1002), (450, 1036)])
    s.edge([(305, 800), (272, 800), (272, 628), (305, 628)], label=('no, keep watching', 280, 715, 'start'), dashed=True)
    s.node('noteb', 20, 148, 220, 100, [('h', 'Why it pulls first'), ('s', 'Play on a stale save and the next'), ('s', 'push erases the other machine, with'), ('s', 'no warning. So it refuses to start'), ('s', 'the emulator if the pull failed.')])
    s.node('noteb', 20, 400, 220, 116, [('h', 'Why the two guards differ'), ('s', 'KEPT is not an error. The device'), ('s', 'copy is the newer one, so the session'), ('s', 'goes ahead and the watch loop sends'), ('s', 'it up later. Only the overwrite is'), ('s', 'skipped. Both answer to --force.')])
    s.node('noteb', 20, 600, 220, 116, [('h', 'Foreground, not process'), ('s', 'Android keeps a game alive long'), ('s', 'after you leave it, so a dead'), ('s', 'process never arrives. The copy'), ('s', 'each pass is what the closing'), ('s', 'push commits, the device by then gone.')])
    s.node('noteb', 20, 856, 220, 116, [('h', 'Why once at the end'), ('s', 'Pushing per mod put a commit in the'), ('s', 'log for every glance at a pinata, and'), ('s', 'a half-hour timer cut a night into'), ('s', 'arbitrary slices. One sitting is now'), ('s', 'one commit, watched on screen instead.')])
    s.text('cap', 520, 1202, 'Never run sync.py pull with a mod already open: the game writes its save on exit and would wipe what you just fetched.', anchor='middle')
    return s

def install():
    s = Sheet('install', 1040, 1190,
        'What install.py does, branch by branch',
        'A flowchart of install.py auto: it reads saves/ to see which mods '
        'you play, fetches each APK and OBB from wherever that mod publishes, '
        'installs both and puts your latest save in place. The signature '
        'refusal and the --force path around it are shown in full.')
    s.text('sect', 20, 34, 'python3 install.py auto')
    s.text('cap', 20, 54, 'Takes a machine from nothing to playable. install.py install <mod> is the same path for one mod.')
    s.node('cmdb', 325, 72, 250, 44, [('cmd', 'install.py auto')])
    s.node('box', 325, 146, 250, 52, [('h', 'reads saves/'), ('s', 'a save is proof you play that mod')])
    s.node('deci', 305, 238, 290, 52, [('h', 'is it already installed,'), ('h', 'at this version?')], rx=14)
    s.node('okb', 655, 242, 265, 44, [('s', 'nothing to do, moves on'), ('s', 'to the next mod')])
    s.node('deci', 305, 340, 290, 52, [('h', 'where does the APK come from?')], rx=14)
    s.node('stopb', 655, 297, 265, 50, [('mono', 'no APK in the folder'), ('s', 'this mod is skipped entirely')])
    s.node('box', 325, 432, 250, 56, [('h', 'downloads the APK'), ('s', 'apk_url wins over the Drive id')])
    s.node('deci', 305, 524, 290, 52, [('h', 'adb install -r')], rx=14)
    s.node('warnb', 655, 517, 265, 62, [('mono', 'INSTALL_FAILED_'), ('mono', 'UPDATE_INCOMPATIBLE'), ('s', 'the rebuild is signed with another key')])
    s.node('deci', 305, 628, 290, 52, [('h', 'is the OBB already there,'), ('h', 'at the right size?')], rx=14)
    s.node('box', 655, 632, 265, 44, [('s', 'keeps it, saves a download'), ('s', 'of up to 1.3 GB')])
    s.node('box', 325, 776, 250, 56, [('h', 'downloads the OBB'), ('s', 'GitHub Releases, else Drive')])
    s.node('box', 325, 858, 250, 50, [('h', 'pushes it to the device'), ('s', '700 MB to 1.3 GB, a few seconds')])
    s.node('deci', 305, 934, 290, 52, [('h', 'does the save folder exist?')], rx=14)
    s.node('box', 655, 938, 265, 44, [('s', 'a fresh app has not made one,'), ('s', 'so it is created here')])
    s.node('box', 325, 1020, 250, 46, [('s', 'drops your latest save in')])
    s.node('okb', 305, 1090, 290, 50, [('h', 'playable, at the progress'), ('h', 'the other machine left off at')])
    s.node('box', 28, 560, 216, 132, [('cmd', '--force'), ('s', '1. pulls the save off the device'), ('s', '2. uninstalls the app'), ('s', '3. installs the new APK'), ('s', '4. puts the save back, preferring'), ('s', '   the copy in saves/')])
    s.edge([(450, 116), (450, 144)])
    s.edge([(450, 198), (450, 236)])
    s.edge([(595, 264), (653, 264)], label=('yes', 625, 257, 'middle'))
    s.edge([(450, 290), (450, 338)], label=('no, or a new version', 458, 306.5, 'middle'))
    s.edge([(595, 366), (653, 322)], label=('nowhere', 625, 359, 'middle'))
    s.edge([(450, 392), (450, 430)], label=('a direct link, or its Drive folder', 458, 406, 'middle'))
    s.edge([(450, 488), (450, 522)])
    s.edge([(595, 550), (653, 548)], label=('refused', 625, 543, 'middle'))
    s.edge([(450, 576), (450, 626)], label=('installed', 458, 593, 'middle'))
    s.edge([(595, 654), (653, 654)], label=('yes', 625, 647, 'middle'))
    s.edge([(450, 680), (450, 774)], label=('no, or the wrong size', 458, 708, 'middle'))
    s.edge([(450, 832), (450, 856)])
    s.edge([(450, 908), (450, 932)])
    s.edge([(595, 960), (653, 960)], label=('no', 625, 953, 'middle'))
    s.edge([(450, 986), (450, 1018)], label=('yes', 458, 998.5, 'middle'))
    s.edge([(450, 1066), (450, 1088)])
    s.edge([(655, 548), (622, 548), (622, 500), (136, 500), (136, 558)], label=('so you rerun it with', 612, 494, 'end'), dashed=True)
    s.edge([(136, 692), (136, 752), (448, 752)], label=('then it carries on here', 150, 746, 'start'), dashed=True)
    s.node('noteb', 20, 238, 220, 100, [('h', 'Which mods it picks'), ('s', 'Whatever has a save in saves/.'), ('s', 'You never list them: playing a mod'), ('s', 'anywhere is what puts its save here,'), ('s', 'and that is the whole signal.')])
    s.node('noteb', 655, 358, 265, 92, [('h', 'Right now this hits rqm and spi'), ('s', 'Requiem hands out MediaFire links inside a'), ('s', 'text file, Spice publishes on itch.io. Neither'), ('s', 'can be scraped, so paste a direct .apk link'), ('s', "into that mod's apk_url in install.json.")])
    s.node('noteb', 20, 830, 220, 116, [('h', 'Your save is never at risk'), ('s', 'Even the uninstall path keeps it:'), ('s', 'the file is copied off before'), ('s', 'anything is removed and put back'), ('s', 'after. And the real copy lives in'), ('s', 'saves/ here regardless.')])
    s.node('noteb', 655, 1016, 265, 92, [('h', 'The table and your machine drift apart'), ('s', 'The level count re-reads itself whenever a'), ('s', 'mod publishes a new build. The copy on your'), ('s', 'machine only changes when you install it. So'), ('s', 'the table can read v1.4.2 while yours says v1.4.0.')])
    s.text('cap', 520, 1172, 'install.py status prints both versions side by side, which is how you know an update is waiting.', anchor='middle')
    return s

def newmod():
    s = Sheet('newmod', 1040, 1240,
        'Adding a mod the tracker has never seen, branch by branch',
        'A flowchart of the new-mod path: install the mod yourself, run '
        'addmod.py to count and name it, run install.py scan to find its APK '
        'and OBB, drop in a logo, commit. The three things that cannot be '
        'worked out automatically are marked.')
    s.text('sect', 20, 34, 'Adding a mod nothing here knows about')
    s.text('cap', 20, 54, 'Done once per mod, on the machine that has it. Boxes marked BY HAND are the only typing involved.')
    s.node('warnb', 305, 72, 290, 56, [('h', 'BY HAND: install the mod'), ('s', 'however its author publishes it')])
    s.node('deci', 305, 164, 290, 52, [('h', 'did the OBB land too?')], rx=14)
    s.node('stopb', 655, 165, 265, 50, [('mono', 'No OBB for ... on device'), ('s', 'an APK alone cannot start,'), ('s', 'and there is nothing to count')])
    s.node('cmdb', 325, 266, 250, 56, [('cmd', 'addmod.py <pkg>'), ('cmd', '--link <download page>')])
    s.node('deci', 305, 358, 290, 52, [('h', 'where does the name come from?')], rx=14)
    s.node('box', 655, 314, 265, 44, [('mono', '--name "..."'), ('s', 'wins over everything else')])
    s.node('box', 655, 372, 265, 44, [('s', 'a mod already in NAME_MAP'), ('s', 'keeps the name it had')])
    s.node('stopb', 655, 427, 265, 50, [('h', 'STOP: no name'), ('s', 'aapt2 missing, or the mod is not'), ('s', 'installed. Rerun with --name.')])
    s.node('box', 325, 452, 250, 50, [('h', 'reads the APK label'), ('s', 'PvZ2 Addendum becomes Addendum')])
    s.node('deci', 305, 542, 290, 52, [('h', 'does the APK carry an OBB URL?')], rx=14)
    s.node('box', 655, 545, 265, 46, [('s', 'counts over the network,'), ('s', 'a few MB by HTTP Range')])
    s.node('box', 325, 634, 250, 48, [('s', 'counts off the device, over adb')])
    s.node('okb', 325, 710, 250, 50, [('h', 'writes worlds/<pkg>.json'), ('s', 'the level count, and the mod name')])
    s.node('deci', 305, 796, 290, 52, [('h', 'was --link given?')], rx=14)
    s.node('warnb', 655, 794, 265, 56, [('h', 'BY HAND: links.json'), ('s', 'nothing on the device says where'), ('s', 'the mod came from, so nothing can guess')])
    s.node('cmdb', 325, 888, 250, 50, [('cmd', 'install.py scan')])
    s.node('deci', 305, 966, 290, 52, [('h', 'how many APKs in that folder?')], rx=14)
    s.node('warnb', 655, 919, 265, 50, [('mono', 'no APK in the folder'), ('s', 'not a Drive folder, or filed oddly:'), ('s', 'paste apk_url into install.json')])
    s.node('box', 655, 991, 265, 50, [('h', 'more than one'), ('mono', 'install.py pick <mod> "60_FPS"'), ('s', 'it will not choose for you')])
    s.node('warnb', 325, 1052, 250, 56, [('h', 'BY HAND: the logo'), ('mono', 'assets/logo/<sfx>.png')])
    s.node('okb', 305, 1144, 290, 50, [('h', 'commit, and it is in the table'), ('s', 'links, install, sources, worlds, logo')])
    s.edge([(450, 128), (450, 162)])
    s.edge([(595, 190), (653, 190)], label=('no', 625, 183, 'middle'))
    s.edge([(450, 216), (450, 264)], label=('yes', 458, 232.5, 'middle'))
    s.edge([(450, 322), (450, 356)])
    s.edge([(595, 384), (653, 336)])
    s.edge([(595, 384), (625, 384), (625, 394), (653, 394)])
    s.edge([(450, 410), (450, 450)], label=('neither', 458, 424.5, 'middle'))
    s.edge([(575, 477), (653, 452)], label=('fails', 615, 470, 'middle'))
    s.edge([(450, 502), (450, 540)])
    s.edge([(595, 568), (653, 568)], label=('yes', 625, 561, 'middle'))
    s.edge([(450, 594), (450, 632)], label=('no', 458, 608, 'middle'))
    s.edge([(450, 682), (450, 708)])
    s.edge([(920, 568), (972, 568), (972, 690), (450, 690), (450, 708)])
    s.edge([(450, 760), (450, 794)])
    s.edge([(595, 822), (653, 822)], label=('no', 625, 815, 'middle'))
    s.edge([(450, 848), (450, 886)], label=('yes, links.json is written for you', 458, 862, 'middle'))
    s.edge([(450, 938), (450, 964)])
    s.edge([(595, 992), (653, 944)], label=('none', 625, 985, 'middle'))
    s.edge([(595, 992), (625, 992), (625, 1016), (653, 1016)])
    s.edge([(450, 1018), (450, 1050)], label=('exactly one, taken', 458, 1030.5, 'middle'))
    s.edge([(450, 1108), (450, 1142)])
    s.edge([(920, 850), (972, 850), (972, 872), (452, 872)], label=('once you add it', 966, 866, 'end'), dashed=True)
    s.node('noteb', 20, 266, 220, 116, [('h', 'The one thing to type'), ('s', 'The download page. The package name'), ('s', 'gives no page, and an APK carries a'), ('s', 'URL only when the mod ships a'), ('s', 'downloader, which is why Collided'), ('s', 'fills its own in and Solstice cannot.')])
    s.node('noteb', 20, 542, 220, 100, [('h', 'Why two ways to count'), ('s', 'A URL means later runs can re-read'), ('s', 'the count on their own, with no'), ('s', 'device. Without one the count is'), ('s', 'frozen until you rebuild it here.')])
    s.node('noteb', 20, 796, 220, 100, [('h', 'Skipping --link is survivable'), ('s', 'The mod is still counted and still'), ('s', 'appears. What breaks is fetching it:'), ('s', 'install.py on another machine has'), ('s', 'nowhere to look.')])
    s.node('noteb', 20, 1044, 220, 84, [('h', 'And the logo'), ('s', 'PNG, WebP or JPG, named after the'), ('s', 'package suffix. Nothing goes looking'), ('s', 'for one, so it is that file or an'), ('s', 'empty first column.')])
    s.text('cap', 520, 1222, 'Run addmod.py with no arguments and it picks up every uncounted mod at once, then lists what is left to do.', anchor='middle')
    return s

def cases():
    s = Sheet('cases', 880, 620,
        'Which command to run in each situation',
        'Four situations side by side: playing a session, setting up a '
        'machine with no mods installed, applying an update, and adding a mod '
        'never played before. Each column lists the commands in order.',
        fonts=dict(h=14, s=11.5, cmd=11.5, cap=11.5), weights=dict(cmd=500),
        lead=18, dy=5)
    s.node('hd1', 10, 16, 200, 52, [('h', 'You just want to play'), ('s', 'the everyday case')])
    s.node('box', 10, 96, 200, 70, [('cmd', 'sync.py play'), ('s', 'pulls, opens the emulator,'), ('s', 'waits, pushes when you quit')], ys=[120, 139, 155])
    s.node('box', 10, 194, 200, 56, [('s', 'the push rebuilds'), ('s', 'the table by itself')], lead=17, dy=2.5)
    s.node('warn', 10, 272, 200, 88, [('h', 'Never'), ('s', 'run pull with a mod open.'), ('s', 'The game writes its save'), ('s', 'on exit and wipes yours.')], ys=[294, 313, 329, 345])
    s.node('hd2', 230, 16, 200, 52, [('h', 'A machine with'), ('h', 'no mods on it')])
    s.node('box', 230, 96, 200, 70, [('cmd', 'install.py auto'), ('s', 'reads saves/ to see what'), ('s', 'you play, then fetches it')], ys=[120, 139, 155])
    s.node('box', 230, 194, 200, 56, [('s', 'APK and OBB installed,'), ('s', 'your save put back')], lead=17, dy=2.5)
    s.node('box', 230, 278, 200, 82, [('h', 'First time only'), ('s', 'no save folder exists yet:'), ('s', 'open the mod once, then'), ('cmd', 'sync.py pull')], ys=[300, 318, 334, 352], dashed=True)
    s.node('hd3', 450, 16, 200, 52, [('h', 'A mod has'), ('h', 'a new build')])
    s.node('box', 450, 96, 200, 70, [('cmd', 'install.py status'), ('s', 'shows installed version'), ('s', 'next to published one')], ys=[120, 139, 155])
    s.node('box', 450, 194, 200, 56, [('cmd', 'install.py install cld'), ('s', 'when the two differ')], lead=19, dy=2.5)
    s.node('box', 450, 278, 200, 82, [('h', 'If it is refused'), ('s', 'signature changed, so add'), ('cmd', '--force'), ('s', 'your save is kept regardless')], ys=[300, 318, 336, 353], dashed=True)
    s.node('hd4', 670, 16, 200, 52, [('h', 'A mod you have'), ('h', 'never played')])
    s.node('box', 670, 96, 200, 52, [('s', 'install it yourself, however'), ('s', 'its author publishes it')], lead=16, dy=4)
    s.node('box', 670, 172, 200, 48, [('cmd', 'addmod.py <pkg>'), ('cmd', '--link <page>')], lead=16, dy=4)
    s.node('box', 670, 244, 200, 48, [('s', 'counts its levels, names it'), ('s', 'from its own APK')], lead=16, dy=4)
    s.node('box', 670, 316, 200, 48, [('cmd', 'install.py scan'), ('s', 'finds its APK and OBB')], lead=17, dy=6.5)
    s.text('h', 20, 424, 'Two details that catch people out')
    s.node('box', 20, 440, 410, 86, [])
    s.text('h', 40, 464, 'The table and your machine differ, and that is fine')
    s.text('s', 40, 484, 'The level count re-reads itself whenever a mod publishes a new')
    s.text('s', 40, 500, 'build. The copy installed on your machine only changes when you')
    s.text('s', 40, 516, 'install it. So the table can read v1.4.2 while yours says v1.4.0.')
    s.node('box', 450, 440, 410, 86, [])
    s.text('h', 470, 464, 'Why pulling before playing is not optional')
    s.text('s', 470, 484, 'Reach level 80 on one machine, then play on another whose save')
    s.text('s', 470, 500, 'still reads 65, and the next push erases level 80 without warning.')
    s.text('s', 470, 516, 'So play refuses to start at all if it could not sync first.')
    s.text('cap', 440, 560, 'A new mod also needs its logo dropped into assets/logo, named after the package suffix.', anchor='middle')
    s.text('cap', 440, 578, 'Blue badge in the table: the level count is re-checked against GitHub every run.', anchor='middle')
    s.text('cap', 440, 596, 'Amber badge: the mod publishes elsewhere, so nothing can watch it and the total may go stale.', anchor='middle')
    return s

def pipeline():
    s = Sheet('pipeline', 880, 620,
        'How the progress number is produced',
        'The save file on the emulator becomes the top half of the fraction '
        "and the mod's OBB becomes the bottom half. Both are read by track.py "
        'on GitHub Actions, which rewrites the table in README.md.',
        fonts=dict(h=15, s=12.5, cap=12, lbl=12.5, m=12),
        weights=dict(lbl=500), arrw=1.6, lead=21, dy=7.5)
    s.text('cap', 230, 34, 'top half of the fraction: what you finished', anchor='middle')
    s.text('cap', 650, 34, 'bottom half: how many there are', anchor='middle')
    s.node('boxa', 70, 48, 320, 82, [('h', 'Your emulator'), ('m', 'pp.dat'), ('s', 'about 3KB, changes every time you play')], ys=[76, 97, 116])
    s.node('boxb', 490, 48, 320, 82, [('h', "The mod's OBB"), ('s', 'on GitHub Releases, 700MB to 1.3GB'), ('s', 'changes only when the author rebuilds')], ys=[76, 97, 116])
    s.text('lbl', 242, 156, 'sync.py play')
    s.text('s', 242, 174, 'pulls first, then pushes after')
    s.text('lbl', 662, 156, 'HTTP Range')
    s.text('s', 662, 174, 'reads a few MB, never the whole file')
    s.node('box', 70, 198, 320, 76, [('h', 'saves/ in this repo'), ('s', 'one pp_<mod>.dat per mod, committed'), ('s', 'so every past version stays recoverable')], ys=[224, 245, 262])
    s.node('box', 490, 198, 320, 76, [('h', 'worlds/<mod>.json'), ('s', 'the level count, plus a fingerprint of'), ('s', 'the OBB so later runs re-read only on change')], ys=[224, 245, 262])
    s.edge([(230, 274), (230, 320), (400, 320), (400, 356)], cls='arr')
    s.edge([(650, 274), (650, 320), (480, 320), (480, 356)], cls='arr')
    s.node('boxc', 280, 358, 320, 76, [('h', 'GitHub Actions runs track.py'), ('s', 'nothing runs on your own computer'), ('s', 'it commits the result straight back here')], ys=[384, 405, 422])
    s.node('box', 640, 358, 200, 76, [('h', 'It runs when'), ('s', 'a save is pushed'), ('s', 'every 6 hours, or by hand')], ys=[382, 401, 418], dashed=True)
    s.node('box', 280, 488, 320, 66, [('h', 'The table in README.md'), ('s', 'rewritten from scratch on every run')], dy=3.5)
    s.text('cap', 440, 586, 'The two halves change on completely different schedules, which is why they are fetched separately.', anchor='middle')
    s.text('cap', 440, 604, 'Playing moves the top half. Only a new release moves the bottom half.', anchor='middle')
    return s


SHEETS = [play, install, newmod, cases, pipeline]


def main():
    want = [a.lower() for a in sys.argv[1:]]
    todo = [f for f in SHEETS if not want or f.__name__ in want]
    if not todo:
        sys.exit('No such diagram. There is: '
                 + ', '.join(f.__name__ for f in SHEETS))
    print(f'drawing {len(todo)} diagram(s)')
    for f in todo:
        f().write()
    print('\n-> ' + OUT)


if __name__ == '__main__':
    main()
