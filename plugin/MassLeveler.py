# -*- coding: utf-8 -*-
"""MassLeveler Escort - group auto leveling for phBot with an escort (by Mosap).

Started by the MassLeveler manager with the extra argument  -mlgroup=<GROUP_ID>.
Without that argument the plugin stays idle, so your normal bots are not affected.

Escort version: the group file has 'stages' (by the group's lowest level). The escort (-mlgroup=ESCORT) and the
group reverse to the stage (reverse scroll); the escort waits until the whole group is around it + a few seconds,
then hunts at the stage's point while the group bot-traces it (attacking too where the stage says so).
The escort levels the group named in escort.json (written by the manager) and waits at the first stage without one.

What it does for every character of a group (same training areas, no tracing):
  * creates the character on the character screen if it does not exist yet (model + items of its build)
  * collects the free items from the item mall storage (learned packet sequence, see the tab)
  * keeps an EXP Booster scroll of its inventory on: a new one whenever none is active (at any level)
  * picks the training area from the plan using the LOWEST level of the whole group
    (all switch together, phBot walks there with its own path finding)
  * every area has several spots; the leader moves the group to the next one when too few monsters come
    into the spot (groups/<id>.spot.json), and every bot records the monsters it sees (spawns/<char>.json)
    so the manager learns where they really spawn
  * loads the phBot profile of its 10-level bracket (Lv001-010 ... Lv091-100, from its build's plan).
    phBot drops every skill the character doesn't have when it loads a profile, so the lists hold the
    skills learned so far (a planned rank not learned yet -> best learned lower rank of the same line),
    and the bracket is reloaded (Lv041-050 <-> Lv041-050b) whenever a planned skill gets learned
  * uses berserk as soon as the gauge is full
  * when the lowest member reaches the final level: stop, return scroll, wait in town, report done
  * writes a status file every few seconds for the manager dashboard

Shared folder:  <phBot>/Config/MassLeveler/
    groups/<id>.json      written by the manager (members, builds, plan, party buffs, settings); re-read when changed
    groups/<id>.spot.json the spot of the current area the group uses (leader / manager)
    spawns/<char>.json    monsters seen per 20x20 cell (written by this plugin)
    status/<char>.json    written by this plugin
    commands/<char>.json  written by the manager (start / stop / finish / quit / area)
    macros.json           learned packet sequences (shared by all characters)
"""

from phBot import *
from threading import Timer
import QtBind
import copy
import hashlib
import json
import math
import os
import struct
import time

pName = 'MassLeveler'
pVersion = '2.9.0-escort'

LEARN_SECONDS = 10
IGNORE_OPCODES = {0x2002, 0x7021, 0x7074, 0x7034}   # ping, movement, attack/skill, inventory move
TICK_SECONDS = 2.0
STATUS_SECONDS = 5.0
START_DELAY = 6.0           # seconds after joining before we touch training area / start the bot
FINISH_TOWN_TIMEOUT = 90.0
TOWN_WORDS = ('jangan', 'donwhang', 'hotan', 'samarkand', 'constantinople', 'alexandria', 'baghdad')
SKILL_CHECK_SECONDS = 10.0
PROFILE_SETTLE = 4.0        # seconds after set_profile before the area is set and the bot started again
SKILL_LISTS = ('sNormal', 'bNormal', 'bScript', 'Healing', 'Resurrect', 'Imbue')
GROUP_CHECK_SECONDS = 10.0  # re-read groups/<id>.json when the manager rewrote it (new spots, radius ...)
SPAWN_SCAN_SECONDS = 5.0    # record the monsters around (spawns/<char>.json: where they really are)
SPAWN_SAVE_SECONDS = 60.0
SPAWN_CELL = 20.0           # world units per recorded cell
ZERK_MAX = 5                # berserk gauge (hwan points) when full
ZERK_RETRY = 4.0
PASSCODE_RETRY = 12.0       # resend the registration once if the server didn't answer
REVERSE_RETRY = 60.0        # one reverse scroll try this often (casting + loading the new place)
COLLECT_WAIT = 25.0         # the storage collect puts the reverse scrolls in the inventory first
# item code sent after the slot in 0x704C (MoFGW learned it on this server); one used by hand is learned too
BOOSTER_CODES = {'Exp Booster (400%) 1 hour': '310c0d15'}
BOOSTER_CHECK = 5.0         # seconds after using a booster: one less in the inventory = used
BOOSTER_RETRY = 30.0
BOOSTER_TRIES = 3
BOOSTER_IDLE = 300.0        # no booster in the inventory (or not taken): look again this often
ESCORT_ARRIVED = 300.0      # the escort is at its stage this close to the stage point
MEMBER_NEAR = 250.0         # a member traces the escort from this close (else it reverses to the stage)
STAGE_NEAR = 400.0          # this close to the stage point a reverse would not help: wait for the escort
TRACE_LOST = 60.0           # tracing but this far from the escort for TRACE_RETRY seconds: start the trace again
TRACE_RETRY = 20.0
STATUS_FRESH = 30.0         # an escort status older than this: the escort is not in game
MEMBER_RES_WAIT = 120.0     # dead member not resurrected (by the escort or the party) this long: town
ESCORT_RES_WAIT = 2.0       # dead escort: town after this, then reverse straight to its death point
DEATH_LOOP_COUNT = 3        # the escort died this often within DEATH_LOOP_SECONDS: no reverse back alone, it waits
DEATH_LOOP_SECONDS = 300.0  # in town for the whole group, then they go back together

# ---------------------------------------------------------------- runtime state
group_id = ''
standard_mode = False         # -mlstandard: the manager's Standard mode, map commands only
my_char = ''
group = None                 # dict from groups/<id>.json
joined_at = 0.0
ready = False                # in game + character data loaded + area applied
bot_started = False
current_area = ''
state = 'idle'
last_tick = 0.0
last_status = 0.0
last_level = None
last_cmd_id = None
PLUGIN_STARTED = time.time()
message = ''
creating = False
create_name = ''
job_pending = False          # a character created this session: its job is picked (learned packet) before it enters
char_names = []              # the last character list (the job selection packet is learned by the name in it)
job_recording = None         # (time, packets) of the job selection the user makes by hand
login_packets = 0
my_passcode = ''             # from phBot's --passcode argument (learned packets are stored per passcode)
passcode_recording = None    # list while we record the client's passcode registration
needs_passcode = False       # waiting at the "new passcode" step without a learned packet
passcode_sent = None         # (time, packets) of our last registration, until the server answers
passcode_retries = 0
applied_skills = None        # skill lists phBot has loaded for this character
profile_name = 'Default'     # phBot profile in use
profile_switched_at = 0.0
finish_wanted = False       # the manager's finish: kept until the final skills are saved
last_skill_check = 0.0
group_mtime = 0.0
last_group_check = 0.0
current_spot = None          # (x, y) of the spot of current_area in use
spot_watch = {'key': None, 'arrived': 0.0, 'seen': {}}   # leader: monsters seen inside the spot (uid -> time)
spawn_cells = {}             # monsters seen since the last save, per cell
spawn_seen = set()
last_spawn_scan = 0.0
last_spawn_save = 0.0
zerk_points = 0
last_zerk = 0.0
zerk_uses = 0
finish_started = 0.0
collected_levels = set()
collect_started = 0.0
booster_due = 0.0            # not before this the next EXP booster (0 = now); kept in the status file
booster_used = None          # (time, boosters in the inventory before) of the last use, until it is checked
booster_tries = 0
booster_missing = False      # "no booster in the inventory" was logged
tracing = False             # member: our start_trace of the escort is on
trace_started = 0.0
trace_far_since = 0.0
last_reverse = 0.0
dead_since = 0.0
death_back = False           # member: respawned in town at once, reverse to the death point next
solo_active = False          # member: leveling alone below the group's solo_level (no party yet)
esc ={'key': None, 'phase': 'travel', 'gather_since': 0.0, 'death_pending': False, 'respawned': False, 'respawn_at': 0.0,
      'deaths': [], 'regroup': False}
reverse_ok = True            # the last reverse scroll went through (False: none left, or a wrong name)
town_wait = False            # escort: it waits in town (regroup after dying, or no reverse scroll): the group comes
last_return = 0.0            # member: last return scroll to the escort waiting in town
learning_until = 0
captured_pending = []
captured_seen = set()

# ---------------------------------------------------------------- GUI
gui = QtBind.init(__name__, pName)
QtBind.createLabel(gui, 'MassLeveler v' + pVersion + ' - group auto leveling (controlled by the MassLeveler manager)', 10, 8)
lblGroup = QtBind.createLabel(gui, 'Group: none (start this bot from the manager)', 10, 30)
lblArea = QtBind.createLabel(gui, 'Area: -', 10, 50)
lblState = QtBind.createLabel(gui, 'State: idle', 10, 70)

QtBind.createLabel(gui, 'Item mall storage collect: press Learn, then click "collect" in the storage under the minimap (10 s)', 10, 100)
QtBind.createButton(gui, 'btn_learn', '  Learn  ', 10, 120)
QtBind.createButton(gui, 'btn_clear_captured', '  Clear  ', 80, 120)
QtBind.createButton(gui, 'btn_test_collect', '  Collect now (test)  ', 150, 120)
lstCaptured = QtBind.createList(gui, 10, 146, 330, 110)
QtBind.createButton(gui, 'btn_add_selected', '  Add selected to collect sequence  >  ', 10, 260)
QtBind.createLabel(gui, 'Collect sequence (sent in this order, shared by all characters):', 360, 128)
lstSequence = QtBind.createList(gui, 360, 146, 330, 110)
QtBind.createButton(gui, 'btn_remove_selected', '  Remove selected  ', 360, 260)


def _log(msg):
    log('[%s] %s' % (pName, msg))
    if my_char:
        # own file, because phBot only writes its log file after the character joined
        try:
            with open(base_dir() + 'logs/' + my_char + '.txt', 'a', encoding='utf-8') as f:
                f.write(time.strftime('%Y-%m-%d %H:%M:%S ') + msg + '\n')
        except Exception:
            pass


def _set_state(s, msg=None):
    global state, message
    state = s
    if msg is not None:
        message = msg
    QtBind.setText(gui, lblState, 'State: %s%s' % (s, (' - ' + message) if message else ''))


# ---------------------------------------------------------------- paths / files
def base_dir():
    return get_config_dir() + pName + '/'


def _ensure_dirs():
    for sub in ('', 'groups', 'status', 'commands', 'logs', 'spawns'):
        d = base_dir() + sub
        if not os.path.isdir(d):
            os.makedirs(d)


def _read_json(path, default=None):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path, data):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    for attempt in range(10):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.05)    # another bot is reading the file right now (Windows)
    os.replace(tmp, path)


def macros_path():
    return base_dir() + 'macros.json'


def load_macros():
    QtBind.clear(gui, lstSequence)
    for item in (_read_json(macros_path(), {}) or {}).get('collect_storage', []):
        QtBind.append(gui, lstSequence, item)


def save_macros():
    data = _read_json(macros_path(), {}) or {}
    data['collect_storage'] = QtBind.getItems(gui, lstSequence)
    _write_json(macros_path(), data)


# ---------------------------------------------------------------- command line
def parse_args():
    global group_id, my_char, my_passcode, standard_mode
    args = get_command_line_args() or []
    for i, a in enumerate(args):
        low = a.lower()
        if low.startswith('-mlgroup='):
            group_id = a.split('=', 1)[1].strip()
        elif low == '--character' and i + 1 < len(args):
            my_char = args[i + 1].strip().strip('"')
        elif low == '--passcode' and i + 1 < len(args):
            my_passcode = args[i + 1].strip().strip('"')
        elif low == '-mlstandard':
            standard_mode = True


def group_file():
    return base_dir() + 'groups/' + group_id + '.json'


def spot_file():
    """which spot of the current area the group uses (written by the leader or the manager)"""
    return base_dir() + 'groups/' + group_id + '.spot.json'


def load_group():
    global group, group_mtime
    if not group_id:
        return None
    try:
        group_mtime = os.path.getmtime(group_file())
    except OSError:
        group_mtime = 0.0
    group = _read_json(group_file()) or group
    if group:
        QtBind.setText(gui, lblGroup, 'Group: %s  members: %s  leader: %s' % (
            group_id, ', '.join(group.get('members', [])), group.get('leader', '')))
    return group


# ---------------------------------------------------------------- group level / area choice
def own_data():
    d = get_character_data()
    if not d or not d.get('name') or not d.get('level'):
        return None             # right after joining phBot reports level 0 (= not loaded yet)
    return d


member_levels = {}     # last level read from each member's status file


def group_min_level(my_level, names=None):
    """A read fails now and then while another bot replaces its status file (Windows); that member keeps
    its last known level instead of counting as level 1 (which sent the group to the level 1 area).
    names: other characters than this group's (the escort's group); my_level None = without me"""
    levels = [my_level] if my_level is not None else []
    me = (my_char or '').lower()
    for name in group.get('members', []) if names is None else names:
        if name.lower() == me:
            continue
        path = base_dir() + 'status/' + name + '.json'
        st = None
        for attempt in range(3):
            st = _read_json(path)
            if st or not os.path.exists(path):
                break
            time.sleep(0.05)
        if st and st.get('level'):
            member_levels[name] = int(st['level'])
        elif not os.path.exists(path):
            member_levels.pop(name, None)
        elif st and name not in member_levels:
            continue                                  # status with level 0: still logging in, level unknown
        levels.append(member_levels.get(name, 1))     # not created / never seen yet -> level 1
    return min(levels) if levels else 1


def area_for(level):
    areas = group.get('areas', [])
    for a in areas:
        if a['min_level'] <= level <= a['max_level']:
            return a
    return areas[-1] if areas else None


def spots_of(area):
    """the area's spots, best first (manual, learned from the bots, the plan's, its alternatives)"""
    return area.get('spots') or [area]


def chosen_spot(area):
    """(index, spot) the group uses: the one named in groups/<id>.spot.json for this area, else the first"""
    spots = spots_of(area)
    sel = _read_json(spot_file()) or {}
    if sel.get('area') == area['name'] and sel.get('x') is not None:
        for i, s in enumerate(spots):
            if abs(s['x'] - sel['x']) < 5 and abs(s['y'] - sel['y']) < 5:
                return i, s
    return 0, spots[0]


def apply_area(area, i=0, spot=None):
    global current_area, current_spot
    spot = spot or spots_of(area)[0]
    ok = False
    try:
        ok = set_training_area(area['name'])
    except Exception:
        ok = False
    if not ok or abs(spot['x'] - area['x']) >= 5 or abs(spot['y'] - area['y']) >= 5:
        # area missing from the profile, or another spot of it: move the active training area there
        set_training_position(int(spot.get('region', 0)), float(spot['x']), float(spot['y']), float(spot.get('z', 0)))
    set_training_radius(float(spot.get('radius', area.get('radius', 50))))
    current_area = area['name']
    current_spot = (spot['x'], spot['y'])
    n = len(spots_of(area))
    where = ' - spot %d/%d (%s)' % (i + 1, n, spot.get('src', 'plan')) if n > 1 else ''
    QtBind.setText(gui, lblArea, 'Area: %s%s  (monster lv %s, %s)' % (area['name'], where, area.get('monster_level', '?'),
                                                                    area.get('town', '')))
    _log('Training area -> %s%s' % (area['name'], where))


def check_spot(area, i, spot, d):
    """leader: if fewer than min_monsters different monsters came into the spot during `window` seconds after
    arriving, move the whole group to the next spot of the area (every member reads the spot file)"""
    rules = group.get('spot_rules') or {}
    window, need = float(rules.get('window', 120)), int(rules.get('min_monsters', 6))
    spots = spots_of(area)
    if my_char != group.get('leader') or len(spots) < 2 or need <= 0 or d.get('dead'):
        return
    now = time.time()
    key = (area['name'], spot['x'], spot['y'])
    if spot_watch['key'] != key:
        spot_watch.update(key=key, arrived=0.0, seen={})
    p = get_position() or {}
    r = float(spot.get('radius', area.get('radius', 40)))
    if math.hypot(p.get('x', 0) - spot['x'], p.get('y', 0) - spot['y']) > r + 20:
        return                                    # still on the way
    if not spot_watch['arrived']:
        spot_watch['arrived'] = now
    try:
        mobs = get_monsters() or {}
    except Exception:
        mobs = {}
    seen = spot_watch['seen']
    for uid, m in mobs.items():
        if math.hypot(m.get('x', 0) - spot['x'], m.get('y', 0) - spot['y']) <= r + 10:
            seen[uid] = now
    for uid in [u for u, t in seen.items() if now - t > window]:
        del seen[uid]
    if now - spot_watch['arrived'] >= window and len(seen) < need:
        nxt = (i + 1) % len(spots)
        _write_json(spot_file(), {'area': area['name'], 'x': spots[nxt]['x'], 'y': spots[nxt]['y'], 'ts': now,
                                  'by': my_char, 'why': '%d monster(s) in %d s at spot %d' % (len(seen), window, i + 1)})
        _log('Spot %d/%d of %s: only %d monster(s) in %d s - moving the group to spot %d' % (
            i + 1, len(spots), area['name'], len(seen), window, nxt + 1))
        spot_watch['key'] = None


def scan_spawns(now, force_save=False):
    """count every monster seen once (new uid = a new spawn) in 20x20 cells; the manager learns the real
    spawn spots from all bots' files"""
    global last_spawn_scan, last_spawn_save
    if now - last_spawn_scan >= SPAWN_SCAN_SECONDS:
        last_spawn_scan = now
        try:
            mobs = get_monsters() or {}
        except Exception:
            mobs = {}
        p = get_position() or {}
        for uid, m in mobs.items():
            if uid in spawn_seen:
                continue
            spawn_seen.add(uid)
            x, y, name = m.get('x'), m.get('y'), m.get('servername') or m.get('name')
            if x is None or y is None or not name:
                continue
            cx, cy = int(x // SPAWN_CELL), int(y // SPAWN_CELL)
            c = spawn_cells.setdefault('%d|%d' % (cx, cy), {
                'x': round((cx + 0.5) * SPAWN_CELL, 1), 'y': round((cy + 0.5) * SPAWN_CELL, 1),
                'region': p.get('region', 0), 'z': round(p.get('z', 0), 1), 'mobs': {}})
            c['mobs'][name] = c['mobs'].get(name, 0) + 1
        if len(spawn_seen) > 50000:
            spawn_seen.clear()
    if spawn_cells and (force_save or now - last_spawn_save >= SPAWN_SAVE_SECONDS):
        last_spawn_save = now
        path = base_dir() + 'spawns/' + my_char + '.json'
        data = _read_json(path, {}) or {}
        for k, c in spawn_cells.items():
            old = data.setdefault(k, {key: c[key] for key in ('x', 'y', 'region', 'z')})
            old.setdefault('mobs', {})
            for name, n in c['mobs'].items():
                old['mobs'][name] = old['mobs'].get(name, 0) + n
        try:
            _write_json(path, data)
            spawn_cells.clear()
        except Exception as e:
            _log('spawn file not saved: %s' % e)


def restart_bot():
    global bot_started
    try:
        stop_bot()
    except Exception:
        pass
    Timer(2.0, _start_bot).start()
    bot_started = True


def _start_bot():
    try:
        start_bot()
    except Exception as e:
        _log('start_bot failed: %s' % e)


# ---------------------------------------------------------------- skills
def learned_skills():
    """({lower name: phBot name}, {id: name}) of the skills the character has now."""
    learned, ids = {}, {}
    for sid, s in (get_skills() or {}).items():
        n = str((s or {}).get('name', '')).strip()
        if n:
            learned[n.lower()] = n
            ids[str(sid)] = n
    return learned, ids


def my_build():
    bid = (group.get('build_of') or {}).get(my_char)
    return (group.get('builds') or {}).get(bid) if bid else None


def bracket_for(build, level):
    """the 10-level bracket (its own phBot profile Lv001-010 ... Lv091-100) for this level"""
    brackets = build.get('brackets') or []
    for b in brackets:
        if b['min'] <= level <= b['max']:
            return b
    return brackets[-1] if brackets else None


def resolve(name, lines, learned):
    """the planned rank if learned, else the best learned lower rank of its line (None = nothing yet)"""
    for n, lv in reversed(lines.get(name) or [[name, 0]]):
        if n.lower() in learned:
            return learned[n.lower()]
    return None


def skill_setup(bracket, lines, learned):
    """phBot skill lists of the bracket, limited to what the character has learned (phBot drops the rest)"""
    s = {}
    for key in ('sNormal', 'bNormal', 'bScript', 'Healing', 'Resurrect'):
        out = []
        for n in bracket.get(key, []):
            r = resolve(n, lines, learned)
            if r and r not in out:
                out.append(r)
        s[key] = out
    s['Imbue'] = (resolve(bracket['Imbue'], lines, learned) or '') if bracket.get('Imbue') else ''
    return s


def profile_file(name):
    base = get_config_dir() + '%s_%s' % (group.get('server') or 'Elysian', my_char)
    return base + ('.json' if name == 'Default' else '.' + name + '.json')


def disk_profile_name():
    """Default, or the named profile the default file points at."""
    data = _read_json(profile_file('Default'), {}) or {}
    return data['Profile'] if len(data) == 1 and data.get('Profile') else 'Default'


def sync_skills(level, extra=None):
    """Loads the bracket profile of this level with the skills learned so far. True if the profile was switched.
    The same bracket is reloaded (Lv041-050 <-> Lv041-050b) when a planned skill gets learned inside it, or
    when the stage's settings change (extra = {profile section: values}, e.g. Trace and Scrolls; None = leave them)."""
    global applied_skills, profile_name, profile_switched_at, current_area, bot_started, tracing
    build = my_build()
    bracket = bracket_for(build, level) if build else None
    if not bracket:
        return False
    learned, ids = learned_skills()
    if not learned:
        return False                      # skill list not loaded yet
    setup = skill_setup(bracket, build.get('lines') or {}, learned)
    state_now = (bracket['name'], setup, extra)
    if state_now == applied_skills:
        return False
    names = (bracket['name'], bracket['name'] + 'b')
    prof = _read_json(profile_file(profile_name), {}) or {}
    if len(prof) < 5:
        prof = _read_json(profile_file(bracket['name']), {}) or {}
    if len(prof) < 5:
        applied_skills = state_now
        _log('Skills: profile file %s not found - skill lists not updated' % profile_file(profile_name))
        return False
    sk = prof.setdefault('Skills', {})
    extra_ok = all((prof.get(sec) or {}).get(k) == v for sec, vals in (extra or {}).items() for k, v in vals.items())
    if applied_skills is None and profile_name in names and extra_ok and \
            all(sk.get(k) == setup[k] for k in SKILL_LISTS):
        applied_skills = state_now        # phBot started with this bracket and these lists already
        _log('Skills loaded (profile %s): %s' % (profile_name, describe_skills(setup)))
        return False
    sk.update(copy.deepcopy(setup))
    for sec, vals in (extra or {}).items():
        prof[sec] = dict(prof.get(sec) or {}, **vals)
    pb = (group.get('party_buffs') or {}).get(my_char)
    if pb is not None:
        sk['Party Buffs'] = dict({k: [] for k in sk.get('Party Buffs', {})}, **pb)
    idmap = dict(sk.get('ID') or {})
    idmap.update(ids)
    sk['ID'] = idmap
    script = (prof.get('Loop') or {}).get('Script') or {}
    if current_area in script:
        for name, a in script.items():
            a['Enabled'] = name == current_area
    target = names[1] if profile_name == names[0] else names[0]
    _write_json(profile_file(target), prof)
    try:
        stop_bot()
    except Exception:
        pass
    try:
        ok = set_profile(target)
    except Exception as e:
        ok = False
        _log('set_profile failed: %s' % e)
    applied_skills = state_now
    if not ok:                            # keep leveling, but say it loudly
        _log('Skills: phBot did not load profile %s - attack skills are NOT active' % target)
        bot_started = False
        return False
    profile_name = target
    profile_switched_at = time.time()
    current_area = ''                     # event_loop sets the area again and restarts the bot
    bot_started = False
    tracing = False                       # ... and starts the trace again
    tr, sc = (extra or {}).get('Trace'), (extra or {}).get('Scrolls')
    _log('Skills updated (profile %s): %s%s%s' % (target, describe_skills(setup),
                                                 ' | trace attack %s' % tr['Attack Monsters'] if tr else '',
                                                 ' | speed scroll %s' % sc['Speed'] if sc else ''))
    return True


def describe_skills(s):
    return 'attack [%s] | buffs [%s] | walk [%s] | imbue [%s] | heal [%s] | resurrect [%s]' % (
        ', '.join(s['sNormal']), ', '.join(s['bNormal']), ', '.join(s['bScript']), s['Imbue'],
        ', '.join(s['Healing']), ', '.join(s['Resurrect']))


# ---------------------------------------------------------------- item mall storage collect
def parse_item(item):
    left, _, right = item.partition('|')
    opcode = int(left.strip(), 16)
    hexdata = right.strip()
    data = bytearray.fromhex(hexdata) if hexdata and hexdata != '(empty)' else bytearray()
    return opcode, data


def run_collect(reason):
    seq = (_read_json(macros_path(), {}) or {}).get('collect_storage', [])
    if not seq:
        _log('No collect sequence learned yet - skipped (%s)' % reason)
        return False

    def worker(i=0):
        if i >= len(seq):
            _log('Item mall storage collected (%s)' % reason)
            return
        try:
            opcode, data = parse_item(seq[i])
            inject_joymax(opcode, data, False)
        except Exception as e:
            _log('Bad collect line "%s": %s' % (seq[i], e))
        Timer(0.8, worker, (i + 1,)).start()

    worker()
    return True


def auto_equip():
    """the manager's Auto Equip: sends the helmet-button packets MoAutoEquip learned (its Config/MoAutoEquip.json)"""
    cfg = _read_json(get_config_dir() + 'MoAutoEquip.json', {}) or {}
    seq, enc = cfg.get('sequence') or [], bool(cfg.get('encrypted'))
    if not seq:
        _log('Auto equip: MoAutoEquip has no learned packet (learn it in its tab first)')
        return

    def worker(i=0):
        if i >= len(seq):
            _log('Auto equip pressed (manager)')
            return
        try:
            opcode, data = parse_item(seq[i])
            inject_joymax(opcode, data, enc)
        except Exception as e:
            _log('Bad auto equip line "%s": %s' % (seq[i], e))
        Timer(0.7, worker, (i + 1,)).start()

    worker()


def collect_levels():
    c = group.get('collect', {}) if group else {}
    return set(int(x) for x in c.get('levels', []))


# ---------------------------------------------------------------- EXP booster scrolls
def booster_key():
    return ((group or {}).get('exp_booster_name') or 'Exp Booster').lower()


def booster_codes():
    codes = dict(BOOSTER_CODES)
    codes.update((_read_json(macros_path(), {}) or {}).get('item_codes', {}))
    return codes


def boosters():
    """(slot, name, quantity) of every EXP booster in the inventory (slots 0-12 are the equipment)"""
    try:
        items = get_inventory().get('items', [])
    except Exception:
        return []
    key = booster_key()
    return [(i, it['name'], int(it.get('quantity') or 1)) for i, it in enumerate(items)
            if i >= 13 and it and key in (it.get('name') or '').lower()]


def booster_active():
    """an EXP booster buff is on (phBot's active skills, by name)"""
    try:
        buffs = get_active_skills() or {}
    except Exception:
        return False
    key = booster_key()
    return any(key in str(v.get('name', '') if isinstance(v, dict) else v).lower() for v in buffs.values())


def booster_tick(d, now):
    """keeps an EXP booster on: a new one whenever none is active (no booster buff, and the last one was used
    exp_booster_minutes ago), at any level. None in the inventory or not taken: looks again every BOOSTER_IDLE"""
    global booster_due, booster_used, booster_tries, booster_missing
    if now < booster_due or d.get('dead') or not group.get('exp_booster', True) or group.get('escort') or \
            now - collect_started < COLLECT_WAIT:
        return
    found = boosters()
    count = sum(q for _, _, q in found)
    if booster_used:
        if now - booster_used[0] < BOOSTER_CHECK:
            return
        before, booster_used = booster_used[1], None
        if count < before:
            every = max(float(group.get('exp_booster_minutes', 60)), 1.0)
            booster_due, booster_tries = now + every * 60, 0
            _log('EXP booster used (%d left), the next one in %d min' % (count, every))
            return
        booster_tries += 1
        if booster_tries >= BOOSTER_TRIES:
            _log('EXP booster: the server did not take it after %d tries - again in %d min' % (
                booster_tries, BOOSTER_IDLE // 60))
            booster_due, booster_tries = now + BOOSTER_IDLE, 0
        else:
            booster_due = now + BOOSTER_RETRY
        return
    if booster_active():
        booster_due = now + BOOSTER_RETRY     # one is still on (used by hand, or before a restart)
        return
    if not found:
        if not booster_missing:
            _log('No EXP booster ("%s") in the inventory - looking again every %d min' % (booster_key(), BOOSTER_IDLE // 60))
        booster_missing, booster_due = True, now + BOOSTER_IDLE
        return
    booster_missing = False
    codes = booster_codes()
    slot, name, _ = found[0]
    code = codes.get(name) or next((c for n, c in codes.items() if booster_key() in n.lower()), None)
    if not code:
        _log('EXP booster: no item code for "%s" - use one by hand once' % name)
        booster_due = now + BOOSTER_IDLE
        return
    inject_joymax(0x704C, bytes([slot]) + bytes.fromhex(code), True)
    booster_used = (now, count)
    _log('Using %s (slot %d)' % (name, slot))


def learn_item_code(data):
    """an EXP booster used by hand: keep its item code (another booster than the known one works then too)"""
    try:
        items = get_inventory().get('items', [])
        name = (items[data[0]] or {}).get('name', '') if data[0] < len(items) else ''
    except Exception:
        return
    code = bytes(data[1:]).hex()
    if name and booster_key() in name.lower() and booster_codes().get(name) != code:
        m = _read_json(macros_path(), {}) or {}
        m.setdefault('item_codes', {})[name] = code
        _write_json(macros_path(), m)
        _log('Learned the item code of ' + name)


# ---------------------------------------------------------------- character creation
def inject_char_list():
    inject_joymax(0x7007, b'\x02', False)


def inject_check_name(name):
    p = b'\x04' + struct.pack('<H', len(name)) + name.encode('ascii')
    inject_joymax(0x7007, p, False)


def _model_of(obj):
    if not obj:
        return None
    return obj.get('model') or obj.get('id')


def inject_create(name):
    c = (my_build() or {}).get('create') or group.get('create', {})
    model = _model_of(get_monster_string(c.get('model', 'CHAR_CH_MAN_ADVENTURER')))
    items = [_model_of(get_item_string(c.get(k, ''))) for k in ('chest', 'legs', 'shoes', 'weapon')]
    if not model or not all(items):
        _set_state('error', 'character codenames not found on this server')
        _log('Create failed: codenames not found ' + json.dumps(c))
        return
    p = b'\x01' + struct.pack('<H', len(name)) + name.encode('ascii')
    p += struct.pack('<I', model) + struct.pack('<B', 0)
    for it in items:
        p += struct.pack('<I', it)
    _log('Creating character %s' % name)
    inject_joymax(0x7007, p, False)
    Timer(2.5, inject_char_list).start()


def character_listing(args):
    global creating, create_name, char_names, job_pending
    names = [str(n).lstrip('*') for n in (args or [])]
    char_names = names
    if not group or not my_char:
        return
    _log('Character list: %s' % (', '.join(names) or '(empty)'))
    if my_char.lower() in [n.lower() for n in names]:
        _set_state('selecting', '')
        wait = 3.0
        if job_pending:
            job_pending = False
            seq = (_read_json(macros_path(), {}) or {}).get('job_select') or []
            if seq:
                _log('New character: picking its job with the learned packet')
                _set_state('selecting', 'picking the job')
                send_job_packets(seq, next(n for n in names if n.lower() == my_char.lower()))
                wait = 4.0 + 1.5 * len(seq)
            else:
                _log('New character goes in without a job: none learned yet - pick the job by hand once '
                     'at the character screen (any account), it is learned for all new characters')
        Timer(wait, _select_if_needed).start()
        return
    if len(names) >= int(group.get('max_characters', 4)):
        _set_state('error', 'no free character slot on this account')
        return
    creating = True
    create_name = my_char
    _set_state('creating', my_char)
    Timer(2.0, inject_check_name, (my_char,)).start()


def _select_if_needed():
    if not joined_at:
        select_character(my_char)


def _name_bytes(name):
    return struct.pack('<H', len(name)) + name.encode('ascii')


def _hex(data):
    return ' '.join('{:02X}'.format(b) for b in data)


def record_job_packet(opcode, data):
    """the user picked a job at the character screen by hand: kept with the character's name cut out
    (several packets within 15 s = one selection), so every new character gets the same job"""
    global job_recording
    enc = next((_name_bytes(n) for n in char_names if _name_bytes(n) in data), None)
    if enc is None:
        return
    i = bytes(data).index(enc)
    item = {'opcode': '0x%04X' % opcode, 'before': _hex(data[:i]), 'after': _hex(data[i + len(enc):])}
    now = time.time()
    if not job_recording or now - job_recording[0] > 15:
        job_recording = (now, [])
    job_recording[1].append(item)
    stored = _read_json(macros_path(), {}) or {}
    stored['job_select'] = job_recording[1]
    _write_json(macros_path(), stored)
    _log('Job selection learned (0x%04X %s <name> %s) - new characters get the same job' % (
        opcode, item['before'], item['after']))


def send_job_packets(seq, name):
    def send(i=0):
        if i < len(seq):
            p = seq[i]
            data = bytearray.fromhex(p['before']) + _name_bytes(name) + bytearray.fromhex(p['after'])
            inject_joymax(int(p['opcode'], 16), data, False)
            Timer(1.5, send, (i + 1,)).start()

    Timer(2.0, send).start()


def passcode_key():
    return hashlib.sha1(my_passcode.encode('utf-8')).hexdigest()[:12] if my_passcode else 'any'


def learned_passcode_packets():
    """Registration packets learned for this account's passcode (the client encrypts the passcode,
    so a packet learned on one account works on every account with the same passcode)."""
    reg = (_read_json(macros_path(), {}) or {}).get('passcode_register')
    if isinstance(reg, list):            # v1.0 kept one list for all accounts
        return reg
    if isinstance(reg, dict):
        return reg.get(passcode_key()) or reg.get('any') or []
    return []


def save_passcode_packets(seq):
    stored = _read_json(macros_path(), {}) or {}
    reg = stored.get('passcode_register')
    reg = dict(reg) if isinstance(reg, dict) else {}
    reg.pop('any', None)                 # replaced by the packet of a known passcode
    reg[passcode_key()] = seq
    stored['passcode_register'] = reg
    _write_json(macros_path(), stored)


def send_passcode_packets(seq):
    global passcode_sent

    def send(i=0):
        if i < len(seq):
            opcode, data = parse_item(seq[i])
            inject_joymax(opcode, data, False)
            Timer(1.0, send, (i + 1,)).start()

    passcode_sent = (time.time(), seq)
    Timer(1.5, send).start()


def passcode_prompt(mode):
    """0x2116: 01 = enter passcode (phBot does it), 00 = account has no passcode yet, 02 = passcode blocked."""
    global passcode_recording, needs_passcode, passcode_retries
    needs_passcode = False
    if mode == 1:
        return
    if mode == 2:
        _set_state('error', 'passcode blocked by the server - log this account in by hand once')
        _log('Server says the passcode is blocked (0x2116 02)')
        return
    seq = learned_passcode_packets()
    if seq:
        _set_state('registering passcode', '')
        _log('New account: registering the passcode with the learned packet')
        passcode_retries = 0
        send_passcode_packets(seq)
    else:
        passcode_recording = []
        needs_passcode = True     # another bot may learn it meanwhile; event_loop retries
        _set_state('error', 'new account: show the client and set the passcode once by hand - it will be learned')
        _log('Account has no passcode yet (0x2116 00). Set it in the client once; the packet is recorded for all accounts.')


def passcode_reply(data):
    """0xA117 xx yy: xx 01 = registration, 04 = login with the passcode; yy 01 = accepted."""
    global passcode_recording, passcode_sent
    if not data or len(data) < 2:
        return
    action, ok = data[0], data[1] == 1
    if passcode_recording:
        if ok:
            save_passcode_packets(passcode_recording)
            _log('Passcode registration learned (%d packet(s)) - new accounts will do it automatically' % len(passcode_recording))
            _set_state('waiting login', '')
        passcode_recording = None
    if passcode_sent and action == 1:
        passcode_sent = None
        if ok:
            _log('Passcode registered on the new account')
            _set_state('waiting login', '')
        else:
            _set_state('error', 'server refused the passcode registration - set it by hand once')
            _log('Passcode registration refused: ' + ' '.join('{:02X}'.format(b) for b in data[:8]))
    elif action == 4 and not ok:
        _log('Server refused the passcode (0xA117 %s) - check the passcode column in accounts.csv'
             % ' '.join('{:02X}'.format(b) for b in data[:8]))


def check_passcode_retry(now):
    global passcode_retries, passcode_sent
    if passcode_sent and now - passcode_sent[0] > PASSCODE_RETRY:
        seq = passcode_sent[1]
        passcode_sent = None
        if passcode_retries < 1:
            passcode_retries += 1
            _log('No answer to the passcode registration - sending it again')
            send_passcode_packets(seq)
        else:
            _set_state('error', 'no answer to the passcode registration')


def berserk_points(data):
    """0x304E 04 <points> <source uid>: berserk gauge of my character."""
    global zerk_points
    if len(data) >= 2:
        zerk_points = data[1]
        if zerk_points >= ZERK_MAX:
            use_berserk()


def use_berserk():
    global last_zerk, zerk_uses
    if not group or not joined_at or state != 'leveling' or time.time() - last_zerk < ZERK_RETRY:
        return
    d = own_data()
    if not d or d.get('dead'):
        return
    last_zerk = time.time()
    zerk_uses += 1
    inject_joymax(0x70A7, b'\x01', False)
    if zerk_uses <= 3 or zerk_uses % 25 == 0:
        _log('Berserk used (gauge full) - %d time(s) this session' % zerk_uses)


def handle_joymax(opcode, data):
    global creating, login_packets, job_pending
    if group and opcode == 0x2116 and data:
        passcode_prompt(data[0])
    if group and opcode == 0xA117:
        passcode_reply(data)
    if group and opcode == 0x304E and data and data[0] == 4:
        berserk_points(data)
    if group and not joined_at and login_packets < 60 and opcode not in (0x2002, 0x5000, 0x9000):
        # login diagnostics: what the server answers before we reach the game
        login_packets += 1
        _log('Login packet 0x%04X: %s' % (opcode, ' '.join('{:02X}'.format(b) for b in (data or b'')[:24])))
    if opcode == 0xB007 and data and len(data) >= 2 and data[0] != 2:
        _log('Character screen reply: ' + ' '.join('{:02X}'.format(b) for b in data[:12]))
    if opcode == 0xB007 and creating and data and len(data) >= 2:
        action, success = data[0], data[1]
        if action == 4:
            if success == 1:
                inject_create(create_name)
            else:
                creating = False
                _set_state('error', 'name %s is taken' % create_name)
                _log('Name %s is taken - change the prefix in the manager' % create_name)
        elif action == 1:
            creating = False
            if success == 1:
                _log('Character %s created' % create_name)
                job_pending = True
            else:
                _set_state('error', 'character creation failed')
                _log('Character creation failed: ' + ' '.join('{:02X}'.format(b) for b in data[:16]))
    return True


# ---------------------------------------------------------------- learning (client only)
def handle_silkroad(opcode, data):
    if passcode_recording is not None and opcode == 0x6117:
        passcode_recording.append('0x{:04X} | {}'.format(opcode, ' '.join('{:02X}'.format(b) for b in data) if data else '(empty)'))
    if char_names and not joined_at and data and opcode not in (0x7001, 0x2002) and \
            not (opcode == 0x7007 and data[0] in (1, 2, 3, 4, 5)):
        # at the character screen, a packet with a character's name that is not create / list / delete /
        # check name / restore / select: the job selection made by hand
        record_job_packet(opcode, data)
    if group and opcode == 0x704C and data and len(data) > 1:
        learn_item_code(data)
    if learning_until and time.time() < learning_until and opcode not in IGNORE_OPCODES:
        hexdata = ' '.join('{:02X}'.format(b) for b in data) if data else '(empty)'
        item = '0x{:04X} | {}'.format(opcode, hexdata)
        if item not in captured_seen:
            captured_seen.add(item)
            captured_pending.append(item)
    return True


def btn_learn():
    global learning_until
    captured_seen.clear()
    learning_until = time.time() + LEARN_SECONDS
    _log('Learning for %d s - click collect in the item mall storage now' % LEARN_SECONDS)


def btn_clear_captured():
    captured_seen.clear()
    QtBind.clear(gui, lstCaptured)


def btn_add_selected():
    item = QtBind.text(gui, lstCaptured)
    if item:
        QtBind.append(gui, lstSequence, item)
        save_macros()


def btn_remove_selected():
    item = QtBind.text(gui, lstSequence)
    if item:
        QtBind.remove(gui, lstSequence, item)
        save_macros()


def btn_test_collect():
    run_collect('manual test')


# ---------------------------------------------------------------- status / commands
def write_status(d):
    if not my_char:
        return
    st = {'name': my_char, 'group': group_id, 'pid': os.getpid(), 'ts': time.time(),
          'state': state, 'message': message, 'area': current_area, 'bot_started': bot_started, 'town_wait': town_wait,
          'spot': list(current_spot) if current_spot else None,
          'collected_levels': sorted(collected_levels), 'booster_due': booster_due, 'profile': profile_name, 'zerk_uses': zerk_uses,
          'attack_skills': len(applied_skills[1]['sNormal']) if applied_skills else 0,
          'build': (my_build() or {}).get('id', '')}
    try:
        st['client_pid'] = (get_client() or {}).get('pid', 0)
    except Exception:
        st['client_pid'] = 0
    if d:
        mx = d.get('max_exp') or 0
        st.update({'level': d.get('level', 1), 'exp_pct': round(100.0 * d.get('current_exp', 0) / mx, 2) if mx else 0,
                   'hp': d.get('hp', 0), 'hp_max': d.get('hp_max', 0), 'mp': d.get('mp', 0), 'mp_max': d.get('mp_max', 0),
                   'dead': bool(d.get('dead')), 'region': d.get('region', 0), 'x': round(d.get('x', 0), 1),
                   'y': round(d.get('y', 0), 1), 'gold': d.get('gold', 0)})
        try:
            st['zone'] = get_zone_name(d.get('region', 0)) or ''
        except Exception:
            st['zone'] = ''
        try:
            st['party'] = len(get_party() or {})
        except Exception:
            st['party'] = 0
    else:
        old = _read_json(base_dir() + 'status/' + my_char + '.json', {}) or {}
        st['level'] = old.get('level', 1)    # keep last known level while logged out
    _write_json(base_dir() + 'status/' + my_char + '.json', st)


def read_command():
    """the manager's latest command; one written before this plugin started (an old Stop from the last run)
    is not for this run. Its arguments go to last_cmd_args."""
    global last_cmd_id, last_cmd_args
    c = _read_json(base_dir() + 'commands/' + my_char + '.json')
    if not c or c.get('id') == last_cmd_id:
        return None
    last_cmd_id = c.get('id')
    if isinstance(c.get('id'), (int, float)) and c['id'] < PLUGIN_STARTED:
        return None
    last_cmd_args = c.get('args') or {}
    return c.get('cmd')


# ---------------------------------------------------------------- Standard mode (-mlstandard): the manager's map
# Only these commands, no leveling: navigate = phBot's own route (as its map's Navigate, teleports included, the
# way MoJobSuit's walk does it), then the lines in 'then' (DoStorage, teleport,<from>,<to>); stop_walk.
last_cmd_args = {}
nav = {'stop': False}


def nav_walk(region, x, y, z, then):
    nav['stop'] = False
    if region is None:
        region = (get_position() or {}).get('region', 0)
    lines = None
    for attempt in range(4):
        lines = generate_script(int(region), float(x), float(y), float(z))
        if lines is None:
            _log('Map: phBot found no route to %.0f, %.0f' % (x, y))
            return
        if lines is not False:          # False = asked again within 5 s
            break
        end = time.time() + 5.5
        while time.time() < end:
            if nav['stop']:
                return
            time.sleep(0.2)
    if not lines:
        _log('Map: phBot made no route (not in the game?)')
        return
    stop_bot()                          # the bot would walk back to its training area
    stop_script()
    start_script('\n'.join(list(lines) + list(then)))
    _log('Map: walking to %.0f, %.0f%s' % (x, y, ('  then ' + ' / '.join(then)) if then else ''))


def standard_tick():
    global last_tick
    now = time.time()
    if now - last_tick < 0.5:
        return
    last_tick = now
    cmd = read_command()
    if cmd == 'navigate':
        a = last_cmd_args
        Timer(0.0, nav_walk, (a.get('region'), a['x'], a['y'], a.get('z', 0), a.get('then') or [])).start()
    elif cmd == 'stop_walk':
        nav['stop'] = True
        stop_script()
        _log('Map: walk stopped')


def in_town(d):
    try:
        zone = (get_zone_name(d.get('region', 0)) or '').lower()
    except Exception:
        zone = ''
    return any(w in zone for w in TOWN_WORDS)


def start_finish():
    global finish_started, bot_started
    finish_started = time.time()
    bot_started = False
    _set_state('finishing', 'returning to town')
    stop_tracing()
    try:
        stop_bot()
    except Exception:
        pass
    d = own_data()
    if d and d.get('dead'):
        inject_joymax(0x3053, b'\x01', False)   # resurrect in town
    else:
        Timer(2.0, use_return_scroll).start()


# ---------------------------------------------------------------- escort stages
def stage_for(level):
    stages = group.get('stages') or []
    for s in stages:
        if s['min_level'] <= level <= s['max_level']:
            return s
    return stages[-1] if stages else None


def dist(a, b):
    return math.hypot(a.get('x', 0) - b.get('x', 0), a.get('y', 0) - b.get('y', 0))


def fresh_status(name):
    st = _read_json(base_dir() + 'status/' + name + '.json') or {}
    return st if time.time() - (st.get('ts') or 0) < STATUS_FRESH and st.get('level') else None


def use_reverse(zone, now):
    """reverse scroll to a zone of the game's reverse list (zone '' = to the death point); one try a minute"""
    global last_reverse, reverse_ok
    if now - last_reverse < REVERSE_RETRY:
        return
    last_reverse = now
    try:
        ok = reverse_return(1, '') if not zone else reverse_return(3, zone)
    except Exception as e:
        ok = False
        _log('reverse_return failed: %s' % e)
    reverse_ok = bool(ok)
    _log('Reverse scroll -> %s%s' % (zone or 'death point', '' if ok else ' FAILED (name not in the game\'s reverse list, or no reverse scroll)'))


def member_trace(gmin):
    """the profile's Trace values the group's stage wants (bot trace of the escort, on top of it, buffs on)"""
    if not group.get('escort_name'):
        return None
    stage = stage_for(gmin) or {}
    return {'Name': group['escort_name'], 'Distance': 0.0, 'Game': False, 'Buffs': True, 'Party Buffs': True,
            'Attack Monsters': bool(stage.get('attack')), 'Attack Monsters All': False}


def member_scrolls(gmin):
    """the profile's Scrolls values: speed scroll while tracing until the group's lowest level is speed_level"""
    return {'Speed': gmin < int(group.get('speed_level', 52)), 'ScrollsBottingOnly': False, 'ScrollsTracingOnly': True}


def member_extra(gmin):
    tr = member_trace(gmin)
    return {'Trace': tr, 'Scrolls': member_scrolls(gmin)} if tr else None


def solo_tick(lvl, gmin, cmd):
    """below solo_level nobody can invite to a party: every member levels alone at the plan's area of its level
    (not past the last area below solo_level, so it waits there for the others)"""
    global solo_active
    stop_tracing()
    solo = int(group.get('solo_level', 5))
    sp = group.get('solo_spot')           # one spot for all of it (monsters by the town gate), else the plan
    area = dict(sp, name=sp.get('name') or 'Solo spot', radius=sp.get('radius') or group.get('radius', 40)) if sp \
        else area_for(min(lvl, solo - 1))
    if area and (area['name'] != current_area or not bot_started or cmd == 'start'):
        apply_area(area)
        if sp:                            # not a training area of the profile: always by its coordinates
            set_training_position(int(sp.get('region', 0)), float(sp['x']), float(sp['y']), float(sp.get('z', 0)))
        restart_bot()
    if not solo_active:
        _log('Group lowest level %d: leveling alone to %d first (party invites need level %d)' % (gmin, solo, solo))
    solo_active = True
    _set_state('leveling', 'lv %d, group lowest %d - alone at %s until everyone is level %d' % (
        lvl, gmin, area['name'] if area else '?', solo))


def stop_tracing():
    global tracing
    if tracing:
        try:
            stop_trace()
        except Exception:
            pass
    tracing = False


def member_escort_tick(d, lvl, gmin, now, cmd):
    """a group member: reverse to the stage, then bot-trace the escort (it waits for everyone there)"""
    global tracing, trace_started, trace_far_since, dead_since, current_area, solo_active, bot_started, current_spot, \
        death_back, last_reverse, last_return
    stage = stage_for(gmin)
    if not stage:
        _set_state('error', 'no escort stages in the group file')
        return
    escort = fresh_status(group['escort_name']) if group.get('escort_name') else None
    esc_waits = bool(escort and escort.get('town_wait'))    # the escort waits in town: everyone goes to it
    fast = lvl < int(group.get('fast_respawn_level', 0))
    if d.get('dead') and fast:
        tracing = False
        if now - dead_since > 5:          # the respawn packet again if the first one did not go through
            _log('Dead at level %d - respawn in town, then reverse to the death point' % lvl)
            inject_joymax(0x3053, b'\x01', False)
            dead_since, death_back, last_reverse = now, True, 0.0
        _set_state('leveling', 'dead - respawning in town')
        return
    if death_back:
        if in_town(d) and esc_waits:
            death_back = False            # stays with the escort in town
        elif in_town(d):
            stop_tracing()
            use_reverse('', now)
            _set_state('leveling', 'back from town to the death point')
            return
        if now - dead_since < 15:
            _set_state('leveling', 'respawning in town')
            return                        # alive, but the teleport to town has not happened yet
        death_back = False                # out of town: trace the escort again as usual
    if d.get('dead'):
        tracing = False
        dead_since = dead_since or now
        if now - dead_since > MEMBER_RES_WAIT or (esc_waits and now - dead_since > 2):
            _log('Dead for %d s and not resurrected - respawn in town' % (now - dead_since) if not esc_waits
                 else 'Dead and the escort waits in town - respawn in town')
            inject_joymax(0x3053, b'\x01', False)
            dead_since = now
        _set_state('leveling', 'dead - waiting for a resurrect')
        return
    dead_since = 0.0
    if now - collect_started < COLLECT_WAIT:
        _set_state('leveling', 'collecting the item mall storage')
        return
    if gmin < int(group.get('solo_level', 5)):
        solo_tick(lvl, gmin, cmd)
        return
    current_spot = None               # the manager keeps a bot on its spot only while it has one (solo)
    if solo_active:
        _log('Whole group level %d - party, then to %s' % (gmin, stage['name']))
        solo_active = False
        try:
            stop_bot()
        except Exception:
            pass
        bot_started = False
    current_area = stage['name']
    me = get_position() or {'x': d.get('x', 0), 'y': d.get('y', 0)}
    if esc_waits and not in_town(d):
        stop_tracing()                    # the group does not stay split: back to the escort in town
        if now - last_return > REVERSE_RETRY:
            last_return = now
            try:
                ok = use_return_scroll()
            except Exception as e:
                ok = False
                _log('use_return_scroll failed: %s' % e)
            _log('The escort %s waits in town - return scroll%s' % (group['escort_name'], '' if ok else
                                                                    ' FAILED (none left?)'))
        _set_state('leveling', 'going back to town: the escort %s waits there' % group['escort_name'])
        return
    free = bool(stage.get('free'))        # no place: trace the escort wherever the user takes it
    if escort and not escort.get('dead') and (free or esc_waits or dist(me, escort) < MEMBER_NEAR):
        far = dist(me, escort) > TRACE_LOST
        trace_far_since = (trace_far_since or now) if far else 0.0
        if cmd == 'start' or not tracing or (trace_far_since and now - trace_far_since > TRACE_RETRY):
            stop_tracing()
            try:
                tracing = bool(start_trace(group['escort_name']))
            except Exception as e:
                tracing = False
                _log('start_trace failed: %s' % e)
            trace_started, trace_far_since = now, 0.0
            _log('Trace %s %s (%s, attack %s)' % (group['escort_name'], 'started' if tracing else 'FAILED',
                                                 stage['name'], 'on' if stage.get('attack') else 'off'))
        _set_state('leveling', 'lv %d, group lowest %d - tracing %s at %s' % (lvl, gmin, group['escort_name'], stage['name']))
        return
    stop_tracing()
    if free:
        _set_state('leveling', 'lv %d - free: waiting for the escort %s' % (lvl, group['escort_name']))
    elif dist(me, stage) > STAGE_NEAR:
        use_reverse(stage['reverse'], now)
        _set_state('leveling', 'going to %s (reverse %s)' % (stage['name'], stage['reverse']))
    else:
        _set_state('leveling', 'at %s, waiting for the escort %s' % (stage['name'], group['escort_name']))


def apply_stage_point(stage):
    global current_area
    set_training_position(int(stage.get('region', 0)), float(stage['x']), float(stage['y']), float(stage.get('z', 0)))
    set_training_radius(float(group.get('radius', 40)))
    current_area = stage['name']
    QtBind.setText(gui, lblArea, 'Area: %s (%d, %d) radius %d' % (stage['name'], stage['x'], stage['y'], group.get('radius', 40)))


def escort_tick(d, now, cmd):
    """the escort: go to the stage of its group's lowest level, wait for everyone + a few seconds, hunt there"""
    global bot_started, current_area, last_reverse, town_wait
    target = _read_json(base_dir() + 'escort.json') or {}
    names = target.get('members') or []
    gmin = group_min_level(None, names) if names else 1
    stage = stage_for(gmin)
    town_wait = False
    if not stage:
        _set_state('error', 'no escort stages in the group file')
        return
    if d.get('dead'):
        if bot_started:
            stop_bot()
            bot_started = False
        if not esc.get('dead_since'):
            esc['deaths'] = [t for t in esc['deaths'] if now - t < DEATH_LOOP_SECONDS] + [now]
        esc['dead_since'] = esc.get('dead_since') or now
        if now - esc['dead_since'] > ESCORT_RES_WAIT and (not esc['respawned'] or now - esc['respawn_at'] > 5):
            _log('Escort died - respawn in town, then reverse to the death point')
            inject_joymax(0x3053, b'\x01', False)
            esc['respawned'], esc['death_pending'], esc['respawn_at'] = True, True, now
            last_reverse = 0.0
        _set_state('leveling', 'dead')
        return
    esc['dead_since'], esc['respawned'] = 0.0, False
    me = get_position() or {'x': d.get('x', 0), 'y': d.get('y', 0)}
    if esc['death_pending']:
        deaths = [t for t in esc['deaths'] if now - t < DEATH_LOOP_SECONDS]
        if in_town(d) and len(deaths) >= DEATH_LOOP_COUNT and names:
            esc.update(death_pending=False, regroup=True, gather_since=0.0)
            _log('Escort died %d times in %d minutes - waits in town for the whole group, then they go back '
                 'together' % (len(deaths), DEATH_LOOP_SECONDS // 60))
        elif in_town(d):
            use_reverse('', now)            # straight back to where it died, no town script
            _set_state('leveling', 'back from town to the death point')
            return
        elif now - esc.get('respawn_at', 0) < 15:
            _set_state('leveling', 'respawning in town')
            return                          # alive, but not teleported to town yet
        else:
            esc['death_pending'] = False
            if esc['phase'] == 'hunt':
                apply_stage_point(stage)
                restart_bot()
                _log('Back at the death point - hunting at %s again' % stage['name'])
    if esc['regroup'] and not (in_town(d) and names):
        esc['regroup'] = False              # taken out of town by hand, or the group finished
    if esc['regroup']:
        town_wait = True
        online = [n for n in names if fresh_status(n)]     # closed bots come by themselves when restarted
        here = [n for n in online if (lambda st: not st.get('dead') and dist(me, st) <= float(group.get('gather', 60)))(
            fresh_status(n))]
        if not online or len(here) < len(online):
            esc['gather_since'] = 0.0
            _set_state('leveling', 'died %d times: waiting in town for the group, %d/%d here' % (
                len(esc['deaths']), len(here), len(online)))
            return
        esc['gather_since'] = esc['gather_since'] or now
        left = float(group.get('wait', 10)) - (now - esc['gather_since'])
        if left > 0:
            _set_state('leveling', 'everyone in town - going back together in %d s' % math.ceil(left))
            return
        _log('Whole group with the escort in town - going back to %s together' % stage['name'])
        esc.update(regroup=False, deaths=[], gather_since=0.0)
        last_reverse = 0.0
        if stage.get('free'):               # the user's place: back to where it died
            esc.update(death_pending=True, respawn_at=now)
            return
        esc['phase'] = 'travel'             # reverse to the stage, wait there for everyone, then hunt
    town_wait = in_town(d) and not reverse_ok
    if town_wait and not esc.get('stuck_logged'):
        _log('In town and the reverse scroll failed (none left?) - the group comes to wait with it in town')
    esc['stuck_logged'] = town_wait
    if stage.get('free'):
        # the user takes the escort anywhere by hand: no reverse, no training point, its bot is left alone
        key = (target.get('group'), stage['name'])
        if esc['key'] != key:
            _log('Escort: group %s, lowest level %d -> %s: the escort is yours, the group traces it' % (
                target.get('group') or 'none', gmin, stage['name']))
            esc.update(key=key, phase='free', gather_since=0.0)
            if bot_started:
                stop_bot()
                bot_started = False
        current_area = stage['name']
        _set_state('leveling', 'free: group %s lowest %d traces the escort' % (target.get('group'), gmin))
        return
    # a spot or reverse changed in the manager's settings: the escort goes to the new place too
    key = (target.get('group'), stage['name'], stage.get('reverse'), stage.get('x'), stage.get('y'))
    if esc['key'] != key:
        _log('Escort: group %s, lowest level %d -> %s (%s, %s, reverse %s)' % (
            target.get('group') or 'none', gmin, stage['name'], stage.get('x'), stage.get('y'), stage.get('reverse')))
        was_free = esc['phase'] == 'free'     # the user may have started its bot by hand meanwhile
        esc.update(key=key, phase='travel', gather_since=0.0)
        if bot_started or was_free:
            stop_bot()
            bot_started = False
    current_area = stage['name']
    if esc['phase'] != 'hunt' and dist(me, stage) > ESCORT_ARRIVED:
        use_reverse(stage['reverse'], now)
        _set_state('leveling', 'going to %s (reverse %s)' % (stage['name'], stage['reverse']))
        return
    if esc['phase'] != 'hunt':
        esc['phase'] = 'gather'
        if not names:
            _set_state('leveling', 'waiting for a group at %s' % stage['name'])
            return
        here = [n for n in names if (lambda st: st and not st.get('dead') and dist(me, st) <= float(group.get('gather', 60)))(
            fresh_status(n))]
        if gmin < int(group.get('solo_level', 5)):
            esc['gather_since'] = 0.0
            _set_state('leveling', 'waiting at %s: the group levels alone to %d first (lowest %d)' % (
                stage['name'], int(group.get('solo_level', 5)), gmin))
            return
        if len(here) < len(names):
            esc['gather_since'] = 0.0
            _set_state('leveling', 'waiting for the group at %s: %d/%d here' % (stage['name'], len(here), len(names)))
            return
        esc['gather_since'] = esc['gather_since'] or now
        left = float(group.get('wait', 10)) - (now - esc['gather_since'])
        if left > 0:
            _set_state('leveling', 'everyone here - starting in %d s' % math.ceil(left))
            return
        apply_stage_point(stage)
        restart_bot()
        esc['phase'] = 'hunt'
        _log('Whole group here - hunting at %s (%d, %d) radius %s' % (stage['name'], stage['x'], stage['y'], group.get('radius', 40)))
    elif cmd == 'start' or not bot_started:
        apply_stage_point(stage)
        restart_bot()
    _set_state('leveling', 'hunting at %s, group %s lowest %d' % (stage['name'], target.get('group'), gmin))


# ---------------------------------------------------------------- phBot events
def connected():
    if group:
        _log('Connected to the game server')


def joined_game():
    global joined_at, ready, bot_started, current_area, applied_skills, profile_name, last_skill_check, zerk_points, \
        tracing, solo_active
    if group:
        _log('Joined the game')
    joined_at = time.time()
    ready = False
    bot_started = False
    tracing = False
    solo_active = False
    esc.update(key=None, phase='travel', gather_since=0.0)
    current_area = ''
    applied_skills = None
    last_skill_check = 0.0
    zerk_points = 0
    if group:
        profile_name = disk_profile_name()
        _set_state('joined', '')


def disconnected():
    global joined_at, ready, bot_started
    joined_at = 0.0
    ready = False
    bot_started = False
    if group and state not in ('done', 'error'):
        _log('Disconnected')
        _set_state('disconnected', '')


def check_group_file(now):
    """the manager rewrites the group file when spots change (manual spot, learned spots, radius ...)"""
    global last_group_check
    if now - last_group_check < GROUP_CHECK_SECONDS:
        return
    last_group_check = now
    try:
        changed = os.path.getmtime(group_file()) != group_mtime
    except OSError:
        changed = False
    if changed and load_group():
        _log('Group file changed - training spots reloaded')


def event_loop():
    global learning_until, last_tick, last_status, ready, last_level, bot_started, last_skill_check, current_area, \
        collect_started, finish_wanted, booster_due
    while captured_pending:
        QtBind.append(gui, lstCaptured, captured_pending.pop(0))
    if learning_until and time.time() >= learning_until:
        learning_until = 0
        _log('Learning finished - select the collect packet(s) and add them to the sequence')

    if standard_mode:
        standard_tick()
        return
    if not group:
        return
    now = time.time()
    if now - last_tick < TICK_SECONDS:
        return
    last_tick = now
    check_group_file(now)
    d = own_data() if joined_at else None

    if now - last_status >= STATUS_SECONDS:
        last_status = now
        try:
            write_status(d)
        except Exception as e:
            _log('status write failed: %s' % e)

    if needs_passcode and not passcode_recording and learned_passcode_packets():
        passcode_prompt(0)        # learned by another bot meanwhile
    check_passcode_retry(now)
    cmd = read_command()
    if cmd == 'quit':
        _log('Manager asked to quit')
        os._exit(0)
    if cmd == 'auto_equip':
        if d:
            auto_equip()
        else:
            _log('Auto equip: not in the game')
        cmd = None
    if d and cmd == 'start' and state == 'done' and not group.get('escort') and \
            group_min_level(int(d['level'])) < int(group.get('final_level', 101)):
        _log('Manager: start after finishing - leveling on to %d' % int(group.get('final_level', 101)))
        _set_state('leveling', '')
    if not d or now - joined_at < START_DELAY or state in ('done', 'error'):
        return

    lvl = int(d['level'])
    if group.get('escort'):
        now_group = (_read_json(base_dir() + 'escort.json') or {}).get('group')
        if cmd == 'stop':
            stop_bot()
            bot_started = False
            esc['stopped_group'] = now_group
            _set_state('stopped', 'by manager')
        elif state == 'stopped' and cmd != 'start' and now_group != esc.get('stopped_group'):
            # stopped by hand (e.g. to take it anywhere at 80+): the next group brings it back to the loop
            _log('Escort: group %s finished - back to the loop for %s' % (esc.get('stopped_group'), now_group or 'the next group'))
            _set_state('leveling', '')
            escort_tick(d, now, 'start')
        elif state != 'stopped' or cmd == 'start':
            escort_tick(d, now, cmd)
        return
    gmin = group_min_level(lvl)
    final_level = int(group.get('final_level', 101))

    if state == 'finishing':
        if in_town(d) or now - finish_started > FINISH_TOWN_TIMEOUT:
            _set_state('done', 'in town, waiting for the manager')
            write_status(d)
        return
    if cmd == 'finish':
        finish_wanted = True
    if finish_wanted or gmin >= final_level:
        if now - profile_switched_at < PROFILE_SETTLE:
            return                    # phBot is loading the final skill profile
        if not d.get('dead') and sync_skills(lvl, member_extra(gmin)):
            _log('Level %d skills saved in the profile before finishing' % lvl)
            _set_state('leveling', 'saving the final skills (%s)' % profile_name)
            return
        finish_wanted = False
        _log('Group lowest level %d reached %d - finishing' % (gmin, final_level))
        start_finish()
        return

    if not ready:
        ready = True
        if lvl not in collected_levels and (lvl == 1 or lvl in collect_levels()):
            collected_levels.add(lvl)
            collect_started = now
            run_collect('first login' if lvl == 1 else 'level %d' % lvl)
            if lvl == 1 and group.get('exp_booster', True):
                booster_due = now + COLLECT_WAIT      # the collected boosters are in the inventory by then
    elif last_level is not None and lvl > last_level:
        for L in collect_levels():
            if last_level < L <= lvl and L not in collected_levels:
                collected_levels.add(L)
                collect_started = now
                run_collect('level %d' % L)
    last_level = lvl

    if cmd == 'stop':
        stop_bot()
        stop_tracing()
        bot_started = False
        _set_state('stopped', 'by manager')
        return
    if state == 'stopped' and cmd != 'start':
        return
    if cmd == 'area':
        # the manager saw phBot stopped or on another area: set the spot again (phBot walks there from anywhere,
        # unlike a coordinate change from the manager) and start the bot; dead and not resurrected -> town
        _log('Manager: training area set again and bot started' + (' (dead: respawn in town)' if d.get('dead') else ''))
        if d.get('dead'):
            inject_joymax(0x3053, b'\x01', False)
            return
        current_area = ''

    booster_tick(d, now)
    if now - profile_switched_at < PROFILE_SETTLE:
        return                    # phBot is loading the new skill profile
    if not d.get('dead') and now - last_skill_check >= SKILL_CHECK_SECONDS:
        last_skill_check = now
        if sync_skills(lvl, member_extra(gmin)):
            _set_state('leveling', 'loading skills of ' + profile_name)
            return
    if zerk_points >= ZERK_MAX:
        use_berserk()             # gauge still full: the last try didn't go through
    if group.get('escort_name'):
        member_escort_tick(d, lvl, gmin, now, cmd)
        return

    if not d.get('dead'):
        scan_spawns(now)
    area = area_for(gmin)
    if area:
        i, spot = chosen_spot(area)
        if area['name'] != current_area or (spot['x'], spot['y']) != current_spot:
            apply_area(area, i, spot)
            restart_bot()
        elif cmd == 'start' or not bot_started:
            restart_bot()
        else:
            check_spot(area, i, spot, d)
    elif cmd == 'start' or not bot_started:
        restart_bot()
    _set_state('leveling', 'lv %d, group lowest %d' % (lvl, gmin))


# ---------------------------------------------------------------- load
_ensure_dirs()
load_macros()
parse_args()
if group_id:
    if load_group():
        old_status = _read_json(base_dir() + 'status/' + my_char + '.json', {}) or {}
        collected_levels.update(old_status.get('collected_levels', []))
        booster_due = float(old_status.get('booster_due') or 0)
        profile_name = disk_profile_name()    # plugin reloaded while phBot runs a named profile
        _set_state('waiting login', my_char)
        _log('v%s loaded for %s in group %s' % (pVersion, my_char, group_id))
    else:
        _log('Group file %sgroups/%s.json not found' % (base_dir(), group_id))
elif standard_mode:
    _log('v%s loaded for %s - Standard mode: map commands only' % (pVersion, my_char))
else:
    _log('v%s loaded (idle - no -mlgroup argument)' % pVersion)
