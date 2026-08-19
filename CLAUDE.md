# AddParticipants — a Gramps gramplet

## What this is

A Gramps addon that solves a specific annoyance: attaching one event to many
people. Stock Gramps stores the event reference on the *person*, not the event,
so sharing an event means opening each person and re-adding it one at a time.
This gramplet does the whole batch from the Events view.

It lives in the Events category bottombar. Select an event and it shows every
participant with their role; you can edit roles inline, detach participants, and
type-ahead search to add new ones. Everything applies in one `DbTxn` so a single
Edit → Undo reverses the batch.

New event references are inserted **chronologically** into each person's event
list rather than appended, which is the stock behaviour and the second thing
that prompted this project.

## Files

- `addparticipants.gpr.py` — plugin registration. `navtypes=["Event"]`,
  `gramps_target_version="6.0"`.
- `addparticipants.py` — the `AddParticipants(Gramplet)` class, all of it.
- `test_addparticipants.py` — logic tests. Gramps embeds libpython and ships
  no interpreter, so these stub out Gramps and GTK and cover the plain logic
  only. `python3 test_addparticipants.py`, no framework needed. They do not
  touch the GTK wiring.

## Environment

- macOS, Gramps 6.0 (the .app bundle from the DMG)
- Tree is ~2,400 people
- **This repo lives in `~/sources/AddParticipants`.** Edit here, never in the
  Gramps directory.
- Gramps user directory: `~/Library/Application Support/gramps/gramps60/`
- The two `.py` files are **symlinked** into
  `~/Library/Application Support/gramps/gramps60/plugins/AddParticipants/`,
  so edits in this repo are live — no copy step. If a new source file is added
  to the addon, symlink it too or Gramps won't see it.

## Testing loop

Gramps must be fully quit and relaunched to pick up code changes. Launch from
Terminal so tracebacks are visible:

```bash
/Applications/Gramps.app/Contents/MacOS/Gramps 2>&1 | tee /tmp/gramps.log
```

Then: Events view → right-click bottombar → Add Participants.

Load failures surface in the Plugin Manager (separate dialog from the Addon
Manager). Registered Plugins / Loaded Plugins tabs; double-click a failed row
for the traceback.

The `reload_plugins` addon from https://github.com/kkujansuu/gramps can refresh
plugin code without a full restart — worth installing if the restart loop gets
tedious.

## Status: NOT YET RUN IN GRAMPS

The GTK wiring has still never been exercised — nothing here has been loaded
into a running Gramps. What *has* been done: every Gramps API call was checked
against the 6.0 sources in
`/Applications/Gramps.app/Contents/Resources/lib/python3.13/site-packages/gramps`,
and `test_addparticipants.py` covers the non-GTK logic.

The original suspect list is resolved — all five were **correct as written**:

1. `self.gui.textview` removal in `init()` — matches stock
   `plugins/gramplet/events.py:65-69` exactly.
2. `EventRoleType().get_standard_names()` — exists, `gen/lib/grampstype.py:291`.
3. `db.get_event_roles()` — exists, `gen/db/generic.py:2463`, returns the
   custom role names.
4. `find_backlink_handles` — signature matches; deduplicated upstream via
   `set()` at `plugins/db/dbapi/dbapi.py:750`, so no repeated rows.
5. `connect_signal("Event", ...)` — `gen/plug/_gramplet.py:90`, connects to
   the history's `active-changed`.

Two further things worth not re-deriving:

- `main()` as a plain function (not a generator) is fine: `_updater`
  type-checks at `_gramplet.py:327` and 21 stock gramplets do the same.
- A nested `Gtk.ScrolledWindow` inside `get_container_widget()` is fine — stock
  `todogramplet.py:82-91` does it, and `grampletpane.py:964` puts a concrete
  `set_size_request(-1, height)` on the outer scroller.

**The real trap was elsewhere:** `db.get_*_from_handle()` **raises
`HandleError`** for a dangling handle rather than returning `None`
(`gen/db/generic.py:1449`). Every `if obj is None` guard was dead code. All
lookups now go through `_get_person` / `_get_family` / `_get_event`. Use those
rather than calling the db directly.

## Design decisions already made

- New participants default to role **Unknown**, matching stock Gramps behaviour
  for *shared* events (Primary is the default for events *added* in the Person
  editor, which is a different operation).
- Roles are editable per row, with a combo backed by standard + custom roles.
- Families are included as participants, not just people — a marriage event is
  referenced by the Family object, so a person-only list would be misleading.
  Detaching a family drops both spouses at once; that's correct but blunt.
- `main()` only rebuilds the list when the active event handle actually changes,
  so an incidental refresh doesn't discard pending edits.
- Undated events (`get_sort_value()` of 0) are appended rather than sorted, so
  deliberate manual ordering isn't disturbed. Manual event ordering is a feature
  the user actively likes — do not add anything that bulk-reorders event lists.

## Ideas not yet built

- Double-click a participant row to open the person editor (the stock
  Participants gramplet does this; would make this a full replacement).
- A "browse" button using Gramps' `SelectPerson` dialog alongside type-ahead.
- Chronological insertion for events added through the normal Person editor —
  would require monkey-patching Gramps internals. `ChildMerge` and `ParentPlaces`
  in the kkujansuu repo are working examples of that technique, at the cost of
  binding to internal class layouts that shift between releases.

## Conventions

- Python 3, GTK 3 via PyGObject. Match the existing code style.
- Guard optional Gramps APIs in try/except with sane fallbacks rather than
  letting the whole gramplet fail to load.
- Any database write goes inside a `DbTxn` — never commit outside one.
