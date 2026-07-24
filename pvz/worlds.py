#!/usr/bin/env python3
"""Work out a PvZ2 mod's total level count by reading its OBB directly.

    python3 -m pvz.worlds com.ea.game.pvz2_cld

Writes worlds/<pkg>.json with an OBB fingerprint (source + size). track.py
compares that fingerprint every run, so a mod update regenerates the counts
instead of silently keeping stale ones.

Counting rules, verified against the numbers the game's world map shows:
  - a world counts when Hidden == false in PACKAGES/WORLDMAPLIST.RTON
  - a level counts when m_eventType == 'level' AND m_dataString does NOT
    contain 'dangerroom' (see la_danger_room)
"""
import argparse
import json
import re
import os
import sys

from pvz.rsb import RSB, AdbReader, FileReader, HttpReader
from pvz.device import find_adb, pick_device, sh

from pvz import ROOT as HERE
WORLDS_DIR = os.path.join(HERE, 'worlds')


def is_danger_room(e):
    """Is this node a Danger Room, i.e. one the game leaves out of the count.

    Judged by m_dataString (the level file the node points at) rather than
    m_levelNodeType. Reflourished mislabels its carnival danger room as
    'normal' yet the game still excludes it. Testing for an empty
    m_displayText breaks elsewhere: Collided's lc_zomboss is empty too but is
    a real level. This rule matched all 6 numbers read off the game UI.
    """
    return 'dangerroom' in re.sub(r'[^a-z0-9]', '',
                                  str(e.get('m_dataString', '')).lower())


def _url_size(url):
    """Content-Length of the remote file, used as the OBB fingerprint."""
    import urllib.request
    from pvz.net import UA
    try:
        req = urllib.request.Request(url, headers={'User-Agent': UA})
        req.get_method = lambda: 'HEAD'
        with urllib.request.urlopen(req, timeout=60) as r:
            return int(r.headers.get('Content-Length') or 0)
    except Exception:
        return 0


def obb_info(adb, dev, pkg):
    """(path, size, mtime) of the installed OBB."""
    out = sh(adb, 'shell', f"ls -1 /sdcard/Android/obb/{pkg}/*.obb 2>/dev/null",
             serial=dev, check=False).strip()
    paths = [p for p in out.splitlines() if p.strip().endswith('.obb')]
    if not paths:
        sys.exit(f'No OBB for {pkg} on device {dev}.')
    path = paths[0].strip()
    st = sh(adb, 'shell', f"stat -c '%s %Y' '{path}'", serial=dev).split()
    return path, int(st[0]), int(st[1])


def is_lte_replay(m):
    """Is this world a replay of a past limited-time event.

    Told by the entry point, not the name: Reflourished's fifteen are all
    entered at an lte_replay level and every other world is not, so one test
    covers them without listing any. They are dropped for the same reason the
    LTEReplay quest category is, and dropping them is what takes Reflourished
    from 1128 of 1409 to complete, which is what playing it says.
    """
    return str(m.get('EntryPoint', '')).lower().startswith('lte_replay')


def _maps_in_packages(rsb, name):
    """{world name: objdata} for maps kept in the shared PACKAGES archive.

    Only worth doing when WORLDMAPLIST names worlds that have no package of
    their own. Blobs are filtered on the raw bytes before being parsed, since
    Reflourished ships 4411 of them and only a handful are world maps.
    """
    if not name:
        return {}
    from pvz.rton import RTON
    want = {str(t).lower() for t in name}
    out = {}
    for v in rsb.rsg_files('Packages').values():
        if v[:4] != b'RTON' or b'm_worldId' not in v:
            continue
        try:
            d = RTON(v[8:]).obj()
        except Exception:
            continue
        for o in d.get('objects') or []:
            od = o.get('objdata') if isinstance(o, dict) else None
            if isinstance(od, dict) and str(od.get('m_worldName', '')).lower() in want:
                out[str(od['m_worldName']).lower()] = od
    return out


def build(reader, fingerprint):
    rsb = RSB(reader)
    maplist = rsb.rton('Packages', 'WORLDMAPLIST.RTON')['objects'][0]['objdata']['MapList']
    have = rsb.rsgs()

    def pkg_key(name):
        return 'WORLDPACKAGES_' + str(name).upper()

    # A world map is usually its own WORLDPACKAGES_ package, but not always:
    # Reflourished keeps the 28 maps behind its Travel Log inside the shared
    # PACKAGES archive instead, which is why they looked uncountable. Index
    # those by name so both layouts are found.
    loose = _maps_in_packages(
        rsb, [str(m['MapName']) for m in maplist if pkg_key(m['MapName']) not in have])

    def get_map(nm):
        k = pkg_key(nm)
        if k in have:
            return rsb.rton(k, 'WORLDMAP.RTON')['objects'][0]['objdata']
        return loose.get(str(nm).lower())

    has_map = {str(m['MapName']).lower() for m in maplist
              if pkg_key(m['MapName']) in have or str(m['MapName']).lower() in loose}
    # Worlds listed in WORLDMAPLIST that ship no world map of their own. Their
    # levels cannot be reached from here, so they cannot be counted. Reported
    # in the output instead of dropped in silence: skipping these quietly is
    # how Reflourished's whole Travel Log stayed invisible.
    no_map = sorted(str(m['MapName']) for m in maplist
                       if pkg_key(m['MapName']) not in have
                       and str(m['MapName']).lower() not in loose)

    # Which worlds a gate leads to. A world hidden from the main map but
    # opened by a gate is reachable content, just reached through a hub; a
    # hidden world nothing points at is leftover vanilla data. Both carry
    # Hidden=true, so the gates are the only thing telling them apart.
    gates = set()
    for m in maplist:
        if m.get('ComingSoon') or m.get('Disabled') or is_lte_replay(m):
            continue
        od0 = get_map(m['MapName'])
        if not od0:
            continue
        for e in od0.get('m_eventList', []):
            if e.get('m_eventType') == 'worldgate':
                t = str(e.get('m_dataString', '')).split('-')[0].lower()
                if t:
                    gates.add(t)

    worlds, order, on_map = {}, 0, []
    for m in maplist:
        nm = m['MapName']
        if m.get('ComingSoon') or m.get('Disabled') or is_lte_replay(m):
            continue
        od = get_map(nm)
        if od is None:
            continue

        # A hub world: its gates open sub-worlds that ship no map of their own
        # (Reflourished's Travel Log opens 28). The hub's own nodes are real
        # levels and do count, but everything behind the gates is unreachable
        # from here, so it is recorded in 'opens' rather than left unsaid.
        gate_targets = [str(e.get('m_dataString', '')).split('-')[0].lower()
               for e in od.get('m_eventList', [])
               if e.get('m_eventType') == 'worldgate']
        hub = sorted({c for c in gate_targets if c and c not in has_map})

        # Count map NODES, not distinct m_eventId values. Nodes can share an
        # id (Addendum's Egypt has 44 nodes but only 40 ids) and the game UI
        # counts 44. The save only records ids, so any node whose id is
        # cleared counts as cleared.
        lv, ex = [], []
        for e in sorted((x for x in od.get('m_eventList', [])
                         if x.get('m_eventType') == 'level'),
                        key=lambda x: x['m_eventId']):
            rec = [e['m_eventId'], e.get('m_name', ''),
                   e.get('m_levelNodeType', '')]
            (ex if is_danger_room(e) else lv).append(rec)
            on_map.append(e.get('m_dataString', ''))
        order += 1
        wid = str(od['m_worldId'])
        excluded = bool(m.get('Hidden', False)) and str(nm).lower() not in gates

        # Two worlds can share an m_worldId (Addendum gives eighties and
        # beach both id 10). The save keys on worldId so it cannot tell them
        # apart either. Merging is the only way not to lose levels; skipping
        # the second one used to drop whole worlds from the total.
        if wid in worlds:
            cur = worlds[wid]
            if cur['counted'] and excluded:
                continue                      # never let a hidden world win
            if excluded and not cur['counted']:
                continue
            cur['nodes'] += lv
            cur['excluded'] += ex
            cur['total'] = len(cur['nodes'])
            if nm not in cur['name'].split('+'):
                cur['name'] += '+' + nm
            cur['counted'] = cur['counted'] or not excluded
            continue

        rec = {
            'name': nm, 'code': nm, 'order': order,
            'counted': not excluded,
            'total': len(lv), 'nodes': lv, 'excluded': ex,
        }
        if excluded:
            rec['reason'] = 'hidden'
        elif m.get('Hidden'):
            rec['reason'] = 'reached through a hub'
        if hub:
            rec['opens'] = hub
        worlds[wid] = rec

    # The other half of the mod's levels: the quest registry. Kept apart from
    # the world counts rather than added in, because the save records quest
    # progress by whole chains and map progress level by level.
    import pvz.quests as quests
    q = quests.read(rsb, on_map) or {'world': {}, 'quest': {}}

    from pvz import collection
    return {'_fingerprint': fingerprint, '_quest': q,
            '_collection': {'plants': collection.plants(rsb),
                            'costumes': collection.costumes(rsb)},
            '_note': [
        "Generated by pvz/worlds.py, which overwrites this file. Do not edit.",
        "counted=false: hidden on the map and no gate leads to it, so it is",
        "  leftover vanilla data rather than content.",
        "reason='reached through a hub': hidden from the main map but opened by",
        "  a gate, which is how Reflourished's Travel Log worlds work. Counted.",
        "excluded: Danger Rooms, which the game leaves out of its own total.",
        "opens: a Travel-Log-style hub. Its own nodes are counted, but the",
        "  worlds it opens ship no world map, so THEIR levels are not in the",
        "  total. Reflourished's Travel Log hides ~480 levels this way.",
        "_untracked: every WORLDMAPLIST world with no map package at all.",
        "_quest: levels the quest registry reaches that no world map shows.",
        "  'quest' is the Epic chains, 'world' is everything else, and the",
        "  value against each level is the quest that has to be finished.",
        "_collection: how many plants and costumes the mod defines, from",
        "  PLANTTYPES and from the costume entries in the shop.",
    ], '_untracked': no_map, 'worlds': worlds}


def main():
    ap = argparse.ArgumentParser(description="Compute a PvZ2 mod's level counts from its OBB")
    ap.add_argument('pkg')
    ap.add_argument('--obb', help='read a local OBB file instead of going through adb')
    ap.add_argument('--url', help='read the OBB straight from a URL over HTTP Range, '
                                  'no Android device needed (used by CI)')
    a = ap.parse_args()

    if a.url:
        # Only a few scattered megabytes of a multi-gigabyte file. The
        # fingerprint uses Content-Length to spot a changed release.
        from pvz.net import http_get
        reader = HttpReader(a.url)
        size = len(http_get(a.url, {'Range': 'bytes=0-0'}))  # probe that the range read works
        head = reader.read(0, 4)
        if head != b'1bsr':
            sys.exit(f'URL did not return an RSB archive (got {head!r}). '
                     f'Does the server support HTTP Range?')
        fp = {'source': a.url, 'size': _url_size(a.url)}
        print(f'reading {a.url}  ({fp["size"]:,} bytes) over HTTP Range')
    elif a.obb:
        reader = FileReader(a.obb)
        fp = {'source': a.obb, 'size': os.path.getsize(a.obb),
              'mtime': int(os.path.getmtime(a.obb))}
    else:
        adb = find_adb()
        dev = pick_device(adb, a.pkg)
        path, size, mtime = obb_info(adb, dev, a.pkg)
        print(f'reading {path}  ({size:,} bytes) from device {dev}')
        reader = AdbReader(adb, dev, path)
        fp = {'source': path, 'size': size, 'mtime': mtime}

    data = build(reader, fp)
    os.makedirs(WORLDS_DIR, exist_ok=True)
    out = os.path.join(WORLDS_DIR, f'{a.pkg}.json')
    json.dump(data, open(out, 'w'), indent=1, ensure_ascii=False)

    total = sum(w['total'] for w in data['worlds'].values() if w['counted'])
    for w in sorted(data['worlds'].values(), key=lambda x: x['order']):
        mark = '' if w['counted'] else '   (hidden, not counted)'
        if w.get('opens'):
            mark += f"   [hub: opens {len(w['opens'])} uncounted worlds]"
        ex = f"  +{len(w['excluded'])} danger" if w['excluded'] else ''
        print(f"  {w['name']:<12}{w['total']:>4} levels{ex}{mark}")
    print(f'\nTOTAL: {total} levels  ->  {out}')


if __name__ == '__main__':
    main()
