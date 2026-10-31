"""Map data for the Standard mode's Map tab (no GUI here): phBot's minimap tiles, NPCs, teleporters and area names
from phBot's game database.

World coordinates (what phBot shows): x = (rx - 135) * 192 + local x / 10, y = (ry - 92) * 192 + local y / 10,
region = ry << 8 | rx. Every region is one 256 x 256 tile <phBot>/minimap/<rx>x<ry>.jpg, north up.
Caves (region >= 0x8000) have other tiles (minimap/d) and are not drawn.
"""
import glob
import json
import math
import os
import sqlite3

REGION = 192.0          # game units per region
TILE = 256              # pixels per tile


def region_of(x, y):
    return (int(math.floor(y / REGION)) + 92) << 8 | (int(math.floor(x / REGION)) + 135)


def tile_of(x, y):
    """(rx, ry) of the tile a world point is on"""
    return int(math.floor(x / REGION)) + 135, int(math.floor(y / REGION)) + 92


def tile_origin(rx, ry):
    """world (x, y) of a tile's south-west corner (bottom left of the image)"""
    return (rx - 135) * REGION, (ry - 92) * REGION


def to_world(region, lx, ly):
    """a region's local x / y (the game's tenths) -> world x / y"""
    return ((region & 0xFF) - 135) * REGION + lx / 10.0, ((region >> 8) - 92) * REGION + ly / 10.0


def game_db(phbot_dir, server):
    """phBot's game database of this server (Data/<id>.db3: its 'data' table has the client path of vSRO.json)"""
    paths = [p for p in glob.glob(os.path.join(phbot_dir, 'Data', '*.db3')) if os.path.basename(p).lower() != 'login.db3']
    try:
        cfg = json.load(open(os.path.join(phbot_dir, 'vSRO.json'), encoding='utf-8'))
        want = next((v.get('path') for k, v in cfg.items() if isinstance(v, dict) and k.lower() == server.lower()), None)
    except (OSError, ValueError):
        want = None
    norm = lambda p: os.path.normcase(os.path.normpath(p or ''))
    for p in paths:
        try:
            con = sqlite3.connect('file:%s?mode=ro' % p, uri=True)
            try:
                path = dict(con.execute('select k, v from data').fetchall()).get('path')
            finally:
                con.close()
        except sqlite3.Error:
            continue
        if want and norm(path) == norm(want):
            return p
    return paths[0] if len(paths) == 1 else None


class MapData:
    def __init__(self, phbot_dir, server):
        self.tiles = os.path.join(phbot_dir, 'minimap')
        self.npcs, self.teleports, self.zones = [], [], {}
        self.db = game_db(phbot_dir, server)
        if not self.db:
            return
        con = sqlite3.connect('file:%s?mode=ro' % self.db, uri=True)
        try:
            for nid, region, lx, z, ly in con.execute('select id, region, x, z, y from npcpos where region < 32768'):
                row = con.execute('select servername, name from monsters where id = ?', (nid,)).fetchone()
                if row:
                    x, y = to_world(region, lx, ly)
                    self.npcs.append({'servername': row[0], 'name': row[1] or row[0], 'x': x, 'y': y, 'z': z})
            rows = con.execute('select id, servername, name, destination, region, pos_x, pos_y from teleport').fetchall()
            names = {r[0]: r[2] for r in rows}
            by_id = {}
            for tid, servername, name, dest, region, px, py in rows:
                t = by_id.setdefault(tid, {'name': name, 'servername': servername, 'x': px, 'y': py, 'dests': []})
                if dest in names and names[dest] not in t['dests'] and names[dest] != name:
                    t['dests'].append(names[dest])
            self.teleports = [t for t in by_id.values() if t['dests'] and region_of(t['x'], t['y']) < 32768]
            self.zones = dict(con.execute('select id, name from zones where id > 0 and id < 32768').fetchall())
        finally:
            con.close()

    def tile_path(self, rx, ry):
        p = os.path.join(self.tiles, '%dx%d.jpg' % (rx, ry))
        return p if os.path.isfile(p) else None

    def zone(self, x, y):
        return self.zones.get(region_of(x, y), '')

    @staticmethod
    def _near(items, x, y, within):
        found = [(math.hypot(i['x'] - x, i['y'] - y), i) for i in items]
        return [i for d, i in sorted(found, key=lambda f: f[0]) if d <= within]

    def npcs_near(self, x, y, within):
        return self._near(self.npcs, x, y, within)

    def teleports_near(self, x, y, within):
        return self._near(self.teleports, x, y, within)

    def storage_near(self, x, y, within=3000):
        """the nearest storage keeper (NPC ..._WAREHOUSE...)"""
        found = self._near([n for n in self.npcs if 'WAREHOUSE' in n['servername'].upper()], x, y, within)
        return found[0] if found else None
