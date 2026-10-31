"""End-to-end test of manager/ml_core.py with tools/fake_phbot.py (no game needed).

Run:  py -I tools/test_manager.py
"""
import json
import os
import shutil
import struct
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'manager'))
import ml_core  # noqa: E402

TMP = tempfile.mkdtemp(prefix='mlmgr_')
PHBOT = os.path.join(TMP, 'phbot')
os.makedirs(os.path.join(PHBOT, 'Config'))
with open(os.path.join(TMP, 'accounts.csv'), 'w', encoding='utf-8') as f:
    f.write('username,password,passcode,character,group,notes\n#skip,me,,,,\n')
    for i in range(1, 7):
        f.write('user%02d,secret%02d,%s,,,\n' % (i, i, '1234' if i == 1 else ''))
with open(os.path.join(TMP, 'proxies.csv'), 'w', encoding='utf-8') as f:
    f.write('ip,port,user,pass,notes\n10.0.0.1,1080,pu,pp,\n')
settings = {
    'phbot_dir': PHBOT,
    'phbot_command': [sys.executable, '-I', os.path.join(ROOT, 'tools', 'fake_phbot.py')],
    'group_size': 3, 'final_level': 8, 'launch_delay_seconds': 0.2, 'max_parallel_groups': 2,
    'use_direct_ip': True, 'stale_minutes': 1, 'max_restarts': 3, 'login_timeout_minutes': 0.05,
    'disconnect_restart_seconds': 1,
    'accounts_csv': os.path.join(TMP, 'accounts.csv'), 'proxies_csv': os.path.join(TMP, 'proxies.csv'),
    'progress': os.path.join(TMP, 'progress.json'), 'skip': os.path.join(TMP, 'skip.json'),
    'escort_solo_spot': {'name': 'Jangan gate', 'region': 24744, 'x': 6440, 'y': 838, 'z': -44},
}
OLD_PLAN = {'CH': os.path.join(ROOT, 'tools', 'test_plan.json')}     # the standard version's level plan
json.dump(settings, open(os.path.join(TMP, 'settings.json'), 'w'))
# fnuk01 ran before: phBot points at the plugin's MLa profile, which has old scroll settings and learned skills
cfg_dir = os.path.join(PHBOT, 'Config')
json.dump({'Profile': 'MLa'}, open(os.path.join(cfg_dir, 'Elysian_fnuk01.json'), 'w'))
old = json.load(open(os.path.join(ROOT, 'templates', 'ch_nuker.json')))
old['Scrolls']['ScrollsTracingOnly'] = True
old['Scrolls']['EXP Increase'] = True
old['Berserk']['Full'] = False
old['Skills']['sNormal'] = ['Shock Lion Shout']
old['Lure']['LureWalkAmount'] = 4321
old['Loop']['DontWalkAround'] = True
json.dump(old, open(os.path.join(cfg_dir, 'Elysian_fnuk01.MLa.json'), 'w'))
os.environ['CRASH_ONCE'] = 'fnuk05'
os.environ['STUCK_ONCE'] = 'fnuk02'
os.environ['DISCONNECT_ONCE'] = 'fnuk06'

logs = []
mgr = ml_core.Manager(os.path.join(TMP, 'settings.json'), log=logs.append)
failures = []


def check(cond, what):
    print(('PASS ' if cond else 'FAIL ') + what)
    if not cond:
        failures.append(what)


check([g.gid for g in mgr.groups] == ['G001', 'G002'], 'six accounts become two groups of three')
check([m.character for m in mgr.groups[0].members] == ['fnuk01', 'fnuk02', 'fnuk03'], 'names are prefix + number')
check(mgr.groups[0].members[0].role == 'leader', 'first character is the leader')
check(len(mgr.slots) == 2, 'direct IP + one proxy = two connection slots')

mgr.start()
deadline = time.time() + 60
while time.time() < deadline and not all(g.state == 'done' for g in mgr.groups):
    time.sleep(0.5)
mgr.shutdown(kill=True)

check(all(g.state == 'done' for g in mgr.groups), 'both groups reach the final level and finish')
prog = json.load(open(settings['progress']))
check(all(prog.get('fnuk%02d' % i) == 'done' for i in range(1, 7)), 'progress.json marks every character done')
check(any('fnuk05 closed - restarting' in l for l in logs), 'a crashed bot is restarted')
check(any('fnuk02 stuck before the game' in l for l in logs), 'a bot stuck at login is restarted')
check(any(l.startswith('fnuk06 disconnected - restarting') for l in logs),
      'a disconnected bot is reopened after disconnect_restart_seconds, not after the login timeout')

cfg = os.path.join(PHBOT, 'Config')
lead = json.load(open(os.path.join(cfg, 'Elysian_fnuk01.json')))
mem = json.load(open(os.path.join(cfg, 'Elysian_fnuk02.json')))
check(lead['Party']['AutoInviteParty'] and lead['Party']['InviteList'] == ['fnuk02', 'fnuk03'], 'leader invites the members')
check(mem['Party']['AutoAcceptParty'] and mem['Party']['InviteList'] == ['fnuk01'], 'members accept only the leader')
areas = lead['Loop']['Script']
check(list(areas) == ['Jangan gate'] and areas['Jangan gate']['Enabled'] and
      (areas['Jangan gate']['Region'], areas['Jangan gate']['X'], areas['Jangan gate']['Y']) == (24744, 6440, 838),
      'the only training area in the profiles is the Jangan gate (levels 1-4 alone)')
check('Profile' not in lead and lead['Lure']['LureWalkAmount'] == 4321,
      'follows phBot\'s pointer to the MLa profile and keeps what was saved there')
brk = sorted(f for f in os.listdir(cfg) if f.startswith('Elysian_fnuk01.Lv'))
check(len(brk) == 10 and brk[0] == 'Elysian_fnuk01.Lv001-010.json' and brk[-1] == 'Elysian_fnuk01.Lv091-100.json',
      '10 bracket profiles per character (Lv001-010 ... Lv091-100)')
b91 = json.load(open(os.path.join(cfg, 'Elysian_fnuk01.Lv091-100.json')))['Skills']
b21 = json.load(open(os.path.join(cfg, 'Elysian_fnuk01.Lv021-030.json')))['Skills']
check(b91['sNormal'][0] == 'Flame Wave - HellFire' and b21['sNormal'][0] == 'Flame Wave - Arrow' and b91['bNormal'] ==
      ['Source Therapy', 'Cure Therapy - Heaven'], 'each bracket has the skills of its levels, final skills at 91-100')
pb2 = json.load(open(os.path.join(cfg, 'Elysian_fnuk02.Lv091-100.json')))['Skills']['Party Buffs']
pb3 = json.load(open(os.path.join(cfg, 'Elysian_fnuk03.Lv091-100.json')))['Skills']['Party Buffs']
check(b91['Party Buffs'].get('Magical Buff') == ['fnuk02', 'fnuk03'] and b91['Party Buffs'].get('Physical Buff') ==
      ['fnuk02', 'fnuk03'] and pb2['Magical Buff'] == ['fnuk01'] and pb3['Physical Buff'] == ['fnuk01'],
      'nukers in pairs give each other Vital Flow magical + physical (01<->02, odd 03<->01)')
check(lead['Scrolls']['ScrollsTracingOnly'] is True and lead['Scrolls']['ScrollsBottingOnly'] is False and
      lead['Scrolls']['Speed'] and not any(v for k, v in lead['Scrolls'].items() if k not in (
      'ScrollsTracingOnly', 'Speed')) and lead['Berserk']['Full'],
      'scroll and berserk settings are reset from the template; escort: speed scroll while tracing (lowest < 52)')
check(lead['Auto Resurrect']['ResurrectAllPartyMembers'] and lead['Town']['AutoAcceptRes'], 'party resurrect is on')
check(not lead['Union Party']['AcceptUnionPartyInvites'] and not mem['Union Party']['AcceptUnionPartyInvites']
      and not lead['Union Party']['AutoUnionPartyInvite'], 'no union party any more')
check(lead['Loop']['DontWalkAround'] is False, 'bots walk around the training area (Don\'t walk around reset to off)')
check(mem['Scrolls']['ScrollsTracingOnly'] is True and mem['Skills']['sNormal'] == [], 'a new character starts from the template')
import sqlite3  # noqa: E402
pdb = os.path.join(cfg, 'Elysian_fnuk02.db3')
cnt = sqlite3.connect(pdb).execute('select sum(pick), sum(pet), sum(drop_item) from pickfilter').fetchone() \
    if os.path.isfile(pdb) else None
check(cnt == (486, 631, 150), 'a new character gets the pick filter of Death before its first login %s' % (cnt,))
check(mem['Pick']['PickPet'] and mem['Pick']['PickOtherPlayersItems'] and not mem['Pick']['DontPickItems']
      and mem['Shared Pick Filter'] is False, 'and the same loot options, with its own filter')
grp = json.load(open(os.path.join(cfg, 'MassLeveler', 'groups', 'G001.json')))
check({a['radius'] for a in grp['areas']} == {40} and {a['Radius'] for a in lead['Loop']['Script'].values()} == {40},
      'every training area has radius 40 (training_radius) in the group file and the profiles')
check(grp['server'] == 'Elysian' and grp['build_of']['fnuk01'] == 'ch_nuker' and grp['builds']['ch_nuker']['brackets'],
      'group file carries the server, the build of every member and its brackets')

st = json.load(open(os.path.join(cfg, 'MassLeveler', 'status', 'fnuk01.json')))
a = st['args']
check('--client' in a and '--passcode' in a and '-mlgroup=G001' in a, 'leader launches with client, passcode and group')
st2 = json.load(open(os.path.join(cfg, 'MassLeveler', 'status', 'fnuk02.json')))
check('--client' not in st2['args'], 'members launch clientless')
proxied = [json.load(open(os.path.join(cfg, 'MassLeveler', 'status', 'fnuk%02d.json' % i)))['args'] for i in (4, 5, 6)]
check(all('--proxyip' in x and '10.0.0.1' in x for x in proxied), 'second group runs through the proxy')
check(not any('secret' in l for l in logs), 'passwords never appear in the log')

# second run: everything is already done
mgr2 = ml_core.Manager(os.path.join(TMP, 'settings.json'), log=lambda m: None)
check(all(g.state == 'done' for g in mgr2.groups), 'finished groups are skipped on the next start')

# EU party: a build per slot, EU training plan, party buffs by policy, accounts.csv can override a build
with open(os.path.join(TMP, 'accounts_eu.csv'), 'w', encoding='utf-8') as f:
    f.write('username,password,passcode,character,group,notes,build\n')
    for i in range(1, 5):
        f.write('eu%02d,pw,,eu%02d,,,%s\n' % (i, i, 'eu_wizard' if i == 4 else ''))
eu = dict(settings, accounts_csv=os.path.join(TMP, 'accounts_eu.csv'), group_size=4, composition='EU party',
          progress=os.path.join(TMP, 'progress_eu.json'))
json.dump(eu, open(os.path.join(TMP, 'settings_eu.json'), 'w'))
mgr3 = ml_core.Manager(os.path.join(TMP, 'settings_eu.json'), log=lambda m: None)
g = mgr3.groups[0]
check([m.build for m in g.members] == ['eu_warrior', 'eu_cleric', 'eu_bard', 'eu_wizard'],
      'composition gives every slot its build (leader = warrior), csv "build" column overrides')
mgr3._begin(g, 0)
w = json.load(open(os.path.join(cfg, 'Elysian_eu01.json')))
c = json.load(open(os.path.join(cfg, 'Elysian_eu02.Lv091-100.json')))['Skills']
check(list(w['Loop']['Script']) == ['Jangan gate'], 'an EU group also levels alone at the solo spot only')
check(w['Auto Mastery']['Mastery'] == ['Warrior', 'Cleric', 'Job Skills'] and
      w['Auto Mastery']['Skill'][-1] == 'Job - Trader Pouch' and c['Party Buffs']['Str'] == ['eu01', 'eu03', 'eu04'],
      'masteries from the build, then Job Skills + Trader Pouch; cleric buffs the whole party')
w2 = json.load(open(os.path.join(cfg, 'Elysian_eu01.Lv041-050.json')))
check(w2['Auto Mastery']['Mastery'].count('Job Skills') == 1, 'bracket profiles too, Job Skills only once')
check(w['Scrolls']['Speed'] and not w['Scrolls']['EXP Increase'] and w['AutoPotions']['UseSpeedDrug'],
      'EU characters: only the speed scroll (tracing, lowest < 52), and keep speed drugs')
wb = json.load(open(os.path.join(cfg, 'Elysian_eu01.Lv091-100.json')))['Skills']['Party Buffs']
check(wb.get('Pain Quota') == ['eu04'], 'warrior Pain Quota only on the damage dealers')
grp = json.load(open(os.path.join(cfg, 'MassLeveler', 'groups', g.gid + '.json')))
check(grp['builds']['eu_wizard']['create']['weapon'] == 'ITEM_EU_TSTAFF_01_A_DEF', 'EU characters are created with their own items')
mgr3.save_settings({'composition': 'CH nukers'})
check(mgr3.groups[0].members[0].build == 'eu_warrior', 'a running group keeps its builds when the settings change')
g.state = 'stopped'
mgr3.save_settings({'composition': 'CH nukers'})
check(mgr3.groups[0].members[3].build == 'eu_wizard' and mgr3.groups[0].members[0].build == 'ch_nuker',
      'changing the composition in the settings re-assigns the builds')

# skip / active from the dashboard: a skipped character is left out, a skipped group is never started
sk = dict(settings, progress=os.path.join(TMP, 'progress_skip.json'))
json.dump(sk, open(os.path.join(TMP, 'settings_skip.json'), 'w'))
mgr4 = ml_core.Manager(os.path.join(TMP, 'settings_skip.json'), log=lambda m: None)
g1, g2 = mgr4.groups
mgr4.set_member_skip(g1, g1.all_members[0], True)
mgr4.set_group_skip(g2, True)
check([m.character for m in g1.members] == ['fnuk02', 'fnuk03'] and g1.leader.role == 'leader'
      and g1.all_members[0].role == 'skipped', 'an unticked character leaves the group, the next one leads')
check(g2.state == 'skipped', 'a skipped group shows as skipped')
mgr4.reload()
g1, g2 = mgr4.groups
check(g2.state == 'skipped' and [m.character for m in g1.members] == ['fnuk02', 'fnuk03'],
      'skips are saved and come back after a reload')
check(g1.all_members[0].build == 'ch_nuker' and len(g1.all_members) == 3, 'the skipped character stays listed')
mgr4._begin(g1, 0)
l2 = json.load(open(os.path.join(cfg, 'Elysian_fnuk02.json')))
check(l2['Party']['AutoInviteParty'] and l2['Party']['InviteList'] == ['fnuk03'], 'the party is made without the skipped one')
mgr4.kill_group(g1, 'stopped')
mgr4.tick()
check(g2.state == 'skipped', 'the loop never starts a skipped group')
mgr4.set_group_skip(g2, False)
check(g2.state == 'pending' and 'G002' not in mgr4.skip['groups'], 'ticking the group makes it pending again')
for m in g1.all_members:
    mgr4.set_member_skip(g1, m, True)
check(g1.state == 'stopped' and not g1.members, 'unticking every character empties the group')
mgr4.start_group_now(g1)
check(g1.state == 'stopped', 'a group with nobody ticked is not started')

# Mark finished: green for good, never started, kept across reloads whatever the level
mf = dict(settings, progress=os.path.join(TMP, 'progress_mf.json'), skip=os.path.join(TMP, 'skip_mf.json'))
json.dump(mf, open(os.path.join(TMP, 'settings_mf.json'), 'w'))
mgrF = ml_core.Manager(os.path.join(TMP, 'settings_mf.json'), log=lambda m: None)
gA = mgrF.groups[0]
mgrF.mark_finished(gA)
check(gA.state == 'done' and 'by hand' in gA.message, 'Mark finished: the group is done')
mgrF.reload()
gA = mgrF.groups[0]
check(gA.state == 'done', 'a group marked finished stays done after a reload (characters below final level)')
mgrF.tick()
check(gA.state == 'done' and not any(m.proc for m in gA.members), 'the loop never starts a group marked finished')

# Stop all, then Start: the stopped group comes back, not the next one; Restart bots closes and reopens a group
rs = dict(settings, progress=os.path.join(TMP, 'progress_rs.json'), skip=os.path.join(TMP, 'skip_rs.json'),
          max_parallel_groups=1)
json.dump(rs, open(os.path.join(TMP, 'settings_rs.json'), 'w'))
mgr5 = ml_core.Manager(os.path.join(TMP, 'settings_rs.json'), log=logs.append)
mgr5.running = True
mgr5._begin(mgr5.groups[0], 0)
mgr5.groups[0].state = 'running'
mgr5.kill_group(mgr5.groups[0], 'stopped')          # what Stop all does
mgr5.tick()
check(mgr5.groups[0].state == 'launching' and mgr5.groups[1].state == 'pending',
      'Start after Stop all brings the stopped group back instead of the next one')
mgr5.groups[0].state = 'running'
m0 = mgr5.groups[0].members[0]
import subprocess  # noqa: E402
m0.proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
old_proc = m0.proc
check(mgr5.restart_group(mgr5.groups[0]) and old_proc.wait(10) is not None and m0.proc is None
      and mgr5.groups[0].state == 'launching' and mgr5.groups[0].slot == 0,
      'Restart bots closes the bots and launches the same group again on its slot')
mgr5.kill_group(mgr5.groups[0], 'done')
mgr5.tick()
check(mgr5.groups[1].state == 'launching', 'the next group starts when the current one is done')
mgr5.kill_group(mgr5.groups[1], 'stopped')

# the dashboard is closed ("leave the bots running") and opened again: it takes the running bots over
os.environ.pop('CRASH_ONCE', None)
os.environ.pop('STUCK_ONCE', None)
os.environ.pop('DISCONNECT_ONCE', None)
ra = dict(settings, progress=os.path.join(TMP, 'progress_ra.json'), skip=os.path.join(TMP, 'skip_ra.json'),
          max_parallel_groups=1, final_level=100000, launch_delay_seconds=0.1)
ra_path = os.path.join(TMP, 'settings_ra.json')
json.dump(ra, open(ra_path, 'w'))
mgr6 = ml_core.Manager(ra_path, log=lambda m: None)
mgr6.start()
deadline = time.time() + 40
while time.time() < deadline and not (mgr6.groups[0].state == 'running' and all(
        m.status.get('state') == 'leveling' for m in mgr6.groups[0].members)):
    time.sleep(0.3)
pids = {m.character: m.proc.pid for m in mgr6.groups[0].members}
mgr6.shutdown(kill=False)
mgr6.thread.join(5)
lb = []
mgr7 = ml_core.Manager(ra_path, log=lb.append)
g = mgr7.groups[0]
check(g.state == 'running' and g.slot == 0 and {m.character: m.proc.pid for m in g.members} == pids
      and mgr7.groups[1].state == 'pending', 'a reopened dashboard takes over the bots that are still running')
mgr7.tick()
time.sleep(1)
mgr7.tick()
check(not any(l.startswith('launch ') for l in lb) and g.state == 'running', 'and does not launch them again')
victim = g.members[1]
ml_core.kill_tree(victim.proc.pid)
time.sleep(0.5)
mgr7.tick()
check(any(l.startswith('%s closed - restarting' % victim.character) for l in lb),
      'a taken-over bot that closes is restarted as usual')
held = g.members[2]
mgr7.terminate([held])
time.sleep(0.5)
mgr7.tick()
check(held.proc is None and not any(l.startswith('%s closed - restarting' % held.character) for l in lb),
      'Terminate closes a bot and it stays closed')
mgr7.restart_member(g, held)
check(held.proc is not None and not held.held, 'Restart character opens a terminated bot again')
mgr7.kill_group(g, 'stopped')
f = ml_core.phbot_fields('start_trace', player='Death')
check(f[-1] == (0x429, b'\x01') and f[0][1].startswith(b'Death\0') and
      ml_core.phbot_fields('arena', kind='party', mode='points') == [(0x72c, (0x20 | 0x04).to_bytes(4, 'little'))],
      'phBot Manager commands write the documented fields, trigger last')
mgr8 = ml_core.Manager(ra_path, log=lambda m: None)
check(mgr8.groups[0].state == 'pending', 'closed bots are not taken over')

# training spots: manual first, then learned from the bots, then the plan's, then its alternatives
sp = dict(settings, progress=os.path.join(TMP, 'progress_sp.json'), skip=os.path.join(TMP, 'skip_sp.json'),
          custom_spots=os.path.join(TMP, 'custom.json'), bad_spots=os.path.join(TMP, 'bad.json'),
          escort_solo_spot=None, plans=OLD_PLAN)        # without a solo spot: the old level plan and its spots
json.dump(sp, open(os.path.join(TMP, 'settings_sp.json'), 'w'))
mgrS = ml_core.Manager(os.path.join(TMP, 'settings_sp.json'), log=lambda m: None)
gS = mgrS.groups[0]
a1 = mgrS.plans['CH'][0]
srcs = [s['src'] for s in mgrS.plan_for(gS)[0]['spots']]
check(srcs[0] == 'plan' and srcs[1:] == ['alt'] * len(a1['alternatives']), 'an area has the plan spot + its alternatives')
os.makedirs(os.path.join(cfg, 'MassLeveler', 'spawns'), exist_ok=True)
lx, ly = a1['x'] + 400, a1['y'] + 400
json.dump({'1|1': {'x': lx, 'y': ly, 'region': a1['region'], 'z': 0, 'mobs': {'MOB_TEST_LV3': 40, 'MOB_TEST_LV60': 90}}},
          open(os.path.join(cfg, 'MassLeveler', 'spawns', 'fnuk01.json'), 'w'))
ml_core._monster_levels[PHBOT] = {'MOB_TEST_LV3': a1['monster_level'], 'MOB_TEST_LV60': 60}
area1 = mgrS.plan_for(gS)[0]
check(area1['spots'][0]['src'] == 'learned' and area1['spots'][0]['seen'] == 40 and area1['x'] == lx,
      'a spot where the bots saw many monsters of the right level comes first (wrong levels are not counted)')
mgrS.add_custom_spot({'region': a1['region'], 'x': a1['x'] - 500, 'y': a1['y'], 'z': 0}, a1['min_level'], a1['max_level'])
check(mgrS.plan_for(gS)[0]['spots'][0]['src'] == 'manual', 'a spot set by hand comes before everything')
mgrS._begin(gS, 0)
json.dump({'name': gS.leader.character, 'area': a1['name'], 'state': 'leveling'},
          open(os.path.join(cfg, 'MassLeveler', 'status', gS.leader.character + '.json'), 'w'))
spots = json.load(open(os.path.join(cfg, 'MassLeveler', 'groups', gS.gid + '.json')))['areas'][0]['spots']
mgrS.next_spot(gS)
sel = json.load(open(mgrS.spot_file(gS)))
check(sel['area'] == a1['name'] and (sel['x'], sel['y']) == (spots[1]['x'], spots[1]['y']), 'Next spot moves the group')
mgrS.mark_spot_bad(gS)
spots2 = json.load(open(os.path.join(cfg, 'MassLeveler', 'groups', gS.gid + '.json')))['areas'][0]['spots']
check(spots2[0]['src'] == 'learned' and not json.load(open(os.path.join(TMP, 'custom.json'))),
      'a spot marked bad is never used again (a manual one is deleted)')
# phBot went back to the profile's area (stopped + started from outside): the manager puts it back on the spot
lead_m = gS.leader
want = [spots2[0]['x'], spots2[0]['y']]
st_lead = {'spot': want, 'area': a1['name'], 'state': 'leveling'}
writes = []
real_status, real_write = ml_core.phbot_status, ml_core.phbot_shm_write
live = {'in_game': True, 'dead': False, 'botting': True, 'training': (1, want[0] + 300, want[1], 40.0)}
ml_core.phbot_status = lambda pid: dict(live)
ml_core.phbot_shm_write = lambda pid, fields: writes.append(fields) or True
mgrS.bot_pid = lambda m: 4242
cmd_file = os.path.join(cfg, 'MassLeveler', 'commands', lead_m.character + '.json')
last_cmd = lambda: (json.load(open(cmd_file)) if os.path.exists(cmd_file) else {}).get('cmd')
if os.path.exists(cmd_file):
    os.remove(cmd_file)
t0 = time.time()
mgrS._keep_spot(gS, lead_m, st_lead, t0)
check(last_cmd() is None, 'a short difference is left alone (the plugin may be moving to a new spot right now)')
mgrS._keep_spot(gS, lead_m, st_lead, t0 + 9)
check(last_cmd() == 'area' and not writes,
      'phBot on another area for 8 s -> the plugin sets the spot again (the manager never moves phBot itself)')
os.remove(cmd_file)
live.update(training=(1, want[0], want[1], 40.0), botting=False)
st_lead['bot_started'] = True
mgrS._keep_spot(gS, lead_m, st_lead, t0 + 50)
mgrS._keep_spot(gS, lead_m, st_lead, t0 + 71)
check(last_cmd() == 'area', 'phBot stopped for 20 s while leveling ("too far away") -> started again by the plugin')
ml_core.phbot_status, ml_core.phbot_shm_write = real_status, real_write

mgrS.unmark_bad()
mgrS.first_spot(gS)
check(not os.path.exists(mgrS.spot_file(gS)), 'back to the first spot')
mgrS.kill_group(gS, 'stopped')

# level 80+ spot from the settings dialog: nearest reverse point from phBot's game database
RD = os.path.join(TMP, 'rev_phbot')
os.makedirs(os.path.join(RD, 'Data'))
con = sqlite3.connect(os.path.join(RD, 'Data', '111.db3'))
con.execute('create table reverseteleport (id INTEGER, zone TEXT, name TEXT)')
con.execute('create table zones (id INTEGER, location TEXT, name TEXT)')
con.executemany('insert into reverseteleport values (?, ?, ?)', [(1, 'a', 'Heart Peak'), (2, 'b', 'Ong Habitat')])
con.executemany('insert into zones values (?, ?, ?)', [(23664, 'Roc', 'Peak of Heart'), (26219, 'Ca', 'Ong Habitat')])
con.commit()
con.close()
found = ml_core.nearest_reverse(RD, {'region': 23664, 'x': -4303, 'y': 82})
check(found and found[0] == 'Heart Peak', 'nearest reverse to a Wing Peak spot: Heart Peak (zone "Peak of Heart")')
check(ml_core.nearest_reverse(RD, {'region': 99, 'x': 0, 'y': 0}) is None, 'region of another server: none')
check(ml_core.exact_reverse(RD, 'heart  peak') == 'Heart Peak', 'a typed reverse name gets the game spelling')
check(ml_core.exact_reverse(RD, 'ong') == 'Ong Habitat', 'one word that only one reverse point has: that one')
check(ml_core.exact_reverse(RD, 'Echo valley') is None, 'a name not in the game: none')
check(ml_core.exact_reverse(os.path.join(TMP, 'nothing'), 'echo valley') == 'echo valley', 'no game data: as typed')
FREE2 = [{'name': 'Free', 'min_level': 80, 'max_level': 94, 'free': True, 'attack': True},
         {'name': 'Free 95', 'min_level': 95, 'max_level': 100, 'free': True, 'attack': True}]
WP = {'region': 23664, 'x': -4303, 'y': 82, 'z': 4209, 'reverse': 'Heart Peak'}
stg = ml_core.with_high_spots(FREE2, {'80': WP})
check(not stg[0].get('free') and stg[0]['reverse'] == 'Heart Peak' and stg[0]['x'] == -4303.0 and stg[0]['attack'],
      'a set 80+ spot replaces the 80 free stage (reverse, point, attack)')
check(stg[1].get('free') and stg[0]['user_spot'] and stg[1]['user_spot'], '95 stage without a spot: stays free')
stg = ml_core.with_high_spots(FREE2, {'80': WP, '95': dict(WP, x=100, reverse='Ancient Remains')})
check(stg[0]['reverse'] == 'Heart Peak' and stg[1]['reverse'] == 'Ancient Remains' and stg[1]['x'] == 100.0,
      'two spots: 80 and 95 each get their own')
check(ml_core.with_high_spots([{'name': 'Free', 'min_level': 80, 'free': True}], None)[0]['free'], 'no spot set: stays free')
check(ml_core.high_spots({'escort_high_spot': WP}) == {'80': WP}, 'old single escort_high_spot = the 80 spot')
check(ml_core.high_spots({'escort_high_spot': WP, 'escort_high_spots': {'80': None}}) == {'80': None},
      'new setting wins over the old one')

# Standard mode: a plain phBot manager (no -mlgroup, phBot's own status, its own ticks / finished / Auto start)
std = dict(settings, standard={'group_size': 2, 'launch_delay_seconds': 0.3, 'disconnect_restart_seconds': 0.5,
                               'progress': os.path.join(TMP, 'std_progress.json'),
                               'skip': os.path.join(TMP, 'std_skip.json'),
                               'auto_start': os.path.join(TMP, 'std_auto.json')})
json.dump(std, open(os.path.join(TMP, 'settings_std.json'), 'w'))
json.dump({'fnuk05': 'finished', 'fnuk06': 'finished'}, open(os.path.join(TMP, 'std_progress.json'), 'w'))
json.dump(['fnuk01'], open(os.path.join(TMP, 'std_auto.json'), 'w'))
os.environ['STD_CRASH_ONCE'] = 'fnuk01,fnuk03'
slog = []
mgrD = ml_core.Manager(os.path.join(TMP, 'settings_std.json'), log=slog.append, mode='standard')
D = {g.gid: g for g in mgrD.groups}
ch = lambda name: next(m for g in mgrD.groups for m in g.all_members if m.character == name)
check(sorted(D) == ['G001', 'G002', 'G003'] and D['G003'].state == 'done',
      'Standard: groups of its own size, its own finished marks (G003 green)')
mass_skip = lambda: open(settings['skip']).read() if os.path.exists(settings['skip']) else ''
before = mass_skip()
mgrD.set_member_skip(D['G001'], ch('fnuk02'), True)
mgrD.set_member_skip(D['G002'], ch('fnuk04'), True)
check(json.load(open(os.path.join(TMP, 'std_skip.json')))['characters'] == ['fnuk02', 'fnuk04'] and mass_skip() == before,
      'Standard ticks are its own (the Mass Leveler skip file is untouched)')
cmd = ml_core.build_command(mgrD.s, D['G001'], D['G001'].members[0], None, standard=True)
check(not any(a.startswith('-mlgroup') for a in cmd) and '--minimize' in cmd,
      'Standard command: no -mlgroup (the plugin stays idle), the first character is no leader')
mgrD.start()
deadline = time.time() + 60
while time.time() < deadline and not (ch('fnuk01').status.get('state') == 'botting' and
                                     any('fnuk01 closed - Auto start' in l for l in slog) and 'fnuk03 closed' in slog):
    time.sleep(0.5)
st1 = ch('fnuk01').status
check(st1.get('state') == 'botting' and st1.get('level') == 50 and st1.get('exp_pct') == '25.00' and
      st1.get('zone') == 'Jangan' and st1.get('area') == '6440, 838',
      'opened, logged in, bot started; the numbers come from phBot\'s own status: %s' % st1)
check(any('fnuk01 closed - Auto start opens it again' in l for l in slog), 'an Auto start bot that closed opens again')
check('fnuk03 closed' in slog and not ml_core.Manager._alive(ch('fnuk03')) and
      ch('fnuk03').status.get('state') == 'closed' and D['G002'].state == 'stopped',
      'a bot without Auto start stays closed (its group: stopped)')
check(not ml_core.Manager._alive(ch('fnuk02')) and not any(l.startswith(('launch fnuk05', 'launch fnuk06')) for l in slog),
      'unticked and finished characters are not opened')
args1 = json.load(open(os.path.join(PHBOT, 'standard_args_fnuk01.json')))
check(not any(a.startswith('-mlgroup') for a in args1) and args1[args1.index('--character') + 1] == 'fnuk01',
      'phBot got no -mlgroup')
# another window finds the open bot by its name in phBot, and does not open it twice
slog2 = []
mgrD2 = ml_core.Manager(os.path.join(TMP, 'settings_std.json'), log=slog2.append, mode='standard')
lead2 = next(g for g in mgrD2.groups if g.gid == 'G001').members[0]
check(next(g for g in mgrD2.groups if g.gid == 'G001').state == 'running' and ml_core.Manager._alive(lead2)
      and lead2.start_sent, 'bots still open from before are taken over, their bot left as it is')
lead2.proc = None
mgrD2.launch(next(g for g in mgrD2.groups if g.gid == 'G001'), lead2)
check(ml_core.Manager._alive(lead2) and any('already open in phBot' in l for l in slog2) and
      not any(l.startswith('launch fnuk01') for l in slog2), 'a character open in phBot is not opened twice')
mgrD.save_settings({'launch_delay_seconds': 0.4})
raw = json.load(open(os.path.join(TMP, 'settings_std.json')))
check(raw['standard']['launch_delay_seconds'] == 0.4 and raw['launch_delay_seconds'] == 0.2 and
      ml_core.load_settings(os.path.join(TMP, 'settings_std.json'))['launch_delay_seconds'] == 0.2,
      'Standard settings are saved apart (Mass Leveler keeps its own)')
launches = sum(1 for l in slog if l.startswith('launch fnuk01'))
mgrD.kill_group(next(g for g in mgrD.groups if g.gid == 'G001'), 'stopped')
time.sleep(5)
check(not ml_core.Manager._alive(ch('fnuk01')) and sum(1 for l in slog if l.startswith('launch fnuk01')) == launches,
      'closed from here: not opened again, even with Auto start')
mgrD.shutdown(kill=True)

# the escort is the party leader: groups of 7, members accept only from it, it invites them (no union)
with open(os.path.join(TMP, 'accounts_esc.csv'), 'w', encoding='utf-8') as f:
    f.write('username,password,passcode,character,group,notes,escort\nesc,pw,,wiz01,,,yes\n')
    for i in range(1, 17):
        f.write('u%02d,pw,,,,,\n' % i)
esc_s = dict(settings, group_size=8, accounts_csv=os.path.join(TMP, 'accounts_esc.csv'),
             progress=os.path.join(TMP, 'progress_esc.json'), skip=os.path.join(TMP, 'skip_esc.json'))
json.dump(esc_s, open(os.path.join(TMP, 'settings_esc.json'), 'w'))
esc_prof = dict(old, Party={'AutoInviteParty': False, 'InviteList': []},
                **{'Union Party': {'AutoUnionPartyInvite': True, 'UnionPartyInvite': ['x']}})
json.dump(esc_prof, open(os.path.join(cfg, 'Elysian_wiz01.json'), 'w'))
mgrE = ml_core.Manager(os.path.join(TMP, 'settings_esc.json'), log=lambda m: None)
check([(g.gid, len(g.members)) for g in mgrE.groups] == [('ESCORT', 1), ('G001', 7), ('G002', 7), ('G003', 2)],
      'with the escort a group is 7 (party of 8 with the escort): %s' % [(g.gid, len(g.members)) for g in mgrE.groups])
gE = mgrE.groups[1]
mgrE.files.write_profile(gE, gE.members[0], mgrE.templates['ch_nuker'], mgrE.plan_for(gE))
pE = json.load(open(os.path.join(cfg, 'Elysian_%s.json' % gE.members[0].character)))
check(not pE['Party']['AutoInviteParty'] and pE['Party']['AutoAcceptParty'] and pE['Party']['InviteList'] == ['wiz01']
      and pE['Party']['Party Leader Names'] == ['wiz01'] and not pE['Union Party']['AcceptUnionPartyInvites'],
      'even the first character of a group only accepts the escort\'s party, no union')
mgrE.update_escort_party()
eP = json.load(open(os.path.join(cfg, 'Elysian_wiz01.json')))
check(eP['Party']['AutoInviteParty'] and eP['Party']['InviteList'] == [m.character for g in mgrE.groups[1:] for m in g.members]
      and not eP['Party']['AutoAcceptParty'] and not eP['Union Party']['AutoUnionPartyInvite'],
      'the escort invites every group character into its party, union invites off')
mgrES = ml_core.Manager(os.path.join(TMP, 'settings_esc.json'), log=lambda m: None, mode='standard')
check([len(g.members) for g in mgrES.groups if not g.escort] == [8, 8], 'Standard mode keeps groups of 8')

shutil.rmtree(TMP, ignore_errors=True)
print('\n%d failure(s)' % len(failures))
sys.exit(1 if failures else 0)
