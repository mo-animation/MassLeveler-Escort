"""Takes the loot setup of one character (default: Death) for every MassLeveler character.

Usage:
    py -I tools/make_pick.py [--char Death] [--profile Elysian_Death.Sabo.json] [--blank Elysian_fnuk08.db3]

Writes
    templates/pick_filter.db3   a clean character database (from --blank, a character that barely played)
                                with the item filter of --char: pick / pick with pet / drop / sell / store
    data/pick.json              the "Pick" options of --profile (pick pet, other players' / party items, ...)
The manager copies the filter into every character's <Server>_<char>.db3 before launching it (a new
character gets the whole template), and tools/make_builds.py puts data/pick.json into every template.
"""
import argparse
import json
import os
import shutil
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'manager'))
import ml_core  # noqa: E402


def main(char='Death', profile='Elysian_Death.Sabo.json', blank='Elysian_fnuk08.db3', phbot_dir=None):
    s = ml_core.load_settings()
    cfg = os.path.join(phbot_dir or s['phbot_dir'], 'Config')
    src_db = os.path.join(cfg, '%s_%s.db3' % (s['server'], char))
    out_db = os.path.join(ROOT, 'templates', 'pick_filter.db3')
    shutil.copyfile(os.path.join(cfg, blank), out_db)
    db = sqlite3.connect(out_db)
    for t in ('chat', 'notifications', 'npc'):        # nothing personal of the blank character
        db.execute('DELETE FROM "%s"' % t)
    db.commit()
    db.close()
    n = ml_core.copy_pick_filter(src_db, out_db)
    prof = ml_core.read_json(os.path.join(cfg, profile), {}) or {}
    pick = prof.get('Pick')
    if not pick:
        sys.exit('No "Pick" section in ' + profile)
    ml_core.write_json(os.path.join(ROOT, 'data', 'pick.json'), {'source': profile, 'Pick': pick})
    db = sqlite3.connect('file:%s?mode=ro' % out_db, uri=True)
    counts = {c: db.execute('SELECT count(*) FROM pickfilter WHERE "%s" = 1' % c).fetchone()[0]
              for c in ('pick', 'pet', 'drop_item', 'sell', 'store')}
    db.close()
    print('pick filter of %s -> %s (%d rows) | %s' % (char, out_db, n, counts))
    print('pick options of %s -> data/pick.json | pick pet %s, other players %s, party %s, when full %s, '
          "don't pick %s" % (profile, pick.get('PickPet'), pick.get('PickOtherPlayersItems'), pick.get('PickPartyItems'),
                             pick.get('PickFull'), pick.get('DontPickItems')))


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--char', default='Death')
    ap.add_argument('--profile', default='Elysian_Death.Sabo.json')
    ap.add_argument('--blank', default='Elysian_fnuk08.db3')
    a = ap.parse_args()
    main(a.char, a.profile, a.blank)
