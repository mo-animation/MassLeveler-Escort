"""Offline test of plugin/MassLeveler.py with fake phBot / QtBind modules.

Run:  py -I tools/test_plugin.py
"""
import json
import os
import shutil
import sys
import tempfile
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix='mltest_')
CFG = TMP.replace('\\', '/') + '/Config/'
os.makedirs(CFG + 'MassLeveler/groups')
os.makedirs(CFG + 'MassLeveler/status')

calls = []
char = {'name': '', 'level': 1, 'dead': False, 'region': 25000, 'x': 6400.0, 'y': 1000.0, 'hp': 100, 'hp_max': 100,
        'mp': 50, 'mp_max': 50, 'current_exp': 10, 'max_exp': 100, 'gold': 0}
zone = {'name': 'Jangan'}

skills = {}                 # id -> {'name': ...} like phBot's get_skills()
profiles = {'ok': True}

fake = types.ModuleType('phBot')
fake.get_config_dir = lambda: CFG
fake.get_command_line_args = lambda: ['phBot.exe', '--username', 'u', '--character', 'fnuk02', '--passcode', '01234567',
                                      '-mlgroup=G001']
fake.get_skills = lambda: dict(skills)
fake.set_profile = lambda n: calls.append(('profile', n)) or profiles['ok']
fake.log = lambda m: calls.append(('log', m))
fake.get_character_data = lambda: dict(char) if char['name'] else {}
fake.get_party = lambda: {}
fake.get_client = lambda: {'pid': 0}
fake.get_zone_name = lambda r: zone['name']
monsters = {}               # uid -> {'x', 'y', 'name'} like phBot's get_monsters()
fake.get_monsters = lambda: dict(monsters)
fake.get_position = lambda: {'region': char['region'], 'x': char['x'], 'y': char['y'], 'z': 0.0}
fake.set_training_area = lambda n: calls.append(('area', n)) or True
fake.set_training_position = lambda *a: calls.append(('pos', a)) or True
fake.set_training_radius = lambda r: True
fake.start_bot = lambda: calls.append(('start_bot',)) or True
fake.stop_bot = lambda: calls.append(('stop_bot',)) or True
fake.use_return_scroll = lambda: calls.append(('return',)) or True
fake.select_character = lambda n: calls.append(('select', n))
fake.inject_joymax = lambda op, data, enc: calls.append(('inject', op, bytes(data)))
fake.get_monster_string = lambda s: {'model': 1907, 'servername': s}
fake.get_item_string = lambda s: {'model': {'ITEM_CH_SPEAR_01_A_DEF': 3634}.get(s, 3637), 'servername': s}
sys.modules['phBot'] = fake

qt = types.ModuleType('QtBind')
qt._items = {}
qt.init = lambda *a: 'gui'
_n = [0]


def _new(*a, **k):
    _n[0] += 1
    qt._items[_n[0]] = []
    return _n[0]


for fn in ('createLabel', 'createButton', 'createList', 'createLineEdit', 'createCheckBox'):
    setattr(qt, fn, _new)
qt.setText = lambda *a: None
qt.text = lambda g, w: ''
qt.clear = lambda g, w: qt._items.__setitem__(w, [])
qt.append = lambda g, w, v: qt._items[w].append(v)
qt.getItems = lambda g, w: list(qt._items[w])
qt.remove = lambda g, w, v: qt._items[w].remove(v)
sys.modules['QtBind'] = qt

plan = json.load(open(os.path.join(ROOT, 'tools', 'test_plan.json')))['areas']     # the old level plan, tests only
nuker = json.load(open(os.path.join(ROOT, 'data', 'builds', 'ch_nuker.json')))
group = {'group': 'G001', 'server': 'Elysian', 'members': ['fnuk01', 'fnuk02', 'fnuk03'], 'leader': 'fnuk01',
         'final_level': 101, 'areas': plan, 'max_characters': 4, 'collect': {'levels': [10]},
         'build_of': {'fnuk01': 'ch_nuker', 'fnuk02': 'ch_nuker', 'fnuk03': 'ch_nuker'},
         'builds': {'ch_nuker': nuker}, 'party_buffs': {'fnuk02': {'Magical Buff': ['fnuk03']}}}
json.dump(group, open(CFG + 'MassLeveler/groups/G001.json', 'w'))
json.dump({'collect_storage': ['0x7168 | 01 02']}, open(CFG + 'MassLeveler/macros.json', 'w'))
# Default profile as the manager writes it (skill lists empty)
json.dump({'Skills': {'sNormal': [], 'bNormal': [], 'bScript': [], 'Healing': [], 'Resurrect': [], 'Imbue': '', 'ID': {}},
           'Loop': {'Script': {a['name']: {'Enabled': i == 0} for i, a in enumerate(plan)}},
           'Party': {}, 'Scrolls': {}, 'Berserk': {}, 'Town': {}},
          open(CFG + 'Elysian_fnuk02.json', 'w'))


def status(name, level, state='leveling'):
    json.dump({'name': name, 'level': level, 'state': state}, open(CFG + 'MassLeveler/status/%s.json' % name, 'w'))


sys.path.insert(0, os.path.join(ROOT, 'plugin'))
import MassLeveler as ml  # noqa: E402

failures = []


def check(cond, what):
    print(('PASS ' if cond else 'FAIL ') + what)
    if not cond:
        failures.append(what)


def run_loop(times=1):
    for _ in range(times):
        ml.last_tick = 0
        ml.event_loop()


def wait_timers():
    time.sleep(3.5)


check(ml.group_id == 'G001' and ml.my_char == 'fnuk02' and ml.my_passcode == '01234567',
      'reads -mlgroup, --character and --passcode from the command line')

# --- secondary passcode
REG = bytes([1, 8, 0, 0xAA, 0xBB])
ml.handle_joymax(0x2116, bytes([2]))
check(ml.state == 'error' and 'blocked' in ml.message, 'blocked passcode (02) is reported')
ml.handle_joymax(0x2116, bytes([0]))
check(ml.passcode_recording == [] and ml.state == 'error', 'new account (00) without a learned packet: asks to set it by hand and records')
ml.handle_silkroad(0x6117, REG)
ml.handle_joymax(0xA117, bytes([1, 1]))
learned = json.load(open(CFG + 'MassLeveler/macros.json')).get('passcode_register')
check(learned == {ml.passcode_key(): ['0x6117 | 01 08 00 AA BB']} and ml.state == 'waiting login',
      'learns the passcode registration packet from the client, stored for this passcode')
calls.clear()
ml.handle_joymax(0x2116, bytes([0]))
time.sleep(2.0)
check(('inject', 0x6117, REG) in calls, 'next new account replays the learned registration')
ml.handle_joymax(0xA117, bytes([1, 1]))
check(ml.passcode_sent is None and ml.state == 'waiting login', 'server accepts the registration')
# no answer -> sent once more, then reported
calls.clear()
ml.handle_joymax(0x2116, bytes([0]))
ml.passcode_sent = (time.time() - 60, ml.passcode_sent[1])
run_loop()
time.sleep(2.0)
check(calls.count(('inject', 0x6117, REG)) == 2, 'registration is sent again when the server does not answer')
ml.passcode_sent = (time.time() - 60, ml.passcode_sent[1])
run_loop()
check(ml.state == 'error' and 'no answer' in ml.message, 'gives up after one retry')
ml.handle_joymax(0x2116, bytes([0]))
time.sleep(2.0)
ml.handle_joymax(0xA117, bytes([1, 2]))
check(ml.state == 'error' and 'refused' in ml.message, 'a refused registration is reported')
# packets learned by v1.0 (one list for all accounts) still work
json.dump({'passcode_register': ['0x6117 | 01 08 00 CC DD']}, open(CFG + 'MassLeveler/macros.json', 'w'))
check(ml.learned_passcode_packets() == ['0x6117 | 01 08 00 CC DD'], 'v1.0 learned packet is still used')
# a different passcode does not get another passcode's packet
json.dump({'passcode_register': {'0123456789ab': ['0x6117 | 01 08 00 EE FF']}}, open(CFG + 'MassLeveler/macros.json', 'w'))
check(ml.learned_passcode_packets() == [], 'packet of another passcode is not used')
# a bot that was already waiting before the packet was learned picks it up later
os.remove(CFG + 'MassLeveler/macros.json')
ml.handle_joymax(0x2116, bytes([0]))
ml.passcode_recording = None
json.dump({'passcode_register': {ml.passcode_key(): ['0x6117 | 01 08 00 AA BB']}}, open(CFG + 'MassLeveler/macros.json', 'w'))
calls.clear()
run_loop()
time.sleep(2.0)
check(('inject', 0x6117, REG) in calls and not ml.needs_passcode, 'a waiting bot uses the packet once another bot learned it')
ml.handle_joymax(0xA117, bytes([1, 1]))
json.dump({'collect_storage': ['0x7168 | 01 02']}, open(CFG + 'MassLeveler/macros.json', 'w'))
ml.handle_joymax(0x2116, bytes([1]))
ml._set_state('waiting login', '')

# --- character creation
calls.clear()
ml.character_listing(['someoneelse'])
wait_timers()
check(('inject', 0x7007, b'\x04\x06\x00fnuk02') in calls, 'checks the name when the character is missing')
calls.clear()
ml.handle_joymax(0xB007, bytes([4, 1]))
wait_timers()
inj = [c for c in calls if c[0] == 'inject' and c[2][:1] == b'\x01']
check(bool(inj) and inj[0][2].endswith((3634).to_bytes(4, 'little')), 'sends the create packet with the spear last')
check(('inject', 0x7007, b'\x02') in calls, 'asks for the character list again after creating')
ml.handle_joymax(0xB007, bytes([1, 1]))
calls.clear()
ml.character_listing(['fnuk02'])
wait_timers()
check(('select', 'fnuk02') in calls, 'selects the character once it exists')
check(any(c[0] == 'log' and 'without a job' in c[1] for c in calls), 'no job selection learned: says so, goes in')

# --- job selection: learned once from the client by hand, replayed for every new character
ml.handle_silkroad(0x7007, b'\x02')
ml.handle_silkroad(0x7001, b'\x06\x00fnuk02')
check('job_select' not in json.load(open(CFG + 'MassLeveler/macros.json')), 'list / select are not the job selection')
ml.handle_silkroad(0x7007, b'\x09\x06\x00fnuk02\x02')
stored = json.load(open(CFG + 'MassLeveler/macros.json'))
check(stored.get('job_select') == [{'opcode': '0x7007', 'before': '09', 'after': '02'}] and stored.get('collect_storage'),
      'learns the job packet by hand, the name cut out')
ml.creating, ml.create_name = True, 'fnuk02'
ml.handle_joymax(0xB007, bytes([1, 1]))
calls.clear()
ml.character_listing(['fnuk02'])
time.sleep(3.0)
check(('inject', 0x7007, b'\x09\x06\x00fnuk02\x02') in calls and ('select', 'fnuk02') not in calls,
      'new character: the learned job packet with its name, before selecting')
time.sleep(3.0)
check(('select', 'fnuk02') in calls, '... then selects it')
calls.clear()
ml.character_listing(['fnuk02'])
wait_timers()
check(not any(c[0] == 'inject' for c in calls) and ('select', 'fnuk02') in calls, 'an old character: no job packet')

# --- join + first area + collect
char['name'] = 'fnuk02'
status('fnuk01', 1)
status('fnuk03', 1)
ml.joined_game()
ml.joined_at = time.time() - 10
calls.clear()
run_loop()
wait_timers()
check(('area', plan[0]['name']) in calls, 'level 1 group goes to the first area')
check(('start_bot',) in calls, 'starts the bot after setting the area')
check(any(c[0] == 'inject' and c[1] == 0x7168 for c in calls), 'collects the item mall storage on first login')

# --- group minimum decides the area
char['level'] = 30
status('fnuk01', 30)
status('fnuk03', 12)
calls.clear()
run_loop()
want = next(a for a in plan if a['min_level'] <= 12 <= a['max_level'])['name']
check(('area', want) in calls, 'area follows the LOWEST member (12), not my level (30)')
check(any(c[0] == 'inject' and c[1] == 0x7168 for c in calls), 'collects again when passing a collect level (10)')
calls.clear()
run_loop()
check(not any(c[0] == 'area' for c in calls), 'does not switch again while the lowest level is unchanged')

status('fnuk03', 31)
calls.clear()
run_loop()
want = next(a for a in plan if a['min_level'] <= 30 <= a['max_level'])['name']
check(('area', want) in calls, 'everyone moves on when the lowest member levels')

# --- a status file that can't be read right now (being replaced) keeps the member's last level
real_read = ml._read_json
ml._read_json = lambda path, default=None: default if path.endswith('fnuk03.json') else real_read(path, default)
check(ml.group_min_level(40) == 30 and ml.member_levels.get('fnuk03') == 31,
      'a failed read keeps the last level (not level 1 -> no jump to the first area)')
ml._read_json = real_read

# --- a member still logging in (status level 0) does not pull the group down to level 1
ml.member_levels.pop('fnuk03', None)
status('fnuk03', 0)
check(ml.group_min_level(40) == 30, 'a member with level 0 (logging in) is not counted as level 1')
status('fnuk03', 31)

# --- a command written before the plugin started (an old Stop) is not carried out
json.dump({'id': ml.PLUGIN_STARTED - 60, 'cmd': 'stop'}, open(CFG + 'MassLeveler/commands/fnuk02.json', 'w'))
check(ml.read_command() is None, 'an old command from the last run is ignored')
json.dump({'id': time.time(), 'cmd': 'start'}, open(CFG + 'MassLeveler/commands/fnuk02.json', 'w'))
check(ml.read_command() == 'start', 'a new one is carried out')

# --- unknown member counts as level 1
os.remove(CFG + 'MassLeveler/status/fnuk03.json')
check(ml.group_min_level(30) == 1, 'a member without a status file counts as level 1')
status('fnuk03', 31)

# --- spots: the leader moves the whole group to the next spot of the area when too few monsters come
area = ml.area_for(30)
spot2 = dict(region=area['region'], x=area['x'] + 300, y=area['y'], z=0, radius=40, src='alt')
area['spots'] = [dict(region=area['region'], x=area['x'], y=area['y'], z=0, radius=40, src='plan'), spot2]
ml.group['leader'] = 'fnuk02'
ml.group['spot_rules'] = {'window': 0.5, 'min_monsters': 3}
char['x'], char['y'] = area['x'], area['y']
monsters.clear()
monsters[101] = {'x': area['x'] + 5, 'y': area['y'], 'name': 'Tiger'}
run_loop()
time.sleep(0.6)
calls.clear()
run_loop()
sel = json.load(open(CFG + 'MassLeveler/groups/G001.spot.json'))
check(sel['area'] == area['name'] and sel['x'] == spot2['x'], 'leader: 1 monster in the window (< 3) -> next spot')
run_loop()
check(('pos', (area['region'], spot2['x'], spot2['y'], 0.0)) in calls, 'and the group walks to that spot')
calls.clear()
run_loop()
check(not any(c[0] in ('pos', 'area') for c in calls), 'stays there (not arrived yet, no new switch)')

ml.last_spawn_scan = 0
ml.scan_spawns(time.time())
ml.last_spawn_scan = 0
ml.scan_spawns(time.time(), force_save=True)       # seen again: still one monster
sp = json.load(open(CFG + 'MassLeveler/spawns/fnuk02.json'))
check(sum(c['mobs'].get('Tiger', 0) for c in sp.values()) == 1, 'records each monster seen once (spawns/<char>.json)')

g2 = json.load(open(CFG + 'MassLeveler/groups/G001.json'))
g2['leader'] = 'fnuk01'
json.dump(g2, open(CFG + 'MassLeveler/groups/G001.json', 'w'))
os.utime(CFG + 'MassLeveler/groups/G001.json', (time.time() + 5, time.time() + 5))
os.remove(CFG + 'MassLeveler/groups/G001.spot.json')
ml.last_group_check = 0
calls.clear()
run_loop()
check(any(c[0] == 'log' and 'Group file changed' in c[1] for c in calls) and 'spots' not in ml.area_for(30),
      're-reads the group file when the manager rewrites it')
check(any(c == ('area', area['name']) for c in calls), 'and goes back to the first spot when the spot file is gone')

# --- 'area' from the manager: set the spot again and start (phBot walks there); dead -> respawn in town
json.dump({'id': 'a1', 'cmd': 'area'}, open(CFG + 'MassLeveler/commands/fnuk02.json', 'w'))
calls.clear()
run_loop()
check(any(c[0] == 'area' for c in calls) and any(c[0] == 'log' and 'set again' in c[1] for c in calls),
      "'area' command: the plugin sets its area again and starts the bot")
wait_timers()
check(('start_bot',) in calls, 'and the bot is started')
char['dead'] = True
json.dump({'id': 'a2', 'cmd': 'area'}, open(CFG + 'MassLeveler/commands/fnuk02.json', 'w'))
calls.clear()
run_loop()
check(('inject', 0x3053, b'\x01') in calls, "'area' while dead: respawn in town")
char['dead'] = False

# --- manager commands
json.dump({'id': time.time(), 'cmd': 'stop'}, open(CFG + 'MassLeveler/commands/fnuk02.json', 'w'))
calls.clear()
run_loop()
check(('stop_bot',) in calls and ml.state == 'stopped', 'stop command stops the bot')
calls.clear()
run_loop()
check(not any(c[0] == 'start_bot' for c in calls), 'stays stopped')
json.dump({'id': time.time() + 1, 'cmd': 'start'}, open(CFG + 'MassLeveler/commands/fnuk02.json', 'w'))
run_loop()
wait_timers()
check(ml.state == 'leveling', 'start command resumes')

# --- skills: lists follow what the character has learned
def learn(*names):
    for n in names:
        skills[str(1000 + len(skills))] = {'name': n}


def settle():
    """pretend phBot finished loading the profile and the next skill check is due"""
    ml.profile_switched_at = 0
    ml.last_skill_check = 0


B30 = 'Lv021-030'
check([b['name'] for b in nuker['brackets']][:3] == ['Lv001-010', 'Lv011-020', B30] and len(nuker['brackets']) == 10,
      'the nuker build has 10 brackets of 10 levels')
learn('Ice River Force', 'River Fire force', 'Self Breathe Heal', 'Shock Lion Shout', 'Flame Wave - Arrow', "Wolf's Thunderbolt",
      'Soul Rebirth Art', 'Heal - Medical Hand', 'Fire Shield - Phoenix')
settle()
calls.clear()
run_loop()       # level 30 -> bracket Lv021-030
p30 = json.load(open(CFG + 'Elysian_fnuk02.%s.json' % B30))['Skills']
check(p30['sNormal'] == ['Flame Wave - Arrow', "Wolf's Thunderbolt", 'Shock Lion Shout'],
      'bracket profile: planned attack skills in plan order, only the learned ones (Ice shot not learned yet)')
check(p30['bNormal'] == [], 'no buffs outside the plan (Fire Shield is not a Force buff)')
check(p30['Imbue'] == 'River Fire force', 'Fire imbue: planned Extreme Fire force, River used until learned')
check(p30['Healing'] == ['Self Breathe Heal', 'Heal - Medical Hand'],
      'a planned rank not learned yet (Self Force Heal) falls back to the learned lower rank')
check(p30['Resurrect'] == ['Soul Rebirth Art'] and p30['Party Buffs'].get('Magical Buff') == ['fnuk03'],
      'resurrect skill and party buffs are set')
check(('profile', B30) in calls and ('stop_bot',) in calls and ml.profile_name == B30, 'loads the bracket profile')
loop_on = [n for n, a in json.load(open(CFG + 'Elysian_fnuk02.%s.json' % B30))['Loop']['Script'].items() if a['Enabled']]
check(loop_on == [ml.area_for(ml.group_min_level(30))['name']] or loop_on == [plan[0]['name']],
      'the new profile keeps one training area enabled')
calls.clear()
run_loop()
check(not any(c[0] in ('area', 'start_bot') for c in calls), 'waits while phBot loads the profile')
ml.profile_switched_at = 0
run_loop()
wait_timers()
check(any(c[0] == 'area' for c in calls) and ('start_bot',) in calls, 'then sets the area again and restarts the bot')
settle()
calls.clear()
run_loop()
check(not any(c[0] == 'profile' for c in calls), 'no reload while nothing new is learned')
learn('Snow Storm - Ice shot', 'Self Force Heal')
settle()
calls.clear()
run_loop()
p30b = json.load(open(CFG + 'Elysian_fnuk02.%sb.json' % B30))['Skills']
check(('profile', B30 + 'b') in calls and p30b['sNormal'][1] == 'Snow Storm - Ice shot' and p30b['Healing'][0] == 'Self Force Heal',
      'a planned skill learned inside the bracket is used right away (bracket reloaded as ...b)')
ml.profile_switched_at = 0
run_loop()
wait_timers()
char['level'] = 31
settle()
calls.clear()
run_loop()
p40 = json.load(open(CFG + 'Elysian_fnuk02.Lv031-040.json'))['Skills']
check(('profile', 'Lv031-040') in calls and p40['sNormal'][-1] == 'Shock Lion Shout',
      'level 31 loads the next bracket (Heaven Lion Shout planned, Shock used until learned)')
# relog: phBot starts on Lv031-040 (default file points at it) with the right lists -> no reload
json.dump({'Profile': 'Lv031-040'}, open(CFG + 'Elysian_fnuk02.json', 'w'))
ml.joined_game()
ml.joined_at = time.time() - 10
ml.ready = True
settle()
calls.clear()
run_loop()
check(ml.profile_name == 'Lv031-040' and not any(c[0] == 'profile' for c in calls), 'after a relog the saved lists are kept')
profiles['ok'] = False
learn('Heaven Lion Shout')
settle()
calls.clear()
run_loop()
check(ml.state == 'leveling' and any('NOT active' in c[1] for c in calls if c[0] == 'log'),
      'if phBot cannot load the profile it keeps leveling and logs it')
profiles['ok'] = True
wait_timers()

# --- berserk as soon as the gauge is full
ml._set_state('leveling', '')
calls.clear()
ml.handle_joymax(0x304E, bytes([4, 3, 1, 0, 0, 0]))
check(not any(c[0] == 'inject' and c[1] == 0x70A7 for c in calls), 'no berserk while the gauge is not full')
ml.handle_joymax(0x304E, bytes([4, 5, 1, 0, 0, 0]))
check(('inject', 0x70A7, b'\x01') in calls, 'berserk when the gauge is full')
calls.clear()
ml.last_zerk = 0
char['dead'] = True
ml.handle_joymax(0x304E, bytes([4, 5, 1, 0, 0, 0]))
check(not any(c[0] == 'inject' and c[1] == 0x70A7 for c in calls), 'no berserk while dead')
char['dead'] = False
ml.handle_joymax(0x304E, bytes([4, 0, 1, 0, 0, 0]))

# --- finish at 101 when the lowest reaches it
char['level'] = 101
status('fnuk01', 101)
status('fnuk03', 100)
calls.clear()
run_loop()
check(ml.state == 'leveling', 'does not finish while one member is still 100')
status('fnuk03', 101)
zone['name'] = 'Roc Mountain'
learn('Flame Wave - Arrow')
ml.applied_skills = ('changed',)
ml.profile_switched_at = 0
calls.clear()
run_loop()
check(ml.state == 'leveling' and 'final skills' in ml.message and not ('return',) in calls,
      'lowest 101: saves the final skills in the profile first')
run_loop()
check(not ('return',) in calls, 'waits while phBot loads that profile')
settle()
run_loop()
wait_timers()
check(ml.state == 'finishing' and ('return',) in calls, 'finishes: stop + return scroll when the lowest is 101')
run_loop()
check(ml.state == 'finishing', 'waits until in town')
zone['name'] = 'Hotan'
run_loop()
st = json.load(open(CFG + 'MassLeveler/status/fnuk02.json'))
check(ml.state == 'done' and st['state'] == 'done', 'reports done once in town')

shutil.rmtree(TMP, ignore_errors=True)
print('\n%d failure(s)' % len(failures))
sys.exit(1 if failures else 0)
