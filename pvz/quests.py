#!/usr/bin/env python3
"""Levels reachable through the quest system but absent from the world map.

Every mod keeps levels in two places. The world map is the one the tracker has
always counted; QUESTS.RTON is the other, and it is where the Epic section
lives. Addendum's Holly Barrier is six levels there and appears on no map.

The registry has the same shape in every mod checked, so one rule covers all of
them. Only the scale differs, from Collided's 30 entries to Reflourished's 2361.

What is counted:

  - a quest with a LevelName counts as that level
  - a chain (a quest carrying UniqueIDList) counts as all the levels its
    members name
  - levels already on the world map are dropped, since the map column has
    them. This matters: arcade, zombotany and remixed levels sit on the map as
    minigame nodes, and 57 of Addendum's 81 quest levels are already there.
  - a level goes to the quest column when its chain is an EpicQuestData or
    when its own category is Epic or PremiumPlant, and to the world column
    otherwise. Both signals are needed: Alternate UniverZ files a plainly Epic
    chain under the category Future, and files 223 standalone Epic quests that
    belong to no chain at all.
  - schedule-driven entries are dropped whole: daily activities, piñata
    hunts, timed events, limited-time replays, anything marked Repeatable, and
    anything whose Slot rather than category names a schedule. Reflourished
    files its Feastivus yeti quests under the category Epic with the Slot
    Scheduled, so reading only the category keeps a repeating event.
  - a disabled chain takes its levels with it. The flag sits on the chain, not
    on the levels underneath, so checking only the entry that carries the
    LevelName counts content the mod has switched off: Reflourished has 129
    such levels behind six disabled chains.
  - sub-levels no chain claims are dropped, as there is no telling what they
    belong to.

Progress comes from the save's `cqi` list, whose `i` is the FNV-1 hash of a
quest's UniqueID. Only whole chains are recorded there, never their individual
levels, so a chain half played still reads as nothing done.
"""
import re

SCHEDULED = {'DailyActivities', 'DailyPinataHunt', 'Event', 'Scheduled', 'LTEReplay'}


def fnv1(s):
    """32-bit FNV-1, which is how the save keys a completed quest."""
    h = 0x811c9dc5
    for c in str(s).encode():
        h = ((h * 0x01000193) & 0xffffffff) ^ c
    return h


def _key(x):
    """A level reference reduced to something comparable across sources."""
    return re.sub(r'\.rton$', '', str(x).lower().replace('\\', '/').strip()).split('-')[0]


def _find_registry(rsb):
    """The quest registry, or None.

    Looked up by name first, then by content. Reflourished needs the second
    path: its archive is large enough that the file-name trie decodes into
    nonsense, while the file bodies stay perfectly readable.
    """
    from pvz.rton import RTON
    files = rsb.rsg_files('Packages')
    for k, v in files.items():
        if k.upper().endswith('PACKAGES\\QUESTS.RTON'):
            return v
    for v in files.values():
        if v[:4] != b'RTON' or len(v) < 4000:
            continue
        try:
            d = RTON(v[8:]).obj()
        except Exception:
            continue
        o = d.get('objects')
        if isinstance(o, list) and any(
                isinstance(x, dict) and x.get('objclass') == 'QuestMgr' for x in o):
            return v
    return None


def read(rsb, on_map):
    """{'levels': {level: quest uid}, 'world': [...], 'quest': [...]}.

    `tren_map` is every level the world map already shows. Returns the levels
    it does not, split by which column they belong to.
    """
    from pvz.rton import RTON
    blob = _find_registry(rsb)
    if blob is None:
        return None
    try:
        d = RTON(blob[8:]).obj()
        qs = [list(x.values())[0] for x in
              next(x for x in d['objects']
                   if x.get('objclass') == 'QuestMgr')['objdata']['Quests']]
        loai = [list(x.keys())[0] for x in
                next(x for x in d['objects']
                     if x.get('objclass') == 'QuestMgr')['objdata']['Quests']]
    except Exception:
        return None

    # A sub-level carries no useful category of its own, so take the parent's,
    # and the parent's data type decides which column it lands in.
    parent = {}
    for k, x in zip(loai, qs):
        for u in (x.get('UniqueIDList') or []):
            parent[u] = (k, x.get('QuestCategory'), x.get('UniqueID'),
                      bool(x.get('Disabled')), x.get('Slot'),
                      bool(x.get('Repeatable')))

    mapped = {_key(x) for x in on_map}
    out = {'world': {}, 'quest': {}}
    for k, x in zip(loai, qs):
        if x.get('Disabled') or not x.get('LevelName'):
            continue
        p = parent.get(x.get('UniqueID'))
        cat = x.get('QuestCategory')
        if cat in (None, 'Unused'):
            if not p:
                continue                       # orphan, nothing claims it
            parent_kind, cat, uid, off, slot, repeats = p
        else:
            parent_kind, uid = k, x.get('UniqueID')
            off, slot, repeats = (bool(x.get('Disabled')), x.get('Slot'),
                              bool(x.get('Repeatable')))
        if off or repeats or cat in SCHEDULED or slot in SCHEDULED:
            continue
        n = _key(x['LevelName'])
        if n in mapped:
            continue
        col = ('quest' if parent_kind == 'EpicQuestData'
               or cat in ('Epic', 'PremiumPlant') else 'world')
        out[col].setdefault(n, uid)
    return out


def completed(info, levels):
    """How many of `levels` the save says are finished.

    `levels` maps a level to the quest that unlocks it. A quest counts as
    finished when the FNV-1 of its id is in the save's cqi list; the levels
    under it then all count, because the save records nothing finer.
    """
    done = {e['i'] for e in (info.get('cqi') or []) if isinstance(e, dict) and 'i' in e}
    return sum(1 for uid in levels.values() if fnv1(uid) in done)
