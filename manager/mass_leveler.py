"""MassLeveler desktop dashboard.

Run:  py manager/mass_leveler.py            (or double-click MassLeveler.bat)
"""
import ctypes
import importlib.util
import math
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ml_core  # noqa: E402
import ml_map  # noqa: E402

COLUMNS = ('level', 'exp', 'zone', 'area', 'state')
HEADINGS = {'level': 'Lv', 'exp': 'EXP %', 'zone': 'Zone', 'area': 'Training area', 'state': 'State'}
WIDTHS = {'level': 40, 'exp': 60, 'zone': 130, 'area': 230, 'state': 380}     # State takes the rest of the width
MEMBER_MODES = ('client', 'clientless', 'client_then_clientless')
RUNNING = ('launching', 'running')
STATS_COLUMNS = (('level', 'Lv', 40), ('exp_pct', 'EXP %', 60), ('exp_hour', 'EXP %/h', 70),
                 ('time_to_level', 'To level', 70), ('kills', 'Kills', 60), ('deaths', 'Deaths', 55),
                 ('drops', 'Drops', 55), ('rare_drops', 'Rare', 45), ('gold', 'Gold', 90),
                 ('gold_per_loop', 'Gold/loop', 80), ('botting_time', 'Bot time', 70), ('state', 'State', 90),
                 ('zone', 'Zone', 150))


def names(members):
    return ', '.join(m.character for m in members)


def hours_text(h):
    return '-' if not h or h < 0 or h > 9999 else '%d:%02d' % (int(h), int(h * 60) % 60)


class PlanWindow:
    """the training plan of a group: every area with its spots, best first (manual, learned, plan, alternatives).
    Right-click a spot: delete it (manual) or mark it bad; the running groups get the change within seconds."""

    COLS = (('src', 'Source', 80), ('seen', 'Monsters seen', 100), ('x', 'X', 80), ('y', 'Y', 80),
            ('radius', 'Radius', 60), ('monster', 'Monster', 200))

    def __init__(self, app, g):
        self.app, self.g = app, g
        top = self.top = tk.Toplevel(app.root)
        top.title('Training plan - %s' % g.gid)
        top.geometry('900x620')
        dark_title(top)
        bar = ttk.Frame(top, padding=6)
        bar.pack(fill='x')
        ttk.Label(bar, foreground=DARK['dim'], text='> = the spot the group uses now.  Right-click a spot to delete '
                                                    'it (manual) or mark it bad.').pack(side='left')
        ttk.Button(bar, text='Clear bad marks', command=self.clear_bad).pack(side='right')
        ttk.Button(bar, text='Refresh', command=self.fill).pack(side='right', padx=4)
        self.tree = ttk.Treeview(top, columns=[c for c, _, _ in self.COLS])
        self.tree.heading('#0', text='Area / spot')
        self.tree.column('#0', width=260)
        for c, title, w in self.COLS:
            self.tree.heading(c, text=title)
            self.tree.column(c, width=w, anchor='w')
        sb = ttk.Scrollbar(top, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side='left', fill='both', expand=True, padx=(6, 0), pady=(0, 6))
        sb.pack(side='right', fill='y', pady=(0, 6))
        self.tree.tag_configure('now', foreground=DARK['done'])
        self.tree.tag_configure('area', font=('Segoe UI', 9, 'bold'))
        self.tree.bind('<Button-3>', self.on_menu)
        self.spots = {}
        self.fill()

    def fill(self):
        self.tree.delete(*self.tree.get_children())
        self.spots = {}
        cur_area, cur_spots, cur_i = self.app.mgr.group_area(self.g) if self.g.state in RUNNING else (None, [], 0)
        cur = cur_spots[cur_i] if cur_spots else None
        for ai, a in enumerate(self.app.mgr.plan_for(self.g)):
            here = cur_area is not None and cur_area['name'] == a['name']
            aid = self.tree.insert('', 'end', text=a['name'], open=here, tags=('area',),
                                   values=('%d spot(s)' % len(a['spots']), '', '', '', '', 'monster lv %s' % a.get('monster_level', '?')))
            alts = {(x['x'], x['y']): x for x in a.get('alternatives', [])}
            for si, s in enumerate(a['spots']):
                now = here and cur is not None and ml_core.near(s, cur, 5)
                monster = a['monster'] if s['src'] == 'plan' else alts.get((s['x'], s['y']), {}).get('monster', '')
                iid = self.tree.insert(aid, 'end', text=('> ' if now else '   ') + 'spot %d' % (si + 1),
                                       tags=('now',) if now else (),
                                       values=(s['src'], s.get('seen', ''), '%.0f' % s['x'], '%.0f' % s['y'],
                                               s['radius'], monster))
                self.spots[iid] = s
            if here:
                self.tree.see(aid)

    def on_menu(self, e):
        iid = self.tree.identify_row(e.y)
        s = self.spots.get(iid)
        if not s:
            return
        self.tree.selection_set(iid)
        menu = tk.Menu(self.top, tearoff=0)
        if s['src'] == 'manual':
            self.app.add_item(menu, 'Delete this manual spot', lambda: self.act(self.app.mgr.delete_custom_spot, s))
        self.app.add_item(menu, 'Mark this spot bad (never use it)', lambda: self.act(self.mark_bad, s))
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()

    def mark_bad(self, s):
        path = ml_core.path_in_root(self.app.mgr.s['bad_spots'])
        ml_core.write_json(path, (ml_core.read_json(path, []) or []) + [{'region': s['region'], 'x': s['x'], 'y': s['y'],
                                                                          'ts': time.time()}])
        if s['src'] == 'manual':
            self.app.mgr.delete_custom_spot(s)
        self.app.mgr.refresh_spots()

    def act(self, fn, s):
        fn(s)
        self.fill()

    def clear_bad(self):
        if messagebox.askyesno('Training plan', 'Use every spot marked bad again?', parent=self.top):
            self.app.mgr.unmark_bad()
            self.fill()


class StatsWindow:
    """phBot's live numbers (shared memory) for some characters, refreshed every 2 seconds"""

    def __init__(self, app, members):
        self.app, self.members = app, list(members)
        top = self.top = tk.Toplevel(app.root)
        top.title('Stats - ' + names(self.members))
        top.geometry('%dx%d' % (1100, 90 + 24 * len(self.members)))
        dark_title(top)
        self.tree = ttk.Treeview(top, columns=[c for c, _, _ in STATS_COLUMNS], selectmode='none')
        self.tree.heading('#0', text='Character')
        self.tree.column('#0', width=90)
        for c, title, w in STATS_COLUMNS:
            self.tree.heading(c, text=title)
            self.tree.column(c, width=w, anchor='w')
        self.tree.pack(fill='both', expand=True, padx=6, pady=6)
        for m in self.members:
            self.tree.insert('', 'end', iid=m.character, text=m.character)
        self.update()

    def update(self):
        if not self.top.winfo_exists():
            return
        for m in self.members:
            s = ml_core.phbot_status(self.app.mgr.bot_pid(m))
            if not s:
                vals = [''] * (len(STATS_COLUMNS) - 2) + ['not running', '']
            else:
                s = dict(s, exp_pct='%.2f' % (100.0 * s['exp'] / s['level_exp']) if s['level_exp'] else '',
                         exp_hour='%.0f' % s['exp_hour'], time_to_level=hours_text(s['time_to_level']),
                         gold='{:,}'.format(s['gold']), gold_per_loop='{:,}'.format(s['gold_per_loop']),
                         botting_time=hours_text(s['botting_time'] / 60.0),
                         state='dead' if s['dead'] else 'botting' if s['botting'] else 'tracing' if s['tracing']
                         else 'in game' if s['in_game'] else 'connected' if s['connected'] else 'offline')
                vals = [s[c] for c, _, _ in STATS_COLUMNS]
            self.tree.item(m.character, values=vals)
        self.top.after(2000, self.update)
DARK = {'bg': '#1e1e1e', 'panel': '#252526', 'field': '#2d2d30', 'line': '#3f3f46', 'fg': '#f0f0f0',
        'dim': '#8a8a8a', 'accent': '#0e639c', 'select': '#094771', 'hover': '#3e3e42', 'link': '#64b5f6',
        'done': '#81c784', 'bad': '#f14c4c', 'escort': '#f2cc60'}


def apply_dark(root):
    """black UI with white text: ttk widgets through the clam theme, classic tk widgets (menus, log, lists) by options"""
    c = DARK
    st = ttk.Style(root)
    st.theme_use('clam')
    st.configure('.', background=c['bg'], foreground=c['fg'], fieldbackground=c['field'], bordercolor=c['line'],
                 darkcolor=c['bg'], lightcolor=c['bg'], troughcolor=c['panel'], selectbackground=c['select'],
                 selectforeground='#ffffff', insertcolor=c['fg'], arrowcolor=c['fg'], focuscolor=c['accent'])
    st.map('.', foreground=[('disabled', c['dim'])])
    st.configure('TButton', background=c['field'], padding=(10, 2))
    st.map('TButton', background=[('pressed', c['accent']), ('active', c['hover'])])
    st.configure('Accent.TButton', background=c['accent'], bordercolor=c['accent'], foreground='#ffffff')
    st.map('Accent.TButton', background=[('pressed', c['select']), ('active', '#1177bb')])
    st.configure('Treeview', background=c['panel'], fieldbackground=c['panel'], foreground=c['fg'], rowheight=20)
    st.map('Treeview', background=[('selected', c['select'])], foreground=[('selected', '#ffffff')])
    st.configure('Treeview.Heading', background=c['field'], foreground=c['fg'], relief='flat')
    st.map('Treeview.Heading', background=[('active', c['hover'])])
    for w in ('TEntry', 'TCombobox', 'TSpinbox'):
        st.configure(w, fieldbackground=c['field'], background=c['field'], foreground=c['fg'])
        st.map(w, fieldbackground=[('readonly', c['field'])], foreground=[('readonly', c['fg'])],
               background=[('active', c['hover'])])
    st.map('TCheckbutton', background=[('active', c['bg'])])
    # check boxes drawn like the tick boxes of the group list
    root.box_images = box_image(False), box_image(True)     # a Tk image lives as long as a Python reference
    st.element_create('Box.indicator', 'image', root.box_images[0], ('selected', root.box_images[1]),
                      padding=(0, 0, 6, 0))
    st.layout('TCheckbutton', [('Checkbutton.padding', {'sticky': 'nswe', 'children': [
        ('Box.indicator', {'side': 'left', 'sticky': ''}),
        ('Checkbutton.focus', {'side': 'left', 'sticky': 'w', 'children': [('Checkbutton.label', {'sticky': 'nswe'})]})]})])
    for w in ('Vertical.TScrollbar', 'Horizontal.TScrollbar'):
        st.configure(w, background=c['field'], troughcolor=c['bg'], arrowcolor=c['fg'])
        st.map(w, background=[('active', c['hover'])])
    st.configure('TSeparator', background=c['line'])
    st.configure('TPanedwindow', background=c['line'])
    st.configure('TNotebook', background=c['bg'], bordercolor=c['line'], lightcolor=c['bg'], darkcolor=c['bg'])
    st.configure('TNotebook.Tab', background=c['field'], foreground='#bbbbbb', bordercolor=c['line'],
                 lightcolor=c['field'], darkcolor=c['field'], padding=(14, 3))
    st.map('TNotebook.Tab', background=[('selected', c['bg']), ('active', c['hover'])],
           foreground=[('selected', '#ffffff')], lightcolor=[('selected', c['accent'])])
    root.configure(bg=c['bg'])
    for key, val in (('*Background', c['bg']), ('*Foreground', c['fg']), ('*selectBackground', c['select']),
                     ('*selectForeground', '#ffffff'), ('*insertBackground', c['fg']),
                     ('*activeBackground', c['select']), ('*activeForeground', '#ffffff'),
                     ('*disabledForeground', c['dim']), ('*Menu.relief', 'flat'), ('*Menu.borderWidth', 1),
                     ('*TCombobox*Listbox.background', c['field']), ('*TCombobox*Listbox.foreground', c['fg'])):
        root.option_add(key, val)


def dark_title(win):
    """dark Windows title bar (Windows 10 1809+)"""
    if sys.platform != 'win32':
        return
    try:
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        on = ctypes.c_int(1)
        for attr in (20, 19):       # DWMWA_USE_IMMERSIVE_DARK_MODE (19 on older builds)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(on), ctypes.sizeof(on)) == 0:
                break
    except Exception:
        pass


def box_image(on):
    """16x16 tick box for the tree rows"""
    img = tk.PhotoImage(width=16, height=16)
    img.put(DARK['accent'] if on else DARK['dim'], to=(1, 1, 15, 15))
    img.put(DARK['accent'] if on else DARK['panel'], to=(2, 2, 14, 14))
    if on:
        for x, y in ((4, 7), (5, 8), (6, 9), (7, 10), (8, 9), (9, 8), (10, 7), (11, 6), (12, 5)):
            img.put('#ffffff', to=(x, y, x + 1, y + 2))
    return img


class SettingsDialog:
    """Settings in pages: General (phBot, server, accounts), Groups, Escort (the 80+ / 95+ spots) and Builds (the
    composition gives every slot of a group its build). Buttons act at once, the fields when Save is pressed.
    Standard mode: General and Groups only, saved to its own part of settings.json."""

    INTS = ('group_size', 'max_parallel_groups', 'final_level', 'launch_delay_seconds', 'disconnect_restart_seconds',
            'training_radius')

    def __init__(self, app, page=0):
        self.app = app
        mgr = app.mgr
        s = mgr.s
        self.builds = sorted(mgr.builds)
        self.comps = {k: list(v) for k, v in (s.get('compositions') or {}).items()}
        top = self.top = tk.Toplevel(app.root)
        top.title('Settings - Standard' if app.standard else 'Settings')
        top.transient(app.root)
        top.resizable(False, False)
        dark_title(top)
        top.grab_set()
        self.nb = ttk.Notebook(top)
        self.nb.pack(fill='both', expand=True, padx=8, pady=(8, 0))
        general, groups = self.page('General'), self.page('Groups')
        self.vars, self.labels, self.pages = {}, {}, {}
        self.high = []      # (level, spot var, reverse var) of the Escort page's moves

        ttk.Label(general, text='phBot').grid(row=0, column=0, sticky='w', pady=3)
        row = ttk.Frame(general)
        row.grid(row=0, column=1, columnspan=2, sticky='w')
        self.lbl_phbot = ttk.Label(row, wraplength=420)
        self.lbl_phbot.pack(side='left')
        ttk.Button(row, text='Choose...', command=self.choose_phbot).pack(side='left', padx=6)
        self.show_phbot()
        r = self.fields(general, 1, [('server', 'Server name (as in phBot)', 'entry'),
                                     ('accounts_csv', 'Accounts file', 'file'),
                                     ('name_prefix', 'Character name prefix', 'entry')])
        ttk.Separator(general).grid(row=r, column=0, columnspan=3, sticky='ew', pady=10)
        row = ttk.Frame(general)
        row.grid(row=r + 1, column=0, columnspan=3, sticky='w')
        ttk.Button(row, text='Open accounts.csv', command=lambda: app.open_path(s['accounts_csv'])).pack(side='left')
        if not app.standard:        # Standard: every bot on this PC's own connection
            ttk.Button(row, text='Open proxies.csv', command=lambda: app.open_path(s['proxies_csv'])).pack(
                side='left', padx=(6, 0))
        ttk.Button(row, text='Reload accounts and settings', command=self.reload).pack(side='left', padx=(6, 0))
        if mgr.deleted:
            ttk.Button(general, text='Restore deleted (%d): %s' % (len(mgr.deleted), ', '.join(sorted(mgr.deleted)[:6]) +
                                                                  (' ...' if len(mgr.deleted) > 6 else '')),
                       command=self.restore).grid(row=r + 2, column=0, columnspan=3, sticky='w', pady=(8, 0))

        if app.standard:
            r = self.fields(groups, 0, [('group_size', 'Players per group', 'spin8'),
                                        ('member_mode', 'Bots run with', 'mode'),
                                        ('launch_delay_seconds', 'Seconds between bot launches', 'spin120'),
                                        ('disconnect_restart_seconds', 'Auto start: open again after (seconds)',
                                         'spin300')])
            ttk.Separator(groups).grid(row=r, column=0, columnspan=3, sticky='ew', pady=10)
            self.fields(groups, r + 1, [('hide_client', 'Hide the game client', 'check'),
                                        ('minimize_bots', 'Start bots hidden (tray)', 'check')])
        else:
            r = self.fields(groups, 0, [('group_size', 'Players per group (+ the escort)', 'spin7'),
                                        ('max_parallel_groups', 'Groups at the same time', 'spin20'),
                                        ('final_level', 'Stop at level', 'spin140'),
                                        ('member_mode', 'Members run with', 'mode'),
                                        ('launch_delay_seconds', 'Seconds between bot launches', 'spin120'),
                                        ('disconnect_restart_seconds', 'Reopen a disconnected bot after (seconds)',
                                         'spin300'),
                                        ('training_radius', 'Training area radius (0 = from the plan)', 'spin200')])
            ttk.Separator(groups).grid(row=r, column=0, columnspan=3, sticky='ew', pady=10)
            self.fields(groups, r + 1, [('hide_client', 'Hide the members\' game client', 'check'),
                                        ('hide_leader_client', 'Hide the leader\'s game client too', 'check'),
                                        ('minimize_bots', 'Start member bots hidden (tray)', 'check'),
                                        ('exp_booster', 'Keep an EXP Booster scroll on (a new one whenever none '
                                                        'is active)', 'check')])
            self.escort_page(self.page('Escort'))
            self.builds_page(self.page('Builds'))

        bar = ttk.Frame(top, padding=8)
        bar.pack(fill='x')
        ttk.Button(bar, text='Cancel', command=top.destroy).pack(side='right')
        ttk.Button(bar, text='Save', style='Accent.TButton', command=self.save).pack(side='right', padx=6)
        self.nb.select(min(page, self.nb.index('end') - 1))

    def escort_page(self, escort):
        """ESCORT_MOVES rows in order: the level the group moves at, the spot and the reverse point. Before any
        were saved they show the stages of settings.json"""
        s = self.app.mgr.s
        moves = list(s.get('escort_moves') or ml_core.stages_to_moves(s.get('escort_stages')))[:ml_core.ESCORT_MOVES]
        moves += [{}] * (ml_core.ESCORT_MOVES - len(moves))
        for c, text in enumerate(('', 'From level', 'Spot (region,X,Y,Z)', 'Reverse scroll to')):
            ttk.Label(escort, text=text, foreground=DARK['dim']).grid(row=0, column=c, sticky='w', padx=(0, 10))
        for n, m in enumerate(moves):
            r = n + 1
            ttk.Label(escort, text='Move %d' % r).grid(row=r, column=0, sticky='w', pady=2, padx=(0, 10))
            lv_v = tk.StringVar(value='1' if n == 0 else str(m.get('level') or ''))
            if n == 0:                  # every group starts with the first move
                ttk.Label(escort, text='1').grid(row=r, column=1, sticky='w', padx=(4, 0))
            else:
                ttk.Spinbox(escort, from_=2, to=100, textvariable=lv_v, width=5).grid(row=r, column=1, sticky='w')
            spot_v = tk.StringVar(value='%d,%g,%g,%g' % (m.get('region', 0), m['x'], m['y'], m.get('z', 0))
                                  if 'x' in m else '')
            rev_v = tk.StringVar(value='' if m.get('reverse_auto') else m.get('reverse', ''))
            ttk.Entry(escort, textvariable=spot_v, width=26).grid(row=r, column=2, sticky='w', padx=(0, 10))
            ttk.Entry(escort, textvariable=rev_v, width=18).grid(row=r, column=3, sticky='w')
            if m.get('reverse') and m.get('reverse_auto'):
                ttk.Label(escort, text='now: ' + m['reverse'], foreground=DARK['dim']).grid(
                    row=r, column=4, sticky='w', padx=6)
            self.high.append((lv_v, spot_v, rev_v))
        ttk.Label(escort, foreground=DARK['dim'], wraplength=560, text=(
            'The group moves on when its lowest member reaches the level.   Empty reverse = the nearest reverse point.'
            '   Empty spot = free: you drive the escort.   Empty level = the move is not used.')).grid(
            row=ml_core.ESCORT_MOVES + 1, column=0, columnspan=5, sticky='w', pady=(10, 0))

    def builds_page(self, builds):
        s, mgr, app = self.app.mgr.s, self.app.mgr, self.app
        ttk.Label(builds, text='Group composition').grid(row=0, column=0, sticky='w', pady=3, padx=(0, 12))
        row = ttk.Frame(builds)
        row.grid(row=0, column=1, sticky='w')
        self.comp = tk.StringVar(value=s.get('composition', ''))
        self.cb_comp = ttk.Combobox(row, textvariable=self.comp, values=sorted(self.comps), state='readonly', width=20)
        self.cb_comp.pack(side='left')
        self.cb_comp.bind('<<ComboboxSelected>>', lambda e: self.show_slots())
        ttk.Button(row, text='New', command=self.new_comp).pack(side='left', padx=(6, 0))
        ttk.Button(row, text='Delete', command=self.del_comp).pack(side='left', padx=(4, 0))
        self.slots_frame = ttk.Frame(builds)
        self.slots_frame.grid(row=1, column=0, columnspan=2, sticky='w', pady=8)
        ttk.Label(builds, text='Builds: ' + ', '.join('%s = %s' % (b, mgr.builds[b].get('name', b)) for b in self.builds),
                  wraplength=460, foreground=DARK['dim']).grid(row=2, column=0, columnspan=2, sticky='w')
        ttk.Separator(builds).grid(row=3, column=0, columnspan=2, sticky='ew', pady=10)
        row = ttk.Frame(builds)
        row.grid(row=4, column=0, columnspan=2, sticky='w')
        ttk.Button(row, text='Edit builds.json', command=lambda: app.open_path('data/builds.json')).pack(side='left')
        ttk.Button(row, text='Rebuild skill plans', command=app.on_rebuild).pack(side='left', padx=6)
        self.vars['group_size'].trace_add('write', lambda *a: self.show_slots())
        self.slot_vars = []
        self.show_slots()

    def page(self, title):
        f = ttk.Frame(self.nb, padding=12)
        self.nb.add(f, text=title)
        return f

    def fields(self, parent, r, rows):
        """a label and a field per setting, from grid row r on; returns the next free row"""
        s = self.app.mgr.s
        for key, label, kind in rows:
            if kind == 'check':
                v = tk.BooleanVar(value=bool(s.get(key)))
                ttk.Checkbutton(parent, text=label, variable=v).grid(row=r, column=0, columnspan=3, sticky='w', pady=2)
            else:
                ttk.Label(parent, text=label).grid(row=r, column=0, sticky='w', pady=3, padx=(0, 12))
                v = tk.StringVar(value=str(s.get(key, '')))
                if kind == 'mode':
                    w = ttk.Combobox(parent, textvariable=v, values=MEMBER_MODES, state='readonly', width=24)
                elif kind.startswith('spin'):
                    w = ttk.Spinbox(parent, textvariable=v, from_=0 if key == 'training_radius' else 1,
                                    to=int(kind[4:]), width=8)
                elif kind == 'file':        # the entry with its ... button right after it
                    w = ttk.Frame(parent)
                    ttk.Entry(w, textvariable=v, width=30).pack(side='left')
                    ttk.Button(w, text='...', width=3, command=lambda v=v: self.pick_file(v)).pack(side='left', padx=4)
                else:
                    w = ttk.Entry(parent, textvariable=v, width=30)
                w.grid(row=r, column=1, sticky='w')
            self.vars[key], self.labels[key], self.pages[key] = v, label, parent
            r += 1
        return r

    def show_phbot(self):
        p = ml_core.phbot_exe_path(self.app.mgr.s)
        found = os.path.isfile(p)
        self.lbl_phbot.config(text=os.path.normpath(p) + ('' if found else '   (NOT FOUND)'),
                              foreground=DARK['link'] if found else DARK['bad'])

    def choose_phbot(self):
        if any(g.state in RUNNING for g in self.app.mgr.groups):
            messagebox.showinfo('MassLeveler', 'Close the running groups first.', parent=self.top)
            return
        p = filedialog.askopenfilename(parent=self.top, title='Choose phBot.exe', initialdir=self.app.mgr.s['phbot_dir'],
                                       filetypes=[('phBot', '*.exe'), ('All files', '*.*')])
        if not p:
            return
        self.app.mgr.set_phbot(p)
        self.app.rebuild_tree()
        self.show_phbot()
        for prob in self.app.mgr.problems():
            self.app._log('CHECK: ' + prob)

    def restore(self):
        """the deleted characters come back (in new groups at the end)"""
        self.top.destroy()
        self.app.mgr.restore_deleted()
        self.app.rebuild_tree()

    def reload(self):
        """accounts.csv / settings.json changed by hand: read them again and show the new values"""
        page = self.nb.index('current')
        self.top.destroy()
        self.app.on_reload()
        SettingsDialog(self.app, page)

    def pick_file(self, v):
        p = filedialog.askopenfilename(parent=self.top, filetypes=[('CSV', '*.csv'), ('All files', '*.*')])
        if p:
            v.set(p)

    def keep_slots(self):
        name = self.comp.get()
        if name in self.comps and self.slot_vars:
            self.comps[name] = [v.get() for v in self.slot_vars]

    def show_slots(self):
        try:
            size = min(max(1, int(self.vars['group_size'].get())), ml_core.PARTY_MAX - 1)    # + the escort
        except ValueError:
            return
        old = [v.get() for v in self.slot_vars]
        for w in self.slots_frame.winfo_children():
            w.destroy()
        comp = self.comps.get(self.comp.get()) or old or ['ch_nuker']
        self.slot_vars = []
        for i in range(size):
            ttk.Label(self.slots_frame, text='Leader' if i == 0 else 'Slot %d' % (i + 1), width=8).grid(
                row=i // 2, column=(i % 2) * 2, sticky='w')
            v = tk.StringVar(value=comp[i % len(comp)])
            v.trace_add('write', lambda *a: self.keep_slots())
            ttk.Combobox(self.slots_frame, textvariable=v, values=self.builds, state='readonly', width=16).grid(
                row=i // 2, column=(i % 2) * 2 + 1, sticky='w', padx=(0, 12), pady=1)
            self.slot_vars.append(v)

    def new_comp(self):
        name = simpledialog.askstring('Composition', 'Name of the new composition:', parent=self.top)
        if name:
            self.comps[name] = [v.get() for v in self.slot_vars] or ['ch_nuker']
            self.cb_comp.config(values=sorted(self.comps))
            self.comp.set(name)
            self.show_slots()

    def del_comp(self):
        if len(self.comps) > 1 and self.comp.get() in self.comps:
            del self.comps[self.comp.get()]
            self.cb_comp.config(values=sorted(self.comps))
            self.comp.set(sorted(self.comps)[0])
            self.show_slots()

    def read_spot(self, title, text, rev):
        """{region, x, y, z, reverse, reverse_auto} of an Escort page row; None after telling what is wrong.
        Empty reverse = the nearest reverse point of phBot's game data"""
        self.nb.select(2)               # the Escort page, for the messages below
        try:
            region, x, y, z = App.parse_coords(text.strip())
        except ValueError as e:
            messagebox.showwarning(title, str(e), parent=self.top)
            return None
        spot = {'region': region, 'x': x, 'y': y, 'z': z}
        rev = rev.strip()
        if rev:
            exact = ml_core.exact_reverse(self.app.mgr.s['phbot_dir'], rev)
            if not exact:
                messagebox.showwarning(title, '"%s" is not in the game\'s reverse list.\nType it the way the '
                                       'reverse scroll window shows it, or leave it empty for the nearest one.'
                                       % rev, parent=self.top)
                return None
            spot.update(reverse=exact, reverse_auto=False)
        else:
            found = ml_core.nearest_reverse(self.app.mgr.s['phbot_dir'], spot)
            if not found:
                messagebox.showwarning(title, 'No reverse point found near it in phBot\'s game data.\n'
                                       'Type the reverse point name yourself.', parent=self.top)
                return None
            spot.update(reverse=found[0], reverse_auto=True)
            messagebox.showinfo(title, 'Nearest reverse point: %s (about %d away)' % found, parent=self.top)
        return spot

    def save(self):
        if any(g.state in ('launching', 'running') for g in self.app.mgr.groups):
            if not messagebox.askyesno('MassLeveler', 'Groups are running. The new settings apply to groups started '
                                       'from now on. Save?', parent=self.top):
                return
        out = {}
        for key, v in self.vars.items():
            out[key] = v.get()
            if key in self.INTS:
                try:
                    out[key] = int(out[key])
                except ValueError:
                    self.nb.select(self.pages[key])
                    messagebox.showwarning('Settings', 'Write a number in "%s".' % self.labels[key], parent=self.top)
                    return
        if self.app.standard:       # its own part of settings.json: no escort spots / compositions
            self.app.mgr.save_settings(out)
            self.app.rebuild_tree()
            self.top.destroy()
            return
        self.keep_slots()
        moves = []
        for n, (lv_v, spot_v, rev_v) in enumerate(self.high):
            text = lv_v.get().strip()
            if not text:
                if spot_v.get().strip():
                    self.nb.select(2)
                    messagebox.showwarning('Settings', 'Move %d: write the level it starts at.' % (n + 1),
                                           parent=self.top)
                    return
                continue                    # not used
            low = moves[-1]['level'] + 1 if moves else 1
            try:
                lv = int(text)
            except ValueError:
                lv = 0
            if not low <= lv <= 100:
                self.nb.select(2)
                messagebox.showwarning('Settings', 'Move %d: write a level from %d to 100%s.' % (
                    n + 1, low, ', above the move before it' if moves else ''), parent=self.top)
                return
            m = {'level': lv}
            if spot_v.get().strip():        # no spot = free: the user drives the escort
                spot = self.read_spot('Move %d' % (n + 1), spot_v.get(), rev_v.get())
                if not spot:
                    return
                m.update(spot)
            moves.append(m)
        out['escort_moves'] = moves
        out['compositions'] = self.comps
        out['composition'] = self.comp.get()
        self.app.mgr.save_settings(out)
        self.app.rebuild_tree()
        self.top.destroy()


class MapTab:
    """Standard mode: phBot's minimap with the open characters. Pick characters (the list, or click a dot; Ctrl
    adds), then click the map: they walk there by phBot's own route (the plugin, -mlstandard). Drag = move the
    map, wheel = zoom, right-click = Navigate / Storage / Teleport / NPC ... like phBot's map."""

    ZOOMS = (0.25, 0.375, 0.5, 0.75, 1.0, 1.5, 2.0)

    def __init__(self, app, parent):
        self.app = app
        self.data = None            # ml_map.MapData, read on first show
        self.cx, self.cy, self.zi = 6440.0, 1100.0, 4           # Jangan, tiles at their own size
        self.tiles = {}             # (rx, ry, size) -> PhotoImage
        self.sel = set()            # picked characters
        self.press = None
        self.listed = None          # characters in the list now
        self.mouse = None           # world point under the mouse
        self.centered = False
        f = self.frame = ttk.Frame(parent)
        bar = ttk.Frame(f, padding=(0, 4))
        bar.pack(fill='x')
        ttk.Button(bar, text='Center on selected', command=self.center_selected).pack(side='left')
        ttk.Button(bar, text='Stop walking', command=self.stop_walk).pack(side='left', padx=4)
        ttk.Label(bar, foreground=DARK['dim'], text='Click: walk there   Drag: move   Wheel: zoom   Right-click: more'
                  ).pack(side='left', padx=12)
        self.lbl = ttk.Label(bar, foreground=DARK['dim'])
        self.lbl.pack(side='right')
        body = ttk.PanedWindow(f, orient='horizontal')
        body.pack(fill='both', expand=True)
        left = ttk.Frame(body)
        ttk.Label(left, text='Characters (Ctrl = more)', foreground=DARK['dim']).pack(anchor='w', pady=(0, 2))
        self.list = ttk.Treeview(left, show='tree', selectmode='extended')
        self.list.column('#0', width=170)
        self.list.pack(fill='both', expand=True)
        self.list.tag_configure('group', font=('Segoe UI', 9, 'bold'))
        self.list.bind('<<TreeviewSelect>>', self.on_list)
        body.add(left, weight=0)
        self.cv = tk.Canvas(body, bg='#10161a', highlightthickness=0, cursor='crosshair')
        body.add(self.cv, weight=1)
        for ev, fn in (('<ButtonPress-1>', self.on_press), ('<B1-Motion>', self.on_move), ('<ButtonRelease-1>', self.on_release),
                       ('<MouseWheel>', self.on_wheel), ('<Button-3>', self.on_menu), ('<Motion>', self.on_hover),
                       ('<Configure>', lambda e: self.draw())):
            self.cv.bind(ev, fn)

    # -------------------------------------------------- data
    def open_members(self):
        """(group, character) of every open bot with a position"""
        return [(g, m) for g in self.app.mgr.groups for m in g.all_members
                if self.app.mgr._alive(m) and (m.status.get('pos') or (0,))[0]]

    def picked(self):
        return [m for g, m in self.open_members() if m.character in self.sel]

    def show(self):
        if self.data is None:
            s = self.app.mgr.s
            try:
                self.data = ml_map.MapData(s['phbot_dir'], s['server'])
            except Exception as e:
                self.data = ml_map.MapData('', '')
                self.app._log('Map: phBot game data not read: %s' % e)
            if not os.path.isdir(self.data.tiles):
                self.app._log('Map: no minimap folder in ' + s['phbot_dir'])
        self.refresh()

    def refresh(self):
        members = self.open_members()
        names = tuple((g.gid, m.character) for g, m in members)
        if names != self.listed:
            self.listed = names
            self.list.delete(*self.list.get_children())
            for g in self.app.mgr.groups:
                mine = [m for gg, m in members if gg is g]
                if mine:
                    self.list.insert('', 'end', iid=g.gid, text=' ' + g.gid, open=True, tags=('group',))
                    for m in mine:
                        self.list.insert(g.gid, 'end', iid='c|' + m.character, text=' ' + m.character)
            self.sel &= {m.character for g, m in members}
            self.select_list()
        if not self.centered and members:
            self.centered = True
            self.center_on([m for g, m in members])
        self.draw()

    def select_list(self):
        self.syncing = True
        self.list.selection_set(['c|' + n for n in self.sel if self.list.exists('c|' + n)])
        self.syncing = False

    def on_list(self, e):
        if getattr(self, 'syncing', False):
            return
        sel = set()
        for iid in self.list.selection():
            if iid.startswith('c|'):
                sel.add(iid[2:])
            else:       # a group: all its characters
                sel.update(c[2:] for c in self.list.get_children(iid))
        self.sel = sel
        self.draw()

    # -------------------------------------------------- drawing
    def scale(self):
        return ml_map.TILE * self.ZOOMS[self.zi] / ml_map.REGION       # pixels per game unit

    def to_screen(self, x, y):
        p = self.scale()
        return self.cv.winfo_width() / 2 + (x - self.cx) * p, self.cv.winfo_height() / 2 - (y - self.cy) * p

    def to_world(self, sx, sy):
        p = self.scale()
        return self.cx + (sx - self.cv.winfo_width() / 2) / p, self.cy - (sy - self.cv.winfo_height() / 2) / p

    def tile(self, rx, ry, size):
        key = (rx, ry, size)
        if key not in self.tiles:
            if len(self.tiles) > 400:
                self.tiles.clear()
            path = self.data.tile_path(rx, ry) if self.data else None
            img = None
            if path:
                try:
                    from PIL import Image, ImageTk
                    img = ImageTk.PhotoImage(Image.open(path).convert('RGB').resize((size, size)))
                except Exception:
                    img = None
            self.tiles[key] = img
        return self.tiles[key]

    def draw(self):
        cv = self.cv
        w, h = cv.winfo_width(), cv.winfo_height()
        if w < 10 or self.data is None:
            return
        cv.delete('all')
        size = int(round(ml_map.TILE * self.ZOOMS[self.zi]))
        x0, y1 = self.to_world(0, 0)
        x1, y0 = self.to_world(w, h)
        (rx0, ry0), (rx1, ry1) = ml_map.tile_of(x0, y0), ml_map.tile_of(x1, y1)
        for rx in range(rx0, rx1 + 1):
            for ry in range(ry0, ry1 + 1):
                img = self.tile(rx, ry, size + 1)       # 1 pixel over the next tile: no seams from rounding
                if img:
                    ox, oy = ml_map.tile_origin(rx, ry)
                    sx, sy = self.to_screen(ox, oy + ml_map.REGION)
                    cv.create_image(int(math.floor(sx)), int(math.floor(sy)), image=img, anchor='nw')
        p = self.scale()
        for g, m in self.open_members():
            region, x, y = m.status['pos']
            if region >= 0x8000:
                continue                # caves: no tiles
            sx, sy = self.to_screen(x, y)
            picked = m.character in self.sel
            t = m.status.get('train')
            if picked and t and (t[1] or t[2]) and t[3]:
                tx, ty = self.to_screen(t[1], t[2])
                cv.create_oval(tx - t[3] * p, ty - t[3] * p, tx + t[3] * p, ty + t[3] * p, outline='#2f6fd8')
            color = DARK['bad'] if m.status.get('dead') else '#64b5f6' if picked else '#9ccc65'
            cv.create_rectangle(sx - 4, sy - 4, sx + 4, sy + 4, fill=color, outline='#ffffff' if picked else '#000000')
            if picked:
                cv.create_text(sx + 8, sy, text=m.character, anchor='w', fill='#ffffff', font=('Segoe UI', 8))
        self.show_label()

    def show_label(self):
        x, y = self.mouse or (self.cx, self.cy)
        self.lbl.config(text='%s   %.0f, %.0f   %d selected' % (self.data.zone(x, y) if self.data else '', x, y,
                                                                 len(self.sel)))

    def center_on(self, members):
        pts = [m.status['pos'] for m in members if m.status.get('pos') and m.status['pos'][0] < 0x8000]
        if pts:
            self.cx, self.cy = sum(p[1] for p in pts) / len(pts), sum(p[2] for p in pts) / len(pts)
            self.draw()

    def center_selected(self):
        self.center_on(self.picked() or [m for g, m in self.open_members()])

    # -------------------------------------------------- mouse
    def hit(self, sx, sy):
        best = None
        for g, m in self.open_members():
            region, x, y = m.status['pos']
            dx, dy = self.to_screen(x, y)
            d = abs(dx - sx) + abs(dy - sy)
            if d <= 9 and (best is None or d < best[0]):
                best = (d, m)
        return best[1] if best else None

    def on_press(self, e):
        self.press = (e.x, e.y, self.cx, self.cy, False)

    def on_move(self, e):
        if not self.press:
            return
        x0, y0, cx, cy, moved = self.press
        if moved or abs(e.x - x0) + abs(e.y - y0) > 4:
            self.press = (x0, y0, cx, cy, True)
            p = self.scale()
            self.cx, self.cy = cx - (e.x - x0) / p, cy + (e.y - y0) / p
            self.cv.configure(cursor='fleur')
            self.draw()

    def on_release(self, e):
        press, self.press = self.press, None
        self.cv.configure(cursor='crosshair')
        if not press or press[4]:
            return
        m = self.hit(e.x, e.y)
        if m:                           # a character: pick it (Ctrl: add / remove)
            if e.state & 0x4:
                self.sel ^= {m.character}
            else:
                self.sel = {m.character}
            self.select_list()
            self.draw()
        else:
            self.navigate(*self.to_world(e.x, e.y))

    def on_wheel(self, e):
        before = self.to_world(e.x, e.y)
        self.zi = max(0, min(len(self.ZOOMS) - 1, self.zi + (1 if e.delta > 0 else -1)))
        after = self.to_world(e.x, e.y)
        self.cx += before[0] - after[0]
        self.cy += before[1] - after[1]
        self.draw()

    def on_hover(self, e):
        self.mouse = self.to_world(e.x, e.y)
        if self.data is not None:
            self.show_label()

    # -------------------------------------------------- commands
    def members_or_tell(self):
        members = self.picked()
        if not members:
            messagebox.showinfo('Map', 'Pick characters first: the list on the left, or click their dots.',
                                parent=self.app.root)
        return members

    def navigate(self, x, y, then=(), members=None):
        members = members or self.members_or_tell()
        if members:
            self.app.mgr.map_command(members, 'navigate', region=ml_map.region_of(x, y), x=round(x, 1), y=round(y, 1),
                                     z=0, then=list(then))

    def stop_walk(self):
        members = self.picked() or [m for g, m in self.open_members()]
        if members:
            self.app.mgr.map_command(members, 'stop_walk')

    def on_menu(self, e):
        x, y = self.to_world(e.x, e.y)
        members = self.picked()
        on = bool(members)
        menu = tk.Menu(self.app.root, tearoff=0)
        it = lambda m, label, fn, enabled=on: self.app.add_item(m, label, fn, enabled)
        it(menu, 'Navigate  (%d selected)' % len(members), lambda: self.navigate(x, y, members=members))
        it(menu, 'Copy Coordinates', lambda: self.copy(x, y), True)
        it(menu, 'Set Training Area', lambda: self.app.mgr.bot_command(members, 'set_training_area', region=0, x=x, y=y,
                                                                       z=0))
        menu.add_separator()
        st = self.data.storage_near(x, y) if self.data else None
        it(menu, 'Access Storage' + ('  (%s)' % st['name'] if st else ''),
           lambda: self.navigate(st['x'], st['y'], ['DoStorage'], members), on and bool(st))
        am = tk.Menu(menu, tearoff=0)
        cmd = lambda name, **kw: (lambda: self.app.mgr.bot_command(members, name, **kw))
        it(am, 'Styria', cmd('styria'))
        it(am, 'Capture the Flag', cmd('arena', kind='capture_the_flag'))
        for kind in ('job', 'random', 'party', 'guild'):
            for mode in ('ctf', 'points'):
                it(am, '%s (%s)' % (kind.title(), 'CTF' if mode == 'ctf' else 'Points'), cmd('arena', kind=kind, mode=mode))
        menu.add_cascade(label='     Arena', menu=am)
        tm = tk.Menu(menu, tearoff=0)
        for t in (self.data.teleports_near(x, y, 300) if self.data else [])[:8]:
            sub = tk.Menu(tm, tearoff=0)
            for dest in t['dests']:
                it(sub, dest, lambda t=t, dest=dest: self.navigate(t['x'], t['y'], ['teleport,%s,%s' % (t['name'], dest)],
                                                                  members))
            tm.add_cascade(label='     ' + t['name'], menu=sub)
        if tm.index('end') is None:
            it(tm, 'No teleporter near here', None, False)
        menu.add_cascade(label='     Teleport', menu=tm)
        npcs = self.data.npcs_near(x, y, 300)[:30] if self.data else []
        nm = tk.Menu(menu, tearoff=0)
        for n in npcs:
            it(nm, n['name'], lambda n=n: self.navigate(n['x'], n['y'], members=members))
        if not npcs:
            it(nm, 'No NPC near here', None, False)
        menu.add_cascade(label='     NPC', menu=nm)
        close = [n for n in npcs if math.hypot(n['x'] - x, n['y'] - y) <= 30][:4]      # right where you clicked
        if close:
            menu.add_separator()
            for n in close:
                it(menu, n['name'], lambda n=n: self.navigate(n['x'], n['y'], members=members))
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()

    def copy(self, x, y):
        self.app.root.clipboard_clear()
        self.app.root.clipboard_append('%d,%.0f,%.0f' % (ml_map.region_of(x, y), x, y))


class App:
    def __init__(self, root, mode='mass'):
        self.root = root
        self.mode = mode
        self.standard = mode == 'standard'      # a plain phBot manager: no leveling (ml_core docstring)
        self.logq = queue.Queue()
        self.shown = {}             # tree row -> what it shows: only rows that changed are drawn again
        self.group_states = {}      # group -> its state at the last refresh (rows open / fold when it changes)
        self.mgr = ml_core.Manager(log=self._log, mode=mode)
        root.title('MassLeveler Escort — %s' % ('Standard' if self.standard else 'Mass Leveler'))
        root.geometry('1100x700+%d+%d' % (max(0, (root.winfo_screenwidth() - 1100) // 2),
                                          max(0, (root.winfo_screenheight() - 700) // 2 - 20)))
        root.minsize(760, 420)
        root.protocol('WM_DELETE_WINDOW', self.on_close)

        # right-click a group / character for its actions; phBot path, files and builds are in Settings
        bar = ttk.Frame(root, padding=(6, 6, 6, 4))
        bar.pack(fill='x')
        ttk.Button(bar, text='Start', style='Accent.TButton', command=self.on_start).pack(side='left')
        for text, command in [('Stop all', self.on_stop_all)] + ([] if self.standard else [
                ('Finish all', self.on_finish_all)]) + [('Restart bots', self.on_restart_all)]:
            ttk.Button(bar, text=text, command=command).pack(side='left', padx=(4, 0))
        ttk.Button(bar, text='Update', command=self.on_update).pack(side='right')
        ttk.Button(bar, text='Settings', command=lambda: SettingsDialog(self)).pack(side='right', padx=4)
        self.lbl_summary = ttk.Label(bar, foreground=DARK['dim'])
        self.lbl_summary.pack(side='right', padx=(0, 8))
        self.lbl_dot = ttk.Label(bar, text='●')     # green: new groups start, grey: paused
        if not self.standard:
            self.lbl_dot.pack(side='right', padx=(0, 4))

        pan = ttk.PanedWindow(root, orient='vertical')
        pan.pack(fill='both', expand=True, padx=6, pady=(0, 6))
        # tabs (more can be added): the characters, and in Standard mode phBot's map
        self.tabs = ttk.Notebook(pan)
        frame = ttk.Frame(self.tabs)
        self.tabs.add(frame, text='Characters')
        self.map = None
        if self.standard:
            self.map = MapTab(self, self.tabs)
            self.tabs.add(self.map.frame, text='Map')
            self.tabs.bind('<<NotebookTabChanged>>', lambda e: self.map_shown() and self.map.show())
        self.img_on, self.img_off = box_image(True), box_image(False)
        self.tree = ttk.Treeview(frame, columns=COLUMNS, selectmode='browse')
        self.tree.heading('#0', text='Group / character', anchor='w')
        self.tree.column('#0', width=170, stretch=False)
        self.tree.bind('<Button-1>', self.on_click)
        self.tree.bind('<B1-Motion>', self.on_drag)          # drag a character onto another group
        self.tree.bind('<ButtonRelease-1>', self.on_drop)
        self.drag = None
        self.tree.bind('<space>', lambda e: self.toggle(self.tree.focus()) or 'break')
        self.tree.bind('<Button-3>', self.on_menu)
        for c in COLUMNS:
            self.tree.heading(c, text=HEADINGS[c], anchor='w')
            self.tree.column(c, width=WIDTHS[c], anchor='w', stretch=c == 'state')
        sb = ttk.Scrollbar(frame, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')
        for tag, color in (('done', 'done'), ('bad', 'bad'), ('escort', 'escort'), ('skipped', 'dim')):
            self.tree.tag_configure(tag, foreground=DARK[color])
        self.tree.tag_configure('group', font=('Segoe UI', 9, 'bold'))
        pan.add(self.tabs, weight=4)

        logf = ttk.Frame(pan)
        self.logbox = tk.Text(logf, height=8, wrap='none', font=('Consolas', 9), bg=DARK['panel'], fg=DARK['fg'],
                              insertbackground=DARK['fg'], relief='flat', highlightthickness=0)
        lsb = ttk.Scrollbar(logf, orient='vertical', command=self.logbox.yview)
        self.logbox.configure(yscrollcommand=lsb.set)
        self.logbox.pack(side='left', fill='both', expand=True)
        lsb.pack(side='right', fill='y')
        pan.add(logf, weight=1)

        for p in self.mgr.problems():
            self._log('CHECK: ' + p)
        self.refresh()

    # -------------------------------------------------- helpers
    def _log(self, msg):
        self.logq.put(time.strftime('%H:%M:%S ') + msg)

    def open_path(self, rel):
        p = ml_core.path_in_root(rel)
        if os.path.exists(p):
            os.startfile(p)
        else:
            messagebox.showinfo('MassLeveler', 'Not found: ' + p)

    def selected(self):
        sel = self.tree.selection()
        return self.item(sel[0]) if sel else (None, None)

    def item(self, iid):
        """(group, character or None) of a tree row"""
        gid, _, char = (iid or '').partition('|')
        g = next((x for x in self.mgr.groups if x.gid == gid), None)
        m = next((x for x in g.all_members if x.character == char), None) if g and char else None
        return g, m

    # -------------------------------------------------- tick boxes / right-click menu
    def on_click(self, e):
        iid = self.tree.identify_row(e.y)
        if iid and 'image' in str(self.tree.identify_element(e.x, e.y)):
            self.tree.selection_set(iid)
            self.toggle(iid)
            return 'break'
        g, m = self.item(iid)
        self.drag = {'m': m, 'y': e.y, 'on': False} if m and not g.escort else None

    # -------------------------------------------------- drag and drop: a character into another group
    def drop_group(self, y):
        """the group under the mouse (its row or one of its characters), None = empty space (a new group)"""
        return self.item(self.tree.identify_row(y))[0]

    def on_drag(self, e):
        if not self.drag:
            return
        if not self.drag['on'] and abs(e.y - self.drag['y']) > 6:
            self.drag['on'] = True
            self.tree.configure(cursor='fleur')
        if self.drag['on']:
            g = self.drop_group(e.y)
            self.tree.selection_set(g.gid if g else ())
            return 'break'

    def on_drop(self, e):
        d, self.drag = self.drag, None
        if not d or not d['on']:
            return
        self.tree.configure(cursor='')
        g = self.drop_group(e.y)
        target = self.mgr.move_member(d['m'], g)
        self.rebuild_tree()
        if target:
            self.tree.item(target.gid, open=True)
            iid = target.gid + '|' + d['m'].character
            self.tree.selection_set(iid)
            self.tree.see(iid)
        return 'break'

    def delete(self, members, what):
        if messagebox.askyesno('Delete', 'Delete %s?\n\nThey are not shown again (Settings: Restore deleted brings '
                               'them back). accounts.csv is not changed.' % what):
            if self.mgr.delete_members(members):
                self.rebuild_tree()

    def toggle(self, iid):
        g, m = self.item(iid)
        if m:
            self.set_active(g, m, m.skip)
        elif g:
            self.set_active(g, None, g.state == 'skipped')

    def set_active(self, g, m, active):
        if m:
            self.mgr.set_member_skip(g, m, not active)
        else:
            if not active and g.state in RUNNING and not messagebox.askyesno(
                    'MassLeveler', 'Close the bots of %s and skip it?' % g.gid):
                return
            self.mgr.set_group_skip(g, not active)
        self.refresh(schedule=False)

    def on_menu(self, e):
        iid = self.tree.identify_row(e.y)
        menu = tk.Menu(self.root, tearoff=0)
        if not iid:
            running = [m for g in self.mgr.groups for m in g.all_members if self.mgr._alive(m)] if self.standard \
                else [m for g in self.mgr.groups if g.state in RUNNING for m in g.members]
            self.add_item(menu, 'All running groups (%d bots)' % len(running), None, False)
            menu.add_separator()
            self.add_bot_items(menu, running)
        else:
            self.tree.selection_set(iid)
            self.tree.focus(iid)
            g, m = self.item(iid)
            if m and self.standard:
                alive = self.mgr._alive(m)
                self.add_active_items(menu, g, m, '%s opens with Start' % m.character)
                self.add_auto_item(menu, [m])
                self.add_item(menu, 'Open', lambda: self.open_member(g, m), not alive and not m.skip)
                self.add_item(menu, 'Restart character', self.restart_selected, alive)
                self.add_item(menu, 'Close', lambda: self.terminate([m]), alive)
                menu.add_separator()
                self.add_bot_menu(menu, [m] if alive else [])
                self.add_edit_items(menu, g, m)
                menu.add_separator()
                sub = tk.Menu(menu, tearoff=0)
                self.add_group_items(sub, g)
                menu.add_cascade(label='     Group %s' % g.gid, menu=sub)
            elif m:
                self.add_active_items(menu, g, m, '%s plays with the group' % m.character)
                self.add_item(menu, 'Restart character', self.restart_selected, g.state in RUNNING)
                menu.add_separator()
                self.add_spot_menu(menu, g, m)
                self.add_bot_menu(menu, [m] if g.state in RUNNING else [])
                self.add_edit_items(menu, g, m)
                menu.add_separator()
                sub = tk.Menu(menu, tearoff=0)
                self.add_group_items(sub, g)
                menu.add_cascade(label='     Group %s' % g.gid, menu=sub)
            else:
                self.add_group_items(menu, g)
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()

    @staticmethod
    def add_item(menu, label, command, enabled=True):
        """Windows draws disabled menu items embossed (unreadable on black): grey them out instead"""
        menu.add_command(label='     ' + label, command=command if enabled else (lambda: None),
                         foreground=DARK['fg'] if enabled else DARK['dim'],
                         activeforeground='#ffffff' if enabled else DARK['dim'])

    def add_active_items(self, menu, g, m, label):
        skipped = m.skip if m else g.state == 'skipped'
        menu.add_command(label=('✓   ' if not skipped else '     ') + 'Active  (%s)' % label,
                         command=lambda: self.set_active(g, m, True))
        menu.add_command(label=('✓   ' if skipped else '     ') + 'Skip', command=lambda: self.set_active(g, m, False))
        menu.add_separator()

    def add_group_items(self, menu, g):
        run = g.state in RUNNING
        if self.standard:
            closed = [m for m in g.members if not self.mgr._alive(m) and self.mgr.progress.get(m.character) != 'finished']
            self.add_active_items(menu, g, None, 'opens with Start')
            self.add_auto_item(menu, g.all_members)
            self.add_item(menu, 'Start now', lambda: self.group_action('start_now', g), bool(closed))
            self.add_item(menu, 'Restart group bots', lambda: self.group_action('restart', g), run)
            self.add_item(menu, 'Close group', lambda: self.group_action('kill', g), run)
            self.add_item(menu, 'Mark finished', lambda: self.group_action('mark_done', g), g.state != 'done')
            self.add_item(menu, 'Reset to pending', lambda: self.group_action('reset', g), not run)
            menu.add_separator()
            self.add_bot_menu(menu, [m for m in g.all_members if self.mgr._alive(m)])
            self.add_edit_items(menu, g)
            return
        self.add_active_items(menu, g, None, 'started with the others')
        self.add_item(menu, 'Start now', lambda: self.group_action('start_now', g), not run)
        self.add_item(menu, 'Finish now (return + done)', lambda: self.group_action('finish', g), run)
        self.add_item(menu, 'Restart group bots', lambda: self.group_action('restart', g), run)
        self.add_item(menu, 'Close group', lambda: self.group_action('kill', g), run)
        self.add_item(menu, 'Mark finished', lambda: self.group_action('mark_done', g),
                      g.state != 'done' and not g.escort)
        self.add_item(menu, 'Reset to pending', lambda: self.group_action('reset', g), not run)
        menu.add_separator()
        self.add_spot_menu(menu, g, None)
        self.add_bot_menu(menu, list(g.members) if run else [])
        self.add_edit_items(menu, g)

    def add_edit_items(self, menu, g, m=None):
        """groups by hand: a group of its own, delete (not while the group runs, never the escort)"""
        ok = g.state not in RUNNING and not g.escort
        menu.add_separator()
        if m:
            self.add_item(menu, 'Move to a new group  (or drag it onto a group)', lambda: self.move_new(m),
                          ok and len(g.all_members) > 1)
            self.add_item(menu, 'Delete %s  (not shown again)' % m.character, lambda: self.delete([m], m.character), ok)
        else:
            self.add_item(menu, 'Delete group %s  (not shown again)' % g.gid, lambda: self.delete(
                list(g.all_members), 'group %s (%s)' % (g.gid, names(g.all_members))), ok)

    def move_new(self, m):
        target = self.mgr.move_member(m, None)
        self.rebuild_tree()
        if target:
            self.tree.item(target.gid, open=True)
            self.tree.selection_set(target.gid + '|' + m.character)

    def add_bot_menu(self, menu, members):
        """phBot's own Manager menu, as a submenu"""
        sub = tk.Menu(menu, tearoff=0)
        self.add_bot_items(sub, members)
        menu.add_cascade(label='     phBot', menu=sub, foreground=DARK['fg'] if members else DARK['dim'])

    def add_auto_item(self, menu, members):
        """Standard: Auto start = the bot opens again whenever it closes by itself"""
        on = bool(members) and all(m.character in self.mgr.auto for m in members)
        menu.add_command(label=('✓   ' if on else '     ') + 'Auto start  (opens again when it closes)',
                         command=lambda: self.set_auto(members, not on))
        menu.add_separator()

    def set_auto(self, members, on):
        self.mgr.set_auto_start(members, on)
        self.refresh(schedule=False)

    def open_member(self, g, m):
        self.mgr.open_member(g, m)
        self.refresh(schedule=False)

    # -------------------------------------------------- training spots
    def add_spot_menu(self, menu, g, m):
        run = g.state in RUNNING
        sm = tk.Menu(menu, tearoff=0)
        if m:
            self.add_item(sm, "Use %s's position for levels..." % m.character, lambda: self.spot_here(g, m), run)
        self.add_item(sm, 'Next spot (this one has no monsters)', lambda: self.mgr.next_spot(g), run)
        self.add_item(sm, 'Mark this spot bad (never again)', lambda: self.mark_bad(g), run)
        self.add_item(sm, 'Back to the first spot', lambda: self.mgr.first_spot(g), run)
        sm.add_separator()
        self.add_item(sm, 'Training plan...', lambda: PlanWindow(self, g))
        menu.add_cascade(label='     Training spot', menu=sm)

    def spot_here(self, g, m):
        s = ml_core.phbot_status(self.mgr.bot_pid(m))
        if not s or not s['in_game']:
            messagebox.showinfo('MassLeveler', '%s is not in the game.' % m.character, parent=self.root)
            return
        area, _, _ = self.mgr.group_area(g)
        default = '%d-%d' % (area['min_level'], area['max_level']) if area else '%d-%d' % (s['level'], s['level'] + 2)
        text = simpledialog.askstring('Training spot', 'Use the spot where %s stands now (%d, %d)\nfor levels '
                                      '(from-to):' % (m.character, s['x'], s['y']), initialvalue=default, parent=self.root)
        if not text:
            return
        try:
            lo, hi = sorted(int(v) for v in text.replace(' ', '').split('-'))
        except ValueError:
            messagebox.showwarning('Training spot', 'Write the levels like 35-36', parent=self.root)
            return
        self.mgr.add_custom_spot(s, lo, hi, groups=[x for x in self.mgr.groups if x.state in RUNNING])

    def mark_bad(self, g):
        area, spots, i = self.mgr.group_area(g)
        if area and messagebox.askyesno('Training spot', 'Never use spot %d/%d of %s again (%s)?\nThe group moves to '
                                        'the next spot.' % (i + 1, len(spots), area['name'], spots[i].get('src', 'plan')),
                                        parent=self.root):
            self.mgr.mark_spot_bad(g)

    # -------------------------------------------------- phBot Manager commands
    def add_bot_items(self, menu, members):
        """the phBot Manager menu, for these characters' bots (greyed out when none is running)"""
        on = bool(members)
        it = lambda m, label, fn: self.add_item(m, label, fn, on)
        cmd = lambda name, **kw: (lambda: self.mgr.bot_command(members, name, **kw))
        sep = menu.add_separator
        it(menu, 'Show Stats', lambda: StatsWindow(self, members))
        sep()
        it(menu, 'Show', lambda: self.set_visible('bot', True, members))
        it(menu, 'Hide', lambda: self.set_visible('bot', False, members))
        sep()
        it(menu, 'Show Client', lambda: self.set_visible('client', True, members))
        it(menu, 'Hide Client', lambda: self.set_visible('client', False, members))
        it(menu, 'Go Clientless', lambda: self.go_clientless(members))
        it(menu, 'Go Client...', lambda: self.go_client(members))
        sep()
        if self.standard:       # the plugin is idle there: phBot's own Start / Stop, no Auto Equip
            it(menu, 'Start Bot', cmd('start_bot'))
            it(menu, 'Stop Bot', cmd('stop_bot'))
        else:
            it(menu, 'Start Bot', lambda: self.mgr.member_command(members, 'start'))
            it(menu, 'Stop Bot', lambda: self.mgr.member_command(members, 'stop'))
            sep()
            it(menu, 'Auto Equip (MoAutoEquip)', lambda: self.mgr.member_command(members, 'auto_equip'))
        sep()
        it(menu, 'Start Trace...', lambda: self.ask_then(members, 'start_trace', 'player', 'Player Name',
                                                         'Enter the name of the player to trace:'))
        it(menu, 'Stop Trace', cmd('stop_trace'))
        sep()
        it(menu, 'Start Trade', cmd('start_trade'))
        it(menu, 'Stop Trade', cmd('stop_trade'))
        sep()
        tm = tk.Menu(menu, tearoff=0)
        it(tm, 'Get Position (train here)', cmd('get_position'))
        it(tm, 'Change Script...', lambda: self.change_script(members))
        it(tm, 'Change Coordinates...', lambda: self.ask_coords(members, 'set_training_area', 'Set Coordinates',
                                                                'for this training area'))
        it(tm, 'Change Radius...', lambda: self.ask_radius(members, 'set_training_radius', 'Set new training radius:'))
        it(tm, 'Change Pick Radius...', lambda: self.ask_radius(members, 'set_pick_radius', 'Set new pick radius:'))
        it(tm, 'Walk to...', lambda: self.ask_coords(members, 'walk_to', 'Walk to', 'you wish to walk to'))
        menu.add_cascade(label='     Training', menu=tm, foreground=DARK['fg'] if on else DARK['dim'])
        am = tk.Menu(menu, tearoff=0)
        it(am, 'Styria', cmd('styria'))
        it(am, 'Capture the Flag', cmd('arena', kind='capture_the_flag'))
        for kind in ('job', 'random', 'party', 'guild'):
            for mode in ('ctf', 'points'):
                it(am, '%s (%s)' % (kind.title(), 'CTF' if mode == 'ctf' else 'Points'), cmd('arena', kind=kind, mode=mode))
        menu.add_cascade(label='     Arena', menu=am, foreground=DARK['fg'] if on else DARK['dim'])
        sep()
        it(menu, 'Return Scroll', cmd('return_scroll'))
        it(menu, 'Reverse Return...', lambda: self.ask_then(members, 'reverse_return', 'target', 'Player Name',
                                                            'Enter the name of the party member to reverse return to:'))
        sep()
        it(menu, 'Change Profile...', lambda: self.change_profile(members))
        sep()
        it(menu, 'Terminate', lambda: self.terminate(members))

    def ask_then(self, members, name, key, title, prompt):
        text = simpledialog.askstring(title, prompt, parent=self.root)
        if text and text.strip():
            self.mgr.bot_command(members, name, **{key: text.strip()})

    @staticmethod
    def parse_coords(text):
        """region,X,Y | region,X,Y,Z | X,Y  ->  (region, x, y, z)"""
        parts = [float(v) for v in text.replace(' ', '').split(',') if v != '']
        if len(parts) == 2:
            return 0, parts[0], parts[1], 0.0
        if len(parts) in (3, 4):
            return int(parts[0]), parts[1], parts[2], parts[3] if len(parts) == 4 else 0.0
        raise ValueError('expected X,Y or region,X,Y[,Z]')

    def ask_coords(self, members, name, title, what):
        text = simpledialog.askstring(title, 'Cave coordinates need the region, otherwise X, Y is enough.\n\n'
                                             'region,X,Y\nregion,X,Y,Z\nX,Y\n\nEnter the coordinates %s:' % what,
                                      parent=self.root)
        if not text:
            return
        try:
            region, x, y, z = self.parse_coords(text)
        except ValueError as e:
            messagebox.showwarning(title, str(e), parent=self.root)
            return
        if name == 'walk_to':
            self.mgr.bot_command(members, name, coords='%g,%g' % (x, y) if not region else '%d,%g,%g,%g' % (region, x, y, z))
        else:
            self.mgr.bot_command(members, name, region=region, x=x, y=y, z=z)

    def ask_radius(self, members, name, prompt):
        r = simpledialog.askfloat('Set Radius', prompt, parent=self.root, initialvalue=50, minvalue=1, maxvalue=1000)
        if r:
            self.mgr.bot_command(members, name, radius=r)

    def change_script(self, members):
        p = filedialog.askopenfilename(title='Script', filetypes=[('Scripts', '*.txt'), ('All files', '*.*')])
        if p:
            if len(p.encode()) >= 0x105:
                messagebox.showwarning('Path Length', 'The script path is too long and cannot be set.', parent=self.root)
                return
            self.mgr.bot_command(members, 'change_script', path=p)

    def change_profile(self, members):
        name = simpledialog.askstring('Profile', 'Enter the name of the profile to change to\n'
                                                 '(enter default for the default profile):', parent=self.root)
        if name and name.strip():
            self.mgr.bot_command(members, 'change_profile',
                                 profile='' if name.strip().lower() == 'default' else name.strip())

    def go_client(self, members):
        ans = messagebox.askyesnocancel('Go Client', 'How do you want to go back to the client?\n\n'
                                                     'Yes = Return / Teleport\nNo = Reconnect', parent=self.root)
        if ans is not None:
            self.mgr.go_client(members, reconnect=not ans)

    def go_clientless(self, members):
        if messagebox.askyesno('Go Clientless', 'Close the game client of %s?' % names(members), parent=self.root):
            self.mgr.go_clientless(members)

    def terminate(self, members):
        if messagebox.askyesno('Terminate', 'Close %s (bot and client)?\n\nThey stay closed until you use '
                                            'Restart character / Restart group bots.' % names(members), parent=self.root):
            self.mgr.terminate(members)

    # -------------------------------------------------- buttons
    def on_start(self):
        probs = [p for p in self.mgr.problems() if 'has only' not in p]
        if probs and not messagebox.askyesno('MassLeveler', 'Problems found:\n\n' + '\n'.join(probs) + '\n\nStart anyway?'):
            return
        self.mgr.start()

    def on_stop_all(self):
        if messagebox.askyesno('MassLeveler', 'Close all running bots now?'):
            self.mgr.pause()
            for g in list(self.mgr.groups):
                if g.state in ('launching', 'running'):
                    self.mgr.kill_group(g, 'stopped')

    def on_finish_all(self):
        """every running group (not the escort) goes back to town and is done; no new group starts"""
        running = [g for g in self.mgr.groups if g.state in RUNNING and not g.escort]
        if not running:
            messagebox.showinfo('MassLeveler', 'No running group.')
            return
        if messagebox.askyesno('MassLeveler', 'Finish %s now?\n\nThey go back to town and are done; no new group '
                               'starts until you press Start.' % ', '.join(g.gid for g in running)):
            self.mgr.pause()
            for g in running:
                self.mgr.send(g, 'finish')

    def on_restart_all(self):
        running = [g for g in self.mgr.groups if g.state in RUNNING]
        if not running:
            messagebox.showinfo('MassLeveler', 'No running group. Press Start - stopped groups start again first.')
            return
        if messagebox.askyesno('MassLeveler', 'Close and reopen all bots of %s?' % ', '.join(g.gid for g in running)):
            for g in running:
                self.mgr.restart_group(g)

    def on_reload(self):
        self.mgr.reload()
        self.rebuild_tree()
        for p in self.mgr.problems():
            self._log('CHECK: ' + p)

    def group_action(self, action, g=None):
        g = g or self.selected()[0]
        if not g:
            messagebox.showinfo('MassLeveler', 'Select a group (or one of its characters) first.')
            return
        if action == 'start_now':
            if not self.standard and (not self.mgr.thread or not self.mgr.thread.is_alive()):
                self.mgr.start()
                self.mgr.pause()          # loop runs, but only this group is started
            self.mgr.start_group_now(g)
        elif action in ('stop', 'start', 'finish'):
            self.mgr.send(g, action)
        elif action == 'restart':
            if messagebox.askyesno('MassLeveler', 'Close and reopen all bots of %s?' % g.gid):
                self.mgr.restart_group(g)
        elif action == 'kill':
            if messagebox.askyesno('MassLeveler', 'Close all bots of %s?' % g.gid):
                self.mgr.kill_group(g, 'stopped')
        elif action == 'mark_done':
            if messagebox.askyesno('MassLeveler', 'Mark %s finished? It turns green and is never started again '
                                   '(Reset to pending undoes it).%s' % (
                                       g.gid, '\nIts bots are closed now.' if g.state in RUNNING else '')):
                self.mgr.mark_finished(g)
        elif action == 'reset':
            if g.state in RUNNING:
                messagebox.showinfo('MassLeveler', 'Close the group first.')
                return
            if g.gid in (self.mgr.skip.get('groups') or []):
                self.mgr.set_group_skip(g, False)
            g.state = 'pending'
            g.message = ''
            for m in g.all_members:
                self.mgr.progress.pop(m.character, None)
            self.mgr.save_progress()
        self.refresh(schedule=False)

    def set_visible(self, what, show, members):
        self.mgr.set_visible(None, members, what, show)

    def on_rebuild(self):
        """tools/make_builds.py: skill plans + templates of every build in data/builds.json"""
        path = ml_core.path_in_root('tools/make_builds.py')
        spec = importlib.util.spec_from_file_location('make_builds', path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        def work():
            try:
                done, problems = mod.main(self.mgr.s['phbot_dir'], log=self._log)
                self._log('Skill plans rebuilt: %s%s' % (', '.join(done), ('  (%d warning(s))' % len(problems)) if problems else ''))
                self.mgr.reload()
            except BaseException as e:
                self._log('Rebuild failed: %s' % e)

        threading.Thread(target=work, daemon=True).start()

    def rebuild_tree(self):
        self.tree.delete(*self.tree.get_children())
        self.shown.clear()
        self.group_states.clear()
        self.refresh(schedule=False)

    def restart_selected(self):
        g, m = self.selected()
        if not m:
            messagebox.showinfo('MassLeveler', 'Select a character row first.')
            return
        if g.state not in ('launching', 'running'):
            messagebox.showinfo('MassLeveler', 'That group is not running.')
            return
        self.mgr.restart_member(g, m)

    def on_update(self):
        """GitHub: pull the files changed there since the last update (git leaves every other file as it is).
        tools/update.bat does it once this window is closed (the running exe cannot be replaced), the bots
        keep running and are taken over when it opens again. On a PC without Git or the link it installs Git
        and links the folder first."""
        git = next((p for p in (shutil.which('git'), os.path.expandvars(r'%ProgramW6432%\Git\cmd\git.exe'),
                                r'C:\Program Files\Git\cmd\git.exe',
                                os.path.expandvars(r'%LOCALAPPDATA%\Programs\Git\cmd\git.exe'))
                    if p and os.path.isfile(p)), None)
        # safe.directory: git refuses a folder another Windows user (or a copy) owns; no fsync: some drives refuse
        # it ("fsync error on .git/objects/..: Bad file descriptor"), Git for Windows turns fsyncObjectFiles on;
        # unpackLimit 1: a fetch keeps GitHub's pack file instead of one file per object in .git/objects/xx
        run = lambda *a: subprocess.run([git, '-c', 'safe.directory=*', '-c', 'core.fsyncObjectFiles=false',
                                         '-c', 'core.fsync=none', '-c', 'fetch.unpackLimit=1'] + list(a),
                                        cwd=ml_core.ROOT,
                                        capture_output=True, text=True,
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if not (git and os.path.isdir(ml_core.path_in_root('.git')) and
                run('rev-parse', '-q', '--verify', '@{u}').returncode == 0):
            if messagebox.askyesno('Update', 'First update on this PC: MassLeveler closes, Git is installed if it '
                                   'is missing (click Yes if Windows asks), the program files come from GitHub '
                                   'and MassLeveler opens again.\n\nsettings.json, accounts.csv, proxies.csv and '
                                   'data\\skip.json stay as they are; running bots are left running.\n\nContinue?'):
                self.close_and_run(r'tools\update.bat', git or '')
            return
        r = run('fetch', '--quiet')
        if r.returncode:
            messagebox.showwarning('Update', 'Could not reach GitHub:\n' + (r.stderr or r.stdout)[-500:])
            return
        files = run('diff', '--name-only', 'HEAD', '@{u}').stdout.split()
        if not files:
            messagebox.showinfo('Update', 'Already up to date.')
            return
        if not messagebox.askyesno('Update', '%d file(s) changed on GitHub:\n\n%s\n\nUpdate now? MassLeveler closes and '
                                   'opens again by itself; running bots are left running.' % (
                                       len(files), '\n'.join(files[:20]) + ('\n...' if len(files) > 20 else ''))):
            return
        self.close_and_run(r'tools\update.bat', git)

    def close_and_run(self, script, *args):
        """run a tools .bat in its own window and close; the bots keep running. The .bat is started directly:
        'cmd /c' drops the quotes around a path with spaces when an argument is quoted too. PyInstaller's own
        variables stay out of its environment: the exe the .bat opens again would take this closed one for its
        parent and stop ("invalid originating onefile parent process")."""
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith('_PYI_')}
        env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
        subprocess.Popen([ml_core.path_in_root(script)] + list(args), cwd=ml_core.ROOT, env=env,
                         creationflags=getattr(subprocess, 'CREATE_NEW_CONSOLE', 0))
        self.mgr.shutdown(kill=False)
        self.root.destroy()

    def on_close(self):
        running = [g for g in self.mgr.groups if g.state in ('launching', 'running')]
        if running:
            ans = messagebox.askyesnocancel('MassLeveler', 'Bots are running.\n\nYes = close the bots too\nNo = leave the bots running\nCancel = stay')
            if ans is None:
                return
            self.mgr.shutdown(kill=bool(ans))
        else:
            self.mgr.shutdown(kill=False)
        self.root.destroy()

    # -------------------------------------------------- refresh
    def refresh(self, schedule=True):
        while not self.logq.empty():
            self.logbox.insert('end', self.logq.get() + '\n')
            self.logbox.see('end')
        if not self.mgr.thread or not self.mgr.thread.is_alive():
            try:
                self.mgr.tick()     # keeps statuses fresh before Start is pressed
            except Exception as e:
                self._log('refresh error: %s' % e)
        counts = {}
        for g in self.mgr.groups:
            counts[g.state] = counts.get(g.state, 0) + 1
            run = g.state in RUNNING
            garea = ''
            if run and not self.standard:
                try:
                    area, spots, i = self.mgr.group_area(g)
                    if area:
                        garea = '%s   [spot %d/%d %s]' % (area['name'], i + 1, len(spots), spots[i].get('src', 'plan'))
                except Exception:
                    pass
            # colors: escort yellow, dead / error red, finished green, skipped grey
            color = 'skipped' if g.state == 'skipped' else 'bad' if g.state == 'failed' else \
                'escort' if g.escort else 'done' if g.state == 'done' else ''
            self.put(g.gid, '', g.gid, (g.min_level(), '', '', garea, '%s  %s' % (g.state, g.message)),
                     ('group', color), self.img_off if g.state == 'skipped' else self.img_on)
            if self.group_states.get(g.gid) != g.state:     # running groups open, the others fold up
                self.tree.item(g.gid, open=g.escort or run)
                self.group_states[g.gid] = g.state
            for m in g.all_members:
                st = m.status or {}
                dead = run and st.get('dead')
                state = st.get('state', '') + (' (dead)' if dead else '')
                if st.get('message'):
                    state += ' - ' + st['message']
                if self.standard and m.character in self.mgr.auto:
                    state += '   (auto start)'
                color ='skipped' if m.skip or g.state == 'skipped' else 'bad' if dead or st.get('state') == 'error' else \
                    'escort' if g.escort else 'done' if st.get('state') == 'done' or \
                    self.mgr.progress.get(m.character) == 'finished' else ''
                self.put(g.gid + '|' + m.character, g.gid, m.character,
                         (st.get('level', ''), st.get('exp_pct', ''), st.get('zone', ''), st.get('area', ''), state),
                         (color,), self.img_off if m.skip else self.img_on)
        self.lbl_summary.config(text=' · '.join('%d %s' % (counts[k], k) for k in (
            'running', 'launching', 'pending', 'done', 'stopped', 'failed', 'skipped') if counts.get(k)))
        self.lbl_dot.config(foreground=DARK['done'] if self.mgr.running else DARK['dim'])
        if self.map_shown():
            self.map.refresh()
        if schedule:
            self.root.after(2000, self.refresh)

    def map_shown(self):
        return bool(self.map) and self.tabs.select() == str(self.map.frame)

    def put(self, iid, parent, text, values, tags, image):
        """add a tree row, or draw it again only when what it shows changed"""
        tags = tuple(t for t in tags if t)
        shown = (values, tags, str(image))
        if not self.tree.exists(iid):
            self.tree.insert(parent, 'end', iid=iid, text=' ' + text, values=values, tags=tags, image=image)
        elif self.shown.get(iid) != shown:
            self.tree.item(iid, values=values, tags=tags, image=image)
        self.shown[iid] = shown


def choose_mode(root):
    """the first window: Standard (a plain phBot manager) or Mass Leveler, the last one used selected.
    'standard' / 'mass', or None when it is closed. A mode stays until the program is closed."""
    path = ml_core.path_in_root('data/last_mode.json')
    last = (ml_core.read_json(path, {}) or {}).get('mode')
    choice, sel = tk.StringVar(value=''), tk.StringVar(value=last if last in ('standard', 'mass') else 'mass')
    root.title('MassLeveler Escort')
    root.resizable(False, False)
    root.protocol('WM_DELETE_WINDOW', lambda: choice.set('quit'))
    f = ttk.Frame(root, padding=16)
    f.pack(fill='both', expand=True)
    ttk.Label(f, text='Open as:').grid(row=0, column=0, columnspan=2, sticky='w', pady=(0, 8))
    cards = {}
    for col, (mode, title, text) in enumerate((
            ('standard', 'Standard', 'A phBot manager: open the ticked characters,\nstart / stop their bots, phBot menu.\n'
                                     'No leveling.'),
            ('mass', 'Mass Leveler', 'Group leveling with the escort,\nas it is now.\n'))):
        card = tk.Frame(f, bg=DARK['panel'], highlightthickness=2, cursor='hand2', padx=12, pady=10)
        card.grid(row=1, column=col, sticky='nsew', padx=(0, 8) if col == 0 else 0)
        tk.Label(card, text=title, bg=DARK['panel'], fg=DARK['fg'], font=('Segoe UI', 10, 'bold'),
                 anchor='w').pack(fill='x')
        tk.Label(card, text=text, bg=DARK['panel'], fg=DARK['dim'], justify='left', anchor='w').pack(fill='x', pady=(4, 0))
        for w in (card,) + tuple(card.winfo_children()):
            w.bind('<Button-1>', lambda e, m=mode: choice.set(m))
            w.bind('<Enter>', lambda e, m=mode: sel.set(m))
        cards[mode] = card

    def show(*_):
        for m, c in cards.items():
            color = DARK['accent'] if sel.get() == m else DARK['line']
            c.configure(highlightbackground=color, highlightcolor=color)
    sel.trace_add('write', show)
    show()
    ttk.Label(f, foreground=DARK['dim'], text='Enter opens the selected one. To switch later, close and open again.').grid(
        row=2, column=0, columnspan=2, sticky='w', pady=(10, 0))
    keys = {'<Return>': lambda e: choice.set(sel.get()), '<Left>': lambda e: sel.set('standard'),
            '<Right>': lambda e: sel.set('mass')}
    for key, fn in keys.items():
        root.bind(key, fn)
    root.update_idletasks()
    root.geometry('+%d+%d' % ((root.winfo_screenwidth() - root.winfo_reqwidth()) // 2,
                              (root.winfo_screenheight() - root.winfo_reqheight()) // 3))
    dark_title(root)
    root.focus_force()
    root.wait_variable(choice)
    for key in keys:
        root.unbind(key)
    f.destroy()
    root.resizable(True, True)
    dark_title(root)        # changing resizable gives the window a light title bar again
    if choice.get() not in ('standard', 'mass'):
        return None
    ml_core.write_json(path, {'mode': choice.get()})
    return choice.get()


def main():
    root = tk.Tk()
    if getattr(sys, 'frozen', False):
        try:
            root.iconbitmap(default=sys.executable)     # the exe's own icon on every window
        except tk.TclError:
            pass
    apply_dark(root)
    mode = choose_mode(root)
    if not mode:
        root.destroy()
        return
    App(root, mode)
    dark_title(root)
    root.mainloop()


if __name__ == '__main__':
    main()
