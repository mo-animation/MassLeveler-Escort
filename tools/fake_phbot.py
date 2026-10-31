"""Stand-in for phBot.exe used by tools/test_manager.py.

Behaves like phBot + the MassLeveler plugin: gains a level every TICK, waits for the group's lowest
member, then reports 'done'. A character listed in CRASH_ONCE exits once to test restarts.
"""
import json
import os
import sys
import time

TICK = 0.25
args = sys.argv[1:]
char = args[args.index('--character') + 1]
gid = next(a.split('=', 1)[1] for a in args if a.startswith('-mlgroup='))
shared = os.path.join(os.getcwd(), 'Config', 'MassLeveler')
group = json.load(open(os.path.join(shared, 'groups', gid + '.json')))
final = group['final_level']
crash_flag = os.path.join(shared, 'crashed_' + char)
crash_once = os.environ.get('CRASH_ONCE', '').split(',')


def read(name):
    try:
        return json.load(open(os.path.join(shared, 'status', name + '.json')))
    except Exception:
        return {}


me = read(char)
level = int(me.get('level', 1))
state = 'leveling'
t0 = time.time()
stuck_flag = os.path.join(shared, 'stuck_' + char)
stuck = char in os.environ.get('STUCK_ONCE', '').split(',') and not os.path.exists(stuck_flag)
if stuck:
    open(stuck_flag, 'w').close()
disc_flag = os.path.join(shared, 'disconnected_' + char)
disc = char in os.environ.get('DISCONNECT_ONCE', '').split(',') and not os.path.exists(disc_flag)
while True:
    others = [int(read(n).get('level', 1)) for n in group['members'] if n != char]
    gmin = min([level] + others)
    if char in crash_once and not os.path.exists(crash_flag) and time.time() - t0 > 1:
        open(crash_flag, 'w').close()
        sys.exit(3)
    if disc and time.time() - t0 > 1:
        open(disc_flag, 'w').close()
        state = 'disconnected'         # stays out of the game this run (phBot did not relog by itself)
    elif stuck:
        state = 'waiting login'        # never reaches the game this run; the manager must restart it
    elif gmin >= final:
        state = 'done'
    elif level < final:
        level += 1
    st = {'name': char, 'group': gid, 'pid': os.getpid(), 'ts': time.time(), 'state': state, 'message': '',
          'level': level, 'area': 'test', 'args': args}
    tmp = os.path.join(shared, 'status', char + '.json.tmp')
    json.dump(st, open(tmp, 'w'))
    for attempt in range(10):
        try:
            os.replace(tmp, os.path.join(shared, 'status', char + '.json'))
            break
        except PermissionError:
            time.sleep(0.05)
    time.sleep(TICK)
