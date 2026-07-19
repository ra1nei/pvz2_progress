#!/usr/bin/env python3
"""Read a PvZ2 save (pp.dat) and work out level progress.

Progress lives in PlayerInfo.objdata.wmed:
    wmed = [ {w: <world id>, e: [{i: <cleared level id>}, ...], r: bool}, ... ]
"""
import json
import os
import sys

from rton import decode

HERE = os.path.dirname(os.path.abspath(__file__))
WORLDS_DIR = os.path.join(HERE, 'worlds')
DEFAULT_PKG = 'com.ea.game.pvz2_cld'


def worlds_path(pkg):
    return os.path.join(WORLDS_DIR, f'{pkg}.json')


def load_worlds(pkg=DEFAULT_PKG):
    try:
        with open(worlds_path(pkg), encoding='utf-8') as f:
            return json.load(f).get('worlds', {})
    except FileNotFoundError:
        sys.exit(f'No level counts for {pkg} yet.\n'
                 f'Run: python3 build_worlds.py {pkg}')


def player_info(save):
    for obj in save.get('objects', []):
        if obj.get('objclass') == 'PlayerInfo':
            return obj.get('objdata', {})
    raise SystemExit('No PlayerInfo object in this save.')


def extract(path, pkg=DEFAULT_PKG):
    """pp.dat -> progress dict."""
    info = player_info(decode(path)['data'])
    worlds = load_worlds(pkg)

    done_by_world = {}
    for w in info.get('wmed', []):
        done_by_world[w.get('w')] = sorted(e['i'] for e in w.get('e', []) if 'i' in e)

    # The denominator must include worlds not opened yet. Counting only the
    # worlds already touched inflates progress: finishing one world would read
    # as 100% while several worlds remain.
    rows, extra = [], []
    for wid in sorted(set(done_by_world) | {int(k) for k in worlds},
                      key=lambda w: (worlds.get(str(w), {}).get('order', 999), w)):
        raw = set(done_by_world.get(wid, []))
        meta = worlds.get(str(wid))
        if meta is None:
            # Present in the save but absent from the main world map: rift
            # and Penny's Pursuit content, generated per event, not part of the
            # story. Counting it pushed one mod to 186% before this guard.
            extra.append({'world_id': wid, 'events': len(raw)})
            continue
        nodes = meta.get('nodes') or []
        ex = {n[0] for n in (meta.get('excluded') or [])}
        # Count map NODES, the way the game UI does. Several nodes can share
        # one eventId, so clearing that id clears the whole group; the save has
        # no way to tell them apart.
        hit = [n for n in nodes if n[0] in raw]
        ids = sorted({n[0] for n in hit})
        rows.append({
            'world_id': wid,
            'name': meta.get('name') or f'World {wid} (missing from worlds.json)',
            'counted': meta.get('counted', True),
            'done': len(hit),
            'total': meta.get('total'),
            'level_ids': ids,
            'names': [n[1] for n in hit],
            'other': len(raw - {n[0] for n in nodes} - ex),
            'dangerroom': len(raw & ex),
        })

    # Hidden worlds with no progress are left out: they are leftover vanilla
    # data still in the OBB but hidden from the map. Tutorial still shows up
    # because it has progress.
    rows = [r for r in rows if r['counted'] or r['done']]

    counted = [r for r in rows if r['counted']]
    missing = [r['world_id'] for r in counted if not r['total']]

    return {
        'missing_totals': missing,
        'extra_worlds': extra,
        'file': path,
        'player': info.get('n', ''),
        'save_version': info.get('v'),
        'coins': info.get('c', 0),
        'gems': info.get('g', 0),
        'mints': info.get('m', 0),
        'plants_unlocked': len(info.get('p', [])),
        'zombies_seen': len(info.get('kz', [])),
        'costumes': len(info.get('cos', [])),
        'last_level': info.get('l', ''),
        'worlds': rows,
        'done_total': sum(r['done'] for r in counted),
        'grand_total': sum(r['total'] for r in counted if r['total']) or None,
    }
