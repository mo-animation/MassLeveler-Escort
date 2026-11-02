"""MassLeveler manager core: accounts -> groups -> phBot launches -> monitoring -> next batch.

Every character has a build (data/builds/<id>.json, made by tools/make_builds.py): its profile template,
its 10 level-bracket skill plans and what it is created with. The group composition (settings
"compositions") says which build each slot of a group plays; accounts.csv can override it per account.
No GUI code here, so it can be tested on its own (see tools/fake_phbot.py).

Standard mode (Manager(mode='standard')): a plain phBot manager. The ticked, unfinished characters open with auto
login and start botting, with their own phBot profiles; no -mlgroup, so the plugin stays idle. Everything shown
comes from phBot's own status (shared memory). Its ticks, finished marks and Auto start list are its own files, and
the settings saved in Standard mode go to settings.json "standard".

Escort version: one strong character (the accounts.csv row with escort = yes) levels every group. It is its own
group ESCORT, launched with a client while groups run; the group traces it through the stages of escort_stages
(reverse scroll to the stage, the escort waits for everyone, then hunts at the stage's point; the plugin does it).
"""
import copy
import csv
import ctypes
import glob
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time

# MassLeveler folder: next to MassLeveler.exe when frozen (tools/build_exe.py), else the parent of manager/
ROOT = os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, 'frozen', False) else \
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHARED = 'MassLeveler'          # folder inside <phBot>/Config shared with the plugin
IN_GAME_STATES = ('joined', 'leveling', 'stopped', 'finishing', 'done', 'error')
# profile settings reset from the template on every launch (everything else phBot saved is kept)
MANAGED = [('Scrolls',), ('Berserk',), ('Auto Resurrect',), ('Auto Mastery',), ('Stat Points',),
           ('Pick',), ('Shared Pick Filter',),
           ('Town', 'AutoAcceptRes'), ('Town', 'OnlyAcceptResParty'), ('Loop', 'DontWalkAround'),
           ('AutoPotions', 'UseSpeedDrug'), ('AutoPotions', 'OnlyUseSpeedInScript'),
           ('Notification', 'Hide Skill Logs'),
           ('Skills', 'HealSelf'), ('Skills', 'HealSelfHP'), ('Skills', 'HealParty'), ('Skills', 'HealPartyHP'),
           ('Skills', 'CastInOrder'), ('Skills', 'UseLowerSkills')]

DEFAULT_SETTINGS = {
    'phbot_dir': 'E:/AI/Claude code/phBot Sabo',
    'phbot_command': ['phBot.exe'],
    'server': 'Elysian',
    'login_args': [],
    'extra_args': ['--skipupdates', '--relogdc'],
    'leader_mode': 'client',
    'member_mode': 'clientless',
    'member_client_args': [],
    'hide_client': False,
    'minimize_bots': True,
    'group_size': 8,
    'name_prefix': 'fnuk',
    'name_start': 1,
    'name_digits': 2,
    'final_level': 101,
    'party_invite_type': 3,
    'max_parallel_groups': 1,
    # Mass Leveler: the first characters keep the groups they played in before the escort led the party
    # (fnuk01-40 in groups of 8, G001-G005); the next ones are in groups of 7 + the escort
    'first_groups': {'characters': 40, 'size': 8},
    'use_direct_ip': True,
    'launch_delay_seconds': 15,
    'stale_minutes': 10,
    'login_timeout_minutes': 4,
    'disconnect_restart_seconds': 10,   # a bot that disconnected or lost its game client is reopened after this
    'training_radius': 40,      # radius of every training area (0 = the radius in the training plan)
    # spots: the leader moves the group to the next spot of an area when fewer than min_monsters different
    # monsters came into it during `window` seconds (0 = never)
    'spot_rules': {'window': 120, 'min_monsters': 6},
    'custom_spots': 'data/custom_spots.json',   # spots set from the dashboard ("Use this spot for levels ...")
    'bad_spots': 'data/bad_spots.json',         # spots marked bad: never used again
    'learned_min_monsters': 25,  # a learned spot needs this many suitable monsters seen in its radius
    'max_restarts': 5,
    'collect_levels': [],
    # a new character uses an EXP booster of its inventory after the first storage collect, then one every
    # exp_booster_minutes while any are left (0 = only the first)
    'exp_booster': True,
    'exp_booster_name': 'Exp Booster',
    'exp_booster_minutes': 60,
    'max_characters': 4,
    'passcode_digits': 8,
    'hide_leader_client': False,
    'pick_template': 'templates/pick_filter.db3',   # made by tools/make_pick.py from your Death character
    'builds_dir': 'data/builds',
    'templates_dir': 'templates',
    'plans': {},                    # escort version: no level plan, the solo spot + escort_stages instead
    # composition = the build of every slot of a group, slot 1 is the leader (repeated if the group is bigger)
    'compositions': {
        'CH nukers': ['ch_nuker'],
        'EU party': ['eu_warrior', 'eu_cleric', 'eu_bard', 'eu_wizard', 'eu_wizard', 'eu_warlock', 'eu_wizard',
                     'eu_cleric'],
    },
    'composition': 'CH nukers',
    'accounts_csv': 'accounts.csv',
    'proxies_csv': 'proxies.csv',
    'progress': 'data/progress.json',
    'skip': 'data/skip.json',       # groups / characters switched off in the dashboard
    'layout': 'data/layout.json',   # groups arranged by hand (drag and drop) + deleted characters
    # escort: the group's lowest level picks the stage; everyone reverses to `reverse`, the escort waits there
    # until the whole group is around it + escort_wait_seconds, then hunts at (region, x, y, z) with
    # training_radius while the group traces it (attack = the group attacks too)
    'escort_stages': [],
    'job_mastery': 'Job Skills',    # phBot Auto Mastery levels this one up too ('' = no)
    'job_skills': ['Job - Trader Pouch'],
    'escort_high_spots': {},       # settings dialog: {'80': {region, x, y, z, reverse, reverse_auto}, '95': ...}
                                    # for the free stages by min_level (no spot = free, the user drives the escort)
    'escort_free_levels': [80, 90],  # settings dialog: the level each free stage starts at, in order
    'escort_wait_seconds': 10,
    'escort_gather_distance': 60,   # a member this close to the escort has arrived
    'escort_solo_level': 5,         # below it nobody can invite to a party: every member levels alone (plan areas)
    'escort_solo_spot': None,       # {region, x, y, z}: where members level alone (else the plan's areas)
    'escort_speed_level': 52,      # speed scrolls while tracing until the group's lowest level is this
    'escort_fast_respawn_level': 52,    # a member below this level that dies: town at once, reverse to the death point
}
# Standard mode puts these over the settings, then settings.json "standard" (what its Settings dialog saves)
STANDARD_DEFAULTS = {
    'group_size': 8,
    'progress': 'data/standard_progress.json',
    'skip': 'data/standard_skip.json',
    'auto_start': 'data/standard_auto.json',     # characters opened again whenever their bot closes by itself
    'layout': 'data/standard_layout.json',
}
ESCORT_GID = 'ESCORT'


SPOT_SAME = 50.0            # spots closer than this are the same spot
MAX_SPOTS = 6               # spots per area
SPOT_REFRESH_SECONDS = 600  # rewrite the running groups' files with what the bots learned meanwhile
KEEP_OFF = 8                # seconds phBot may be on another area before the plugin sets the spot again
KEEP_STOPPED = 20           # ... stopped while the plugin is leveling
KEEP_DEAD = 90              # ... dead without being resurrected (then: respawn in town, walk back)
KEEP_RETRY = 30             # at most one 'area' command per bot this often
LEARNED_PER_AREA = 2


def near(a, b, dist):
    return math.hypot(a['x'] - b['x'], a['y'] - b['y']) < dist


def stage_for(stages, level):
    for st in stages or []:
        if st['min_level'] <= level <= st['max_level']:
            return st
    return (stages or [None])[-1]


def high_spots(s):
    """settings dialog spots of the free stages, by the stage's min_level ('80', '95'); the old single
    escort_high_spot is the 80 one"""
    spots = dict(s.get('escort_high_spots') or {})
    if s.get('escort_high_spot') and '80' not in spots:
        spots['80'] = s['escort_high_spot']
    return spots


def with_free_levels(stages, levels):
    """the free stages (80+, 95+ in settings.json) start at the levels typed in the settings dialog, in order;
    the stage before each one ends a level earlier"""
    out = [dict(st) for st in stages or []]
    free = [i for i, st in enumerate(out) if st.get('free')]
    for i, lv in zip(free, levels or []):
        try:
            lv = int(lv)
        except (TypeError, ValueError):
            continue
        out[i]['min_level'] = lv
        if i:
            out[i - 1]['max_level'] = lv - 1
    return out


def with_high_spots(stages, spots):
    """each free stage (the user drives the escort) whose min_level has a spot set in the settings dialog
    becomes that spot; user_spot marks the stages the dialog offers a spot for"""
    out = [dict(st) for st in stages or []]
    for st in out:
        if not st.get('free'):
            continue
        st['user_spot'] = True
        sp = (spots or {}).get(str(st['min_level']))
        if sp and sp.get('reverse'):
            del st['free']
            st.update(name=sp.get('name') or 'Level %d+ spot' % st['min_level'], reverse=sp['reverse'],
                      region=int(sp.get('region', 0)), x=float(sp['x']), y=float(sp['y']),
                      z=float(sp.get('z', 0)), attack=True)
    return out


def region_xy(region):
    return ((region & 255) - 135) * 192 + 96, ((region >> 8) - 92) * 192 + 96


def nearest_reverse(phbot_dir, spot):
    """(name, distance) of the game's reverse point closest to spot, from phBot's game database (Data/*.db3).
    Reverse points have no coordinates there, but the zones table does: a zone whose name has every word of the
    reverse point's name ('Heart Peak' -> 'Peak of Heart') is that place. None = no database / nothing found."""
    words = lambda t: {w for w in re.findall(r'[a-z0-9]+', t.lower()) if w not in ('of', 'the', 're', 'lv')}
    best = None
    for path in glob.glob(os.path.join(phbot_dir, 'Data', '*.db3')):
        if os.path.basename(path).lower() == 'login.db3':
            continue
        try:
            con = sqlite3.connect('file:%s?mode=ro' % path, uri=True)
            try:
                rev = [n for (n,) in con.execute('select name from reverseteleport')]
                zones = [(r, words(n or '')) for r, n in con.execute('select id, name from zones where id > 0 and id < 32768')]
            finally:
                con.close()
        except sqlite3.Error:
            continue
        region = int(spot.get('region', 0))
        if region and not any(r == region for r, _ in zones):
            continue                    # another server's database
        for name in rev:
            w = words(name)
            for r, zw in zones:
                if w and w <= zw:
                    x, y = region_xy(r)
                    d = math.hypot(x - float(spot['x']), y - float(spot['y']))
                    if best is None or d < best[1]:
                        best = (name, d)
    return best


_reverse_names = {}


def reverse_names(phbot_dir):
    """every reverse point name in phBot's game databases (Data/*.db3), as the game spells them"""
    if phbot_dir not in _reverse_names:
        names = set()
        for path in glob.glob(os.path.join(phbot_dir, 'Data', '*.db3')):
            if os.path.basename(path).lower() == 'login.db3':
                continue
            try:
                con = sqlite3.connect('file:%s?mode=ro' % path, uri=True)
                try:
                    names.update(n for (n,) in con.execute('select name from reverseteleport') if n)
                finally:
                    con.close()
            except sqlite3.Error:
                continue
        _reverse_names[phbot_dir] = sorted(names)
    return _reverse_names[phbot_dir]


def exact_reverse(phbot_dir, typed):
    """the game's spelling of a reverse point name typed by hand: phBot finds it only spelled exactly
    ('echo valley' -> 'Echo valley'; 'echo' -> 'Echo valley' if no other name has that word).
    typed as is when there is no game database, None when the game has no such reverse point"""
    names = reverse_names(phbot_dir)
    if not names:
        return typed
    key = ' '.join(typed.lower().split())
    for n in names:
        if ' '.join(n.lower().split()) == key:
            return n
    words = set(re.findall(r'[a-z0-9]+', key))
    found = [n for n in names if words and words <= set(re.findall(r'[a-z0-9]+', n.lower()))]
    return found[0] if len(found) == 1 else None


_monster_levels = {}


def monster_levels(phbot_dir):
    """{servername and name: level} from phBot's game database (Data/<id>.db3, the biggest one)"""
    if phbot_dir not in _monster_levels:
        out = {}
        dbs = sorted(glob.glob(os.path.join(phbot_dir, 'Data', '*.db3')), key=os.path.getsize, reverse=True)
        if dbs:
            import sqlite3
            db = sqlite3.connect('file:%s?mode=ro' % dbs[0], uri=True)
            for servername, name, level in db.execute('select servername, name, level from monsters'):
                out[servername] = level
                out.setdefault(name, level)
            db.close()
        _monster_levels[phbot_dir] = out
    return _monster_levels[phbot_dir]


def path_in_root(p):
    return p if os.path.isabs(p) else os.path.join(ROOT, p)


def read_json(path, default=None):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    for attempt in range(10):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.05)    # a bot is reading the file right now (Windows)
    os.replace(tmp, path)


def apply_managed(prof, template):
    for path in MANAGED:
        src, dst = template, prof
        for k in path[:-1]:
            src = src.get(k) or {}
            dst = dst.setdefault(k, {})
        if path[-1] in src:
            dst[path[-1]] = copy.deepcopy(src[path[-1]])


# ---------------------------------------------------------------- pick filter (Config/<Server>_<char>.db3)
PICK_COLS = ('pick', 'sell', 'store', 'storeguild', 'takestorage', 'takeguildstorage', 'drop_item',
             'take_quantity', 'pet', 'quantity', 'sound')


def copy_pick_filter(src_db, dst_db):
    """item filter of src (what to pick / pick with pet / drop / sell / store) -> dst; returns rows changed"""
    import sqlite3
    db = sqlite3.connect(dst_db, timeout=10)
    try:
        db.execute('ATTACH DATABASE ? AS src', (src_db,))
        cols = [c[1] for c in db.execute('PRAGMA src.table_info(pickfilter)')]
        have = [c for c in PICK_COLS if c in cols]
        sets = ', '.join('"%s" = (SELECT s."%s" FROM src.pickfilter s WHERE s.id = pickfilter.id)' % (c, c) for c in have)
        n = db.execute('UPDATE pickfilter SET %s WHERE id IN (SELECT id FROM src.pickfilter)' % sets).rowcount
        allc = ', '.join('"%s"' % c for c in cols)
        n += db.execute('INSERT INTO pickfilter (%s) SELECT %s FROM src.pickfilter WHERE id NOT IN '
                        '(SELECT id FROM pickfilter)' % (allc, allc)).rowcount
        db.commit()
        return n
    finally:
        db.close()


def ensure_pick_filter(config_dir, server, char, template_db):
    """a new character gets the template database (phBot keeps it), an existing one gets its filter copied"""
    if not template_db or not os.path.isfile(template_db):
        return None
    dst = os.path.join(config_dir, '%s_%s.db3' % (server, char))
    if not os.path.isfile(dst):
        shutil.copyfile(template_db, dst)
        return 'created'
    copy_pick_filter(template_db, dst)
    return 'updated'


def finished_share_path(s):
    """OneDrive file that tells Mass Runner (on the other PC) which characters finished leveling; '' = no OneDrive"""
    if s.get('finished_share'):
        return s['finished_share']
    base = os.environ.get('OneDrive') or os.path.join(os.path.expanduser('~'), 'OneDrive')
    return os.path.join(base, 'MassLeveler', 'finished.json') if os.path.isdir(base) else ''


def load_settings(path=None, mode='mass'):
    path = path or path_in_root('settings.json')
    s = copy.deepcopy(DEFAULT_SETTINGS)
    raw = read_json(path, {}) or {}
    s.update(raw)
    if mode == 'standard':
        s.update(STANDARD_DEFAULTS)
        s.update(raw.get('standard') or {})
    # the folder is copied between PCs with phBot in different places: the first phBot folder that exists
    if not os.path.isdir(s['phbot_dir']):
        s['phbot_dir'] = next((p for p in s.get('phbot_dirs') or [] if os.path.isdir(p)), s['phbot_dir'])
    s['escort_stages'] = with_high_spots(with_free_levels(s['escort_stages'], s.get('escort_free_levels')),
                                         high_spots(s))
    for st in s['escort_stages']:       # a name typed in another case is spelled the game's way
        if st.get('reverse'):
            st['reverse'] = exact_reverse(s['phbot_dir'], st['reverse']) or st['reverse']
    return s


# ---------------------------------------------------------------- accounts / proxies / groups
class Member:
    def __init__(self, username, password, passcode, character, notes='', build=''):
        self.username = username
        self.password = password
        self.passcode = passcode
        self.character = character
        self.notes = notes
        self.build = build          # '' = taken from the composition when the group is formed
        self.role = 'member'
        self.skip = False           # left out of its group (unticked in the dashboard)
        self.proc = None
        self.restarts = 0
        self.launched_at = 0.0
        self.out_since = 0.0        # when the character was last seen leaving the game / not in it
        self.lost_since = 0.0       # when it was first seen disconnected / without its game client
        self.held = False           # closed with Terminate: not reopened until Restart character / group
        self.no_client = False      # Go Clientless: its client was closed on purpose
        self.spot_fixed_at = 0.0    # last time its training area was put back on the group's spot
        self.spot_off_since = 0.0   # since when phBot's training area differs from that spot
        self.queued = False         # Standard: opens when its turn comes (one bot after another)
        self.reopen_at = 0.0        # Standard: Auto start opens it again at this time
        self.start_sent = False     # Standard: its bot was started once in the game since it opened
        self.status = {}


class Group:
    def __init__(self, gid, members):
        self.gid = gid
        self.all_members = members    # every account of the group (shown in the dashboard)
        self.members = []             # the ones that play: not skipped; the first is the leader
        self.state = 'pending'        # pending, launching, running, done, failed, stopped, skipped
        self.escort = gid == ESCORT_GID   # the escort's own "group" (one character, never done)
        self.slot = None
        self.next_launch = 0
        self.done_at = 0.0
        self.message = ''
        self.use_members()

    def use_members(self):
        self.members = [m for m in self.all_members if not m.skip]
        for m in self.all_members:
            m.role = 'skipped' if m.skip else 'member'
        if self.members:
            self.members[0].role = 'leader'

    @property
    def leader(self):
        return self.members[0]

    def min_level(self):
        return min((int(m.status.get('level', 1) or 1) for m in self.members), default=1)


def read_csv_rows(path):
    if not os.path.isfile(path):
        return []
    with open(path, newline='', encoding='utf-8-sig') as f:
        rows = []
        for r in csv.DictReader(f):
            r = {(k or '').strip().lower(): (v or '').strip() for k, v in r.items()}
            if r.get('username') and not r.get('username', '').startswith('#'):
                rows.append(r)
        return rows


def composition(s):
    comp = (s.get('compositions') or {}).get(s.get('composition')) or []
    return comp or ['ch_nuker']


def party_buffs(g, builds):
    """{char: {phBot Party Buffs category: [names]}} from each build's policy:
    'party' = every other member, 'ring' = the next member of the same build (the other builds are shared out
    between them), 'pair' = same build in twos buffing each other, [build ids] = the members with those builds"""
    out = {}
    for m in g.members:
        res = {}
        for cat, pol in ((builds.get(m.build) or {}).get('party_buffs') or {}).items():
            if pol == 'party':
                res[cat] = [x.character for x in g.members if x is not m]
            elif pol == 'ring':
                same = [x for x in g.members if x.build == m.build]
                rest = [x for x in g.members if x.build != m.build]
                i = same.index(m)
                res[cat] = ([same[(i + 1) % len(same)].character] if len(same) > 1 else []) + \
                    [x.character for j, x in enumerate(rest) if j % len(same) == i]
            elif pol == 'pair':
                same = [x for x in g.members if x.build == m.build]
                i = same.index(m)
                mates = set()
                if len(same) > 1:
                    j = i + 1 if i % 2 == 0 else i - 1
                    mates.add(same[j] if j < len(same) else same[0])        # odd last one -> the first
                    if len(same) % 2 and i == 0:
                        mates.add(same[-1])                                 # the first also buffs the odd last one
                res[cat] = [x.character for x in same if x in mates and x is not m]
            elif isinstance(pol, list):
                res[cat] = [x.character for x in g.members if x is not m and x.build in pol]
        out[m.character] = res
    return out


def bracket_for(build, level):
    for b in build.get('brackets') or []:
        if b['min'] <= level <= b['max']:
            return b
    return (build.get('brackets') or [None])[-1]


def set_skill_lists(prof, bracket, pbuffs):
    sk = prof.setdefault('Skills', {})
    for key in ('sNormal', 'bNormal', 'bScript', 'Healing', 'Resurrect'):
        sk[key] = list(bracket.get(key, []))
    sk['Imbue'] = bracket.get('Imbue', '')
    sk['Party Buffs'] = dict({k: [] for k in sk.get('Party Buffs', {})}, **pbuffs)


# ---------------------------------------------------------------- show / hide windows (Windows)
QT_HELPER_WINDOWS = ('QTrayIconMessageWindow',)    # hidden Qt helper windows: never shown


def _windows_of(pid, helpers=False):
    """titled top-level windows of a process (helpers=True: only Qt's hidden helper windows)"""
    if sys.platform != 'win32' or not pid:
        return []
    user32 = ctypes.windll.user32
    found = []
    proto = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(hwnd, _):
        owner = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        n = user32.GetWindowTextLengthW(hwnd)
        if owner.value == pid and n > 0 and not user32.GetWindow(hwnd, 4):     # no owner (GW_OWNER = 4)
            title = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, title, n + 1)
            if (title.value in QT_HELPER_WINDOWS) == helpers:
                found.append(hwnd)
        return True

    user32.EnumWindows(proto(cb), 0)
    return found


def _phbot_shm(pid, size, work, write=False):
    """map phBot's status segment "phBot_<pid>" (what phBot's own Manager reads and writes) and call
    work(address). It is a QSharedMemory: its Windows name is the prefix + the key's letters + sha1 of the key."""
    if sys.platform != 'win32' or not pid:
        return None
    import hashlib
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    k32.OpenFileMappingW.restype = wintypes.HANDLE
    k32.OpenFileMappingW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    k32.MapViewOfFile.restype = ctypes.c_void_p
    k32.MapViewOfFile.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]
    k32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    key = 'phBot_%d' % pid
    name = 'qipc_sharedmemory_' + ''.join(c for c in key if c.isascii() and c.isalpha()) + \
        hashlib.sha1(key.encode()).hexdigest()
    access = 0x0002 if write else 0x0004                   # FILE_MAP_WRITE / FILE_MAP_READ
    h = k32.OpenFileMappingW(access, False, name)
    if not h:
        return None
    try:
        view = k32.MapViewOfFile(h, access, 0, 0, size)
        if not view:
            return None
        try:
            return work(view)
        finally:
            k32.UnmapViewOfFile(view)
    finally:
        k32.CloseHandle(h)


def phbot_shm_write(pid, fields):
    """write (offset, bytes) pairs in order; the trigger flag goes last"""
    def work(view):
        for off, data in fields:
            ctypes.memmove(view + off, data, len(data))
        return True
    return bool(_phbot_shm(pid, max(off + len(data) for off, data in fields), work, write=True))


def phbot_status(pid):
    """the live numbers phBot shows its Manager (docs/PROTOCOL.md), or None when the bot is not reachable"""
    import struct
    b = _phbot_shm(pid, 0x790, lambda view: ctypes.string_at(view, 0x790))
    if not b:
        return None
    u8 = lambda o: b[o]
    u32 = lambda o: struct.unpack_from('<I', b, o)[0]
    u64 = lambda o: struct.unpack_from('<Q', b, o)[0]
    f32 = lambda o: struct.unpack_from('<f', b, o)[0]
    f64 = lambda o: struct.unpack_from('<d', b, o)[0]
    text = lambda o, n: b[o:o + n].split(b'\0', 1)[0].decode('utf-8', 'replace')
    return {'name': text(0x037, 64), 'connected': bool(u8(0x02d)), 'in_game': bool(u8(0x02d) and u8(0x02f)),
            'botting': bool(u8(0x035)), 'tracing': bool(u8(0x036)), 'dead': bool(u8(0x153)),
            'level': u8(0x137), 'exp': u64(0x15c), 'level_exp': u64(0x164), 'sp': u32(0x158),
            'gold': u64(0x17c), 'drops': u32(0x184), 'rare_drops': u32(0x5e7), 'botting_time': u32(0x2a2),
            'time_to_level': f64(0x2a6), 'exp_hour': f64(0x2b6), 'sp_hour': f64(0x2c6),
            'kills': u32(0x2ce), 'deaths': u32(0x2da), 'gold_per_loop': u64(0x2d2), 'zone': text(0x4ac, 32),
            'client_pid': u32(0x020), 'client_hwnd': u32(0x024),
            'region': (u8(0x13a) << 8) | u8(0x139), 'x': f32(0x13b), 'y': f32(0x13f), 'z': f32(0x781),
            'training': (struct.unpack_from('<h', b, 0x773)[0], f32(0x775), f32(0x779), f32(0x77d))}


DEAD_CHOICE = {'respawn': 1, 'return_to_town': 2, 'resurrect_scroll': 3}
ARENA_MODE = {'ctf': 0x40, 'points': 0x20}
ARENA_KIND = {'job': 0x10, 'random': 0x02, 'party': 0x04, 'guild': 0x08}


def _shm_text(text, size=64):
    raw = text.encode('utf-8')[:size - 1]
    return raw + b'\0' * (size - len(raw))


def phbot_fields(cmd, **kw):
    """the shared-memory writes of one phBot Manager command (docs/PROTOCOL.md); the trigger flag is last"""
    import struct
    B1, B0 = b'\x01', b'\x00'
    f32 = lambda v: struct.pack('<f', float(v))
    if cmd == 'start_bot':
        dead = [(0x5de, bytes([DEAD_CHOICE[kw['dead_choice']]]))] if kw.get('dead_choice') else []
        return dead + [(0x191, B0), (0x190, B1)]
    table = {
        'stop_bot': [(0x190, B0), (0x191, B1)],
        'stop_trace': [(0x429, B0), (0x42a, B1)],
        'start_trade': [(0x700, B0), (0x6ff, B1)],
        'stop_trade': [(0x6ff, B0), (0x700, B1)],
        'show_bot': [(0x4cd, B1)], 'hide_bot': [(0x4cd, b'\x02')],
        'get_position': [(0x4d6, B1)],
        'return_scroll': [(0x192, B1)],
        'styria': [(0x5e4, B1)],
        'quit': [(0x5e6, B1)],
    }
    if cmd in table:
        return table[cmd]
    if cmd == 'start_trace':
        return [(0x42b, _shm_text(kw['player'])), (0x42a, B0), (0x429, B1)]
    if cmd == 'go_client':          # reconnect False = Return/Teleport, True = Reconnect
        return [(0x46b, B1 if kw.get('reconnect') else B0), (0x197, B1)]
    if cmd == 'set_training_area':
        region = int(kw.get('region', 0))
        return [(0x706, struct.pack('<h', region)), (0x2df, f32(kw['x'])), (0x2e3, f32(kw['y'])),
                (0x708, f32(kw.get('z', 0) if region else 0)), (0x2de, B1)]
    if cmd == 'set_training_radius':
        return [(0x5df, f32(kw['radius'])), (0x5e3, B1)]
    if cmd == 'set_pick_radius':
        return [(0x6f9, f32(kw['radius'])), (0x6fd, B1)]
    if cmd == 'change_script':
        path = kw['path'].encode('utf-8')
        if not 0 < len(path) < 0x105:
            raise ValueError('script path too long')
        return [(0x19d, path + b'\0' * (0x105 - len(path))), (0x19c, B1)]
    if cmd == 'change_profile':
        return [(0x639, _shm_text(kw.get('profile', ''))), (0x638, B1)]
    if cmd == 'reverse_return':
        return [(0x5ec, _shm_text(kw['target'])), (0x5eb, B1)]
    if cmd == 'walk_to':
        return [(0x731, _shm_text(kw['coords'])), (0x730, B1)]
    if cmd == 'arena':
        code = 0x80 if kw['kind'] == 'capture_the_flag' else ARENA_MODE[kw['mode']] | ARENA_KIND[kw['kind']]
        return [(0x72c, struct.pack('<I', code))]
    raise ValueError('unknown command %s' % cmd)


def show_bot(pid, show):
    """phBot's own Show / Hide command (status byte 0x4cd = 1 / 2): phBot restores itself from the tray or
    minimize and draws normally. Showing its window from outside leaves it blank."""
    for hwnd in _windows_of(pid, helpers=True):            # put back helper windows shown by older versions
        ctypes.windll.user32.ShowWindow(hwnd, 0)
    if phbot_shm_write(pid, [(0x4cd, b'\x01' if show else b'\x02')]):
        return 1
    return show_windows(pid, show)


def show_windows(pid, show):
    """show (restore) or hide every main window of a process; returns how many windows were changed"""
    user32 = ctypes.windll.user32 if sys.platform == 'win32' else None
    wins = _windows_of(pid)
    for hwnd in wins:
        user32.ShowWindow(hwnd, 9 if show else 0)       # SW_RESTORE / SW_HIDE
        if show:
            user32.SetForegroundWindow(hwnd)
    return len(wins)


def hide_status_widgets():
    """the Elysian client opens a small LV / HP / MP / EXP window (class SRO_StatusWidget) of its own beside
    the game; hide every visible one. Returns how many were hidden."""
    if sys.platform != 'win32':
        return 0
    user32 = ctypes.windll.user32
    found = []
    proto = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(hwnd, _):
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        if cls.value == 'SRO_StatusWidget' and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(proto(cb), 0)
    for hwnd in found:
        user32.ShowWindow(hwnd, 0)                      # SW_HIDE
    return len(found)


PARTY_MAX = 8       # the game's party limit


def group_size(s, escort_party):
    """Mass Leveler with an escort: the escort is the party leader, so a group is one less than a party"""
    size = max(1, int(s['group_size']))
    return min(size, PARTY_MAX - 1) if escort_party else size


def next_group_number(groups):
    return max([int(g.gid[1:]) for g in groups if re.match(r'G\d+$', g.gid)] + [0]) + 1


def load_groups(s, progress, skip=None, escort_party=False, layout=None):
    """skip = {'groups': [gid], 'characters': [name]} switched off in the dashboard. layout = the groups as
    arranged by hand (drag and drop) and the deleted characters: they stay so; new accounts get new groups."""
    skip = skip or {}
    layout = layout or {}
    rows = read_csv_rows(path_in_root(s['accounts_csv']))
    n = s['name_start']
    explicit, ordered, escort = {}, [], []
    for r in rows:
        ch = r.get('character') or '%s%0*d' % (s['name_prefix'], s['name_digits'], n)
        n += 1
        passcode = r.get('passcode', '')
        if passcode.isdigit() and s['passcode_digits']:
            passcode = passcode.zfill(s['passcode_digits'])     # Excel drops leading zeros (01234567 -> 1234567)
        m = Member(r['username'], r.get('password', ''), passcode, ch, r.get('notes', ''), r.get('build', ''))
        m.skip = ch in (skip.get('characters') or [])
        if r.get('escort', '').lower() in ('yes', 'y', '1', 'true', 'x'):
            escort = escort or [m]                              # only the first escort row counts
        elif r.get('group'):
            explicit.setdefault(r['group'], []).append(m)
        else:
            ordered.append(m)
    groups = [Group(ESCORT_GID, escort)] if escort else []
    by_name = {m.character: m for m in ordered + [x for ms in explicit.values() for x in ms]}
    placed = set(layout.get('deleted') or [])
    for gid, names in layout.get('groups') or []:
        ms = [by_name[n] for n in names if n in by_name and n not in placed]
        placed.update(m.character for m in ms)
        if ms:
            groups.append(Group(gid, ms))
    for gid, members in explicit.items():
        members = [m for m in members if m.character not in placed]
        if members:
            groups.append(Group('G-' + gid, members))
    ordered = [m for m in ordered if m.character not in placed]
    size = group_size(s, escort_party and bool(escort))
    # groups that played before the escort led the party keep their size (first_groups); the rest come after
    first = (s.get('first_groups') or {}) if escort_party and escort and not layout.get('groups') else {}
    keep = min(int(first.get('characters', 0)), len(ordered))
    chunks = [ordered[i:i + int(first.get('size', size))] for i in range(0, keep, int(first.get('size', size)))]
    chunks += [ordered[i:i + size] for i in range(keep, len(ordered), size)]
    start = next_group_number(groups)
    for i, chunk in enumerate(chunks):
        groups.append(Group('G%03d' % (start + i), chunk))
    comp = composition(s)
    for g in groups:
        for i, m in enumerate(g.all_members):        # a character keeps its slot's build when others are skipped
            m.build = m.build or comp[i % len(comp)]
        group_state(g, progress, skip)
    return groups


def group_state(g, progress, skip):
    """state of a group that is not running: skipped, done or pending"""
    if g.gid in (skip.get('groups') or []) or not g.members:
        g.state = 'skipped'
        g.message = '' if g.members else 'every character is skipped'
    elif all(progress.get(m.character) in ('done', 'finished') for m in g.members):
        g.state = 'done'
        g.message = 'finished by hand' if any(progress.get(m.character) == 'finished' for m in g.members) \
            else 'finished earlier'
    else:
        g.state = 'pending'
        g.message = ''


def load_proxies(s):
    slots = []
    if s['use_direct_ip']:
        slots.append(None)
    slots.extend(_proxy_rows(s))
    return slots


def _proxy_rows(s):
    path = path_in_root(s['proxies_csv'])
    if not os.path.isfile(path):
        return []
    with open(path, newline='', encoding='utf-8-sig') as f:
        out = []
        for r in csv.DictReader(f):
            r = {(k or '').strip().lower(): (v or '').strip() for k, v in r.items()}
            if r.get('ip') and not r['ip'].startswith('#') and r.get('port'):
                out.append(r)
        return out


# ---------------------------------------------------------------- phBot files
class PhBotFiles:
    def __init__(self, s):
        self.s = s
        self.config_dir = os.path.join(s['phbot_dir'], 'Config')
        self.shared = os.path.join(self.config_dir, SHARED)

    def status(self, char):
        """{} when there is none. A read fails now and then while the bot replaces the file (Windows): retried."""
        path = os.path.join(self.shared, 'status', char + '.json')
        for attempt in range(3):
            st = read_json(path)
            if st or not os.path.exists(path):
                return st or {}
            time.sleep(0.05)
        return {}

    def reset_status(self, char):
        """Forget the old run's state but keep the last known level (the plugin uses it for the group minimum)."""
        st = self.status(char)
        write_json(os.path.join(self.shared, 'status', char + '.json'),
                   {'name': char, 'level': st.get('level', 1), 'state': 'queued', 'pid': None, 'ts': 0,
                    'collected_levels': st.get('collected_levels', [])})

    def command(self, char, cmd, args=None):
        write_json(os.path.join(self.shared, 'commands', char + '.json'), {'id': time.time(), 'cmd': cmd,
                                                                           'args': args or {}})

    def write_group(self, g, plan, builds):
        s = self.s
        if g.escort:
            write_json(os.path.join(self.shared, 'groups', g.gid + '.json'), {
                'group': g.gid, 'escort': True, 'slot': g.slot, 'server': s['server'],
                'members': [m.character for m in g.members], 'leader': g.leader.character,
                'stages': s['escort_stages'], 'radius': s.get('training_radius') or 40,
                'wait': s['escort_wait_seconds'], 'gather': s['escort_gather_distance'],
                'final_level': s['final_level'], 'max_characters': s['max_characters']})
            return
        used = {m.build for m in g.members}
        data = {
            'group': g.gid,
            'slot': g.slot,             # connection slot, to take the group over again after a dashboard restart
            'spot_rules': s.get('spot_rules') or {},
            'server': s['server'],
            'members': [m.character for m in g.members],
            'leader': g.leader.character,
            'final_level': s['final_level'],
            'areas': plan,
            'build_of': {m.character: m.build for m in g.members},
            'builds': {b: builds[b] for b in used if b in builds},
            'party_buffs': party_buffs(g, builds),
            'collect': {'levels': s['collect_levels']},
            'exp_booster': bool(s.get('exp_booster')),
            'exp_booster_name': s.get('exp_booster_name') or 'Exp Booster',
            'exp_booster_minutes': s.get('exp_booster_minutes', 60),
            'max_characters': s['max_characters'],
            'escort_name': s.get('escort_name', ''),    # the group traces this character through the stages
            'stages': s['escort_stages'],
            'solo_level': s['escort_solo_level'],
            'solo_spot': s['escort_solo_spot'],
            'radius': s.get('training_radius') or 40,
            'speed_level': s['escort_speed_level'],
            'fast_respawn_level': s['escort_fast_respawn_level'],
        }
        write_json(os.path.join(self.shared, 'groups', g.gid + '.json'), data)

    def escort_target(self, g):
        """tell the escort which group it levels now (None = no group: it waits at the first stage)"""
        data = {'group': g.gid, 'members': [m.character for m in g.members], 'leader': g.leader.character} if g \
            else {'group': None, 'members': [], 'leader': ''}
        path = os.path.join(self.shared, 'escort.json')
        old = read_json(path, {}) or {}
        if {k: old.get(k) for k in data} != data:
            write_json(path, dict(data, ts=time.time()))
            return True
        return False

    def profile_path(self, char, name='Default'):
        base = os.path.join(self.config_dir, '%s_%s' % (self.s['server'], char))
        return base + ('.json' if name == 'Default' else '.%s.json' % name)

    def write_profile(self, g, m, template, plan, build=None, pbuffs=None):
        """Default profile <Server>_<char>.json + one profile per 10-level bracket (Lv001-010 ... Lv091-100).
        Keeps what phBot already saved, resets the MANAGED settings from the build's template, sets party,
        training areas and the bracket's skill lists. phBot starts on Default (lists of the character's
        current bracket); the plugin then loads the bracket profile of its level."""
        path = self.profile_path(m.character)
        prof = read_json(path)
        if prof and len(prof) == 1 and prof.get('Profile'):     # pointer to the named profile phBot used last
            prof = read_json(os.path.join(self.config_dir, '%s_%s.%s.json' % (
                self.s['server'], m.character, prof['Profile'])))
        if not prof or len(prof) < 5:
            prof = copy.deepcopy(template)
        else:
            apply_managed(prof, template)
        prof.pop('Profile', None)
        others = [x.character for x in g.members if x is not m]
        party = prof.setdefault('Party', {})
        party['PartyInviteType'] = self.s['party_invite_type']
        party['AutoFormParty'] = False
        party['Accept All Invites'] = False
        party['Invite All Players'] = False
        escort = self.s.get('escort_name') if len(g.members) < PARTY_MAX else ''   # 8 + escort: own party
        if escort:      # the escort is the party leader: it invites, the group accepts only from it
            party.update({'AutoInviteParty': False, 'Invite From List': False,
                          'AutoAcceptParty': True, 'Accept Invites From List': True,
                          'InviteList': [escort], 'Party Leader Names': [escort], 'LeaveIfLeaderNotInList': True})
        elif m.role == 'leader':
            party.update({'AutoInviteParty': True, 'Invite From List': True, 'InviteList': others,
                          'AutoAcceptParty': False, 'Accept Invites From List': False,
                          'Party Leader Names': [], 'LeaveIfLeaderNotInList': False})
        else:
            party.update({'AutoInviteParty': False, 'Invite From List': False,
                          'AutoAcceptParty': True, 'Accept Invites From List': True,
                          'InviteList': [g.leader.character], 'Party Leader Names': [g.leader.character],
                          'LeaveIfLeaderNotInList': True})
        # no union party: the escort leads the one party
        prof['Union Party'] = dict(prof.get('Union Party') or {}, AcceptUnionPartyInvites=False,
                                   AutoUnionPartyInvite=False, UnionPartyAccept=[], UnionPartyInvite=[])
        # bot trace (not the game's) of the escort, right on top of it, with buffs and party buffs; whether it
        # attacks too depends on the stage (the plugin switches it)
        stage = stage_for(self.s['escort_stages'], g.min_level())
        prof['Trace'] = dict(prof.get('Trace') or {}, Name=self.s.get('escort_name') or 'None', Distance=0.0,
                             Game=False, Buffs=True, **{'Party Buffs': True, 'Pick': True,
                                                        'Attack Monsters': bool(stage and stage.get('attack')),
                                                        'Attack Monsters All': False})
        prof['Scrolls'] = dict(prof.get('Scrolls') or {}, Speed=g.min_level() < self.s['escort_speed_level'],
                               ScrollsBottingOnly=False, ScrollsTracingOnly=True)    # speed scroll while tracing
        # job mastery + job skills (Trader Pouch: the pick pet picks specialty goods only with it), after the build's
        am = prof.setdefault('Auto Mastery', {})
        for key, names in (('Mastery', [self.s['job_mastery']] if self.s['job_mastery'] else []),
                           ('Skill', self.s['job_skills'])):
            am[key] = list(am.get(key) or []) + [n for n in names if n not in (am.get(key) or [])]
        loop = prof.setdefault('Loop', {})
        script = {}
        for i, a in enumerate(plan):
            script[a['name']] = {'Data': [], 'Enabled': i == 0, 'Path': '', 'Pick Radius': 50, 'Polygon': [],
                                 'Radius': a.get('radius', 50), 'Region': a['region'], 'Type': 0,
                                 'X': a['x'], 'Y': a['y'], 'Z': a.get('z', 0)}
        loop['Script'] = script
        if build and build.get('brackets'):
            for b in build['brackets']:
                bp = copy.deepcopy(prof)
                set_skill_lists(bp, b, pbuffs or {})
                write_json(self.profile_path(m.character, b['name']), bp)
                bfile = self.profile_path(m.character, b['name'] + 'b')
                if os.path.isfile(bfile):
                    os.remove(bfile)             # old reload copy of the plugin
            level = int(self.status(m.character).get('level', 1) or 1)
            set_skill_lists(prof, bracket_for(build, level), pbuffs or {})
        write_json(path, prof)


# ---------------------------------------------------------------- launching
def phbot_exe_path(s):
    """Absolute path of the bot exe (Popen does not look in cwd for the program on Windows)."""
    exe = s['phbot_command'][0]
    return exe if os.path.isabs(exe) else os.path.join(s['phbot_dir'], exe)


def private_server_args(s):
    """--locale private-server --privateserver <key> from phBot's vSRO.json entry that lists our server."""
    data = read_json(os.path.join(s['phbot_dir'], 'vSRO.json'), {}) or {}
    for key, info in data.items():
        if s['server'].lower() in [str(x).lower() for x in (info or {}).get('servers', [])]:
            return ['--locale', 'private-server', '--privateserver', key]
    return []


def login_args(s):
    return list(s['login_args']) if s['login_args'] else private_server_args(s)


def s_mode(s, m):
    """client / clientless / client_then_clientless (that one closes its client on purpose)"""
    return s['leader_mode'] if m.role == 'leader' else s['member_mode']


def build_command(s, g, m, proxy, standard=False):
    """standard = Standard mode: every character the same (no leader) and no -mlgroup, so the plugin stays idle"""
    lead = m.role == 'leader' and not standard
    cmd = list(s['phbot_command'])
    if os.path.isfile(phbot_exe_path(s)):
        cmd[0] = os.path.normpath(phbot_exe_path(s))
    cmd += ['--username', m.username, '--password', m.password, '--server', s['server'],
            '--character', m.character]
    if m.passcode:
        cmd += ['--passcode', m.passcode]
    cmd += login_args(s)
    mode = s['member_mode'] if standard else s_mode(s, m)
    if mode == 'client':
        cmd.append('--client')
        if s['hide_client'] and (not lead or s['hide_leader_client']):
            cmd.append('--hideclient')
    elif mode == 'client_then_clientless':
        cmd += ['--client', '--clientless', '--hideclient']
    if not lead:
        cmd += list(s['member_client_args'])
    if s['minimize_bots'] and not lead:
        cmd.append('--minimize')
    if proxy:
        cmd += ['--proxyip', proxy['ip'], '--proxyport', proxy['port']]
        if proxy.get('user'):
            cmd += ['--proxyuser', proxy['user'], '--proxypass', proxy.get('pass', '')]
    cmd += list(s['extra_args'])
    cmd.append('-mlstandard' if standard else '-mlgroup=' + g.gid)     # Standard: the plugin takes map commands only
    return cmd


def process_image(pid):
    """full exe path of a running process, '' when it is not running"""
    if sys.platform != 'win32' or not pid:
        return ''
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    h = k32.OpenProcess(0x1000, False, int(pid))          # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ''
    try:
        code = wintypes.DWORD()
        if not k32.GetExitCodeProcess(h, ctypes.byref(code)) or code.value != 259:     # STILL_ACTIVE
            return ''
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        return buf.value if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)) else ''
    finally:
        k32.CloseHandle(h)


class RunningBot:
    """a phBot started before the dashboard was reopened; stands in for the Popen of a bot launched now"""

    def __init__(self, pid):
        self.pid = pid
        self.returncode = None

    def poll(self):
        if self.returncode is None and not process_image(self.pid):
            self.returncode = 1
        return self.returncode


def kill_tree(pid):
    if not pid:
        return
    try:
        subprocess.run(['taskkill', '/PID', str(int(pid)), '/T', '/F'], capture_output=True,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except Exception:
        pass


def masked(cmd):
    out, hide = [], False
    for c in cmd:
        out.append('***' if hide else c)
        hide = c in ('--password', '--passcode', '--proxypass')
    return out


# ---------------------------------------------------------------- orchestrator
class Manager:
    def __init__(self, settings_path=None, log=print, mode='mass'):
        self.settings_path = settings_path
        self.log = log
        self.mode = mode
        self.standard = mode == 'standard'      # a plain phBot manager (module docstring)
        self.lock = threading.RLock()
        self.running = False        # launching new groups allowed
        self.thread = None
        self.stop_event = threading.Event()
        self.next_launch = 0.0      # Standard: the next bot opens at this time
        self.reload()

    # -------------------------------------------------- setup
    def reload(self):
        with self.lock:
            self.s = load_settings(self.settings_path, self.mode)
            self.files = PhBotFiles(self.s)
            self.install_plugin()       # Standard too: its bots walk the Map's routes with it (-mlstandard)
            self.progress_path = path_in_root(self.s['progress'])
            self.progress = read_json(self.progress_path, {}) or {}
            for ch, v in list(self.progress.items()):   # "Stop at level" raised: finished characters go on
                if v == 'done' and int(self.files.status(ch).get('level') or 0) < int(self.s['final_level']):
                    del self.progress[ch]
            self.skip_path = path_in_root(self.s['skip'])
            self.skip = read_json(self.skip_path, {}) or {}
            if self.standard:
                self.auto_path = path_in_root(self.s['auto_start'])
                self.auto = set(read_json(self.auto_path, []) or [])
            self.plans = {race: (read_json(path_in_root(p), {}) or {}).get('areas', [])
                          for race, p in (self.s['plans'] or {}).items()}
            self.builds, self.templates = {}, {}
            bdir = path_in_root(self.s['builds_dir'])
            for f in sorted(os.listdir(bdir)) if os.path.isdir(bdir) else []:
                if f.endswith('.json'):
                    bid = f[:-5]
                    self.builds[bid] = read_json(os.path.join(bdir, f), {}) or {}
                    self.templates[bid] = read_json(os.path.join(path_in_root(self.s['templates_dir']), f), {}) or {}
            old = {g.gid: g for g in getattr(self, 'groups', [])}
            self.layout_path = path_in_root(self.s['layout'])
            layout = read_json(self.layout_path, {}) or {}
            self.deleted = set(layout.get('deleted') or [])
            self.groups = load_groups(self.s, self.progress, self.skip, escort_party=not self.standard, layout=layout)
            for g in self.groups:       # keep live groups when reloading while running
                if g.gid in old and old[g.gid].state in ('launching', 'running'):
                    self.groups[self.groups.index(g)] = old[g.gid]
            self.share_finished()
            if self.standard:           # every bot on this PC's own connection
                self.slots = [None]
                self.log('Standard mode: %d group(s) of up to %d' % (len(self.groups), self.s['group_size']))
                self._reattach_standard()
                self.log('Login: %s' % (' '.join(login_args(self.s)) or 'phBot will ask for the server'))
                return
            self.slots = load_proxies(self.s)
            self.s['escort_name'] = self.escort_group().leader.character if self.escort_group() else ''
            self.log('Loaded %d group(s), %d connection slot(s), composition "%s": %s' % (
                len(self.groups), len(self.slots), self.s['composition'], ', '.join(composition(self.s))))
            self._reattach()
            try:
                self.refresh_spots()        # running groups get the reloaded plan now (bots re-read it)
            except Exception as e:
                self.log('group files not refreshed: %s' % e)
            try:
                self.update_escort_party()
            except Exception as e:
                self.log('escort party profile not updated: %s' % e)
            self.log('Login: %s' % (' '.join(login_args(self.s)) or 'phBot will ask for the server'))

    def install_plugin(self):
        """this folder's plugin/MassLeveler.py goes into <phBot>/Plugins whenever it differs, so copying the
        MassLeveler Escort folder to another PC is enough (open phBots load it on their next start)"""
        src = path_in_root('plugin/MassLeveler.py')
        plugins = os.path.join(self.s['phbot_dir'], 'Plugins')
        dst = os.path.join(plugins, 'MassLeveler.py')
        if not os.path.isfile(src) or not os.path.isdir(plugins):
            self.log('Plugin NOT updated: no Plugins folder in %s (phbot_dir / phbot_dirs in settings.json)' %
                     self.s['phbot_dir'])
            return
        try:
            with open(src, 'rb') as a:
                new = a.read()
            old = open(dst, 'rb').read() if os.path.isfile(dst) else b''
            if new != old:
                with open(dst, 'wb') as b:
                    b.write(new)
                self.log('Plugin updated in ' + plugins + ' (open phBots use it after a restart)')
        except OSError as e:
            self.log('Plugin not copied to %s: %s' % (plugins, e))

    def escort_group(self):
        return next((g for g in getattr(self, 'groups', []) if g.escort and g.members), None)

    def group_characters(self, party=False):
        """party = only groups the escort's party has room for (an old group of 8 has its own party)"""
        return [m.character for g in self.groups if g.state != 'skipped' and not g.escort
                and not (party and len(g.members) >= PARTY_MAX) for m in g.members]

    def update_escort_party(self):
        """the escort's phBot profile (the one it uses now): party leader, auto-invites every group character
        (exp + item share; they accept only it and leave when it does), no union, resurrects them all. Returns the
        profile file when it changed (an open phBot reloads it on a profile change)."""
        master = self.s.get('escort_name')
        if not master or not self.escort_group():
            return None
        default = read_json(self.files.profile_path(master))
        if not default:
            self.log('Party: no phBot profile of %s found' % master)
            return None
        name = default['Profile'] if len(default) == 1 and default.get('Profile') else 'Default'
        path = self.files.profile_path(master, name)
        prof = default if name == 'Default' else read_json(path)
        if not prof or len(prof) < 5:
            return None
        up = prof.setdefault('Union Party', {})
        party = prof.setdefault('Party', {})
        res = prof.setdefault('Auto Resurrect', {})
        # the escort is the party leader: it invites the characters of the groups (one group plays at a time)
        want = {'AutoUnionPartyInvite': False, 'AcceptUnionPartyInvites': False, 'UnionPartyInvite': []}
        want_party = {'AutoInviteParty': True, 'Invite From List': True, 'InviteList': self.group_characters(party=True),
                      'AutoAcceptParty': False, 'Accept Invites From List': False, 'Accept All Invites': False,
                      'Invite All Players': False, 'AutoFormParty': False, 'Party Leader Names': [],
                      'LeaveIfLeaderNotInList': False, 'PartyInviteType': self.s['party_invite_type']}
        want_res = {'ResurrectList': self.group_characters()}
        if all(up.get(k) == v for k, v in want.items()) and all(party.get(k) == v for k, v in want_party.items()) \
                and all(res.get(k) == v for k, v in want_res.items()):
            return None
        up.update(want)
        party.update(want_party)
        res.update(want_res)
        write_json(path, prof)
        self.log('Party: %s is the party leader, it invites the group characters (exp + item share)' % master)
        return path

    def _reattach(self):
        """bots still running from before the dashboard was closed: take their group over (running) instead of
        launching it again. A bot counts when its plugin status names this group, its pid is still a running
        phBot and the status is fresh (a reused pid would not write it)."""
        exe = os.path.basename(self.s['phbot_command'][0]).lower()
        now = time.time()
        fresh = max(120, self.s['stale_minutes'] * 60)
        for g in self.groups:
            if g.state not in ('pending', 'stopped', 'failed'):
                continue
            found = {}
            for m in g.members:
                st = self.files.status(m.character)
                if st.get('group') == g.gid and st.get('pid') and now - (st.get('ts') or 0) < fresh and \
                        os.path.basename(process_image(st['pid'])).lower() == exe:
                    found[m.character] = st['pid']
            if not found:
                continue
            slot = (read_json(os.path.join(self.files.shared, 'groups', g.gid + '.json'), {}) or {}).get('slot')
            used = {x.slot for x in self.groups if x.state in ('launching', 'running')}
            if not isinstance(slot, int) or slot >= len(self.slots) or slot in used:
                slot = self._free_slot()
            if slot is None:
                self.log('%s has %d bot(s) running but no free connection slot - not taken over' % (g.gid, len(found)))
                continue
            g.slot, g.state = slot, 'running'
            g.message = 'taken over: %d/%d bots were running' % (len(found), len(g.members))
            for m in g.members:
                m.proc = RunningBot(found[m.character]) if m.character in found else None     # missing ones relaunch
                m.launched_at, m.out_since, m.restarts = now, 0.0, 0
            self.log('%s %s' % (g.gid, g.message))

    def plan_for(self, g):
        """escort version: the only training area is the solo spot (monsters by the Jangan gate) where the members
        level alone to escort_solo_level; everything after that is the escort's stages"""
        sp = self.s.get('escort_solo_spot')
        if sp:
            r = self.s.get('training_radius') or 40
            spot = {'region': sp['region'], 'x': sp['x'], 'y': sp['y'], 'z': sp.get('z', 0), 'radius': r, 'src': 'plan'}
            return [dict(spot, name=sp.get('name') or 'Solo spot', min_level=1,
                         max_level=self.s['escort_solo_level'] - 1, spots=[spot])]
        race =(self.builds.get(g.leader.build) or {}).get('race', 'CH')
        plan = self.plans.get(race) or self.plans.get('CH') or []
        learned = self.learned_spots(plan)
        out = []
        for a in plan:
            spots = self.area_spots(a, learned.get(a['name'], []))
            first = spots[0]
            out.append(dict(a, radius=first['radius'], region=first['region'], x=first['x'], y=first['y'],
                            z=first['z'], spots=spots))
        return out

    # -------------------------------------------------- training spots
    def area_spots(self, a, learned):
        """the spots of an area, best first: set by hand, learned from the bots, the plan's, its alternatives;
        bad ones and doubles (closer than SPOT_SAME) left out"""
        r = self.s.get('training_radius') or a.get('radius', 40)
        spot = lambda d, src, **kw: dict({'region': d.get('region', 0), 'x': d['x'], 'y': d['y'], 'z': d.get('z', 0),
                                          'radius': d.get('radius') if src == 'manual' and d.get('radius') else r,
                                          'src': src}, **kw)
        manual = [spot(c, 'manual') for c in read_json(path_in_root(self.s['custom_spots']), []) or []
                  if c['min_level'] <= a['max_level'] and c['max_level'] >= a['min_level']]
        cands = manual + [spot(l, 'learned', seen=l['seen']) for l in learned] + [spot(a, 'plan')] + \
            [spot(x, 'alt') for x in a.get('alternatives', [])]
        bad = read_json(path_in_root(self.s['bad_spots']), []) or []
        out = []
        for c in cands:
            if any(near(c, b, SPOT_SAME) for b in bad + out):
                continue
            out.append(c)
        return out[:MAX_SPOTS] or [spot(a, 'plan')]

    def learned_spots(self, plan):
        """{area name: [spots]} from every bot's spawns/<char>.json: cells where the most monsters of the area's
        level were really seen (cached; recomputed when the files change)"""
        files = glob.glob(os.path.join(self.files.shared, 'spawns', '*.json'))
        key = (tuple(sorted((f, os.path.getmtime(f)) for f in files)), id(plan), self.s.get('training_radius'))
        if getattr(self, '_learned_key', None) == key:
            return self._learned
        self._learned_key, self._learned = key, {}
        if not files:
            return self._learned
        levels = monster_levels(self.s['phbot_dir'])
        cells = {}
        for f in files:
            for k, c in (read_json(f, {}) or {}).items():
                e = cells.setdefault(k, {'x': c['x'], 'y': c['y'], 'region': c.get('region', 0), 'z': c.get('z', 0),
                                         'mobs': {}})
                for name, n in (c.get('mobs') or {}).items():
                    e['mobs'][name] = e['mobs'].get(name, 0) + n
        r = self.s.get('training_radius') or 40
        need = self.s.get('learned_min_monsters', 25)
        for a in plan:
            ml = a.get('monster_level') or a['max_level']
            lo, hi = ml - 3, ml + 2               # around the level the plan hunts here (group level + offset)
            pts = []
            for c in cells.values():
                n = sum(cnt for name, cnt in c['mobs'].items() if lo <= levels.get(name, -99) <= hi)
                if n:
                    pts.append((c, n))
            scored = sorted(((sum(n for o, n in pts if near(c, o, r)), c) for c, _ in pts), key=lambda t: -t[0])
            best = []
            for score, c in scored:
                if score < need or len(best) == LEARNED_PER_AREA:
                    break
                if not any(near(c, b, 2 * r) for b in best):
                    best.append(dict(c, seen=score))
            if best:
                self._learned[a['name']] = best
        return self._learned

    def group_area(self, g):
        """(area, spots, index of the spot in use) of a running group, from its leader's status + spot file"""
        grp = read_json(os.path.join(self.files.shared, 'groups', g.gid + '.json'), {}) or {}
        st = self.files.status(g.leader.character)
        area = next((a for a in grp.get('areas', []) if a['name'] == st.get('area')), None)
        if not area:
            return None, [], 0
        spots = area.get('spots') or [area]
        cur = st.get('spot') or [area['x'], area['y']]
        i = next((i for i, s in enumerate(spots) if abs(s['x'] - cur[0]) < 5 and abs(s['y'] - cur[1]) < 5), 0)
        return area, spots, i

    def _spot_of(self, g, area_name, xy):
        """the spot dict (region, z, radius) at xy of an area in the group file (cached by its mtime)"""
        path = os.path.join(self.files.shared, 'groups', g.gid + '.json')
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return None
        cache = getattr(self, '_group_cache', {})
        if cache.get(g.gid, (None,))[0] != mtime:
            cache[g.gid] = (mtime, read_json(path, {}) or {})
            self._group_cache = cache
        area = next((a for a in cache[g.gid][1].get('areas', []) if a['name'] == area_name), None)
        if not area:
            return None
        return next((s for s in area.get('spots') or [area] if abs(s['x'] - xy[0]) < 5 and abs(s['y'] - xy[1]) < 5), None)

    def _keep_spot(self, g, m, st, now):
        """Keep a leveling bot going (needs plugin 1.3.1):
        - phBot took the training area from the profile again (stopped + started by hand, another plugin ...)
        - phBot is stopped although the plugin is leveling (e.g. "too far away from the training area")
        - dead and nobody resurrected it
        The plugin then sets its spot again and starts the bot ('area' command). The manager never changes the
        coordinates itself: phBot does not walk to coordinates changed from outside when they are far away."""
        want = st.get('spot')
        if not want or now - m.spot_fixed_at < KEEP_RETRY:
            return
        if any(s['name'] == st.get('area') for s in self.s['escort_stages']):
            return              # tracing the escort at a stage: the plugin keeps it going (old status spot)
        live = phbot_status(self.bot_pid(m))
        if not live or not live['in_game']:
            return
        region, x, y, radius = live['training']
        want_r = (self._spot_of(g, st.get('area'), want) or {}).get('radius') or self.s.get('training_radius') or radius
        if live['dead']:
            why, wait = 'dead and not resurrected', KEEP_DEAD
        elif math.hypot(x - want[0], y - want[1]) > 10 or abs(radius - want_r) > 1:
            # the plugin may have just moved phBot to a new spot and not written it to its status yet (every 5 s)
            why, wait = 'phBot on another area (%.0f, %.0f)' % (x, y), KEEP_OFF
        elif not live['botting'] and st.get('bot_started'):
            why, wait = 'phBot stopped', KEEP_STOPPED
        else:
            m.spot_off_since = 0.0
            return
        m.spot_off_since = m.spot_off_since or now
        if now - m.spot_off_since < wait:
            return
        m.spot_fixed_at, m.spot_off_since = now, 0.0
        self.files.command(m.character, 'area')
        self.log('%s: %s - the plugin sets its spot again and starts the bot' % (m.character, why))

    def spot_file(self, g):
        return os.path.join(self.files.shared, 'groups', g.gid + '.spot.json')

    def refresh_spots(self):
        """rewrite the group file of the running groups (their bots re-read it within seconds)"""
        for g in self._active():
            self.files.write_group(g, self.plan_for(g), self.builds)

    def next_spot(self, g, why='by hand'):
        area, spots, i = self.group_area(g)
        if not area or len(spots) < 2:
            self.log('%s: no other spot for this area' % g.gid)
            return False
        nxt = spots[(i + 1) % len(spots)]
        write_json(self.spot_file(g), {'area': area['name'], 'x': nxt['x'], 'y': nxt['y'], 'ts': time.time(),
                                       'by': 'manager', 'why': why})
        self.log('%s -> spot %d/%d of %s (%s)' % (g.gid, (i + 1) % len(spots) + 1, len(spots), area['name'], nxt['src']))
        return True

    def first_spot(self, g):
        try:
            os.remove(self.spot_file(g))
        except OSError:
            pass
        self.log('%s back to the first spot of its area' % g.gid)

    def mark_spot_bad(self, g):
        """never use the group's current spot again; the group moves to the next one"""
        area, spots, i = self.group_area(g)
        if not area:
            return False
        s = spots[i]
        path = path_in_root(self.s['bad_spots'])
        write_json(path, (read_json(path, []) or []) + [{'region': s['region'], 'x': s['x'], 'y': s['y'],
                                                         'area': area['name'], 'ts': time.time()}])
        if s.get('src') == 'manual':
            self.delete_custom_spot(s)
        self.next_spot(g, 'spot marked bad')
        self.refresh_spots()
        self.log('Spot %d of %s marked bad' % (i + 1, area['name']))
        return True

    def add_custom_spot(self, pos, lo, hi, groups=()):
        """a spot set by hand for levels lo-hi (first choice of those areas); `groups` move there now"""
        path = path_in_root(self.s['custom_spots'])
        spots = [c for c in read_json(path, []) or [] if not (near(c, pos, SPOT_SAME) and c['min_level'] == lo
                                                             and c['max_level'] == hi)]
        spots.append({'min_level': lo, 'max_level': hi, 'region': pos['region'], 'x': round(pos['x'], 1),
                      'y': round(pos['y'], 1), 'z': round(pos.get('z', 0), 1), 'ts': time.time()})
        write_json(path, spots)
        self.refresh_spots()
        for g in groups:
            area, _, _ = self.group_area(g)
            if area and area['min_level'] <= hi and area['max_level'] >= lo:
                write_json(self.spot_file(g), {'area': area['name'], 'x': round(pos['x'], 1), 'y': round(pos['y'], 1),
                                               'ts': time.time(), 'by': 'manager', 'why': 'set by hand'})
        self.log('Spot (%d, %d) set for levels %d-%d' % (pos['x'], pos['y'], lo, hi))

    def delete_custom_spot(self, s):
        path = path_in_root(self.s['custom_spots'])
        write_json(path, [c for c in read_json(path, []) or [] if not near(c, s, SPOT_SAME)])
        self.refresh_spots()

    def unmark_bad(self, s=None):
        path = path_in_root(self.s['bad_spots'])
        write_json(path, [b for b in read_json(path, []) or [] if s is not None and not near(b, s, SPOT_SAME)])
        self.refresh_spots()

    def save_settings(self, changes):
        """write changed keys to settings.json (Standard mode: to its "standard" part) and reload"""
        path = self.settings_path or path_in_root('settings.json')
        raw = read_json(path, {}) or {}
        if self.standard:
            raw['standard'] = dict(raw.get('standard') or {}, **changes)
        else:
            raw.update(changes)
        write_json(path, raw)
        self.reload()

    def set_visible(self, g, members, what, show):
        """show / hide the phBot window ('bot') or the game client ('client') of some members"""
        n = 0
        for m in members:
            # Standard: phBot's own status (a plugin status file there would be an old Mass Leveler run's)
            st = {'pid': self.bot_pid(m), 'client_pid': (phbot_status(self.bot_pid(m)) or {}).get('client_pid', 0)} \
                if self.standard else self.files.status(m.character)
            if what == 'bot':
                # the bot this dashboard launched, else the one the plugin reports (dashboard reopened)
                n += show_bot(m.proc.pid if m.proc and m.proc.poll() is None else st.get('pid', 0), show)
            else:
                n += show_windows(st.get('client_pid', 0), show)
        self.log('%s %s window(s) of %s: %d' % ('Shown' if show else 'Hidden', what,
                                                g.gid if g else ', '.join(m.character for m in members), n))
        return n

    def problems(self):
        p = []
        exe = self.s['phbot_command'][0]
        if exe.lower().endswith('.exe') and not os.path.isfile(phbot_exe_path(self.s)):
            p.append('phBot not found: ' + phbot_exe_path(self.s) + '  (Settings: Choose... to pick it)')
        if self.standard:
            if exe.lower().endswith('.exe') and not login_args(self.s):
                p.append('Server "%s" not found in %s - phBot will ask for the server' % (
                    self.s['server'], os.path.join(self.s['phbot_dir'], 'vSRO.json')))
            if not self.groups:
                p.append('No accounts in ' + self.s['accounts_csv'])
            return p
        if not os.path.isfile(os.path.join(self.s['phbot_dir'], 'Plugins', 'MassLeveler.py')) and exe.lower().endswith('.exe'):
            p.append('Copy plugin/MassLeveler.py into ' + os.path.join(self.s['phbot_dir'], 'Plugins'))
        if exe.lower().endswith('.exe') and not login_args(self.s):
            p.append('Server "%s" not found in %s - phBot will ask for the server. Set "login_args" in settings.json'
                     % (self.s['server'], os.path.join(self.s['phbot_dir'], 'vSRO.json')))
        if not self.s.get('escort_solo_spot'):
            p.append('No escort_solo_spot in settings.json (where the members level alone to %d)' %
                     self.s['escort_solo_level'])
        for b in sorted({m.build for g in self.groups if g.state != 'done' for m in g.members}):
            if not self.builds.get(b, {}).get('brackets') or not self.templates.get(b):
                p.append('Build "%s" has no skill plan / template - run tools/make_builds.py (Rebuild skill plans)' % b)
        if not self.groups:
            p.append('No accounts in ' + self.s['accounts_csv'])
        if not self.escort_group():
            p.append('No escort: mark its row in %s with escort = yes' % self.s['accounts_csv'])
        if len(self.s.get('escort_stages') or []) == 0:
            p.append('No escort_stages in settings.json')
        for g in self.groups:
            if len(g.members) < group_size(self.s, bool(self.escort_group())) and g.state != 'done' and not g.escort:
                p.append('%s has only %d characters (party works, but less EXP share)' % (g.gid, len(g.members)))
        return p

    def set_phbot(self, exe_path):
        """Point the manager at another phBot.exe, save it to settings.json and install the plugin there."""
        path = self.settings_path or path_in_root('settings.json')
        raw = read_json(path, {}) or {}
        part = raw.setdefault('standard', {}) if self.standard else raw     # Standard: its own phBot
        part['phbot_dir'] = os.path.dirname(exe_path).replace('\\', '/')
        cmd = list(part.get('phbot_command') or raw.get('phbot_command') or DEFAULT_SETTINGS['phbot_command'])
        cmd[0] = os.path.basename(exe_path)
        part['phbot_command'] = cmd
        write_json(path, raw)
        plugins = os.path.join(part['phbot_dir'], 'Plugins')
        src = path_in_root('plugin/MassLeveler.py')
        if not self.standard and os.path.isdir(plugins) and os.path.isfile(src):
            shutil.copyfile(src, os.path.join(plugins, 'MassLeveler.py'))
            self.log('Plugin installed in ' + plugins)
        self.reload()

    def save_progress(self):
        write_json(self.progress_path, self.progress)
        self.share_finished()

    def share_finished(self):
        """finished characters (their bots are closed here) -> OneDrive, read by Mass Runner on the other PC"""
        path = '' if self.standard else finished_share_path(self.s)
        if not path:
            return
        users = {m.character: m.username for g in getattr(self, 'groups', []) for m in g.all_members}
        data = {'server': self.s['server'], 'final_level': self.s['final_level'],
                'characters': {ch: {'state': v, 'username': users.get(ch, ''),
                                    'level': int(self.files.status(ch).get('level') or 0)}
                               for ch, v in sorted(self.progress.items()) if v in ('done', 'finished')}}
        old = read_json(path, {}) or {}
        if {k: old.get(k) for k in data} == data:
            return
        try:
            write_json(path, dict(data, updated=time.time()))
        except Exception as e:
            self.log('finished list not written to %s: %s' % (path, e))

    def mark_finished(self, g):
        """the user marks a group finished: done (green) for good, never started again until Reset to pending.
        'finished' (not 'done') so reload keeps it whatever level its characters have"""
        with self.lock:
            if g.state in ('launching', 'running'):
                self.kill_group(g, 'done')
            for m in g.all_members:
                self.progress[m.character] = 'finished'
            self.save_progress()
            group_state(g, self.progress, self.skip)
            g.message = 'finished by hand'
            self.log('%s marked finished by hand - not started again' % g.gid)

    # -------------------------------------------------- skip / active
    def _set_skip(self, key, name, skip):
        names = [x for x in self.skip.get(key) or [] if x != name]
        self.skip[key] = names + ([name] if skip else [])
        write_json(self.skip_path, self.skip)

    def set_group_skip(self, g, skip):
        """skip = never started (a running group is closed); active again = pending (or done)"""
        with self.lock:
            self._set_skip('groups', g.gid, skip)
            if g.state in ('launching', 'running'):
                if not skip:
                    return
                self.kill_group(g, 'skipped')
            group_state(g, self.progress, self.skip)
            self.log('%s %s' % (g.gid, 'skipped' if skip else 'active (%s)' % g.state))

    def set_member_skip(self, g, m, skip):
        """an unticked character is left out of its group; a running group changes when it starts again"""
        with self.lock:
            self._set_skip('characters', m.character, skip)
            m.skip = skip
            if g.state in ('launching', 'running'):
                self.log('%s %s - applies when %s starts again' % (m.character, 'skipped' if skip else 'active', g.gid))
                return
            g.use_members()
            if g.state in ('pending', 'done', 'skipped'):
                group_state(g, self.progress, self.skip)
            self.log('%s %s (%s: %s)' % (m.character, 'skipped' if skip else 'active', g.gid,
                                         ', '.join(x.character for x in g.members) or 'nobody'))

    # -------------------------------------------------- groups by hand (drag and drop, delete)
    def save_layout(self):
        """from the first change on, the groups stay as arranged (layout file of this mode)"""
        write_json(self.layout_path, {'groups': [[g.gid, [m.character for m in g.all_members]]
                                                 for g in self.groups if not g.escort],
                                      'deleted': sorted(self.deleted)})

    def _editable(self, groups):
        busy = [g.gid for g in groups if g.state in ('launching', 'running')]
        if busy:
            self.log('%s is running - close it first to change it' % ', '.join(busy))
        return not busy

    def _changed(self, groups):
        for g in groups:
            g.use_members()
            group_state(g, self.progress, self.skip)
        self.groups = [g for g in self.groups if g.all_members]
        self.save_layout()
        if not self.standard:
            self.update_escort_party()      # its invite list: the groups of 7

    def move_member(self, m, target=None):
        """m goes into the group target (None = a new group of its own); returns that group, or None"""
        with self.lock:
            src = next((g for g in self.groups if m in g.all_members), None)
            if not src or src is target or src.escort or (target and target.escort) or \
                    not self._editable([src] + ([target] if target else [])):
                return None
            if target and len(target.all_members) >= PARTY_MAX:
                self.log('%s already has %d characters (party limit)' % (target.gid, PARTY_MAX))
                return None
            src.all_members.remove(m)
            if target is None:
                target = Group('G%03d' % next_group_number(self.groups), [])
                self.groups.insert(self.groups.index(src) + 1, target)
            target.all_members.append(m)
            self._changed([src, target])
            self.log('%s moved from %s to %s' % (m.character, src.gid, target.gid))
            return target

    def delete_members(self, members):
        """never shown again (Settings: Restore deleted brings them back); not while their group runs"""
        with self.lock:
            groups = [g for g in self.groups if any(m in g.all_members for m in members)]
            if not groups or any(g.escort for g in groups) or not self._editable(groups):
                return False
            for g in groups:
                g.all_members = [m for m in g.all_members if m not in members]
            self.deleted.update(m.character for m in members)
            self._changed(groups)
            self.log('Deleted %s' % ', '.join(m.character for m in members))
            return True

    def restore_deleted(self):
        with self.lock:
            names = sorted(self.deleted)
            self.deleted.clear()
            self.save_layout()
        self.reload()           # they come back in new groups at the end
        self.log('Restored %s' % (', '.join(names) or 'nothing'))

    # -------------------------------------------------- control
    def start(self):
        self.running = True
        self._ensure_loop()
        if self.standard:       # every ticked, unfinished character opens (one after another)
            with self.lock:
                for g in self.groups:
                    if g.state not in ('skipped', 'done'):
                        self._begin_standard(g)
        self.log('Started')

    def _ensure_loop(self):
        if not self.thread or not self.thread.is_alive():
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._loop, daemon=True)
            self.thread.start()

    def pause(self):
        self.running = False
        self.log('Paused: running groups continue, no new groups will start')

    def shutdown(self, kill=True):
        self.running = False
        if kill:
            for g in self.groups:
                if g.state in ('launching', 'running'):
                    self.kill_group(g, 'stopped')
        self.stop_event.set()

    def kill_group(self, g, new_state='stopped'):
        with self.lock:
            for m in g.members:
                self.kill_member(m)
            g.state = new_state
            g.slot = None
            self.log('%s %s' % (g.gid, new_state))

    def kill_member(self, m):
        alive = m.proc and m.proc.poll() is None
        if self.standard:       # phBot's own status: a plugin status file there would be an old Mass Leveler run's
            client = (phbot_status(m.proc.pid) or {}).get('client_pid') if alive else 0
        else:
            client = self.files.status(m.character).get('client_pid')
        if alive:
            kill_tree(m.proc.pid)
        if client:
            kill_tree(client)
        m.proc = None
        m.queued, m.reopen_at = False, 0.0

    def send(self, g, cmd):
        for m in g.members:
            self.files.command(m.character, cmd)
        self.log('%s <- %s' % (g.gid, cmd))

    def restart_group(self, g):
        """close every bot + client of a running group and launch them again (profiles written anew)"""
        with self.lock:
            if g.state not in ('launching', 'running'):
                return False
            slot = g.slot
            self.kill_group(g, 'stopped')
            if self.standard:
                self._begin_standard(g)
            else:
                self._begin(g, slot)
            self.log('%s restarting all bots' % g.gid)
            return True

    def restart_member(self, g, m):
        with self.lock:
            self.kill_member(m)
            m.restarts = 0
            m.held = False
            if self.standard:
                self.open_member(g, m)
            else:
                self.launch(g, m)

    # -------------------------------------------------- phBot Manager commands (right-click menu)
    def bot_pid(self, m):
        """pid of the character's phBot: the one launched here, else the one its plugin reports"""
        if m.proc and m.proc.poll() is None:
            return m.proc.pid
        return 0 if self.standard else self.files.status(m.character).get('pid') or 0

    def bot_command(self, members, cmd, **kw):
        """a phBot Manager command (docs/PROTOCOL.md) to each member's bot; returns how many got it"""
        fields = phbot_fields(cmd, **kw)
        n = sum(1 for m in members if phbot_shm_write(self.bot_pid(m), fields))
        self.log('%s -> %s: %d of %d bot(s)' % (cmd, ', '.join(m.character for m in members), n, len(members)))
        return n

    def member_command(self, members, cmd):
        """MassLeveler plugin command ('start', 'stop', 'finish', 'auto_equip'): the plugin keeps the bot stopped /
        started, or presses MoAutoEquip's learned auto equip packets"""
        for m in members:
            self.files.command(m.character, cmd)
        self.log('%s <- %s' % (', '.join(m.character for m in members), cmd))

    def go_clientless(self, members):
        """close the game client; the bot plays on clientless (and is not restarted for the missing client)"""
        n = 0
        for m in members:
            pid = (phbot_status(self.bot_pid(m)) or {}).get('client_pid') or \
                (0 if self.standard else self.files.status(m.character).get('client_pid'))
            if pid:
                m.no_client = True
                kill_tree(pid)
                n += 1
        self.log('Go Clientless: %d client(s) closed' % n)
        return n

    def go_client(self, members, reconnect):
        for m in members:
            m.no_client = False
        return self.bot_command(members, 'go_client', reconnect=reconnect)

    def terminate(self, members):
        """close bot + client and keep them closed (Restart character / group opens them again)"""
        with self.lock:
            for m in members:
                m.held = True
                self.kill_member(m)
        self.log('Terminated %s - not reopened until restarted' % ', '.join(m.character for m in members))

    def start_group_now(self, g):
        if self.standard:
            with self.lock:
                if g.gid in (self.skip.get('groups') or []):
                    self.set_group_skip(g, False)
                self._begin_standard(g)
            self._ensure_loop()
            return
        with self.lock:
            if g.state in ('launching', 'running'):
                return
            if g.gid in (self.skip.get('groups') or []):
                self.set_group_skip(g, False)
            if not [m for m in g.all_members if not m.skip]:
                self.log('%s: every character is skipped - tick at least one' % g.gid)
                return
            slot = self._free_slot()
            if slot is None:
                self.log('No free connection slot for %s (add proxies or finish a group)' % g.gid)
                return
            self._begin(g, slot)

    # -------------------------------------------------- Standard mode (a plain phBot manager)
    @staticmethod
    def _alive(m):
        return bool(m.proc) and m.proc.poll() is None

    def open_member(self, g, m):
        """open one character (its turn comes after the bots already waiting)"""
        with self.lock:
            if self._alive(m):
                return
            m.queued, m.held, m.reopen_at = True, False, 0.0
            if g.state not in ('launching', 'running'):
                g.state, g.message = 'launching', ''
        self._ensure_loop()

    def map_command(self, members, cmd, **args):
        """Standard Map: 'navigate' (region, x, y, z, then = script lines after the route) / 'stop_walk', done by
        the plugin of each bot (started with -mlstandard)"""
        for m in members:
            self.files.command(m.character, cmd, args)
        self.log('%s -> %s %s' % (', '.join(m.character for m in members), cmd,
                                  ('%.0f, %.0f' % (args['x'], args['y'])) + (('  then ' + ' / '.join(args['then']))
                                                                            if args.get('then') else '')
                                  if 'x' in args else ''))

    def set_auto_start(self, members, on):
        """Auto start: the bot opens again whenever it closes by itself (not when closed from here)"""
        with self.lock:
            for m in members:
                (self.auto.add if on else self.auto.discard)(m.character)
            write_json(self.auto_path, sorted(self.auto))
        self.log('Auto start %s: %s' % ('on' if on else 'off', ', '.join(m.character for m in members)))

    def running_phbots(self):
        """{character in lower case: pid} of the open phBots that are logged in (from phBot's own status)"""
        found = {}
        try:
            out = subprocess.run(['tasklist', '/FO', 'CSV', '/NH', '/FI', 'IMAGENAME eq %s' %
                                  os.path.basename(self.s['phbot_command'][0])], capture_output=True, text=True,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout
        except Exception:
            return found
        for row in csv.reader(out.splitlines()):
            if len(row) > 1 and row[1].isdigit():
                st = phbot_status(int(row[1]))
                if st and st['name']:
                    found[st['name'].lower()] = int(row[1])
        return found

    def _reattach_standard(self):
        """bots open from before this window (or opened by hand) show as running; their bot is left as it is"""
        found = self.running_phbots()
        for g in self.groups:
            mine = [m for m in g.all_members if m.character.lower() in found and not self._alive(m)]
            for m in mine:
                m.proc, m.start_sent, m.launched_at = RunningBot(found[m.character.lower()]), True, time.time()
            if mine:
                g.state, g.message = 'running', 'taken over: %d bot(s) were open' % len(mine)

    def _begin_standard(self, g):
        """the group's ticked, unfinished characters that are not open yet wait for their turn to open"""
        g.use_members()
        todo = [m for m in g.members if self.progress.get(m.character) != 'finished' and not self._alive(m)]
        for m in todo:
            m.queued, m.held, m.reopen_at, m.restarts = True, False, 0.0, 0
        if todo:
            g.state, g.message = 'launching', ''
            self.log('%s opening: %s' % (g.gid, ', '.join(m.character for m in todo)))

    def _tick_standard(self, now):
        """phBot's own numbers for every open bot; one waiting bot opens every launch_delay_seconds, its bot starts
        once it is in the game; an Auto start one that closed by itself opens again"""
        nxt = None
        for g in self.groups:
            for m in g.all_members:
                alive = self._alive(m)
                if m.proc and not alive:        # closed by itself: Close / Stop all clear m.proc themselves
                    m.proc = None
                    if m.character in self.auto and not m.skip:
                        m.reopen_at = now + self.s['disconnect_restart_seconds']
                        self.log('%s closed - Auto start opens it again' % m.character)
                    else:
                        self.log('%s closed' % m.character)
                if m.skip:
                    m.queued, m.reopen_at = False, 0.0
                live = phbot_status(m.proc.pid) if alive else None
                if live and live['in_game'] and not m.start_sent:
                    m.start_sent = bool(self.bot_command([m], 'start_bot'))
                m.status = self._standard_status(m, live, alive)
                if nxt is None and not alive and (m.queued or (m.reopen_at and now >= m.reopen_at)):
                    nxt = (g, m)
            busy = [m for m in g.all_members if m.queued or m.reopen_at or self._alive(m)]
            if busy:
                g.state = 'launching' if any(m.queued for m in busy) else 'running'
            elif g.state in ('launching', 'running'):
                group_state(g, self.progress, self.skip)
                if g.state == 'pending':
                    g.state = 'stopped'
        if nxt and now >= self.next_launch:
            self.launch(*nxt)
            self.next_launch = now + self.s['launch_delay_seconds']

    def _standard_status(self, m, live, alive):
        """what the dashboard shows, from phBot's shared memory"""
        last = m.status.get('level', '')
        if not live:
            return {'level': last, 'state': 'starting' if alive else 'opening again' if m.reopen_at else
                    'waiting to open' if m.queued else 'closed' if m.launched_at else ''}
        t = live['training']
        return {'level': live['level'] or last, 'zone': live['zone'], 'dead': live['dead'], 'in_game': live['in_game'],
                'pos': (live['region'], live['x'], live['y']), 'train': t,      # for the Map
                'exp_pct': '%.2f' % (100.0 * live['exp'] / live['level_exp']) if live['level_exp'] else '',
                'area': '%.0f, %.0f' % (t[1], t[2]) if t[1] or t[2] else '',
                'state': 'botting' if live['botting'] else 'tracing' if live['tracing'] else
                         'in game' if live['in_game'] else 'logging in' if live['connected'] else 'starting'}

    # -------------------------------------------------- internals
    def _free_slot(self):
        # the escort shares a slot (its connection is not one of a group's)
        used = {g.slot for g in self.groups if g.state in ('launching', 'running') and not g.escort}
        for i in range(len(self.slots)):
            if i not in used:
                return i
        return None

    def _active(self):
        return [g for g in self.groups if g.state in ('launching', 'running')]

    def _begin(self, g, slot):
        g.use_members()                 # ticks changed while the group was running
        g.slot = slot
        g.state = 'launching'
        g.next_launch = 0
        g.message = 'via ' + (('proxy %s:%s' % (self.slots[slot]['ip'], self.slots[slot]['port'])) if self.slots[slot] else 'direct IP')
        plan = self.plan_for(g)
        self.files.write_group(g, plan, self.builds)
        if g.escort:
            # the escort plays with its own profile; it only gets the party invites + resurrect list
            self.update_escort_party()
            for m in g.members:
                self.files.reset_status(m.character)
                m.restarts, m.proc, m.held = 0, None, False
            self.log('Escort %s starting (%s)' % (g.leader.character, g.message))
            return
        self.files.escort_target(g)
        pbuffs = party_buffs(g, self.builds)
        for m in g.members:
            self.files.write_profile(g, m, self.templates.get(m.build) or {}, plan, self.builds.get(m.build),
                                     pbuffs.get(m.character))
            try:
                ensure_pick_filter(self.files.config_dir, self.s['server'], m.character,
                                   path_in_root(self.s['pick_template']))
            except Exception as e:
                self.log('pick filter of %s not copied: %s' % (m.character, e))
            self.files.reset_status(m.character)
            m.restarts = 0
            m.proc = None
            m.held = False
        self.log('%s starting (%s): %s' % (g.gid, g.message, ', '.join(m.character for m in g.members)))

    def launch(self, g, m):
        if self.standard:
            m.queued, m.reopen_at, m.start_sent = False, 0.0, False
            pid = self.running_phbots().get(m.character.lower())
            if pid:         # already open in a phBot (by hand, or before this window): that one plays
                m.proc, m.launched_at = RunningBot(pid), time.time()
                self.log('%s is already open in phBot - not opened twice' % m.character)
                return
        proxy = self.slots[g.slot] if g.slot is not None and g.slot < len(self.slots) else None
        cmd = build_command(self.s, g, m, proxy, standard=self.standard)
        try:
            m.proc = subprocess.Popen(cmd, cwd=self.s['phbot_dir'])
            m.launched_at = time.time()
            m.out_since = 0.0
            m.lost_since = 0.0
            m.no_client = False
            self.log('launch %s (%s): %s' % (m.character, m.role, ' '.join(masked(cmd[1:]))))
        except Exception as e:
            m.proc = None
            g.message = 'launch failed: %s' % e
            self.log('launch %s failed: %s' % (m.character, e))

    def _loop(self):
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception as e:
                self.log('manager error: %s' % e)
            self.stop_event.wait(3.0)

    def tick(self):
        hide_status_widgets()
        if self.standard:
            with self.lock:
                self._tick_standard(time.time())
            return
        with self.lock:
            now = time.time()
            for g in self._active():
                for m in g.all_members:
                    # keep the last status if the file could not be read: an empty one looks like a hung bot
                    m.status = self.files.status(m.character) or m.status
                if g.state == 'launching':
                    self._tick_launching(g, now)
                else:
                    self._tick_running(g, now)
            if now - getattr(self, '_spots_refreshed', now) > SPOT_REFRESH_SECONDS:
                self._spots_refreshed = now
                try:
                    self.refresh_spots()        # spots learned meanwhile reach the running groups
                except Exception as e:
                    self.log('spot refresh failed: %s' % e)
            self._spots_refreshed = getattr(self, '_spots_refreshed', now)
            esc = self.escort_group()
            if self.running and esc and esc.state not in ('launching', 'running', 'skipped'):
                self._begin(esc, 0)
            groups = [g for g in self._active() if not g.escort]
            self.files.escort_target(groups[0] if groups else None)
            if self.running:
                while len([g for g in self._active() if not g.escort]) < self.s['max_parallel_groups']:
                    # groups closed by Stop all / Close group come back first; the next pending group only
                    # starts when one finishes (done) or is skipped
                    nxt = next((g for g in self.groups if g.state == 'stopped' and not g.escort), None) or \
                        next((g for g in self.groups if g.state == 'pending' and not g.escort), None)
                    slot = self._free_slot()
                    if not nxt or slot is None:
                        break
                    self._begin(nxt, slot)
            for g in self.groups:
                if g.state not in ('launching', 'running'):
                    for m in g.all_members:
                        m.status = self.files.status(m.character)

    def _tick_launching(self, g, now):
        if now < g.next_launch:
            return
        m = next((x for x in g.members if not x.proc), None)
        if not m:
            g.state = 'running'
            return
        self.launch(g, m)
        g.next_launch = now + self.s['launch_delay_seconds']

    def _tick_running(self, g, now):
        stale = self.s['stale_minutes'] * 60
        for m in g.members:
            st = m.status
            if st.get('state') == 'done' or m.held:
                continue
            if st.get('state') == 'error':
                g.message = '%s: %s' % (m.character, st.get('message', 'error'))
                continue
            dead = m.proc is None or m.proc.poll() is not None
            ts = st.get('ts', 0) if st.get('pid') == (m.proc.pid if m.proc else None) else 0
            frozen = not dead and now - max(ts, m.launched_at) > stale
            if st.get('state') in IN_GAME_STATES:
                m.out_since = 0
                if m.restarts and now - m.launched_at > 600:
                    m.restarts = 0          # 10 minutes in game: a later disconnect starts counting again
            elif not m.out_since:
                m.out_since = now
            stuck = not dead and m.out_since and now - max(m.out_since, m.launched_at) > self.s['login_timeout_minutes'] * 60
            # disconnected, or the game client of a client-mode bot closed: reopen it right away
            lost = ''
            if not dead and ts and st.get('state') == 'disconnected':
                lost = 'disconnected'
            elif not dead and ts and st.get('client_pid') and sys.platform == 'win32' and \
                    s_mode(self.s, m) == 'client' and not m.no_client and not process_image(st['client_pid']):
                lost = 'game client closed'
            m.lost_since = (m.lost_since or now) if lost else 0.0
            if lost and now - m.lost_since < self.s['disconnect_restart_seconds']:
                lost = ''
            if dead or frozen or stuck or lost:
                if m.restarts >= self.s['max_restarts']:
                    g.message = '%s gave up after %d restarts' % (m.character, m.restarts)
                    continue
                m.restarts += 1
                why = 'closed' if dead else 'not responding' if frozen else lost or \
                    'stuck before the game (%s)' % st.get('state', '?')
                self.log('%s %s - restarting (%d/%d)' % (m.character, why,
                                                         m.restarts, self.s['max_restarts']))
                self.kill_member(m)
                self.launch(g, m)
            elif ts and st.get('state') == 'leveling':
                self._keep_spot(g, m, st, now)
        if all(m.status.get('state') == 'done' for m in g.members):
            for m in g.members:
                self.progress[m.character] = 'done'
            self.save_progress()
            self.kill_group(g, 'done')
            g.done_at = now
            g.message = 'all reached %d' % self.s['final_level']
