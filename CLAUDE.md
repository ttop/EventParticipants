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
- `LICENSE` — GPL v2. Gramps is GPL v2-or-later and this gramplet subclasses
  `gen.plug.Gramplet` and imports `gen.lib`, `gen.db` and friends, so it is a
  derivative work: distributing it means GPL-compatible terms. Every bundled
  Gramps plugin with code in it carries the same header, as does every
  third-party addon installed here. Keep the per-file headers in step with it.
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

## Status: IN USE

Running in Gramps and used against the real tree: participants list, apply,
undo, type-ahead search and role editing all exercised. Every Gramps API call
was also checked against the 6.0 sources in
`/Applications/Gramps.app/Contents/Resources/lib/python3.13/site-packages/gramps`,
and `test_addparticipants.py` covers the non-GTK logic.

Everything that actually went wrong in use was a *behaviour* problem the source
reading could not have caught — a blocking index that looked like a broken
matcher, a default role that made new participants invisible in the Events
view, married names that live in the family record rather than on the person,
and alphabetical results burying the best match. When something looks wrong,
check the data and the running behaviour before re-reading the API.

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

- New participants default to role **Primary**. Stock Gramps defaults a
  *shared* event to Unknown, and this addon did too until it was used in
  anger: the Events view's Main Participants column only counts references
  whose role `is_primary()` (`gen/utils/db.py:274`), so anyone added with
  Unknown never appeared in that column at all. Visibility beats matching
  stock here. Change this back only with a plan for that column.
- The name index is built from **raw stored data, not objects**, and in the
  background. Going through the object API cost a point query and a full
  `Event` build for each of the birth and death years plus a `Person` build
  each — 7,200 queries and 7,200 objects on a 2,400-person tree, which is why
  it was first reported as "typing a name matches no one". `get_event_cursor()`
  and `get_person_cursor()` stream whole tables in one query each and hand
  over the stored dicts (`DataDict`, both attribute- and key-accessible), so
  the years become a dict lookup: **two cursor scans, no point queries, no
  objects**. Field names come from the `get_schema()` definitions, and
  `Date._POS_YR` is 2 in `dateval`.
- That binds to the stored layout, so `build_people_cache()` proves the raw
  path on the first row and falls back to the object API on any exception.
  Keep that guard: a silent failure here empties the index and the search box
  just stops matching, with nothing to say why.
  **The first row is only the first row**, so `_index_one()` guards every
  row as well and degrades a bad one to the object API on its own; anything
  escaping the loop is caught, and every exit from `_index_chunk()` goes
  through `_finish_index()`. An exception raised inside a GLib idle callback
  kills the source: `_index_id` stays nonzero, the placeholder sticks on
  "Indexing names..." for the rest of the session, and the sorted cache is
  never published. Keep all exits routed through `_finish_index()`.
- Labels are still built `INDEX_CHUNK` people per GLib idle turn, and the
  search box shows "Indexing names..." until it is done. `test_addparticipants.py`
  asserts the raw and object paths produce byte-identical labels and search
  text — if you touch either, keep that test honest. The `parity()` helper in
  `[S]` is the place to add a case; `_raw_surname()` mirrors
  `gen/lib/surnamebase.py:180` exactly (prefix, connector, prefix-only parts)
  precisely because the two sides must not drift.
- **Applying commits the event itself, inside the transaction.** Nothing else
  invalidates the Events view's cached participant column: that view does
  watch `person-update`, but its handler walks each person's *current* event
  refs (`plugins/view/eventview.py:156`), which by construction cannot see a
  reference that was just removed — so detachments never refreshed.
  Committing the event inside the `DbTxn` makes Gramps emit `event-update` on
  commit (`plugins/db/dbapi/dbapi.py:356`) **and replay it on undo and redo**
  (`gen/db/generic.py:288`). The earlier version emitted the signal by hand
  *after* the transaction closed, so undo never replayed it and an undone
  addition left the column overstating the count. The price is that the
  event's change timestamp moves on every apply — accepted deliberately, and
  the reason the note here used to say the event object is never modified.
- **Rows name which of an object's references to this event they mean**
  (`COL_REFNTH`), not the raw index into its `event_ref_list`. A raw index
  frozen at load time either resolved to nothing when the list shifted
  externally — dropping a staged edit in silence — or, for an object holding
  two references to one event, landed on the wrong one. The counts in the
  "Applied:" line are taken *inside* the transaction, so a row whose
  reference no longer lines up is reported as skipped instead of counted as
  done. `on_apply` also re-reads the event first and refuses to write against
  one that has been deleted.
- Roles are editable per row, with a combo backed by standard + custom roles.
  A `CellRendererCombo` drops down its list but does **not** complete as you
  type, and the editable is rebuilt for every edit, so the completion is
  attached in an `editing-started` handler — the same approach Gramps uses
  for its surname origin column (`gui/editors/displaytabs/surnametab.py:279`).
  A typed role is snapped onto the role list case- and whitespace-insensitively
  first: `EventRoleType(name)` is an *exact* string lookup
  (`gen/lib/grampstype.py:203`), so "primary" minted a CUSTOM role whose
  `is_primary()` is False and the participant vanished from Main Participants.
  A genuinely new spelling still creates a custom role — that is the supported
  way to get one — but only after failing to match a known name.
- Families are included as participants, not just people — a marriage event is
  referenced by the Family object, so a person-only list would be misleading.
  Detaching a family drops both spouses at once; that's correct but blunt.
  **A listed family also covers both its spouses in the type-ahead**
  (`_covered_by_family()`): the Events view counts them through the family
  reference, so offering one again wrote a second, personal reference at
  Primary and had the column count them twice. Giving a spouse a role of their
  own at a family event is still possible the stock way, which is the deal
  this gramplet always makes.
  The coverage is bookkeeping in three places, and all three are needed:
  the spouse handles are recorded **when the row loads** (`_family_spouses`),
  because `refresh_completion` tests the excluded set *before* its own early
  return and re-reading every listed family from the database there defeated
  it; un-detaching a family drops any spouse staged while it was detached
  (`_drop_covered_staged`), since Remove-then-Remove otherwise walked straight
  back into the double count; and `on_apply` repeats the guard where the
  writing happens rather than trusting the offer.
- `main()` only rebuilds the list when the active event handle actually changes,
  so an incidental refresh doesn't discard pending edits. When the handle
  *does* change with edits staged, it says how many it discarded, in the
  status label and the main window's status bar. Deliberately not a dialog:
  this runs inside the history's `active-changed` handler, where re-entering
  the main loop is not something a headless test can vouch for.
  **Count before clearing, on every path that discards.** Deleting the active
  event and switching trees both empty the model themselves, so by the time
  `main()` runs there is nothing left to count and the promise silently did
  not hold.
- **A status notice shows alongside the pending counts, not instead of them**
  (`update_status`). A notice that only appeared when nothing was pending was
  a notice nobody could ever see: the counts win almost every time, and the
  interesting notices ("this event changed elsewhere") are exactly the ones
  raised while edits are staged. Notices are still one-shot.
- **The gramplet watches events, families and bulk rebuilds, not just
  people.** Three classes of change used to arrive as silence:
  `event-update`/`event-delete`, because `active-changed` only fires when the
  handle changes (`gui/views/navigationview.py:206`) so editing the *active*
  event's date refreshed nothing, and editing any birth or death event left
  `_index_years`/`_index_lifespan` stale enough for `_alive_at()` to exclude
  the wrong people; `family-add`/`family-update`/`family-delete`, because
  deleting or unlinking a husband commits the *family* and never the wife, so
  no `person-update` ever names her and she kept a "m. Surname" she is no
  longer known by (`_build_spouse_map` records each family's wife in
  `_index_mothers` so a deleted family can still be traced back to her), and
  because a marriage already on screen otherwise kept the old `Family:` row
  after a relink or left a deleted one visible until some other refresh; when
  that row is part of the active event and nothing is staged, it is reloaded
  at once, and when edits are staged the gramplet says so and leaves the
  reload to Revert rather than discarding them silently; and
  `person-rebuild`/`family-rebuild`/`event-rebuild`, because importers run
  with signals disabled and announce the result with `request_rebuild()`
  alone (`gen/db/generic.py:2646`), leaving every imported person unsearchable
  until the tree was reopened. The three rebuild signals are coalesced onto
  one idle turn, and a coalesced rebuild is cancelled when the tree changes
  so an import's rebuild cannot fire against the tree that replaced it.
  `on_apply` sets `_applying` so its own transaction's signals do not
  re-enter the event handlers and wipe the result message.
- **Signal handlers must survive one bad record, and must not read the tree
  a person at a time.** `Callback.emit` swallows anything a handler raises
  with nothing but a log line (`gen/utils/callback.py:427`), so a handler that
  dies half way through a batch leaves stale labels and no sign at all — the
  same silent staleness the per-row index guards were added for. Every
  per-handle body in `_recache_people`, `on_events_changed` and
  `on_families_changed` is guarded on its own.
  And a re-cache of more than `RECACHE_CHUNK` people moves onto idle turns
  (`_queue_recache`): correcting a shared event's date names every
  participant, and a census with hundreds of them costs a read each plus
  their birth, death and family reads. Deletions stay synchronous — they do
  no reads and have to take effect at once. Within a batch, `_recache_people`
  memoises both the people and the families it touches, and hands both memos
  to `_spouse_dependents` and `_spouse_surnames_for` so the same family is
  not walked twice.
- Undated events (`get_sort_value()` of 0) are appended rather than sorted, so
  deliberate manual ordering isn't disturbed. Manual event ordering is a feature
  the user actively likes — do not add anything that bulk-reorders event lists.
- Type-ahead matches **every word, in any order, against every form of the
  name**. The display format is `LNFN` ("Surname, Given"), so a plain substring
  test failed on the way people actually type — "John Joy" never found
  "Joy, John Mervyn". The searchable text walks
  `[get_primary_name()] + get_alternate_names()` and also folds accents;
  `name_displayer.display()` alone only ever sees the primary name.
  Display and search are separate: `COMP_LABEL` is shown, `COMP_SEARCH` is
  matched.
  `_fold()` also maps the letters NFKD leaves alone — Ø, Æ, Œ, Ł, Đ, Ð, Þ,
  whose difference is the letter itself rather than a combining mark, so
  "Soren" now finds "Søren" as the docstring always claimed — and indexes an
  apostrophe-elided variant of each word, because splitting turned O'Brien
  into "o brien" and a one-word "obrien" then matched nobody.
  **The map is applied after NFKD, never before.** "ǿ" (U+01FF) decomposes to
  "ø" plus a combining acute, so translating first left the stroke behind and
  the name went into the index under a spelling nobody can type.
- **Enter compares what was typed against the name forms, not the label.**
  `_index_forms` holds each person's whole-name forms as word *sets* — one
  set per *spelling*, since `_fold` indexes an apostrophe word both split and
  elided and a single combined set made every way of typing "O'Brien" a
  strict subset of it and so never an exact match (`_fold_variants`). The
  disambiguator used to compare `_fold(label)`, which carries years and
  bracketed annotations, so anyone with a date could never be an exact match
  at all, and it demanded surname-first order. When Enter stages nobody it
  now says why — how many matches were already participants, how many were
  not living then, or that the index is still filling — each of which
  otherwise reads as a search that simply does not work.
- **Anything that can make a person match must be visible in the label**,
  otherwise a correct match looks like a bug — searching "Loretta" returned
  "Casey, Lura Ruth" with no hint that Loretta is an alternate given name she
  is recorded under. `_other_names()` puts every kind in one bracket and
  keeps them distinguishable: a bare alternate surname, `aka <given name>`,
  `nicknamed <nick>`, `called <call name>`, and `m. <surname>` for one
  reached by marriage — `Doe, Jane [Smith, aka Janie, nicknamed Janie-bug,
  m. Brown]`. In this tree 153 people carry an alias, and 156 alternate names
  add *only* a given name, so leaving those unlabelled hid the reason for
  most alias matches. Nick and call names have always been searchable and
  were invisible until they were added here. A call name is usually one of
  the given names already shown, so it only earns a place when it is not —
  tested **whole word and case-folded**, since the search index holds whole
  words: a substring test hid call name "Ann" behind given name "Annette"
  while "ann" stayed separately findable, which is the invisible match the
  rule exists to stop.
  If a future change adds another searchable field, annotate it here too.
- **Type-ahead results are ranked, so the model is rebuilt per keystroke.**
  `GtkEntryCompletion` filters but never reorders — it shows model rows in
  model order — so an alphabetical model put `Johnson, Bonnie [m. Joy]` above
  `Joy, John Mervyn` for "John Joy". `_ranked_matches()` scores each typed word
  against the words of the indexed text (whole word > start of a word > inside
  a word, and earlier positions count for more, since a person's own name
  comes before their alternate and married surnames), and
  `_update_completion()` refills the model best-first, capped at
  `COMPLETION_LIMIT`. Sub-millisecond on 2,400 people.
- **Someone who cannot have been alive at the event is left out of the
  offer.** `_alive_at()` answers True / False / **None**, and only a wholly
  undated person is None — one date is enough to infer the other to within
  `MAX_LIFESPAN` (100 years), which is what makes this worth anything, since
  723 people here have a birth and no death. `DEATH_GRACE` lets burial and
  probate follow a death; an undated event excludes nobody.
  A **christening stands in for a missing birth and a burial for a missing
  death** — the same substitutes `gen/utils/db.py:53` accepts, with the type
  codes asked of `EventType`'s own `is_birth_fallback()` /
  `is_death_fallback()` so the raw and object paths cannot drift. Reading
  only `birth_ref_index`/`death_ref_index` gave christening-only people a
  lifespan of `(0, 0)`, which reads as "no dates at all" and never excludes.
  This makes the filter fire on more people, not fewer.
  Two deliberate departures from `get_birth_or_fallback()`:
  **an undated primary event does not block a dated fallback** (that function
  stops at the first primary reference, dated or not; what is wanted here is
  a *year*, so an undated Birth must not shut out a dated Christening), and
  **`BIRTH_GRACE` mirrors `DEATH_GRACE` on the lower bound** when the birth
  year is really a christening year. A christening follows a birth exactly as
  a burial follows a death, so without it someone christened in 1842 was ruled
  out of an 1841 census. It is applied *only* to a fallback-derived year — a
  recorded birth year gets no grace, because that would be hedging. Two years
  does not cover adult baptism and is not meant to; the stock way of attaching
  a person to an event is there for that. `_index_lifespan` therefore stores
  `(birth, death, birth_grace)`.
  Years are converted to the **Gregorian** calendar first
  (`_gregorian_year` / `_raw_year`): both a raw `dateval` and
  `Date.get_year()` answer in the date's own calendar, so one Hebrew-calendar
  event read as year 5686 and ruled out the entire tree.
  This is deliberately aggressive rather than defensive. An earlier version
  only demoted, to protect against a wrong death year; that was overruled on
  the grounds that this is a *convenience* gramplet and the stock way of
  attaching a person to an event is always available when the shortlist is
  wrong. Do not quietly reintroduce hedging here.
  When a search returns nothing but people were excluded, Enter says so —
  otherwise a filtered-out person reads as a broken search.
  Real numbers: a 1950 event cuts "Wells" from 119 offers to 45, and a 1720
  event to 13. 1,057 of 2,421 people carry neither date and are never
  excluded, which caps how much this can ever narrow.
- **A married surname is almost never stored on the person.** Gramps has
  `NameType.MARRIED` for it, but in this tree exactly 1 person of 2,421 uses
  one — the surname a woman married into lives only in the family record. So
  the index also walks `get_family_cursor()` and gives **each wife her
  husband's surname**, which is what makes "Louisa Reyman" find
  `Heitt, Louisa [m. Reyman]`.
  Note the field names mislead: a Gramps `Family` is a *couple*, and its two
  spouse slots are called `father_handle` and `mother_handle` whether or not
  the couple has children. Reading those as "husband" and "wife" is what the
  code means. **Children are in `child_ref_list` and get nothing from this** —
  Louisa is a child in her parents' family and correctly does not pick up her
  father's surname there.
  **One direction only.** Wives are known by their husbands' surnames, not the
  reverse, so the surname is never exchanged — a husband must not become
  findable under his wife's maiden name. Doing it symmetrically turned a
  search for "John Joy" into every John married to a Joy. The direction is
  already in the record, so no gender lookup is needed.
- When a search behaviour looks wrong, **check what the tree actually stores**
  before changing code. The trees are at the paths listed in
  `~/Library/Application Support/gramps/recent-files-gramps.xml`, and the
  `sqlite.db` there can be read with plain `sqlite3` + `json` — the person,
  family and event rows carry a `json_data` column. Open it `file:...?mode=ro`
  and **not** `immutable=1`: that flag tells SQLite the file cannot change, and
  reading with it while Gramps has the tree open returned a stale death year
  in one run and the correct one in the next. Two rounds
  of fixing the wrong thing here would have been avoided by looking first.

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
