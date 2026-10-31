# MassLeveler – group auto leveling 1 → 101 (Elysian, phBot)

Characters level in groups (up to 8, the party limit). Every member has a build (CH nuker, EU wizard, cleric…),
set by the group composition. Each group is created, partied and moved from area to area together, and stops when
the **lowest** member reaches 101. Then the next batch starts.

```
MassLeveler.bat ──► manager (desktop dashboard)
                     │ reads accounts.csv / proxies.csv / settings.json
                     │ writes phBot profiles + group files, starts phBot.exe for every character
                     ▼
              phBot.exe  ×8 per group  (leader = client, others = clientless)
                     │ plugin MassLeveler.py inside every bot
                     ▼
     phBot/Config/MassLeveler/  ◄── status files ──► dashboard
```

## What each part does

| Part | Job |
|---|---|
| `manager/mass_leveler.py` | Dashboard. Splits accounts into groups and launches phBot with auto login. Gives each group one connection (your IP or a proxy). Restarts crashed or frozen bots, closes a finished group and starts the next one. **Settings...** edits the server, group size, composition and window options. Show/hide buttons for the bot and the game client. |
| `plugin/MassLeveler.py` | Runs inside phBot. Registers the secondary passcode on new accounts, creates the character with its build's model and items. Collects the item-mall storage. Picks the training area from the **group's lowest level**. Loads the skill profile of its 10-level bracket, uses berserk as soon as it's full, finishes at the final level and reports status. Stays idle in bots the manager didn't start. |
| `data/builds.json` | **The builds** (CH nuker, EU wizard / cleric / warrior / bard / warlock), each made from one of your own level-110 profiles. Final skill lists, party buffs, creation items. Edit it, then press **Rebuild skill plans**. |
| `data/builds/<build>.json` + `templates/<build>.json` | Made by `tools/make_builds.py`: the 10 brackets of every build and its profile template. Don't edit them by hand. |
| `settings.json` → `escort_solo_spot` / `escort_stages` | Escort version: levels 1–4 alone at the solo spot (Jangan gate, the profiles' only training area), then the escort's 4 stages. There is no level plan any more. |

## Builds, groups and the 10 skill profiles

- **Composition** (Settings...): the build of every slot of a group, slot 1 is the leader. "CH nukers" = 8 nukers. "EU party" = warrior (leader, tank), cleric, bard, 3 wizards, warlock, cleric. Make your own with **New**. A `build` column in `accounts.csv` overrides the slot for one account.
- **10 profiles per character**: `Lv001-010` … `Lv091-100`, visible in phBot's profile list. Each holds the skills of its build for those levels, in casting order. Every final skill is replaced by the best rank of its line unlocked by the bracket's last level:
  - CH: Flame Wave Arrow → Burning → HellFire, Wolf's → Tiger's → Horse's → Crane's Thunderbolt.
  - EU: Fire Bolt → Meteor, Reverse → Grad Reverse → Reverse Immolation.
  - Level 91-100 has the final skills.
- **While leveling**: phBot drops skills the character hasn't learned yet, so the plugin loads the bracket profile with the skills learned so far. A planned rank not learned yet is replaced by the best learned lower rank of its line. When a planned skill gets learned inside the bracket, the plugin reloads it as `Lv041-050b`. The log shows `Skills updated (profile Lv041-050): attack [...]`.
- **Party buffs** per build: cleric buffs everyone; the warrior gives fences to everyone and Pain Quota to the damage dealers; CH nukers pass Vital Flow - Intellect in a ring.
- **CH nuker rules**: attacks Fire / Lightning / Cold (top rank of each important line), Force buffs only, no imbue.
- **Scrolls**: EXP, SP and speed scrolls are used while botting (`ScrollsTracingOnly` was on before, so they were never used).
- **Berserk**: used as soon as the gauge is full (plugin + phBot's own Berserk tab).
- **Resurrect**: every member resurrects dead party members with the Force skills (Rebirth Art and Resurrection – Spirit Binding). A dead member accepts resurrects from the party only.
- On every launch the manager resets these settings from the template (`MANAGED` in `ml_core.py`) and keeps the rest of what phBot saved.

## Setup (once)

1. **Plugin**: `MassLeveler.py` is already copied into `phBot Sabo\Plugins`. Keep `MoAutoEquip.py` there too, because it handles the auto-equip button at your levels.
2. **Accounts**: open `accounts.csv` and add one line per account you registered on the website:
   ```
   username,password,passcode,character,group,notes
   myacc01,mypass,,,,
   ```
   - Leave `character` empty and names are generated as `fnuk01, fnuk02…` (change `name_prefix` in `settings.json`).
   - Every 8 lines in a row form one group, and the first of the 8 is the leader.
   - The `group` column can force your own grouping.
   - Fill in `passcode` only if the server asks for a secondary passcode.
3. **Proxies** (optional): add SOCKS5 proxies to `proxies.csv`, one per line. Each proxy carries **one group of 8** (your limit is 10 per IP). Your own IP counts as one slot (`use_direct_ip`). Then raise `max_parallel_groups` in `settings.json`.
4. **Learn the item-mall collect click** (one time, with a game client):
   - Start any character with the client.
   - Open the **MassLeveler** tab in phBot and press **Learn**.
   - Within 10 s, click collect in the item storage under the minimap.
   - Select the captured packet(s) and press **Add**. All bots share the sequence (`Config/MassLeveler/macros.json`).
   - The bots collect it on their first login, and again at any level in `collect_levels`.

## Run

Double-click `MassLeveler.bat`, then press **Start**.

- Buttons for the selected group: Start now, Stop bots, Resume bots, Finish now, Close group, Reset to pending. You can also restart a single character.
- Finished characters are saved in `data/progress.json`, so a restart doesn't repeat them.

## How a group plays

1. The manager writes each character's phBot profile. The leader auto-invites the 7 members, and the members accept only the leader. Party type is `party_invite_type` = 3, your current setting.
2. phBot logs in with `--character`. If the character doesn't exist, the plugin creates it and selects it.
3. Levels 1–5: everyone plays solo, because a party isn't possible yet. From level 5 the leader's phBot forms the party.
4. Every 2 s each bot reads the other 7 status files. It uses the **lowest level** to pick the area, so all 8 switch at the same time. phBot then walks there with its own path finding.
5. When the lowest member is 101, every bot stops, uses a return scroll and waits in town. The manager then closes all 8 bots (and the leader's client) and starts the next batch.

## Settings worth knowing (`settings.json`)

| Key | Meaning |
|---|---|
| `member_mode` | `clientless` (default). Use `client_then_clientless` if pure clientless login fails, or `client` to give everyone a client. |
| `login_args` | Empty means phBot uses the server it has saved (Elysian). Use `["--locale","private-server","--privateserver","elysian"]` if it doesn't pick it. |
| `max_parallel_groups` | Groups running at once (each one needs a free IP slot). |
| `final_level` | 101 |
| `stale_minutes` / `max_restarts` | When a frozen bot is restarted, and how many times. |

To rebuild the plans or the builds:
```
py -I tools/make_builds.py
```
Tests (no game needed): `py -I tools/test_plugin.py`, `py -I tools/test_manager.py` and `py -I tools/test_escort.py`
(`tools/test_plan.json` is the old level plan, kept only for the tests).

## Check these on the first real run (not testable offline)

1. **Skill profile switch**: the log of each bot (`Config/MassLeveler/logs/<char>.txt`) should show `Skills updated (profile Lv001-010): attack [...]`, and the phBot log should then show skill casts. If it shows `attack skills are NOT active`, phBot refused `set_profile`.
2. **EU builds**: character creation with EU models and items, and the EU plan from Constantinople, are untested on a real server. Watch the first EU group.
3. **Berserk**: the log should show `Berserk used (gauge full)`. If it never appears, phBot's Berserk tab still does it.
4. **New accounts**: the passcode is registered with the packet learned on fnuk07 (stored per passcode in `macros.json`). The log shows `Passcode registered on the new account`.
5. **Character creation packet**: it uses the same packet as JellyBitz's xAcademy (vSRO format). If the log shows "Character creation failed", send me the hex line it prints.
6. **HWID limit (10)**: proxies change the IP, not the hardware ID. If Elysian counts HWID per PC, you're capped at 10 characters total no matter how many proxies you add.
7. **Walking between towns**: the higher areas are reached from Hotan (Karakoram, Taklamakan, Roc Mountain). Watch the first move from Donwhang to Hotan around level 31.
8. **Other plugins**: every plugin in `Plugins` loads in every bot (MoTelegram, MoJobGuard, MoDungeon…). Disable any you don't want in the leveling bots.
