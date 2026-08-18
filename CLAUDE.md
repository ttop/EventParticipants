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

## Status: UNTESTED

This code was written against the Gramps API docs but has never been run.
Syntax checks clean; nothing else is verified. Suspect areas, in order:

1. **`self.gui.textview` removal in `init()`** — the conventional gramplet
   pattern, but it has shifted between Gramps versions. If the panel renders
   blank, start here.
2. **`EventRoleType().get_standard_names()`** — wrapped in try/except with a
   hardcoded fallback list, so a mismatch degrades rather than crashes.
3. **`db.get_event_roles()`** — same, also guarded.
4. **`find_backlink_handles`** — used to load existing participants. Guarded.
5. **`Gramplet.connect_signal("Event", ...)`** — how the panel tracks the
   selected event. If it never updates when changing rows, this is why.

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
