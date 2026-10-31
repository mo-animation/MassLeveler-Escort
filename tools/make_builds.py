"""Builds the skill plans and profile templates of every build in data/builds.json.

Usage:
    py -I tools/make_builds.py [--phbot "<phBot folder>"]     (default: phbot_dir from settings.json)

For every build it writes
    templates/<build>.json     phBot profile template: your base profile with the farm settings below
    data/builds/<build>.json   the 10 level brackets (skill lists per 10 levels) + the skill lines
Each final skill's "line" is its lower ranks, read from phBot's game database, so every bracket gets the
best rank unlocked by its last level:
    CH: the skills it requires in the same skill column (Flame Wave - Arrow -> Burning -> HellFire)
    EU: the same skill code with a lower tier letter (EU_WIZARD_FIREA_POINT_A Fire Bolt -> _B Meteor);
        EU requirements point at other skills (Meteor needs Fire Spirit), so they are not ranks
"""
import argparse
import copy
import glob
import json
import os
import re
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIST_KEYS = ('sNormal', 'bNormal', 'bScript', 'Healing', 'Resurrect')
SCROLL_KINDS = ('Accuracy', 'Strength', 'Intelligence', 'Damage Increase', 'Damage Absorption', 'HP Increase',
                'MP Increase', 'Subscription Card', 'Skill Point', 'Evasion', 'EXP Increase', 'SP EXP Increase',
                'Speed', 'Trigger', 'EXPTickets', 'SPEXPTickets')
TIER = re.compile(r'^(SKILL_EU_[A-Z]+_[A-Z]+_[A-Z]+_)([A-Z])(?=_|\d|$)')


def read_json(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def write_json(path, data, indent=2):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=indent, sort_keys=indent == 4)


# ---------------------------------------------------------------- skill lines from the game database
class Skills:
    def __init__(self, db_path, race):
        db = sqlite3.connect('file:%s?mode=ro' % db_path, uri=True)
        prefix = 'SKILL_%s_' % race
        self.groups = {}
        for gid, name, level, servername, req, sg in db.execute(
                'select group_id, name, level, servername, requiredlearn_1, skillgroup from skills'):
            if not (servername or '').startswith(prefix):
                continue
            g = self.groups.setdefault(gid, {'name': name, 'unlock': level, 'req': req, 'sn': servername, 'sg': sg})
            if level < g['unlock']:                    # the first level of a skill holds its requirement
                g.update(unlock=level, req=req, sn=servername)
        self.by_name = {}
        self.tiers = {}                                # stem -> [(tier, unlock, name)]
        for gid, g in sorted(self.groups.items(), key=lambda kv: kv[1]['unlock']):
            self.by_name.setdefault(g['name'].lower(), gid)
            m = TIER.match(g['sn'])
            if m:
                self.tiers.setdefault(m.group(1), []).append((m.group(2), g['unlock'], g['name']))

    def line(self, name):
        """[[name, unlock level], ...] lowest rank first, ending with `name`"""
        gid = self.by_name.get(name.lower())
        if gid is None:
            return None
        out, seen = [], set()
        while gid in self.groups and gid not in seen:
            seen.add(gid)
            g = self.groups[gid]
            out.append([g['name'], g['unlock']])
            nxt = self.groups.get(g['req'])
            if not nxt or nxt['sg'] != g['sg']:        # a requirement in another column is not a rank
                break
            gid = g['req']
        if len(out) == 1:
            m = TIER.match(self.groups[self.by_name[name.lower()]]['sn'])
            if m:
                lower = sorted({(t, lv, n) for t, lv, n in self.tiers.get(m.group(1), []) if t < m.group(2)})
                out += [[n, lv] for t, lv, n in reversed(lower) if n != name]
        return list(reversed(out))


def best_rank(line, top):
    ok = [r for r in line if r[1] <= top]
    return ok[-1] if ok else None


# ---------------------------------------------------------------- profile template
def normalize(p, build):
    """farm settings on top of the user's own profile; skill lists are filled per bracket"""
    p = copy.deepcopy(p)
    p.pop('Profile', None)
    p['Auto Configure'] = False
    p.setdefault('Auto Mastery', {})['Gap'] = 0

    sk = p.setdefault('Skills', {})
    sk['ID'] = {}
    sk['UseLowerSkills'] = True
    sk['CastInOrder'] = True
    for k in [k for k in sk if k[:1] in 'sb' and k[1:2].isupper() and isinstance(sk[k], list)]:
        sk[k] = []
    sk['Imbue'] = ''
    sk['Healing'] = []
    sk['Resurrect'] = []
    sk['HealSelf'] = True
    sk['HealSelfHP'] = 50
    sk['HealParty'] = bool(build.get('heal_party'))
    sk['HealPartyHP'] = 40 if build.get('heal_party') else 0
    for k in sk.get('Party Buffs', {}):
        sk['Party Buffs'][k] = []

    p.setdefault('Berserk', {}).update({'Full': True, 'Attacking': True, 'Attacking Amount': 1})
    # no scrolls at all: phBot does not see their effect on this server, so it keeps re-using them every few
    # seconds (EXP Booster, Super scroll), which interrupts attacking and walking
    sc = p.setdefault('Scrolls', {})
    for k in SCROLL_KINDS:
        sc[k] = False
    sc['ScrollsTracingOnly'] = False       # True = scrolls only while tracing, never while botting
    sc['ScrollsBottingOnly'] = True
    pot = p.setdefault('AutoPotions', {})
    pot['UseSpeedDrug'] = build.get('race') != 'CH'      # same clash as the speed scroll
    pot['OnlyUseSpeedInScript'] = False
    p.setdefault('Notification', {})['Hide Skill Logs'] = False

    pet = p.setdefault('Attack Pet', {})
    for k in ('UseAttackPet', 'AutoSummonAttackPet', 'AutoReviveAttackPet', 'AttackPetDontAttack'):
        pet[k] = False
    loot = read_json(os.path.join(ROOT, 'data', 'pick.json')) if os.path.isfile(os.path.join(ROOT, 'data', 'pick.json')) else {}
    if loot.get('Pick'):
        p['Pick'] = copy.deepcopy(loot['Pick'])      # same loot options as your main (tools/make_pick.py)
        p['Shared Pick Filter'] = False              # each character's own filter (copied by the manager)
    else:
        pick = p.setdefault('Pick', {})
        pick['DontPickItems'] = True
        pick['PickPet'] = False
        pick['PickOtherPlayersItems'] = False

    loop = p.setdefault('Loop', {})
    loop['Use Reverse Return Dead'] = True
    loop['Skip Town Script'] = True
    loop['DontWalkAround'] = False                 # walk around the training area looking for monsters
    loop['Script'] = {}

    town = p.setdefault('Town', {})
    town['StopDieAmount'] = 0
    town['StopDeath'] = False
    for k in ('Armor Durability', 'Shield Durability', 'Weapon Durability', 'Inventory', 'HP', 'MP',
              'Arrows/Bolts', 'ReturnIfNotAttackedWithin'):
        town.setdefault('Return', {})[k] = False
    town['AutoAcceptRes'] = True
    town['OnlyAcceptResParty'] = True

    res = p.setdefault('Auto Resurrect', {})
    res.update({'ResurrectAllPartyMembers': True, 'AutoResDelay': 2000, 'AutoResRadius': 60.0,
                'UseResurrectionScroll': True, 'UseResurrectionScrollDelay': 10,
                'ReturnIfNotResd': True, 'ReturnIfNotResdMinutes': 2})

    p.setdefault('Party', {}).update({
        'AutoFormParty': False, 'AutoInviteParty': False, 'AutoAcceptParty': False, 'Accept All Invites': False,
        'Accept Invites From List': False, 'Invite All Players': False, 'Invite From List': False, 'InviteList': [],
        'Party Leader Names': [], 'LeaveIfLeaderNotInList': False, 'PartyInviteType': 3})
    p.setdefault('Academy', {}).update({'Accept All Invites': False, 'Invite All': False})
    p.setdefault('Trace', {})['Name'] = 'None'
    return p


# ---------------------------------------------------------------- plans
def final_lists(build, base, skills, final_level, warn):
    src = build.get('skills') or {k: base.get('Skills', {}).get(k, []) for k in LIST_KEYS + ('Imbue',)}
    lines, final = {}, {}
    for key in LIST_KEYS + ('Imbue',):
        names = src.get(key) or []
        names = [names] if isinstance(names, str) else names
        out = []
        for n in [x for x in names if x]:
            line = skills.line(n)
            if not line:
                warn('%s: "%s" is not a %s skill in the game data - kept as it is' % (build['id'], n, build['race']))
                line = [[n, 1]]
            top = best_rank(line, final_level)
            if top is None:
                warn('%s: "%s" unlocks above level %d - left out' % (build['id'], n, final_level))
                continue
            line = line[:line.index(top) + 1]
            lines[top[0]] = line
            out.append(top[0])
        final[key] = out
    return final, lines


def brackets(final, lines, final_level, size):
    out = []
    count = (final_level + size - 1) // size
    for i in range(count):
        lo, hi = i * size + 1, min((i + 1) * size, final_level)
        b = {'name': 'Lv%03d-%03d' % (lo, hi), 'min': lo, 'max': 999 if i == count - 1 else hi}
        for key, names in final.items():
            picked = []
            for n in names:
                r = best_rank(lines[n], hi)
                if r and r[0] not in picked:
                    picked.append(r[0])
            b[key] = (picked[0] if picked else '') if key == 'Imbue' else picked
        out.append(b)
    return out


def all_lines(lines):
    """line of every rank that can appear in a bracket (rank -> its line up to that rank)"""
    out = {}
    for line in lines.values():
        for i, (n, lv) in enumerate(line):
            out.setdefault(n, line[:i + 1])
    return out


def find_db(phbot_dir):
    found = sorted(glob.glob(os.path.join(phbot_dir, 'Data', '*.db3')), key=os.path.getsize, reverse=True)
    if not found:
        sys.exit('No game database in %s' % os.path.join(phbot_dir, 'Data'))
    return found[0]


def main(phbot_dir=None, log=print):
    settings = read_json(os.path.join(ROOT, 'settings.json')) if os.path.isfile(os.path.join(ROOT, 'settings.json')) else {}
    phbot_dir = phbot_dir or settings.get('phbot_dir')
    spec = read_json(os.path.join(ROOT, 'data', 'builds.json'))
    db = find_db(phbot_dir)
    final_level, size = spec.get('final_level', 100), spec.get('bracket_size', 10)
    problems = []
    done = []
    for bid, build in spec['builds'].items():
        build = dict(build, id=bid)
        base_path = os.path.join(phbot_dir, 'Config', build['base_profile'])
        if not os.path.isfile(base_path):
            problems.append('%s: base profile not found: %s' % (bid, base_path))
            continue
        base = read_json(base_path)
        skills = Skills(db, build['race'])
        final, lines = final_lists(build, base, skills, final_level, problems.append)
        plan = {
            'id': bid, 'name': build.get('name', bid), 'race': build['race'], 'create': build['create'],
            'masteries': (base.get('Auto Mastery') or {}).get('Mastery', []), 'final': final,
            'party_buffs': build.get('party_buffs') or {}, 'brackets': brackets(final, lines, final_level, size),
            'lines': all_lines(lines),
        }
        write_json(os.path.join(ROOT, 'data', 'builds', bid + '.json'), plan)
        write_json(os.path.join(ROOT, 'templates', bid + '.json'), normalize(base, build), indent=4)
        done.append(bid)
        log('%-11s %-48s attack at 100: %s' % (bid, plan['name'], ', '.join(final['sNormal']) or '-'))
    for p in problems:
        log('WARNING ' + p)
    return done, problems


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--phbot')
    a = ap.parse_args()
    main(a.phbot)
