"""Stand-in for phBot.exe used by tools/test_manager.py.

Behaves like phBot + the MassLeveler plugin: gains a level every TICK, waits for the group's lowest
member, then reports 'done'. A character listed in CRASH_ONCE exits once to test restarts.
Without -mlgroup (Standard mode) it is a plain phBot: its status is a shared memory segment like phBot's own.
"""
import json
import os
import sys
import time

TICK = 0.25
args = sys.argv[1:]
char = args[args.index('--character') + 1]


def standard_bot():
    """phBot's status segment "phBot_<pid>" (what the manager reads): logged in after 0.6 s, Start / Stop Bot
    switch botting; STD_CRASH_ONCE characters exit once after 1.5 s"""
    import ctypes
    import hashlib
    import struct
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    k32.CreateFileMappingW.restype = wintypes.HANDLE
    k32.CreateFileMappingW.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                       wintypes.DWORD, wintypes.LPCWSTR]
    k32.MapViewOfFile.restype = ctypes.c_void_p
    k32.MapViewOfFile.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]
    key = 'phBot_%d' % os.getpid()
    name = 'qipc_sharedmemory_' + ''.join(c for c in key if c.isascii() and c.isalpha()) + \
        hashlib.sha1(key.encode()).hexdigest()
    h = k32.CreateFileMappingW(wintypes.HANDLE(-1), None, 0x04, 0, 0x790, name)       # PAGE_READWRITE
    view = k32.MapViewOfFile(h, 0xF001F, 0, 0, 0x790)                                 # FILE_MAP_ALL_ACCESS
    put = lambda off, data: ctypes.memmove(view + off, data, len(data))
    flag = lambda off: ctypes.string_at(view + off, 1)[0]
    json.dump(args, open('standard_args_%s.json' % char, 'w'))
    put(0x037, char.encode() + b'\0')
    put(0x137, bytes([50]))
    put(0x15c, struct.pack('<QQ', 250, 1000))                       # exp, exp of the level
    put(0x4ac, b'Jangan\0')
    put(0x773, struct.pack('<hfff', 0, 6440, 838, 0))               # training area
    crashed = 'std_crashed_' + char
    crash = char in os.environ.get('STD_CRASH_ONCE', '').split(',') and not os.path.exists(crashed)
    t0 = time.time()
    while True:
        put(0x02d, b'\x01' if time.time() - t0 > 0.3 else b'\x00')     # connected
        put(0x02f, b'\x01' if time.time() - t0 > 0.6 else b'\x00')     # in the game
        if flag(0x190):                                                 # Start Bot
            put(0x190, b'\x00')
            put(0x035, b'\x01')
        if flag(0x191):                                                 # Stop Bot
            put(0x191, b'\x00')
            put(0x035, b'\x00')
        if crash and time.time() - t0 > 1.5:
            open(crashed, 'w').close()
            os._exit(3)
        time.sleep(0.1)


if not any(a.startswith('-mlgroup=') for a in args):
    standard_bot()
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
