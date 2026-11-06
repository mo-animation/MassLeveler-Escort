"""Offline test of the escort stages in plugin/MassLeveler.py (a group member and the escort), fake phBot.

Run:  py -I tools/test_escort.py
"""
import importlib.util
import json
import os
import sys
import tempfile
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix='mlesc_')
CFG = TMP.replace('\\', '/') + '/Config/'
SH = CFG + 'MassLeveler/'
for sub in ('groups', 'status', 'commands'):
    os.makedirs(SH + sub)

STAGES = json.load(open(os.path.join(ROOT, 'settings.json')))['escort_stages']
ONG, PENON, KARRA = STAGES[0], STAGES[1], STAGES[2]
AREAS = [{'name': 'A 1-3', 'min_level': 1, 'max_level': 3, 'region': 25773, 'x': 7418.5, 'y': 1628.5},
         {'name': 'A 4-4', 'min_level': 4, 'max_level': 4, 'region': 25773, 'x': 7416.4, 'y': 1724.2},
         {'name': 'A 5-5', 'min_level': 5, 'max_level': 5, 'region': 24996, 'x': 5726.0, 'y': 1096.9}]
JANGAN = (6400.0, 1000.0)
failures = []


def check(cond, what):
    print(('PASS ' if cond else 'FAIL ') + what)
    if not cond:
        failures.append(what)


def status(name, level, x, y, dead=False, ts=None):
    json.dump({'name': name, 'level': level, 'x': x, 'y': y, 'dead': dead, 'state': 'leveling',
               'ts': time.time() if ts is None else ts}, open(SH + 'status/%s.json' % name, 'w'))


def load_plugin(name, args):
    """the plugin as phBot loads it for one character (own fake phBot module)"""
    calls = []
    char = {'name': name, 'level': 1, 'dead': False, 'region': 25000, 'x': JANGAN[0], 'y': JANGAN[1], 'hp': 1,
            'hp_max': 1, 'mp': 1, 'mp_max': 1, 'current_exp': 1, 'max_exp': 100, 'gold': 0}
    zone = {'name': 'Jangan'}
    fake = types.ModuleType('phBot')
    fake.get_config_dir = lambda: CFG
    fake.get_command_line_args = lambda: ['phBot.exe', '--character', name] + args
    fake.get_skills = lambda: {}
    fake.set_profile = lambda n: calls.append(('profile', n)) or True
    fake.log = lambda m: calls.append(('log', m))
    fake.get_character_data = lambda: dict(char)
    fake.get_party = lambda: {}
    fake.get_client = lambda: {'pid': 0}
    fake.get_zone_name = lambda r: zone['name']
    fake.get_monsters = lambda: {}
    fake.get_position = lambda: {'region': char['region'], 'x': char['x'], 'y': char['y'], 'z': 0.0}
    fake.set_training_area = lambda n: True
    fake.set_training_position = lambda *a: calls.append(('pos', a)) or True
    fake.set_training_radius = lambda r: calls.append(('radius', r)) or True
    fake.start_bot = lambda: calls.append(('start_bot',)) or True
    fake.stop_bot = lambda: calls.append(('stop_bot',)) or True
    fake.start_trace = lambda n: calls.append(('trace', n)) or True
    fake.stop_trace = lambda: calls.append(('stop_trace',)) or True
    fake.reverse_return = lambda t, n: calls.append(('reverse', t, n)) or True
    fake.use_return_scroll = lambda: calls.append(('return',)) or True
    fake.select_character = lambda n: None
    fake.inject_joymax = lambda op, data, enc: calls.append(('inject', op, bytes(data)))
    fake.get_monster_string = lambda s: {'model': 1}
    fake.get_item_string = lambda s: {'model': 1}
    sys.modules['phBot'] = fake
    qt = types.ModuleType('QtBind')
    for fn in ('init', 'createLabel', 'createButton', 'createList', 'setText', 'clear', 'append', 'remove'):
        setattr(qt, fn, lambda *a, **k: 1)
    qt.getItems = lambda *a: []
    qt.text = lambda *a: ''
    sys.modules['QtBind'] = qt
    spec = importlib.util.spec_from_file_location('ml_' + name, os.path.join(ROOT, 'plugin', 'MassLeveler.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.joined_game()
    mod.joined_at = time.time() - 60
    return mod, calls, char, zone


def run(mod, times=1):
    for _ in range(times):
        mod.last_tick = 0
        mod.event_loop()


def got(calls, kind):
    return [c for c in calls if c[0] == kind]


# ---------------------------------------------------------------- group files as the manager writes them
json.dump({'group': 'G002', 'server': 'Elysian', 'members': ['fnuk09', 'fnuk10'], 'leader': 'fnuk09',
           'final_level': 85, 'areas': AREAS, 'max_characters': 4, 'solo_level': 5, 'speed_level': 52, 'collect': {'levels': []}, 'build_of': {},
           'builds': {}, 'party_buffs': {}, 'escort_name': 'wiz01', 'stages': STAGES},
          open(SH + 'groups/G002.json', 'w'))
json.dump({'group': 'ESCORT', 'escort': True, 'server': 'Elysian', 'members': ['wiz01'], 'leader': 'wiz01',
           'stages': STAGES, 'radius': 40, 'wait': 10, 'gather': 60, 'final_level': 85, 'max_characters': 4},
          open(SH + 'groups/ESCORT.json', 'w'))
json.dump({'collect_storage': ['0x7168 | 01 02']}, open(SH + 'macros.json', 'w'))

# ---------------------------------------------------------------- member
m, mc, mchar, mzone = load_plugin('fnuk10', ['-mlgroup=G002'])
status('fnuk09', 1, *JANGAN)
run(m)
check(m.collect_started and not got(mc, 'reverse'), 'member level 1: collects the storage first, no reverse yet')
m.collect_started = 0
mc.clear()
run(m)
check(not got(mc, 'reverse') and m.current_area == 'A 1-3' and m.bot_started and not got(mc, 'trace'),
      'level 1: levels alone at the plan area of its level (no party yet), no reverse')
mchar['level'] = 6
mc.clear()
run(m)
check(m.current_area == 'A 4-4' and not got(mc, 'reverse'),
      'level 6 but group lowest 1: waits alone at the last area below 5')
check(m.member_scrolls(1)['Speed'] and m.member_scrolls(51)['Speed'] and not m.member_scrolls(52)['Speed'] and
      m.member_scrolls(1)['ScrollsTracingOnly'], 'speed scrolls while tracing until the lowest is 52')
status('fnuk09', 5, *JANGAN)
mc.clear()
run(m)
check(got(mc, 'stop_bot') and got(mc, 'reverse') == [('reverse', 3, 'Ong Habitat')] and m.current_spot is None,
      'whole group level 5: stops the solo bot, reverse scroll to Ong Habitat, no solo spot in its status any more')
m.group['solo_spot'] = {'region': 25000, 'x': 6300.0, 'y': 1100.0, 'z': 0}
status('fnuk09', 2, *JANGAN)
mc.clear()
run(m)
check(('pos', (25000, 6300.0, 1100.0, 0.0)) in mc and ('radius', 40.0) in mc and m.current_area == 'Solo spot',
      'solo_spot set: levels alone there (radius 40) instead of the plan areas')
status('fnuk09', 5, *JANGAN)
m.last_reverse = 0
run(m)
mc.clear()
run(m)
check(not got(mc, 'reverse'), 'no second reverse within a minute')

mchar.update(x=ONG['x'] - 80, y=ONG['y'] + 50)             # landed at the reverse point
run(m)
check('waiting for the escort' in m.message and not got(mc, 'trace'), 'at Ong without the escort in game: waits, no reverse')
status('wiz01', 100, ONG['x'] - 70, ONG['y'] + 45)
# phBot refuses the trace (its bot runs, or another trace is on): stop both first, try again after 5 s, log once
m.start_trace = lambda n: mc.append(('trace', n)) and False
run(m)
check(got(mc, 'trace') == [('trace', 'wiz01')] and got(mc, 'stop_bot') and not m.tracing and m.trace_fails == 1,
      'trace refused: bot stopped before it, not tracing')
mc.clear()
run(m, 3)
check(not got(mc, 'trace'), 'trace refused: no new try for 5 s (not every tick)')
for _ in range(3):
    m.trace_started -= 6
    run(m)
check(len(got(mc, 'trace')) == 3 and sum('FAILED' in c[1] for c in got(mc, 'log')) == 0,
      'tried again every 5 s, the failure is not written again each time')
m.start_trace = lambda n: mc.append(('trace', n)) or True
m.trace_started -= 6
mc.clear()
run(m)
check(got(mc, 'trace') == [('trace', 'wiz01')] and m.tracing and m.trace_fails == 0 and
      any('Trace wiz01 started' in c[1] for c in got(mc, 'log')), 'phBot takes the trace again: tracing')
mc.clear()
run(m, 3)
check(not got(mc, 'trace'), 'keeps tracing (no restart while close)')
check(m.member_trace(10)['Attack Monsters'] is False and m.member_trace(40)['Attack Monsters'] is False and
      m.member_trace(52)['Attack Monsters'] is True and m.member_trace(70)['Attack Monsters'] is True,
      'trace attacks from Karra on, only traces at Ong and Penon')
check(m.member_trace(1)['Distance'] == 0.0 and m.member_trace(1)['Party Buffs'] and m.member_trace(1)['Buffs'],
      'trace distance 0 with buffs and party buffs')

# lowest reaches 32: the escort goes to Penon, the member follows by reverse
mchar['level'] = 33
status('fnuk09', 32, ONG['x'], ONG['y'])
status('wiz01', 100, PENON['x'], PENON['y'])
m.last_reverse = 0
mc.clear()
run(m)
check(got(mc, 'stop_trace') and got(mc, 'reverse') == [('reverse', 3, 'Ancient Remains')],
      'lowest 32: stops tracing, reverse to Penon (Ancient Remains)')

# dead and not resurrected
mchar.update(x=PENON['x'], y=PENON['y'], dead=True)
mc.clear()
run(m)
check(not got(mc, 'inject'), 'dead: waits for a resurrect first')
m.dead_since = time.time() - m.MEMBER_RES_WAIT - 1
run(m)
check(('inject', 0x3053, b'\x01') in mc, 'dead 2 minutes: respawns in town')
mchar['dead'] = False

# below fast_respawn_level: town at once, then reverse to the death point
m.group['fast_respawn_level'] = 52
mchar.update(level=40, dead=True)
m.dead_since = 0
mc.clear()
run(m)
check(('inject', 0x3053, b'\x01') in mc and m.death_back, 'level 40 dead: respawns in town at once')
mchar['dead'] = False
mzone['name'] = 'Jangan'
mc.clear()
run(m)
check(got(mc, 'reverse') == [('reverse', 1, '')], 'in town: reverse to the death point')
mzone['name'] = 'Karakoram'
m.dead_since = time.time() - 20
mc.clear()
run(m)
check(not m.death_back and not got(mc, 'reverse'), 'back at the death point: traces the escort again')
m.group['fast_respawn_level'] = 0

# level 80+: no place, trace the escort wherever the user takes it (attacking)
status('fnuk09', 80, PENON['x'], PENON['y'])
mchar['level'] = 80
status('wiz01', 100, 9000.0, 9000.0)
m.tracing, m.last_reverse = False, 0
mc.clear()
run(m)
check(got(mc, 'trace') == [('trace', 'wiz01')] and not got(mc, 'reverse') and m.member_trace(80)['Attack Monsters'],
      'lowest 80: traces the escort far away, attack on, no reverse')
status('wiz01', 100, 9000.0, 9000.0, ts=0)
m.tracing = False
mc.clear()
run(m)
check(not got(mc, 'reverse') and 'free' in m.message, 'lowest 80, no escort status: waits, no reverse')

# the escort waits in town (died 3 times in 5 minutes, or no reverse scroll left): the group goes back to it
WING = STAGES[3]


def escort_status(x, y, town_wait):
    json.dump({'name': 'wiz01', 'level': 100, 'x': x, 'y': y, 'dead': False, 'state': 'leveling', 'ts': time.time(),
               'town_wait': town_wait}, open(SH + 'status/wiz01.json', 'w'))


status('fnuk09', 70, WING['x'], WING['y'])
mchar.update(level=70, x=WING['x'], y=WING['y'], dead=False)
mzone['name'] = 'Roc Mountain'
escort_status(*JANGAN, True)
m.tracing, m.last_return, m.last_reverse = True, 0, 0
mc.clear()
run(m)
check(got(mc, 'stop_trace') and got(mc, 'return') and not got(mc, 'reverse') and 'back to town' in m.message,
      'escort waits in town, member at the Wing: stops tracing, return scroll to town (no reverse)')
mc.clear()
run(m)
check(not got(mc, 'return'), 'no second return scroll within a minute')
mchar['dead'] = True
m.dead_since = 0
run(m)
m.dead_since = time.time() - 3
mc.clear()
run(m)
check(('inject', 0x3053, b'\x01') in mc, 'dead while the escort waits in town: respawns in town at once (no 2 min wait)')
mchar.update(dead=False, x=JANGAN[0] + 400, y=JANGAN[1])
mzone['name'] = 'Jangan'
m.tracing = False
mc.clear()
run(m)
check(got(mc, 'trace') == [('trace', 'wiz01')] and not got(mc, 'reverse') and not got(mc, 'return'),
      'in town: traces the escort there (even 400 away), no reverse')
escort_status(WING['x'], WING['y'], False)
m.last_reverse = 0
mc.clear()
run(m)
check(got(mc, 'reverse') == [('reverse', 3, WING['reverse'])], 'the escort went back: reverse to the stage as usual')
escort_status(*JANGAN, False)
mchar.update(x=WING['x'], y=WING['y'])
mzone['name'] = 'Roc Mountain'
m.last_return = 0
mc.clear()
run(m)
check(not got(mc, 'return'), 'escort just in town (not waiting): no return scroll')

# the end
status('fnuk09', 85, PENON['x'], PENON['y'])
mchar['level'] = 85
m.tracing = True
mc.clear()
run(m)
check(m.state == 'finishing' and got(mc, 'stop_trace') and got(mc, 'stop_bot'),
      'lowest 85: stops tracing and finishes (return scroll, town)')

# ---------------------------------------------------------------- escort
e, ec, echar, ezone = load_plugin('wiz01', ['-mlgroup=ESCORT'])
echar['level'] = 100
run(e)
check(got(ec, 'reverse') == [('reverse', 3, 'Ong Habitat')], 'escort without a group: reverse to Ong Habitat')
echar.update(x=ONG['x'] - 70, y=ONG['y'] + 45)
ec.clear()
run(e)
check('waiting for a group' in e.message and not got(ec, 'pos'), 'at Ong: waits for a group, does not hunt')

json.dump({'group': 'G003', 'members': ['fnuk17', 'fnuk18'], 'leader': 'fnuk17'}, open(SH + 'escort.json', 'w'))
status('fnuk17', 1, *JANGAN)
status('fnuk18', 1, *JANGAN)
run(e)
check('levels alone to 5' in e.message and not got(ec, 'pos'), 'group below 5: escort waits at Ong')
status('fnuk17', 5, ONG['x'] - 60, ONG['y'] + 40)
status('fnuk18', 5, *JANGAN)
run(e)
check('1/2 here' in e.message and not got(ec, 'pos'), 'one member still far: keeps waiting')
status('fnuk18', 5, ONG['x'] - 75, ONG['y'] + 50)
run(e)
check('starting in' in e.message and not got(ec, 'pos'), 'everyone here: waits the extra seconds first')
e.esc['gather_since'] = time.time() - 11
run(e)
check(got(ec, 'pos') == [('pos', (ONG['region'], float(ONG['x']), float(ONG['y']), float(ONG['z'])))] and
      ('radius', 40.0) in ec and e.bot_started, 'then hunts at the Ong point, radius 40')

status('fnuk17', 32, ONG['x'], ONG['y'])
status('fnuk18', 33, ONG['x'], ONG['y'])
e.last_reverse = 0
ec.clear()
run(e)
check(got(ec, 'stop_bot') and got(ec, 'reverse') == [('reverse', 3, 'Ancient Remains')],
      'lowest 32: stops and reverses to Penon')

# escort dies while hunting at Karra
status('fnuk17', 52, KARRA['x'], KARRA['y'])
status('fnuk18', 55, KARRA['x'], KARRA['y'])
echar.update(x=KARRA['x'], y=KARRA['y'])
run(e)
e.esc['gather_since'] = time.time() - 11
run(e)
check(e.esc['phase'] == 'hunt', 'hunting at Karra')
echar['dead'] = True
ec.clear()
run(e)
e.esc['dead_since'] = time.time() - 11
run(e)
check(('inject', 0x3053, b'\x01') in ec, 'escort dead 10 s: respawns in town')
echar.update(dead=False, x=JANGAN[0], y=JANGAN[1])
ezone['name'] = 'Jangan'
e.last_reverse = 0
ec.clear()
run(e)
check(got(ec, 'reverse') == [('reverse', 1, '')], 'in town: reverse straight to the death point')
echar.update(x=KARRA['x'] + 20, y=KARRA['y'])
ezone['name'] = 'Fertility Temple'
e.esc['respawn_at'] = time.time() - 20
ec.clear()
run(e)
check(got(ec, 'pos') and e.bot_started and e.esc['phase'] == 'hunt', 'back at the death point: hunts again, no waiting')

# died 3 times in 5 minutes: no reverse back alone, waits in town for the whole group, then they go back together
e.esc['deaths'] = [time.time() - 100, time.time() - 50]
echar['dead'] = True
run(e)
e.esc['dead_since'] = time.time() - 3
ec.clear()
run(e)
check(('inject', 0x3053, b'\x01') in ec and len(e.esc['deaths']) == 3, 'third death in 5 minutes: respawns in town')
echar.update(dead=False, x=JANGAN[0], y=JANGAN[1])
ezone['name'] = 'Jangan'
e.last_reverse = 0
ec.clear()
run(e)
check(not got(ec, 'reverse') and e.esc['regroup'] and e.town_wait and 'waiting in town for the group, 0/2' in e.message,
      'in town: no reverse to the death point, waits for the group (0/2 here)')
e.last_status = 0
run(e)
check(json.load(open(SH + 'status/wiz01.json')).get('town_wait') is True, 'its status tells the group it waits in town')
status('fnuk17', 55, JANGAN[0] + 10, JANGAN[1])
run(e)
check('1/2 here' in e.message and not got(ec, 'reverse'), 'one member back in town: keeps waiting')
status('fnuk18', 52, JANGAN[0] + 20, JANGAN[1] + 5)
run(e)
check('going back together in' in e.message, 'everyone in town: waits the extra seconds')
e.esc['gather_since'] = time.time() - 11
ec.clear()
run(e)
check(not e.esc['regroup'] and e.esc['phase'] == 'travel' and e.esc['deaths'] == [] and
      got(ec, 'reverse') == [('reverse', 3, KARRA['reverse'])],
      'then reverse to the stage (Fertility Temple); the escort waits there for everyone before hunting')
run(e)
check(not e.town_wait, 'not waiting in town any more')

# stuck in town, the reverse fails (no scroll left): it tells the group to come to town
e.reverse_return = lambda t, n: ec.append(('reverse', t, n)) or False
e.last_reverse = 0
run(e, 2)
check(e.town_wait and any('reverse scroll failed' in c[1] for c in got(ec, 'log')), 'reverse failed in town: waits there, the group comes')
e.reverse_return = lambda t, n: ec.append(('reverse', t, n)) or True
e.last_reverse = 0
run(e, 2)
check(not e.town_wait, 'a reverse went through: not waiting any more')
echar.update(x=KARRA['x'], y=KARRA['y'])
ezone['name'] = 'Fertility Temple'
status('fnuk17', 52, KARRA['x'], KARRA['y'])
status('fnuk18', 55, KARRA['x'], KARRA['y'])
run(e)
e.esc['gather_since'] = time.time() - 11
run(e)
check(e.esc['phase'] == 'hunt', 'back at Karra with everyone: hunts')

# Karra's spot changed in the manager's settings while hunting there: the escort goes to the new place
e.group['stages'] = [dict(st, x=KARRA['x'] + 500, reverse='Ancient Remains') if st['name'] == KARRA['name'] else st
                     for st in STAGES]
e.last_reverse = 0
ec.clear()
run(e)
check(e.esc['phase'] == 'travel' and got(ec, 'stop_bot') and got(ec, 'reverse') == [('reverse', 3, 'Ancient Remains')],
      'a changed stage spot: the escort stops hunting and reverses to the new reverse point')
echar.update(x=KARRA['x'] + 500)
status('fnuk17', 52, KARRA['x'] + 500, KARRA['y'])
status('fnuk18', 55, KARRA['x'] + 500, KARRA['y'])
run(e)
e.esc['gather_since'] = time.time() - 11
ec.clear()
run(e)
check(e.esc['phase'] == 'hunt' and [c[1][1] for c in got(ec, 'pos')] == [KARRA['x'] + 500.0],
      'it hunts at the new spot once the group is there')
e.group['stages'] = STAGES
echar.update(x=KARRA['x'])
status('fnuk17', 52, KARRA['x'], KARRA['y'])
status('fnuk18', 55, KARRA['x'], KARRA['y'])
run(e)
e.esc['gather_since'] = time.time() - 11
run(e)
check(e.esc['phase'] == 'hunt', 'spot set back: hunts at Karra again')

status('fnuk17', 80, KARRA['x'], KARRA['y'])
status('fnuk18', 82, KARRA['x'], KARRA['y'])
e.last_reverse = 0
ec.clear()
run(e)
run(e)
check(got(ec, 'stop_bot') and not got(ec, 'reverse') and not got(ec, 'pos') and not e.bot_started and
      'free' in e.message, 'lowest 80: the escort stops its bot and is left to the user (no reverse, no point)')
echar['dead'] = True
ec.clear()
run(e)
e.esc['dead_since'] = time.time() - 3
run(e)
check(('inject', 0x3053, b'\x01') in ec, 'free phase, escort dead: respawns in town')
echar['dead'] = False
ezone['name'] = 'Jangan'
ec.clear()
run(e)
check(got(ec, 'reverse') == [('reverse', 1, '')], 'free phase, in town: reverse to the death point')
ezone['name'] = 'Fertility Temple'
e.esc['respawn_at'] = time.time() - 20
run(e)
check(not e.esc['death_pending'] and 'free' in e.message, 'free phase: back at the death point, still yours')
status('fnuk17', 95, KARRA['x'], KARRA['y'])
status('fnuk18', 96, KARRA['x'], KARRA['y'])
ec.clear()
run(e)
check(e.esc['key'][1] == STAGES[-1]['name'] and STAGES[-1]['min_level'] == 95 and not got(ec, 'reverse'),
      'lowest 95: the 95+ stage (free here too)')
json.dump({'id': time.time(), 'cmd': 'stop'}, open(SH + 'commands/wiz01.json', 'w'))
run(e)
ec.clear()
run(e)
check(e.state == 'stopped' and not got(ec, 'reverse'), 'Stop Bot on the escort: it stays stopped while its group levels')

json.dump({'group': None, 'members': [], 'leader': ''}, open(SH + 'escort.json', 'w'))
e.last_reverse = 0
ec.clear()
run(e)
check(got(ec, 'reverse') == [('reverse', 3, 'Ong Habitat')] and got(ec, 'stop_bot'),
      'group finished: stops the bot the user may have started, back to Ong Habitat to wait')
check(e.state == 'leveling', 'an escort stopped by hand goes back to the loop when its group finishes')

# Auto Equip from the manager's menu: the packets MoAutoEquip learned
json.dump({'sequence': ['0x3567 | 09 00'], 'encrypted': False}, open(CFG + 'MoAutoEquip.json', 'w'))
json.dump({'id': time.time(), 'cmd': 'auto_equip'}, open(SH + 'commands/wiz01.json', 'w'))
ec.clear()
run(e)
check(('inject', 0x3567, b'\x09\x00') in ec, 'Auto Equip command: sends the MoAutoEquip packet')

# EXP boosters: always one on - a new one whenever none is active, at any level
b, bc, bchar, _ = load_plugin('fnuk11', ['-mlgroup=G002'])
inv = [None] * 13 + [{'name': 'Reverse Return Scroll', 'quantity': 5}, {'name': 'Exp Booster (400%) 1 hour', 'quantity': 3}]
b.get_inventory = lambda: {'items': inv}
USE = ('inject', 0x704C, bytes([14]) + bytes.fromhex('310c0d15'))
run(b)
check(b.booster_due and USE not in bc, 'level 1: no EXP booster while the storage collect runs')
b.booster_due = time.time() - 1
b.collect_started = time.time() - 60
buffs = {12345: {'name': 'Exp Booster (400%)'}}
b.get_active_skills = lambda: buffs
run(b)
check(USE not in bc and 0 < b.booster_due - time.time() <= 30, 'a booster buff still on: none used, looks again in 30 s')
buffs.clear()
b.booster_due = time.time() - 1
run(b)
check(USE in bc, 'no booster on: uses the EXP booster of slot 14 with its item code')
inv[14] = dict(inv[14], quantity=2)
b.booster_used = (b.booster_used[0] - 10, 3)
bc.clear()
run(b)
check(USE not in bc and 3500 < b.booster_due - time.time() <= 3600, 'one less in the inventory: the next one in an hour')
b.booster_due = time.time() - 1
run(b)
b.booster_used = (b.booster_used[0] - 10, b.booster_used[1])
run(b)
check(b.booster_tries == 1 and 0 < b.booster_due - time.time() <= 30, 'not taken by the server: tries again in 30 s')
inv[14] = None
b.booster_due, b.booster_used = time.time() - 1, None
bc.clear()
run(b)
check(295 < b.booster_due - time.time() <= 300 and not got(bc, 'inject') and
      sum('No EXP booster' in c[1] for c in got(bc, 'log')) == 1, 'none left: looks again every 5 min (said once)')
b.booster_due = time.time() - 1
run(b)
check(sum('No EXP booster' in c[1] for c in got(bc, 'log')) == 1, 'still none: not said again')
inv[14] = {'name': 'Exp Booster (400%) 1 hour', 'quantity': 1}
b.booster_due = time.time() - 1
bc.clear()
run(b)
check(USE in bc, 'boosters bought / collected later: used again at once')
inv[14] = {'name': 'Skill Exp Booster (100%) 1 hour', 'quantity': 4}
b.booster_due, b.booster_used = time.time() - 1, None
bc.clear()
run(b)
check(not got(bc, 'inject'), 'a Skill Exp Booster (SP) is not an EXP booster: not used')
buffs[1] = {'name': 'Skill Exp Booster (100%)'}
check(not b.booster_active(), 'its buff does not count as an EXP booster on')
buffs.clear()
inv.append({'name': 'Exp Booster (400%) 3 hours', 'quantity': 1})
b.booster_due = time.time() - 1
bc.clear()
run(b)
check(not got(bc, 'inject') and any('no item code for Exp Booster (400%) 3 hours' in c[1] for c in got(bc, 'log')),
      'an EXP booster without a learned code: not sent with another one\'s code')
inv.pop()
inv[14] = None
b.booster_used = None
# a higher level character (not new) uses them too
c, cc, cchar, _ = load_plugin('fnuk12', ['-mlgroup=G002'])
cchar['level'] = 60
c.get_inventory = lambda: {'items': [None] * 13 + [{'name': 'Exp Booster (400%) 1 hour', 'quantity': 2}]}
c.collect_started = time.time() - 60
run(c, 2)
check(('inject', 0x704C, bytes([13]) + bytes.fromhex('310c0d15')) in cc, 'level 60 character: uses a booster too')
inv.append({'name': 'Exp Booster (400%) 3 hours', 'quantity': 1})
b.handle_silkroad(0x704C, bytes([15]) + bytes.fromhex('310c0d16'))
check(json.load(open(SH + 'macros.json'))['item_codes'] == {'Exp Booster (400%) 3 hours': '310c0d16'},
      'a booster used by hand: its item code is learned')
b.group['exp_booster'] = False
b.booster_due = time.time() - 1
bc.clear()
run(b)
check(not got(bc, 'inject'), 'option off: no boosters')

# ---------------------------------------------------------------- members hunt at the escort's spot (no trace)
e.last_status = 0
run(e)
est = json.load(open(SH + 'status/wiz01.json'))
check('phase' in est and est.get('radius') == 40.0, 'the escort\'s status has its phase and radius')


def esc_hunting(stage, phase='hunt', x=None, y=None, ts=None, town_wait=False):
    json.dump({'name': 'wiz01', 'level': 100, 'x': stage['x'] if x is None else x, 'y': stage['y'] if y is None else y,
               'dead': False, 'state': 'leveling', 'ts': time.time() if ts is None else ts, 'town_wait': town_wait,
               'phase': phase, 'area': stage['name'], 'radius': 55.0}, open(SH + 'status/wiz01.json', 'w'))


h, hc, hchar, hzone = load_plugin('fnuk10', ['-mlgroup=G002'])
check(KARRA.get('attack') and not KARRA.get('free') and WING.get('attack') and not WING.get('free'),
      'Karra and Wing are attacking stages (test data)')
status('fnuk09', 55, KARRA['x'], KARRA['y'])
hchar.update(level=55, x=KARRA['x'] + 30, y=KARRA['y'])
hzone['name'] = 'Fertility Temple'
esc_hunting(KARRA, phase='gather', x=KARRA['x'] + 20)
run(h, 2)
check(got(hc, 'trace') and not h.hunt_stage and not got(hc, 'pos'), 'escort still gathering the group: the member traces it')
esc_hunting(KARRA, x=KARRA['x'] + 20)
hc.clear()
run(h)
check(h.hunt_stage and got(hc, 'stop_trace') and not h.tracing and
      ('pos', (KARRA['region'], float(KARRA['x']), float(KARRA['y']), float(KARRA['z']))) in hc and
      ('radius', 55.0) in hc and h.bot_started and 'hunting at' in h.message,
      'the escort hunts: the member stops tracing, bots at the escort\'s spot with the escort\'s radius')
hc.clear()
run(h, 3)
check(not got(hc, 'pos') and not got(hc, 'trace'), 'keeps botting (no restart every tick)')
esc_hunting(KARRA, x=9000.0, y=9000.0)
hc.clear()
run(h, 2)
check(not got(hc, 'trace') and not got(hc, 'reverse') and h.hunt_stage, 'escort far away: keeps hunting alone, no trace')
# dies, not resurrected: town, then the bot walks back by itself
hchar['dead'] = True
run(h)
check(not h.bot_started, 'dead: the bot counts as stopped (phBot stops it)')
h.dead_since = time.time() - h.MEMBER_RES_WAIT - 1
hc.clear()
run(h)
check(('inject', 0x3053, b'\x01') in hc and h.last_respawn, 'dead 2 minutes: respawns in town')
hchar.update(dead=False, x=JANGAN[0], y=JANGAN[1])
hzone['name'] = 'Jangan'
hc.clear()
run(h)
check(not got(hc, 'pos') and 'respawning' in h.message, 'just respawned: waits for the teleport to town first')
h.last_respawn -= 16
hc.clear()
run(h)
check(got(hc, 'pos') and ('radius', 55.0) in hc and h.bot_started and not got(hc, 'reverse') and not got(hc, 'trace')
      and any('walks there from town' in c[1] for c in got(hc, 'log')),
      'in town: bot started at the escort\'s spot, it walks there (no reverse, no trace)')
esc_hunting(KARRA, ts=0)
hc.clear()
run(h, 2)
check(h.hunt_stage and not got(hc, 'trace') and not got(hc, 'pos'), 'escort not in game: keeps hunting there')
# resurrected where it died: the bot starts again at once
hchar.update(dead=True, x=KARRA['x'], y=KARRA['y'])
hzone['name'] = 'Fertility Temple'
run(h)
hchar['dead'] = False
hc.clear()
run(h)
check(got(hc, 'pos') and h.bot_started and not got(hc, 'inject'), 'resurrected in place: bot started again')
# the escort waits in town (died 3 times): bot off, back to town like the others
esc_hunting(KARRA, x=JANGAN[0], y=JANGAN[1], town_wait=True)
h.last_return = 0
hc.clear()
run(h)
check(not h.hunt_stage and got(hc, 'stop_bot') and got(hc, 'return') and not h.bot_started,
      'escort waits in town: stops hunting, return scroll to it')
esc_hunting(KARRA)
run(h)
check(h.hunt_stage, 'the escort hunts again: back to hunting')
# the group moves on: reverse to the next stage, trace the escort until it hunts there
status('fnuk09', WING['min_level'], KARRA['x'], KARRA['y'])
hchar['level'] = WING['min_level']
esc_hunting(WING, phase='travel')
h.last_reverse = 0
hc.clear()
run(h)
check(not h.hunt_stage and got(hc, 'stop_bot') and got(hc, 'reverse') == [('reverse', 3, WING['reverse'])],
      'next stage: stops hunting at Karra, reverse to %s' % WING['name'])
# a stage without attack (Penon): trace as before, even while the escort hunts there
status('fnuk09', 33, PENON['x'], PENON['y'])
hchar.update(level=33, x=PENON['x'], y=PENON['y'])
esc_hunting(PENON)
h.tracing = False
hc.clear()
run(h)
check(not h.hunt_stage and got(hc, 'trace') and not got(hc, 'pos'), 'stage without attack: traces the escort as before')
# phBot keeps refusing the trace at an attacking stage, the escort still gathering: the member walks to the spot itself
status('fnuk09', WING['min_level'], WING['x'], WING['y'])
hchar.update(level=WING['min_level'], x=WING['x'] + 15, y=WING['y'] - 62)
hzone['name'] = 'Mt. Roc field'
esc_hunting(WING, phase='gather', x=WING['x'] + 2, y=WING['y'] - 1)
h.start_trace = lambda n: hc.append(('trace', n)) and False
h.tracing, h.trace_fails, h.trace_started = False, 0, 0
hc.clear()
run(h)
for _ in range(2):
    h.trace_started -= 6
    run(h)
check(len(got(hc, 'trace')) == 3 and h.hunt_stage and h.hunt_stage[0] == WING['name'] and
      any('Trace refused 3 times' in c[1] for c in got(hc, 'log')), 'trace refused 3 times at an attacking stage: hunts there')
hc.clear()
run(h)
check(('pos', (WING['region'], float(WING['x']), float(WING['y']), float(WING['z']))) in hc and ('radius', 55.0) in hc and
      h.bot_started and not got(hc, 'trace'), 'its bot walks to the stage spot (escort radius), no more trace tries')
h.start_trace = lambda n: hc.append(('trace', n)) or True
status('fnuk09', 33, PENON['x'], PENON['y'])
hchar.update(level=33, x=PENON['x'], y=PENON['y'])
esc_hunting(PENON, phase='gather')
h.start_trace = lambda n: hc.append(('trace', n)) and False
h.tracing, h.trace_started = False, 0
hc.clear()
run(h)
for _ in range(3):
    h.trace_started -= 6
    run(h)
check(not h.hunt_stage and len(got(hc, 'trace')) == 4 and h.trace_fails == 4,
      'stage without attack: keeps trying the trace (no hunting there); the count starts again after hunting')

# the escort gathering: a member at the stage's spot counts as here too, not only one by the escort
json.dump({'group': 'G003', 'members': ['fnuk17', 'fnuk18'], 'leader': 'fnuk17'}, open(SH + 'escort.json', 'w'))
e2, ec2, echar2, ezone2 = load_plugin('wiz01', ['-mlgroup=ESCORT'])
echar2.update(level=100, x=KARRA['x'] - 5, y=KARRA['y'] + 40)
ezone2['name'] = 'Fertility Temple'
status('fnuk17', 55, KARRA['x'] - 3, KARRA['y'] + 45)          # by the escort
status('fnuk18', 55, KARRA['x'] + 60, KARRA['y'] - 30)         # 67 from the spot, 120 from the escort
run(e2)
check(e2.esc['phase'] == 'gather' and 'starting in' in e2.message,
      'a member at the stage spot (120 from the escort) counts as here: %s' % e2.message)
status('fnuk18', 55, KARRA['x'] + 160, KARRA['y'])             # 160 from the spot and the escort
run(e2)
check('1/2 here' in e2.message, 'a member far from both: still waited for')

import shutil  # noqa: E402
shutil.rmtree(TMP, ignore_errors=True)
print('\n%d failure(s)' % len(failures))
sys.exit(1 if failures else 0)
