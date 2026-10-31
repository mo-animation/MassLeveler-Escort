"""MassLeveler desktop dashboard.

Run:  py manager/mass_leveler.py            (or double-click MassLeveler.bat)
"""
import ctypes
import importlib.util
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

COLUMNS = ('role', 'build', 'level', 'exp', 'hp', 'zone', 'area', 'state', 'party', 'skills', 'restarts', 'updated')
HEADINGS = {'role': 'Role', 'build': 'Build', 'level': 'Lv', 'exp': 'EXP %', 'hp': 'HP', 'zone': 'Zone',
            'area': 'Training area', 'state': 'State', 'party': 'Party', 'skills': 'Attack skills / zerk',
            'restarts': 'Restarts', 'updated': 'Updated'}
WIDTHS = {'role': 60, 'build': 85, 'level': 40, 'exp': 60, 'hp': 90, 'zone': 120, 'area': 200, 'state': 220,
          'party': 45, 'skills': 110, 'restarts': 60, 'updated': 70}
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
        'done': '#81c784', 'bad': '#ef9a9a'}


def apply_dark(root):
    """black UI with white text: ttk widgets through the clam theme, classic tk widgets (menus, log, lists) by options"""
    c = DARK
    st = ttk.Style(root)
    st.theme_use('clam')
    st.configure('.', background=c['bg'], foreground=c['fg'], fieldbackground=c['field'], bordercolor=c['line'],
                 darkcolor=c['bg'], lightcolor=c['bg'], troughcolor=c['panel'], selectbackground=c['select'],
                 selectforeground='#ffffff', insertcolor=c['fg'], arrowcolor=c['fg'], focuscolor=c['accent'])
    st.map('.', foreground=[('disabled', c['dim'])])
    st.configure('TButton', background=c['field'], padding=(8, 3))
    st.map('TButton', background=[('pressed', c['accent']), ('active', c['hover'])])
    st.configure('Treeview', background=c['panel'], fieldbackground=c['panel'], foreground=c['fg'], rowheight=22)
    st.map('Treeview', background=[('selected', c['select'])], foreground=[('selected', '#ffffff')])
    st.configure('Treeview.Heading', background=c['field'], foreground=c['fg'], relief='flat')
    st.map('Treeview.Heading', background=[('active', c['hover'])])
    for w in ('TEntry', 'TCombobox', 'TSpinbox'):
        st.configure(w, fieldbackground=c['field'], background=c['field'], foreground=c['fg'])
        st.map(w, fieldbackground=[('readonly', c['field'])], foreground=[('readonly', c['fg'])],
               background=[('active', c['hover'])])
    st.configure('TCheckbutton', indicatorbackground=c['field'], indicatorforeground='#ffffff')
    st.map('TCheckbutton', background=[('active', c['bg'])], indicatorbackground=[('selected', c['accent'])])
    for w in ('Vertical.TScrollbar', 'Horizontal.TScrollbar'):
        st.configure(w, background=c['field'], troughcolor=c['bg'], arrowcolor=c['fg'])
        st.map(w, background=[('active', c['hover'])])
    st.configure('TSeparator', background=c['line'])
    st.configure('TPanedwindow', background=c['line'])
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
    """Server, groups and window options; the composition gives every slot of a group its build."""

    def __init__(self, app):
        self.app = app
        mgr = app.mgr
        s = mgr.s
        self.builds = sorted(mgr.builds)
        self.comps = {k: list(v) for k, v in (s.get('compositions') or {}).items()}
        top = self.top = tk.Toplevel(app.root)
        top.title('MassLeveler settings')
        top.transient(app.root)
        dark_title(top)
        top.grab_set()
        f = ttk.Frame(top, padding=10)
        f.pack(fill='both', expand=True)

        self.vars = {}
        rows = [('server', 'Server name (as in phBot)', 'entry'), ('accounts_csv', 'Accounts file', 'file'),
                ('name_prefix', 'Character name prefix', 'entry'), ('group_size', 'Players per group', 'spin8'),
                ('max_parallel_groups', 'Groups at the same time', 'spin20'), ('final_level', 'Stop at level', 'spin140'),
                ('member_mode', 'Members run with', 'mode'), ('launch_delay_seconds', 'Seconds between bot launches', 'spin120'),
                ('disconnect_restart_seconds', 'Reopen a disconnected bot after (seconds)', 'spin300'),
                ('training_radius', 'Training area radius (0 = from the plan)', 'spin200'),
                ('hide_client', 'Hide the members\' game client', 'check'),
                ('hide_leader_client', 'Hide the leader\'s game client too', 'check'),
                ('minimize_bots', 'Start member bots hidden (tray)', 'check')]
        for i, (key, label, kind) in enumerate(rows):
            ttk.Label(f, text=label).grid(row=i, column=0, sticky='w', pady=2)
            if kind == 'check':
                v = tk.BooleanVar(value=bool(s.get(key)))
                ttk.Checkbutton(f, variable=v).grid(row=i, column=1, sticky='w')
            else:
                v = tk.StringVar(value=str(s.get(key, '')))
                if kind == 'mode':
                    w = ttk.Combobox(f, textvariable=v, values=MEMBER_MODES, state='readonly', width=24)
                elif kind.startswith('spin'):
                    w = ttk.Spinbox(f, textvariable=v, from_=1, to=int(kind[4:]), width=8)
                else:
                    w = ttk.Entry(f, textvariable=v, width=40)
                w.grid(row=i, column=1, sticky='w')
                if kind == 'file':
                    ttk.Button(f, text='...', width=3, command=lambda v=v: self.pick_file(v)).grid(row=i, column=2)
            self.vars[key] = v

        r = len(rows)
        # one spot per free stage of settings.json (80+, 95+): empty = free, the user drives the escort
        spots = ml_core.high_spots(s)
        self.high = []      # (level, spot var, reverse var)
        for lv in [str(st['min_level']) for st in s.get('escort_stages') or [] if st.get('user_spot')]:
            sp = spots.get(lv) or {}
            ttk.Label(f, text='Level %s+ spot: region,X,Y,Z (empty = free, you drive the escort)' % lv).grid(
                row=r, column=0, sticky='w', pady=2)
            spot_v = tk.StringVar(value='%d,%g,%g,%g' % (sp.get('region', 0), sp['x'], sp['y'], sp.get('z', 0))
                                  if sp else '')
            ttk.Entry(f, textvariable=spot_v, width=40).grid(row=r, column=1, sticky='w')
            ttk.Label(f, text='Reverse scroll to (empty = the nearest one)').grid(row=r + 1, column=0, sticky='w', pady=2)
            rev_v = tk.StringVar(value='' if sp.get('reverse_auto', True) else sp.get('reverse', ''))
            ttk.Entry(f, textvariable=rev_v, width=40).grid(row=r + 1, column=1, sticky='w')
            if sp.get('reverse'):
                ttk.Label(f, text='now: ' + sp['reverse'], foreground=DARK['dim']).grid(row=r + 1, column=2, sticky='w')
            self.high.append((lv, spot_v, rev_v))
            r += 2
        ttk.Separator(f).grid(row=r, column=0, columnspan=3, sticky='ew', pady=8)
        ttk.Label(f, text='Group composition').grid(row=r + 1, column=0, sticky='w')
        cbar = ttk.Frame(f)
        cbar.grid(row=r + 1, column=1, columnspan=2, sticky='w')
        self.comp = tk.StringVar(value=s.get('composition', ''))
        self.cb_comp = ttk.Combobox(cbar, textvariable=self.comp, values=sorted(self.comps), state='readonly', width=22)
        self.cb_comp.pack(side='left')
        self.cb_comp.bind('<<ComboboxSelected>>', lambda e: self.show_slots())
        ttk.Button(cbar, text='New', command=self.new_comp).pack(side='left', padx=4)
        ttk.Button(cbar, text='Delete', command=self.del_comp).pack(side='left')
        self.slots_frame = ttk.Frame(f)
        self.slots_frame.grid(row=r + 2, column=0, columnspan=3, sticky='w', pady=4)
        ttk.Label(f, text='Builds: ' + ', '.join('%s = %s' % (b, mgr.builds[b].get('name', b)) for b in self.builds),
                  wraplength=560, foreground=DARK['dim']).grid(row=r + 3, column=0, columnspan=3, sticky='w')
        self.vars['group_size'].trace_add('write', lambda *a: self.show_slots())
        self.slot_vars = []
        self.show_slots()

        bar = ttk.Frame(f)
        bar.grid(row=r + 4, column=0, columnspan=3, sticky='e', pady=(10, 0))
        ttk.Button(bar, text='Save', command=self.save).pack(side='left', padx=4)
        ttk.Button(bar, text='Cancel', command=top.destroy).pack(side='left')

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
            size = max(1, int(self.vars['group_size'].get()))
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

    def save(self):
        if any(g.state in ('launching', 'running') for g in self.app.mgr.groups):
            if not messagebox.askyesno('MassLeveler', 'Groups are running. The new settings apply to groups started '
                                       'from now on. Save?', parent=self.top):
                return
        self.keep_slots()
        out = {}
        for key, v in self.vars.items():
            val = v.get()
            out[key] = int(val) if key in ('group_size', 'max_parallel_groups', 'final_level',
                                           'launch_delay_seconds', 'disconnect_restart_seconds',
                                           'training_radius') else val
        out['escort_high_spot'] = None      # the old single 80+ spot, now in escort_high_spots
        out['escort_high_spots'] = {}
        for lv, spot_v, rev_v in self.high:
            text = spot_v.get().strip()
            if not text:
                continue
            title = 'Level %s+ spot' % lv
            try:
                region, x, y, z = App.parse_coords(text)
            except ValueError as e:
                messagebox.showwarning(title, str(e), parent=self.top)
                return
            spot = {'region': region, 'x': x, 'y': y, 'z': z}
            rev = rev_v.get().strip()
            if rev:
                exact = ml_core.exact_reverse(self.app.mgr.s['phbot_dir'], rev)
                if not exact:
                    messagebox.showwarning(title, '"%s" is not in the game\'s reverse list.\nType it the way the '
                                           'reverse scroll window shows it, or leave it empty for the nearest one.'
                                           % rev, parent=self.top)
                    return
                spot.update(reverse=exact, reverse_auto=False)
            else:
                found = ml_core.nearest_reverse(self.app.mgr.s['phbot_dir'], spot)
                if not found:
                    messagebox.showwarning(title, 'No reverse point found near it in phBot\'s game data.\n'
                                           'Type the reverse point name yourself.', parent=self.top)
                    return
                spot.update(reverse=found[0], reverse_auto=True)
                messagebox.showinfo(title, 'Nearest reverse point: %s (about %d away)' % found, parent=self.top)
            out['escort_high_spots'][lv] = spot
        out['compositions'] = self.comps
        out['composition'] = self.comp.get()
        self.app.mgr.save_settings(out)
        self.app.rebuild_tree()
        self.top.destroy()


class App:
    def __init__(self, root):
        self.root = root
        self.logq = queue.Queue()
        self.mgr = ml_core.Manager(log=self._log)
        root.title('MassLeveler Escort - group auto leveling with an escort')
        root.geometry('1280x760')
        root.protocol('WM_DELETE_WINDOW', self.on_close)

        bar = ttk.Frame(root, padding=6)
        bar.pack(fill='x')
        self.btn_start = ttk.Button(bar, text='Start', command=self.on_start)
        self.btn_start.pack(side='left')
        ttk.Button(bar, text='Pause new groups', command=self.mgr.pause).pack(side='left', padx=4)
        ttk.Button(bar, text='Stop all (close bots)', command=self.on_stop_all).pack(side='left', padx=4)
        ttk.Button(bar, text='Restart bots', command=self.on_restart_all).pack(side='left')
        ttk.Separator(bar, orient='vertical').pack(side='left', fill='y', padx=8)
        ttk.Button(bar, text='Reload accounts/settings', command=self.on_reload).pack(side='left')
        ttk.Button(bar, text='accounts.csv', command=lambda: self.open_path(self.mgr.s['accounts_csv'])).pack(side='left', padx=4)
        ttk.Button(bar, text='proxies.csv', command=lambda: self.open_path(self.mgr.s['proxies_csv'])).pack(side='left')
        ttk.Button(bar, text='Settings...', command=lambda: SettingsDialog(self)).pack(side='left', padx=4)
        ttk.Button(bar, text='Builds (skills)', command=lambda: self.open_path('data/builds.json')).pack(side='left')
        ttk.Button(bar, text='Rebuild skill plans', command=self.on_rebuild).pack(side='left', padx=4)
        ttk.Button(bar, text='Training plan...', command=self.on_plan).pack(side='left')
        ttk.Button(bar, text='Update', command=self.on_update).pack(side='left', padx=4)
        self.lbl_summary = ttk.Label(bar, text='')
        self.lbl_summary.pack(side='right')

        pbar = ttk.Frame(root, padding=(6, 0, 6, 6))
        pbar.pack(fill='x')
        ttk.Label(pbar, text='phBot:').pack(side='left')
        self.lbl_phbot = ttk.Label(pbar, text='', foreground=DARK['link'])
        self.lbl_phbot.pack(side='left', padx=4)
        ttk.Button(pbar, text='Choose phBot...', command=self.on_choose_phbot).pack(side='left', padx=4)
        self.update_phbot_label()

        gbar = ttk.Frame(root, padding=(6, 0))
        gbar.pack(fill='x')
        ttk.Label(gbar, text='Selected group:').pack(side='left')
        ttk.Button(gbar, text='Start now', command=lambda: self.group_action('start_now')).pack(side='left', padx=2)
        ttk.Button(gbar, text='Stop bots', command=lambda: self.group_action('stop')).pack(side='left', padx=2)
        ttk.Button(gbar, text='Resume bots', command=lambda: self.group_action('start')).pack(side='left', padx=2)
        ttk.Button(gbar, text='Finish now (return + done)', command=lambda: self.group_action('finish')).pack(side='left', padx=2)
        ttk.Button(gbar, text='Close group', command=lambda: self.group_action('kill')).pack(side='left', padx=2)
        ttk.Button(gbar, text='Reset to pending', command=lambda: self.group_action('reset')).pack(side='left', padx=2)
        ttk.Separator(gbar, orient='vertical').pack(side='left', fill='y', padx=8)
        ttk.Button(gbar, text='Restart selected character', command=self.restart_selected).pack(side='left', padx=2)

        ttk.Label(root, padding=(6, 6, 6, 0), foreground=DARK['dim'],
                  text='Tick box = plays, no tick = skipped.   Right-click a group or character: active / skip, '
                       'group actions, show / hide bot and client (right-click empty space: all running).').pack(fill='x')

        pan = ttk.PanedWindow(root, orient='vertical')
        pan.pack(fill='both', expand=True, padx=6, pady=6)
        frame = ttk.Frame(pan)
        self.img_on, self.img_off = box_image(True), box_image(False)
        self.tree = ttk.Treeview(frame, columns=COLUMNS, selectmode='browse')
        self.tree.heading('#0', text='Group / character')
        self.tree.column('#0', width=200)
        self.tree.bind('<Button-1>', self.on_click)
        self.tree.bind('<space>', lambda e: self.toggle(self.tree.focus()) or 'break')
        self.tree.bind('<Button-3>', self.on_menu)
        for c in COLUMNS:
            self.tree.heading(c, text=HEADINGS[c])
            self.tree.column(c, width=WIDTHS[c], anchor='w')
        sb = ttk.Scrollbar(frame, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')
        self.tree.tag_configure('done', foreground=DARK['done'])
        self.tree.tag_configure('bad', foreground=DARK['bad'])
        self.tree.tag_configure('skipped', foreground=DARK['dim'])
        self.tree.tag_configure('group', font=('Segoe UI', 9, 'bold'))
        pan.add(frame, weight=4)

        logf = ttk.Frame(pan)
        self.logbox = tk.Text(logf, height=10, wrap='none', font=('Consolas', 9), bg=DARK['panel'], fg=DARK['fg'],
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
            running = [m for g in self.mgr.groups if g.state in RUNNING for m in g.members]
            self.add_item(menu, 'All running groups (%d bots)' % len(running), None, False)
            menu.add_separator()
            self.add_bot_items(menu, running)
        else:
            self.tree.selection_set(iid)
            self.tree.focus(iid)
            g, m = self.item(iid)
            if m:
                self.add_active_items(menu, g, m, '%s plays with the group' % m.character)
                self.add_item(menu, 'Restart character', self.restart_selected, g.state in RUNNING)
                self.add_spot_menu(menu, g, m)
                menu.add_separator()
                self.add_bot_items(menu, [m] if g.state in RUNNING else [])
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
        self.add_active_items(menu, g, None, 'started with the others')
        self.add_item(menu, 'Start now', lambda: self.group_action('start_now', g), not run)
        self.add_item(menu, 'Finish now (return + done)', lambda: self.group_action('finish', g), run)
        self.add_item(menu, 'Restart group bots', lambda: self.group_action('restart', g), run)
        self.add_item(menu, 'Close group', lambda: self.group_action('kill', g), run)
        self.add_item(menu, 'Mark finished (done, never started again)', lambda: self.group_action('mark_done', g),
                      g.state != 'done' and not g.escort)
        self.add_item(menu, 'Reset to pending', lambda: self.group_action('reset', g), not run)
        self.add_spot_menu(menu, g, None)
        menu.add_separator()
        self.add_bot_items(menu, list(g.members) if run else [])

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

    def on_plan(self):
        g = self.selected()[0] or next((x for x in self.mgr.groups if x.state in RUNNING), None) or \
            (self.mgr.groups[0] if self.mgr.groups else None)
        if g:
            PlanWindow(self, g)

    def on_restart_all(self):
        running = [g for g in self.mgr.groups if g.state in RUNNING]
        if not running:
            messagebox.showinfo('MassLeveler', 'No running group. Press Start - stopped groups start again first.')
            return
        if messagebox.askyesno('MassLeveler', 'Close and reopen all bots of %s?' % ', '.join(g.gid for g in running)):
            for g in running:
                self.mgr.restart_group(g)

    def update_phbot_label(self):
        p = ml_core.phbot_exe_path(self.mgr.s)
        self.lbl_phbot.config(text=os.path.normpath(p) + ('' if os.path.isfile(p) else '   (NOT FOUND)'))

    def on_choose_phbot(self):
        if any(g.state in ('launching', 'running') for g in self.mgr.groups):
            messagebox.showinfo('MassLeveler', 'Close the running groups first.')
            return
        p = filedialog.askopenfilename(title='Choose phBot.exe', initialdir=self.mgr.s['phbot_dir'],
                                       filetypes=[('phBot', '*.exe'), ('All files', '*.*')])
        if not p:
            return
        self.mgr.set_phbot(p)
        self.update_phbot_label()
        for prob in self.mgr.problems():
            self._log('CHECK: ' + prob)

    def on_reload(self):
        self.mgr.reload()
        self.update_phbot_label()
        for p in self.mgr.problems():
            self._log('CHECK: ' + p)

    def group_action(self, action, g=None):
        g = g or self.selected()[0]
        if not g:
            messagebox.showinfo('MassLeveler', 'Select a group (or one of its characters) first.')
            return
        if action == 'start_now':
            if not self.mgr.thread or not self.mgr.thread.is_alive():
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
        for iid in self.tree.get_children():
            self.tree.delete(iid)
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
        keep running and are taken over when it opens again"""
        git = shutil.which('git') or r'C:\Program Files\Git\cmd\git.exe'
        if not os.path.isfile(git) or not os.path.isdir(ml_core.path_in_root('.git')):
            messagebox.showinfo('Update', 'This folder is not linked to GitHub yet (needs Git and the repository).')
            return
        run = lambda *a: subprocess.run([git] + list(a), cwd=ml_core.ROOT, capture_output=True, text=True,
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
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
        subprocess.Popen(['cmd', '/c', ml_core.path_in_root(r'tools\update.bat'), git], cwd=ml_core.ROOT,
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
        now = time.time()
        counts = {}
        for g in self.mgr.groups:
            counts[g.state] = counts.get(g.state, 0) + 1
            garea = ''
            if g.state in RUNNING:
                try:
                    area, spots, i = self.mgr.group_area(g)
                    if area:
                        garea = '%s   [spot %d/%d %s]' % (area['name'], i + 1, len(spots), spots[i].get('src', 'plan'))
                except Exception:
                    pass
            gvals = ('', '', g.min_level(), '', '', '', garea, '%s  %s' % (g.state, g.message), '', '', '', '')
            gtags = ('group', {'done': 'done', 'skipped': 'skipped'}.get(g.state, ''))
            gimg = self.img_off if g.state == 'skipped' else self.img_on
            if self.tree.exists(g.gid):
                self.tree.item(g.gid, values=gvals, tags=gtags, image=gimg)
            else:
                self.tree.insert('', 'end', iid=g.gid, text=' ' + g.gid, values=gvals, open=True, tags=gtags, image=gimg)
            for m in g.all_members:
                st = m.status or {}
                iid = g.gid + '|' + m.character
                age = now - st.get('ts', 0) if st.get('ts') else None
                hp = '%s/%s' % (st.get('hp', ''), st.get('hp_max', '')) if st.get('hp_max') else ''
                state = st.get('state', '')
                if st.get('dead'):
                    state += ' (dead)'
                if st.get('message'):
                    state += ' - ' + st['message']
                skills = '%s / %s' % (st['attack_skills'], st.get('zerk_uses', 0)) if 'attack_skills' in st else ''
                vals = ('skip' if m.skip else m.role, m.build, st.get('level', ''), st.get('exp_pct', ''), hp, st.get('zone', ''),
                        st.get('area', ''), state, st.get('party', ''), skills, m.restarts,
                        ('%ds' % age) if age is not None and age < 3600 else '-')
                tag = 'skipped' if m.skip else 'done' if st.get('state') == 'done' or \
                    self.mgr.progress.get(m.character) == 'finished' else \
                    'bad' if st.get('state') == 'error' else ''
                img = self.img_off if m.skip else self.img_on
                if self.tree.exists(iid):
                    self.tree.item(iid, values=vals, tags=(tag,), image=img)
                else:
                    self.tree.insert(g.gid, 'end', iid=iid, text=' ' + m.character, values=vals, tags=(tag,), image=img)
        self.lbl_summary.config(text='%s   |   launching: %s' % (
            '  '.join('%s: %d' % kv for kv in sorted(counts.items())), 'ON' if self.mgr.running else 'paused'))
        if schedule:
            self.root.after(2000, self.refresh)


def main():
    root = tk.Tk()
    if getattr(sys, 'frozen', False):
        try:
            root.iconbitmap(default=sys.executable)     # the exe's own icon on every window
        except tk.TclError:
            pass
    apply_dark(root)
    App(root)
    dark_title(root)
    root.mainloop()


if __name__ == '__main__':
    main()
