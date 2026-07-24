#!/usr/bin/env python3
"""Plants and costumes: how many a mod has, and how many the save owns.

Two more registries beside the world maps and the quest list, read the same
way and out of the same archive.

    PLANTALMANACDATA.RTON   the plants the almanac describes
    PLANTTYPES.RTON         which of those the mod actually switched on
    PRODUCTS.RTON           the shop, where every costume appears as an item

The save keeps both as plain lists of numbers, `p` and `cos`, so the owned
half needs no lookup at all. The totals are what the OBB is read for.

Counting only, never identifying. The numbers in the save are ids from a
space the game keeps to itself, not positions in these registries: they run
past the end of the list, and a mod that reorders its plants makes any
positional reading produce confident nonsense. How many is answerable, which
ones is not.
"""


def _registry(rsb, name, objclass):
    """A named registry, or the one whose objects carry `objclass`.

    Looked up by name first and by content second, for the same reason the
    quest list is: Reflourished's archive is large enough that the file-name
    trie decodes into nonsense while the file bodies stay perfectly readable.
    """
    from pvz.rton import RTON
    files = rsb.rsg_files('Packages')
    for k, v in files.items():
        if k.upper().endswith(f'PACKAGES\\{name}'):
            return RTON(v[8:]).obj()
    for v in files.values():
        if v[:4] != b'RTON' or len(v) < 1000:
            continue
        try:
            d = RTON(v[8:]).obj()
        except Exception:
            continue
        objs = d.get('objects')
        if isinstance(objs, list) and objs and all(
                isinstance(x, dict) and x.get('objclass', '').startswith(objclass)
                for x in objs[:20]):
            return d
    return None


def plants(rsb):
    """How many plants a mod actually offers, or 0.

    The almanac to begin with, rather than PLANTTYPES, which lists everything
    built like a plant and so also holds level furniture: Reflourished's ends
    with servant_girl_tea_trap and carnie_minigame_coconutcannon.

    Then the ones the mod switched off. Every mod here is built on the
    international game and keeps its data, so its almanac still describes
    plants it has no intention of handing out. Two flags in PLANTTYPES say
    so: HideInPlantViewers keeps a plant out of the almanac, Enabled false
    takes it out of the game. Reflourished hides eighteen, all but one of
    them Mints, and dropping them turns a save holding everything on offer
    from 188 of 207 into 188 of 189.
    """
    alm = _registry(rsb, 'PLANTALMANACDATA.RTON', 'PlantAlmanacData')
    if not alm:
        return 0
    # An entry with no stats behind it has no almanac page to show. Marigold
    # is the one that does this: the mod leaves it switched on, since it is
    # still planted by other things, and empties its almanac data so it never
    # appears as a plant to collect.
    named = {(o.get('objdata') or {}).get('TypeName'): (o.get('objdata') or {})
             for o in alm['objects'] if isinstance(o, dict)}
    names = {t for t, od in named.items()
             if t and od.get('AlmanacDataEntries')}
    types = _registry(rsb, 'PLANTTYPES.RTON', 'PlantType')
    if not types:
        return len(names)
    by = {(o.get('objdata') or {}).get('TypeName'): (o.get('objdata') or {})
          for o in types['objects'] if isinstance(o, dict)}
    return sum(1 for t in names
               if not by.get(t, {}).get('HideInPlantViewers')
               and by.get(t, {}).get('Enabled') is not False)


def costumes(rsb):
    """How many distinct costumes the mod sells, or 0.

    There is no costume registry: they exist as shop items, so the shop is
    where they are counted. Distinct ids, because a costume can be listed more
    than once when it is sold in several places.
    """
    d = _registry(rsb, 'PRODUCTS.RTON', 'StoreProduct')
    if not d:
        return 0
    ids = {o['objdata'].get('ObjectItem') for o in d['objects']
           if isinstance(o, dict) and o.get('objdata', {}).get('ObjectType') == 'costume'}
    return len({x for x in ids if x})


def owned(info):
    """(plants, costumes) the save has, straight off two lists of ids."""
    return len(info.get('p') or []), len(info.get('cos') or [])
