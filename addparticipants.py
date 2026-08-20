#
# Add Participants - a Gramps gramplet
#
# Copyright (C) 2026 Todd Wells <todd@wellshub.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, see <https://www.gnu.org/licenses/>.
#
# Select an event in the Events view to see every participant (people and
# families) with their roles. Edit roles inline, detach participants, and
# type-ahead search to add new ones. All changes apply in one transaction.
#
# New event references are inserted chronologically into each person's
# event list rather than appended.
#

import logging
import re
import unicodedata
from xml.sax.saxutils import escape as xml_escape

from gi.repository import GLib, Gtk, Pango

from gramps.gen.plug import Gramplet
from gramps.gen.lib import Date, EventRef, EventRoleType, EventType
from gramps.gen.db import DbTxn
from gramps.gen.display.name import displayer as name_displayer
from gramps.gen.datehandler import get_date
from gramps.gen.errors import HandleError
from gramps.gen.const import GRAMPS_LOCALE as glocale

_ = glocale.translation.gettext

LOG = logging.getLogger(".AddParticipants")

# Columns in the participant model
COL_NAME = 0
COL_ROLE = 1
COL_STATE = 2      # internal token, never shown
COL_HANDLE = 3
COL_KIND = 4       # "Person" or "Family"
COL_ORIG_ROLE = 5
COL_REFIDX = 6     # index into the object's event_ref_list, -1 for new
COL_WEIGHT = 7     # bold for staged additions
COL_STATE_TEXT = 8  # translated text for the state column

# Columns in the type-ahead model
COMP_LABEL = 0
COMP_HANDLE = 1
COMP_SEARCH = 2    # folded text the matcher searches, never displayed

# People absorbed into the name index per idle turn. The index costs two
# database reads per person for the birth and death years, so on a few
# thousand people it must not run in one go on the main loop.
INDEX_CHUNK = 250

# Most rows the type-ahead will offer at once. GtkEntryCompletion shows model
# rows in model order, so the model is rebuilt per keystroke, best first.
COMPLETION_LIMIT = 40

# Someone who cannot have been alive when the event happened is left out of
# the offer entirely. This is a convenience gramplet: the stock way of
# attaching a person to an event is always there when the shortlist is wrong,
# so a tighter list is worth more than covering for a bad date.
#
# A missing date is inferred rather than treated as unknown: with only a
# birth, assume death within MAX_LIFESPAN of it; with only a death, assume
# birth within MAX_LIFESPAN before it. Neither date at all stays unknown, and
# unknown is never held against anyone.
MAX_LIFESPAN = 100   # years
DEATH_GRACE = 2      # burials, probate and the like follow a death

STATE_EXISTING = ""
STATE_NEW = "new"
STATE_DETACH = "detach"

# The STATE_* values double as internal tokens, so they stay untranslated
# and the visible text lives here instead.
STATE_TEXT = {
    STATE_EXISTING: "",
    STATE_NEW: _("new"),
    STATE_DETACH: _("detach"),
}


def _raw_surname(name_data):
    """SurnameBase.get_surname() from raw data.

    A faithful mirror of gen/lib/surnamebase.py:180, joiner for joiner: the
    raw and object index paths have to produce byte-identical names, and the
    married-surname map compares a husband's surname read one way against a
    wife's read the other. Dropping the connector, or skipping a surname part
    that has only a prefix, made the two disagree about the same person.
    """
    totalsurn = ""
    for surname in name_data["surname_list"]:
        fsurn = surname["surname"]
        prefix = surname["prefix"]
        if prefix:
            fsurn = _("%(first)s %(second)s") % {"first": prefix,
                                                 "second": fsurn}
        fsurn = fsurn.strip()
        connector = surname["connector"]
        if connector:
            fsurn = _("%(first)s %(second)s") % {"first": fsurn,
                                                 "second": connector}
        fsurn = fsurn.strip()
        totalsurn = _("%(first)s %(second)s") % {"first": totalsurn,
                                                 "second": fsurn}
    return totalsurn.strip()


def _fallback_type_values():
    """Event type codes that stand in for a missing birth or death.

    Asked of EventType rather than listed out, so the raw path cannot drift
    from the is_birth_fallback()/is_death_fallback() predicates
    (gen/lib/eventtype.py:327) the object path uses. These are the same
    substitutes gen/utils/db.py:53 accepts.
    """
    births, deaths = set(), set()
    try:
        for value in EventType().get_map():
            probe = EventType(value)
            if probe.is_birth_fallback():
                births.add(value)
            if probe.is_death_fallback():
                deaths.add(value)
    except Exception:
        LOG.debug("could not enumerate the event fallback types", exc_info=True)
    return frozenset(births), frozenset(deaths)


BIRTH_FALLBACKS, DEATH_FALLBACKS = _fallback_type_values()


def _gregorian_year(date):
    """The year of a Date in the Gregorian calendar, or 0.

    Date.get_year() answers in the date's *own* calendar, so one event
    entered in the Hebrew calendar reads as year 5686: nonsense in a label,
    and on its own enough for _alive_at() to rule out everybody in the tree.
    gen/lib/date.py:2133 converts the same way.
    """
    if date is None:
        return 0
    try:
        if date.get_calendar() != Date.CAL_GREGORIAN:
            converted = Date(date)
            converted.convert_calendar(Date.CAL_GREGORIAN)
            return converted.get_year() or 0
    except Exception:
        LOG.debug("could not convert a date to the Gregorian calendar",
                  exc_info=True)
    return date.get_year() or 0


def _raw_year(date_data):
    """The Gregorian year of a stored date, or 0.

    dateval carries the year in whatever calendar the date was entered in,
    so a non-Gregorian one is rebuilt just far enough to convert it. Nearly
    every date is Gregorian and constructs nothing.
    """
    if not date_data:
        return 0
    dateval = date_data["dateval"]
    if not dateval or len(dateval) <= Date._POS_YR:
        return 0
    year = dateval[Date._POS_YR] or 0
    calendar = date_data["calendar"]
    if not year or not calendar:
        return year
    try:
        date = Date()
        date.set(quality=date_data["quality"],
                 modifier=date_data["modifier"],
                 calendar=calendar,
                 value=tuple(dateval),
                 text=date_data["text"],
                 newyear=date_data["newyear"])
        return _gregorian_year(date)
    except Exception:
        LOG.debug("could not convert a stored date to the Gregorian calendar",
                  exc_info=True)
        return year


def _fold(text):
    """Reduce text to lowercase, unaccented, space-separated words.

    Both the typed key and the searchable text go through this, so "Muller"
    finds "Müller" and punctuation in the display format stops mattering.
    GTK casefolds the key it hands us but leaves accents in place.
    """
    decomposed = unicodedata.normalize("NFKD", text or "")
    bare = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(part for part in re.split(r"[^\w]+", bare.casefold()) if part)


class AddParticipants(Gramplet):
    """View and edit every participant of the active event."""

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def init(self):
        self.event = None
        self.event_handle = None
        self.people_cache = []      # sorted list of (label, handle, search)
        self.people_labels = {}     # handle -> (label, search)
        self._completion_excluded = frozenset()
        self._matches = []          # ranked (label, handle) for the typed text
        self._index_id = 0          # idle source building the name index
        self._index_iter = None
        self._index_touched = set() # changed since the snapshot, build skips them
        self._index_raw = False     # reading raw data rather than objects
        self._index_years = {}      # event handle -> year, for labels
        self._index_spouses = {}    # person handle -> spouse surnames
        self._index_lifespan = {}   # person handle -> (birth year, death year)
        self._not_living = 0        # left out of the last search by date
        self.gui.WIDGET = self.build_gui()
        container = self.gui.get_container_widget()
        if self.gui.textview in container.get_children():
            container.remove(self.gui.textview)
        container.add(self.gui.WIDGET)
        self.gui.WIDGET.show_all()

    def build_gui(self):
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        vbox.set_border_width(6)

        # --- Header: which event are we working on? ---
        self.header = Gtk.Label()
        self.header.set_halign(Gtk.Align.START)
        self.header.set_line_wrap(True)
        self.header.set_markup("<i>%s</i>" % _("No event selected."))
        vbox.pack_start(self.header, False, False, 0)

        # --- Search entry with type-ahead ---
        self.completion_model = Gtk.ListStore(str, str, str)
        completion = Gtk.EntryCompletion()
        completion.set_model(self.completion_model)
        completion.set_text_column(0)
        completion.set_minimum_key_length(2)
        completion.set_match_func(self._match_func, None)
        completion.connect("match-selected", self.on_match_selected)

        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text(_("Type a name to add someone..."))
        self.entry.set_completion(completion)
        self.entry.connect("activate", self.on_entry_activate)
        # Connected after set_completion so GTK's own handler runs first and
        # then sees the reordered model.
        self.entry.connect("changed", self._update_completion)
        vbox.pack_start(self.entry, False, False, 0)

        # --- Participants ---
        self.model = Gtk.ListStore(
            str, str, str, str, str, str, int, int, str
        )
        self.role_model = Gtk.ListStore(str)

        tree = Gtk.TreeView(model=self.model)
        tree.set_headers_visible(True)

        name_cell = Gtk.CellRendererText()
        name_col = Gtk.TreeViewColumn(_("Participant"))
        name_col.pack_start(name_cell, True)
        name_col.add_attribute(name_cell, "text", COL_NAME)
        name_col.add_attribute(name_cell, "weight", COL_WEIGHT)
        name_col.set_expand(True)
        tree.append_column(name_col)

        role_cell = Gtk.CellRendererCombo()
        role_cell.set_property("model", self.role_model)
        role_cell.set_property("text-column", 0)
        role_cell.set_property("editable", True)
        role_cell.set_property("has-entry", True)
        role_cell.connect("edited", self.on_role_edited)
        role_cell.connect("editing-started", self.on_role_editing_started)
        role_col = Gtk.TreeViewColumn(_("Role"), role_cell, text=COL_ROLE)
        role_col.set_min_width(140)
        tree.append_column(role_col)

        state_col = Gtk.TreeViewColumn(
            "", Gtk.CellRendererText(), text=COL_STATE_TEXT
        )
        tree.append_column(state_col)

        self.tree = tree

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_shadow_type(Gtk.ShadowType.IN)
        scroll.add(tree)
        vbox.pack_start(scroll, True, True, 0)

        # --- Buttons ---
        bbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        remove_btn = Gtk.Button.new_with_label(_("Remove"))
        remove_btn.set_tooltip_text(
            _("Unstage a new addition, or mark an existing "
              "participant to be detached from this event")
        )
        remove_btn.connect("clicked", self.on_remove)
        bbox.pack_start(remove_btn, False, False, 0)

        revert_btn = Gtk.Button.new_with_label(_("Revert"))
        revert_btn.set_tooltip_text(_("Discard all pending changes"))
        revert_btn.connect("clicked", self.on_revert)
        bbox.pack_start(revert_btn, False, False, 0)

        self.status = Gtk.Label()
        self.status.set_halign(Gtk.Align.START)
        self.status.set_ellipsize(Pango.EllipsizeMode.END)
        bbox.pack_start(self.status, True, True, 0)

        self.apply_btn = Gtk.Button.new_with_label(_("Apply"))
        self.apply_btn.connect("clicked", self.on_apply)
        self.apply_btn.set_sensitive(False)
        bbox.pack_end(self.apply_btn, False, False, 0)

        vbox.pack_start(bbox, False, False, 0)
        return vbox

    # ------------------------------------------------------------------
    # Database wiring
    # ------------------------------------------------------------------

    def db_changed(self):
        # A new tree invalidates the cached selection. Without this the
        # "did the handle change?" test in main() can leave the previous
        # tree's participants on screen.
        self.event = None
        self.event_handle = None
        self.model.clear()
        self.connect(self.dbstate.db, "person-add", self.on_people_changed)
        self.connect(self.dbstate.db, "person-update", self.on_people_changed)
        self.connect(self.dbstate.db, "person-delete", self.on_people_deleted)
        self.connect_signal("Event", self.update)
        self.build_people_cache()
        self.build_role_model()

    def on_people_changed(self, handles=None):
        """Refresh only the people that actually changed.

        Gramps emits these signals once per transaction with a list of
        handles, so rebuilding the whole cache here meant re-reading every
        person in the tree after any unrelated edit.
        """
        self._recache_people(handles, removed=False)

    def on_people_deleted(self, handles=None):
        self._recache_people(handles, removed=True)

    def _recache_people(self, handles, removed):
        if handles is None:
            self.build_people_cache()
            return
        removed_set = set(handles) if removed else set()
        # Read each touched person once; the spouse lookups below reuse it, so
        # a person with no families still costs a single read, as before.
        people = {}
        for handle in handles:
            if handle not in removed_set:
                people[handle] = self._get_person(handle)
        # Rebuild the labels of the touched people plus any spouse whose
        # married-surname set depends on one of them: a new or renamed husband
        # changes the name his wives are searchable by, and a new wife gains
        # the surname she married into.
        to_cache = set(people)
        to_cache.update(removed_set)
        for handle, person in people.items():
            if person is not None:
                to_cache.update(self._spouse_dependents(handle, person))
        # Keep the married-surname map in step before the labels are rebuilt,
        # since both the label and the search text read from it.
        for handle in to_cache:
            if handle in removed_set:
                self._index_spouses.pop(handle, None)
                continue
            person = people.get(handle)
            if person is None:
                person = self._get_person(handle)
            if person is not None:
                self._index_spouses[handle] = self._spouse_surnames_for(
                    handle, person)
        for handle in to_cache:
            self.people_labels.pop(handle, None)
            if handle in removed_set:
                continue
            person = people.get(handle)
            if person is None:
                person = self._get_person(handle)
            if person is not None:
                self.people_labels[handle] = self._person_entry(person)
        if self._index_id:
            # A build is in flight; it publishes the sorted list when it
            # finishes, so skip the expensive re-sort but keep the change -
            # iter_people() may already be past this person.
            #
            # The raw build walks a snapshot of the table taken before these
            # changes, so mark the handles it must not clobber: an add or
            # update is already reflected above, and a delete must stay gone.
            self._index_touched.update(to_cache)
            return
        self._sort_people_cache()

    @staticmethod
    def _primary_surname(person):
        if person is None:
            return ""
        primary = person.get_primary_name()
        return primary.get_surname() if primary else ""

    def _spouse_surnames_for(self, handle, person):
        """Surnames `person` is known by through marriage, read live from her
        families: for each family where she is the wife, the husband's
        surname unless it is already her own. Mirrors _build_spouse_map for a
        single person, so someone added or renamed mid-session is searchable
        by the name she married into without a full rebuild."""
        own = self._primary_surname(person)
        result = set()
        for fam_handle in person.family_list:
            family = self._get_family(fam_handle)
            if family is None or family.get_mother_handle() != handle:
                continue
            father = family.get_father_handle()
            if not father:
                continue
            surname = self._primary_surname(self._get_person(father))
            if surname and surname != own:
                result.add(surname)
        return sorted(result)

    def _spouse_dependents(self, handle, person):
        """Spouses whose surname set includes `person`'s - her husbands' wives
        take his surname, so they must be re-cached when a husband is added or
        renamed; a wife's own set does not depend on anyone else's."""
        wives = set()
        for fam_handle in person.family_list:
            family = self._get_family(fam_handle)
            if family is not None and family.get_father_handle() == handle:
                mother = family.get_mother_handle()
                if mother:
                    wives.add(mother)
        return wives

    # Gramps raises HandleError for a dangling handle rather than returning
    # None, so every lookup needs a guard: one broken reference anywhere in
    # the tree would otherwise take out the whole gramplet.

    def _get_person(self, handle):
        try:
            return self.dbstate.db.get_person_from_handle(handle)
        except HandleError:
            return None

    def _get_family(self, handle):
        try:
            return self.dbstate.db.get_family_from_handle(handle)
        except HandleError:
            return None

    def _get_event(self, handle):
        try:
            return self.dbstate.db.get_event_from_handle(handle)
        except HandleError:
            return None

    def build_people_cache(self):
        """Start rebuilding the name index in the background.

        Indexing costs two database reads per person for the birth and death
        years, so on a few thousand people doing it in one pass blocks the
        main loop: the search box sits there matching nothing, with no sign
        that anything is happening.
        """
        self._cancel_index()
        db = self.dbstate.db
        self.people_labels = {}
        self._index_lifespan = {}
        self._index_touched = set()
        self._sort_people_cache()
        if db is None or not db.is_open():
            self._show_index_progress(done=True)
            return
        # Prefer raw data. Building labels through the object API costs a
        # query and a full Event construction for each of the birth and death
        # years, plus a Person construction each - thousands of queries and
        # object builds. The cursors stream the whole table in one query and
        # hand over the stored dicts, so the years become a lookup and no
        # Person or Event is ever constructed.
        self._index_raw = True
        self._index_years = {}
        self._index_fallbacks = {}
        try:
            self._index_years, self._index_fallbacks = \
                self._build_event_maps(db)
            with db.get_person_cursor() as cursor:
                rows = [data for _handle, data in cursor]
            self._index_spouses = self._build_spouse_map(
                db, {data["handle"]: _raw_surname(data["primary_name"])
                     for data in rows}
            )
            if rows:
                # Prove the raw layout before committing to it, so a changed
                # field name degrades to the object API instead of silently
                # producing an empty index.
                self._raw_person_entry(rows[0])
            self._index_iter = iter(rows)
        except Exception:
            LOG.debug("raw indexing unavailable; using the object API",
                      exc_info=True)
            self._index_raw = False
            self._index_years = {}
            self._index_fallbacks = {}
            try:
                handles = list(db.get_person_handles())
            except Exception:
                handles = [person.get_handle() for person in db.iter_people()]
            try:
                self._index_spouses = self._build_spouse_map(db, None)
            except Exception:
                LOG.debug("spouse surnames unavailable", exc_info=True)
                self._index_spouses = {}
            self._index_iter = iter(handles)
        self._index_id = GLib.idle_add(
            self._index_chunk, priority=GLib.PRIORITY_LOW
        )
        self._show_index_progress(done=False)

    def _cancel_index(self):
        """Drop any in-flight index build, e.g. when the tree changes."""
        if self._index_id:
            GLib.source_remove(self._index_id)
        self._index_id = 0
        self._index_iter = None

    def _index_chunk(self):
        """Absorb one slice of people, then yield back to the main loop."""
        if self._index_iter is None:
            self._finish_index()
            return False
        absorbed = 0
        try:
            for item in self._index_iter:
                self._index_one(item)
                absorbed += 1
                if absorbed >= INDEX_CHUNK:
                    self._show_index_progress(done=False)
                    return True
        except Exception:
            # Anything that escapes here takes the idle source with it:
            # PyGObject drops the callback, _index_id stays set, the search
            # box sits on "Indexing names..." for the rest of the session and
            # the sorted index is never published. Publish what there is.
            LOG.exception(
                "Add Participants: indexing stopped early; "
                "the name index may be incomplete"
            )
        self._finish_index()
        return False

    def _finish_index(self):
        """Publish the index and clear the idle bookkeeping.

        Every exit from _index_chunk comes through here, successful or not:
        leaving _index_id set once the idle source is gone is what strands
        the placeholder and the completion.
        """
        self._index_id = 0
        self._index_iter = None
        self._sort_people_cache()
        self._show_index_progress(done=True)

    def _index_one(self, item):
        """Absorb one person, degrading a bad raw row to the object API.

        build_people_cache() proves the raw layout on the first row only, so
        a row further in that does not fit is the first sign the stored shape
        is not what this expects. Read that one person as an object rather
        than losing the rest of the build.
        """
        if not self._index_raw:
            self._index_person_object(item)
            return
        handle = None
        try:
            handle = item["handle"]
            # A concurrent add/update/delete owns this row now; the
            # pre-captured snapshot is stale for it, so leave it alone
            # rather than clobbering the fresher value (or re-adding a
            # person who was deleted).
            if handle in self._index_touched:
                return
            self.people_labels[handle] = self._raw_person_entry(item)
        except Exception:
            LOG.debug("raw row unusable; reading %s as an object", handle,
                      exc_info=True)
            if handle and handle not in self._index_touched:
                self._index_person_object(handle)

    def _index_person_object(self, handle):
        """Index one person through the object API, or skip them.

        A person nothing can read is left out of the offer rather than
        stopping everyone else from being indexed.
        """
        try:
            person = self._get_person(handle)
            if person is not None:
                self.people_labels[handle] = self._person_entry(person)
        except Exception:
            LOG.debug("could not index person %s", handle, exc_info=True)

    def _build_event_maps(self, db):
        """(handle -> Gregorian year, handle -> fallback type) in one query.

        The second map holds only the types that can stand in for a missing
        birth or death, which is the only reason the raw path needs an event
        type at all.
        """
        years = {}
        fallbacks = {}
        with db.get_event_cursor() as cursor:
            for _handle, data in cursor:
                handle = data["handle"]
                year = _raw_year(data["date"])
                if year:
                    years[handle] = year
                value = data["type"]["value"]
                if value in BIRTH_FALLBACKS or value in DEATH_FALLBACKS:
                    fallbacks[handle] = value
        return years, fallbacks

    def _build_spouse_map(self, db, surname_by_handle):
        """Person handle -> surnames they married into.

        Most trees never record a married name: it is implied by the marriage
        and lives only in the family record, so searching for someone under
        the surname they married into has to come from here rather than from
        their alternate names.

        A Gramps Family record is a couple, and its two spouse slots are
        named father_handle and mother_handle whether or not there are any
        children. So this reads "the husband's surname, given to the wife".
        Children sit in child_ref_list and get nothing from it.

        One direction only: wives are known by their husbands' surnames and
        not the reverse, so the surname is never exchanged - a husband must
        not become findable under his wife's maiden name. Matching both ways
        turned a search for "John Joy" into every John married to a Joy.
        """
        if surname_by_handle is None:
            surname_by_handle = {
                person.get_handle():
                    person.get_primary_name().get_surname()
                for person in db.iter_people()
            }
        spouses = {}
        with db.get_family_cursor() as cursor:
            for _handle, data in cursor:
                father = data["father_handle"]
                mother = data["mother_handle"]
                if not father or not mother:
                    continue
                surname = surname_by_handle.get(father)
                if surname and surname != surname_by_handle.get(mother):
                    spouses.setdefault(mother, set()).add(surname)
        return {handle: sorted(names) for handle, names in spouses.items()}

    def _other_names(self, handle, primary_given, primary_surname,
                     alt_givens, alt_surnames):
        """Every other name this person answers to, for the label.

        Anything here can make the person match, so it has to be visible:
        otherwise searching "Loretta" turns up "Casey, Lura Ruth" with no
        indication of why. Alternate surnames appear as they are, an
        alternate given name is marked "aka", and a surname reached by
        marriage is marked "m." - which reads correctly for a husband too,
        since he is not known by his wife's surname but is married to it.
        """
        others = []
        for surname in alt_surnames:
            if surname and surname != primary_surname and surname not in others:
                others.append(surname)
        for given in alt_givens:
            if given and given != primary_given:
                marked = _("aka %s") % given
                if marked not in others:
                    others.append(marked)
        for surname in self._index_spouses.get(handle, ()):
            marked = _("m. %s") % surname  # married into, not her own
            if surname != primary_surname and marked not in others:
                others.append(marked)
        return others

    @staticmethod
    def _decorate(name, others, years):
        """name [other names] (b. YYYY d. YYYY).

        One place, so the raw and object paths cannot format the same person
        differently - that byte-parity is what the tests hold them to.
        """
        label = name
        if others:
            label += " [%s]" % ", ".join(others)
        marked = ["%s %d" % (marker, year)
                  for marker, year in zip(("b.", "d."), years) if year]
        if marked:
            label += " (%s)" % " ".join(marked)
        return label

    def _raw_person_years(self, data):
        """(birth year, death year) from stored data, via the year map.

        Plenty of people have no birth event at all, only a christening, and
        no death event, only a burial. Reading nothing but
        birth_ref_index/death_ref_index left them with no years in the label
        and nothing for _alive_at() to exclude them by - a filter that fires
        on more people, not fewer, which is the point of it.
        """
        refs = data["event_ref_list"]
        years = [0, 0]
        found = [False, False]
        for slot, key in enumerate(("birth_ref_index", "death_ref_index")):
            index = data[key]
            if index is not None and 0 <= index < len(refs):
                years[slot] = self._index_years.get(refs[index]["ref"], 0)
                found[slot] = True
        if not all(found):
            for ref in refs:
                if all(found):
                    break
                if ref["role"]["value"] != EventRoleType.PRIMARY:
                    continue
                value = self._index_fallbacks.get(ref["ref"])
                if value is None:
                    continue
                # A stillbirth stands in for both, so neither test excludes
                # the other - the same as get_birth_or_fallback() and
                # get_death_or_fallback() run independently.
                if not found[0] and value in BIRTH_FALLBACKS:
                    years[0] = self._index_years.get(ref["ref"], 0)
                    found[0] = True
                if not found[1] and value in DEATH_FALLBACKS:
                    years[1] = self._index_years.get(ref["ref"], 0)
                    found[1] = True
        return tuple(years)

    def _raw_person_entry(self, data):
        """(label, folded search text) straight from stored person data."""
        years = self._raw_person_years(data)
        self._index_lifespan[data["handle"]] = years
        label = self._raw_person_label(data, years)
        return label, self._raw_person_search_text(data, label)

    def _raw_person_label(self, data, years):
        """The object path's _person_label, without building a Person."""
        primary = data["primary_name"]
        alternates = data["alternate_names"]
        name = name_displayer.raw_display_name(primary)
        primary_surname = _raw_surname(primary)
        others = self._other_names(
            data["handle"], primary["first_name"], primary_surname,
            [alt["first_name"] for alt in alternates],
            [_raw_surname(alt) for alt in alternates],
        )
        return self._decorate(name, others, years)

    def _raw_person_search_text(self, data, label):
        """The object path's _person_search_text, from stored data."""
        parts = [label]
        for name_data in [data["primary_name"]] + list(data["alternate_names"]):
            parts.append(name_displayer.raw_display_name(name_data))
            parts.append(name_data["first_name"])
            parts.append(_raw_surname(name_data))
            parts.append(name_data["call"])
            parts.append(name_data["nick"])
        parts.extend(self._index_spouses.get(data["handle"], ()))
        return _fold(" ".join(part for part in parts if part))

    def _show_index_progress(self, done):
        """Say so in the search box while the index is still filling."""
        if done:
            self.entry.set_placeholder_text(
                _("Type a name to add someone...")
            )
        else:
            self.entry.set_placeholder_text(
                _("Indexing names... %d so far") % len(self.people_labels)
            )

    def _sort_people_cache(self):
        """Derive the sorted (label, handle) list the completion reads."""
        self.people_cache = sorted(
            ((label, handle, search)
             for handle, (label, search) in self.people_labels.items()),
            key=lambda row: row[0],
        )
        # The completion is built from this list, so it has to be rebuilt.
        self.refresh_completion(force=True)

    def _person_label(self, person, years=None):
        """Primary name, any other surnames, then birth/death years.

        `years` is the pair _person_entry has already read; passing it in
        stops the birth and death events being fetched twice per person.
        """
        if years is None:
            years = self._person_years(person)
        name = name_displayer.display(person)
        primary = person.get_primary_name()
        primary_surname = primary.get_surname() if primary else ""
        primary_given = primary.get_first_name() if primary else ""
        alternates = person.get_alternate_names()
        others = self._other_names(
            person.get_handle(), primary_given, primary_surname,
            [alt.get_first_name() for alt in alternates],
            [alt.get_surname() for alt in alternates],
        )
        return self._decorate(name, others, years)

    def _person_search_text(self, person, label):
        """Every form of the name, folded, for the type-ahead to search.

        A married name is an *alternate* name, so searching only the primary
        name never finds it. This walks [primary] + alternates, the same
        idiom the rest of Gramps uses.
        """
        parts = [label]
        for name in [person.get_primary_name()] + person.get_alternate_names():
            parts.append(name_displayer.display_name(name))
            parts.append(name.get_first_name())
            parts.append(name.get_surname())
            parts.append(name.get_call_name())
            parts.append(name.get_nick_name())
        parts.extend(self._index_spouses.get(person.get_handle(), ()))
        return _fold(" ".join(part for part in parts if part))

    def _ref_year(self, ref):
        """The Gregorian year of the event a reference points at, or 0."""
        event = self._get_event(ref.ref)
        if event is None:
            return 0
        return _gregorian_year(event.get_date_object())

    def _person_years(self, person):
        """(birth year, death year) in the Gregorian calendar, 0 for unknown.

        The object-path twin of _raw_person_years: a christening stands in
        for a missing birth and a burial for a missing death, the same
        substitutes gen/utils/db.py:53 accepts.
        """
        years = [0, 0]
        found = [False, False]
        for slot, ref in enumerate((person.get_birth_ref(),
                                    person.get_death_ref())):
            if ref:
                years[slot] = self._ref_year(ref)
                found[slot] = True
        if not all(found):
            for ref in person.get_primary_event_ref_list():
                if all(found):
                    break
                event = self._get_event(ref.ref)
                if event is None:
                    continue
                etype = event.get_type()
                if not found[0] and etype.is_birth_fallback():
                    years[0] = _gregorian_year(event.get_date_object())
                    found[0] = True
                if not found[1] and etype.is_death_fallback():
                    years[1] = _gregorian_year(event.get_date_object())
                    found[1] = True
        return tuple(years)

    def _person_entry(self, person):
        """(display label, folded search text) for one person."""
        years = self._person_years(person)
        self._index_lifespan[person.get_handle()] = years
        label = self._person_label(person, years)
        return label, self._person_search_text(person, label)

    def _family_label(self, family):
        names = []
        for get_handle in (family.get_father_handle, family.get_mother_handle):
            handle = get_handle()
            if handle:
                person = self._get_person(handle)
                if person:
                    names.append(name_displayer.display(person))
        if names:
            return _("Family: %s") % " & ".join(names)
        return _("Family %s") % family.get_gramps_id()

    def build_role_model(self):
        """Standard event roles plus any custom ones already in the tree."""
        self.role_model.clear()
        try:
            names = list(EventRoleType().get_standard_names())
        except Exception:
            names = [_("Primary"), _("Family"), _("Witness"), _("Unknown")]
        try:
            for role in self.dbstate.db.get_event_roles():
                if str(role) not in names:
                    names.append(str(role))
        except Exception:
            pass
        for name in names:
            self.role_model.append([name])

    def _default_role(self):
        """Primary, so the participant shows up where people look for it.

        Stock Gramps defaults a *shared* event to Unknown, but the Events
        view's Main Participants column only counts references whose role
        is_primary() (gen/utils/db.py:274). Defaulting to Unknown meant a
        person added here never appeared in that column at all.
        """
        return str(EventRoleType(EventRoleType.PRIMARY))

    # ------------------------------------------------------------------
    # Active event tracking
    # ------------------------------------------------------------------

    def main(self):
        handle = self.get_active("Event")
        self.event = self._get_event(handle) if handle else None

        if self.event is None:
            self.event_handle = None
            self.model.clear()
            self.completion_model.clear()
            self.header.set_markup("<i>%s</i>" % _("No event selected."))
            self.update_status()
            return

        # Only rebuild when the event actually changed, so pending edits
        # survive an incidental refresh.
        if handle != self.event_handle:
            self.event_handle = handle
            self.load_participants()

        date_text = get_date(self.event) or _("no date")
        label = "<b>%s</b> &#8212; %s" % (
            xml_escape(str(self.event.get_type())),
            xml_escape(date_text),
        )
        desc = self.event.get_description()
        if desc:
            label += "\n%s" % xml_escape(desc)
        self.header.set_markup(label)

        self.refresh_completion()
        self.update_status()

    def load_participants(self):
        """Populate the list from everything referencing this event."""
        self.model.clear()
        db = self.dbstate.db
        ev_handle = self.event.get_handle()

        try:
            backlinks = list(
                db.find_backlink_handles(ev_handle, ["Person", "Family"])
            )
        except Exception:
            backlinks = []

        for class_name, handle in backlinks:
            if class_name == "Person":
                obj = self._get_person(handle)
                label = self._person_label(obj) if obj else None
            elif class_name == "Family":
                obj = self._get_family(handle)
                label = self._family_label(obj) if obj else None
            else:
                continue
            if obj is None:
                continue
            for index, ref in enumerate(obj.get_event_ref_list()):
                if ref.ref != ev_handle:
                    continue
                role = str(ref.get_role())
                self.model.append(
                    [label, role, STATE_EXISTING, handle,
                     class_name, role, index, int(Pango.Weight.NORMAL),
                     STATE_TEXT[STATE_EXISTING]]
                )

    def refresh_completion(self, force=False):
        """Note who is already listed and refresh what the type-ahead offers.

        This runs on every event selection, so skip the work unless the set
        of excluded people actually moved. Callers that changed the people
        cache itself pass force=True.
        """
        listed = frozenset(
            row[COL_HANDLE]
            for row in self.model
            if row[COL_KIND] == "Person" and row[COL_STATE] != STATE_DETACH
        )
        if not force and listed == self._completion_excluded:
            return
        self._completion_excluded = listed
        self._update_completion()

    def _update_completion(self, *_):
        """Refill the type-ahead with the best matches for what is typed.

        GtkEntryCompletion filters but never reorders: it shows model rows in
        model order. Ranking therefore means rebuilding the model itself,
        which also keeps the popup to a readable size.
        """
        text = self.entry.get_text().strip()
        self._matches = self._ranked_matches(text) if text else []
        self.completion_model.clear()
        for label, handle, search in self._matches[:COMPLETION_LIMIT]:
            self.completion_model.append([label, handle, search])

    def _event_year(self):
        """Gregorian year of the selected event, or 0 when it is undated."""
        if self.event is None:
            return 0
        return _gregorian_year(self.event.get_date_object())

    def _alive_at(self, handle, year):
        """True, False, or None when neither date is recorded.

        Only a wholly undated person is unknown. One date is enough to infer
        the other to within MAX_LIFESPAN, which is what makes this worth
        anything: most people here have a birth year and no death year.
        """
        birth, death = self._index_lifespan.get(handle, (0, 0))
        if not birth and not death:
            return None
        if birth and year < birth:
            return False
        if death and year > death + DEATH_GRACE:
            return False
        if birth and not death and year > birth + MAX_LIFESPAN:
            return False
        if death and not birth and year < death - MAX_LIFESPAN:
            return False
        return True

    def _ranked_matches(self, text):
        """(label, handle, search) for every match, best first.

        Scoring is per typed word against the words of the indexed text:
        landing on a whole word beats starting one, which beats appearing in
        the middle of one, and hits early in the text - where the person's own
        name lives, ahead of alternate and married surnames - count for more.
        So "John Joy" puts "Joy, John Mervyn" above "Johnson, Bonnie [m. Joy]".
        """
        tokens = _fold(text).split()
        if not tokens:
            return []
        event_year = self._event_year()
        self._not_living = 0
        scored = []
        for label, handle, search in self.people_cache:
            if handle in self._completion_excluded:
                continue
            # Cheap reject first; only survivors are worth scoring.
            if not all(token in search for token in tokens):
                continue
            if event_year and self._alive_at(handle, event_year) is False:
                self._not_living += 1
                continue
            words = search.split()
            score = 0
            for token in tokens:
                best = 0
                for position, word in enumerate(words):
                    if word == token:
                        quality = 100
                    elif word.startswith(token):
                        quality = 60
                    elif token in word:
                        quality = 25
                    else:
                        continue
                    quality += max(0, 20 - position)
                    if quality > best:
                        best = quality
                score += best
            scored.append((-score, label, handle, search))
        scored.sort()
        return [(label, handle, search) for _s, label, handle, search in scored]

    @staticmethod
    def _match_func(completion, key, treeiter, _data):
        """Match when every typed word appears somewhere in the name.

        The display format is "Surname, Given", so a plain substring test
        never matched a name typed the way people say it.
        """
        tokens = _fold(key).split()
        if not tokens:
            return False
        haystack = completion.get_model()[treeiter][COMP_SEARCH]
        return all(token in haystack for token in tokens)

    # ------------------------------------------------------------------
    # Editing
    # ------------------------------------------------------------------

    def on_match_selected(self, completion, model, treeiter):
        label = model[treeiter][0]
        handle = model[treeiter][1]
        self.stage_person(label, handle)
        self.entry.set_text("")
        return True

    def on_entry_activate(self, entry):
        """Enter stages the person when the text picks out exactly one."""
        text = entry.get_text().strip()
        if not text:
            return
        matches = self._ranked_matches(text)
        exact = [row for row in matches if _fold(row[0]) == _fold(text)]
        if exact:
            matches = exact
        if len(matches) == 1:
            self.stage_person(matches[0][0], matches[0][1])
            entry.set_text("")
        elif not matches and self._not_living:
            self.status.set_text(
                _("No match for '%(text)s' (%(count)d not living then)")
                % {"text": text, "count": self._not_living}
            )
        elif not matches:
            self.status.set_text(_("No match for '%s'") % text)
        else:
            self.status.set_text(
                _("%(count)d people match '%(text)s'")
                % {"count": len(matches), "text": text}
            )

    def stage_person(self, label, handle):
        # Always take the indexed label rather than whatever was displayed,
        # so nothing the completion adds for presentation can leak into the
        # participant list.
        known = self.people_labels.get(handle)
        if known:
            label = known[0]
        for row in self.model:
            if row[COL_HANDLE] == handle and row[COL_KIND] == "Person":
                # refresh_completion() keeps detach-staged people in the
                # type-ahead, so picking one again has to mean "undo the
                # detach" rather than silently doing nothing.
                if row[COL_STATE] == STATE_DETACH:
                    self._set_state(row, STATE_EXISTING)
                    self.refresh_completion()
                    self.update_status()
                return
        self.model.append(
            [label, self._default_role(), STATE_NEW, handle,
             "Person", "", -1, int(Pango.Weight.BOLD),
             STATE_TEXT[STATE_NEW]]
        )
        self.refresh_completion()
        self.update_status()

    @staticmethod
    def _set_state(row, state):
        """Keep the internal token and the visible text in step."""
        row[COL_STATE] = state
        row[COL_STATE_TEXT] = STATE_TEXT[state]

    def on_role_editing_started(self, _cell, editable, _path):
        """Give the role combo's entry a completion as the edit begins.

        A CellRendererCombo drops down its list but does nothing as you type;
        the entry only completes if a completion is attached to it, and the
        editable is built fresh for each edit. Same approach as Gramps' own
        surname origin column (gui/editors/displaytabs/surnametab.py:279).
        """
        entry = editable.get_child() if hasattr(editable, "get_child") else None
        if not isinstance(entry, Gtk.Entry):
            return
        completion = Gtk.EntryCompletion()
        completion.set_model(self.role_model)
        completion.set_text_column(0)
        completion.set_minimum_key_length(1)
        completion.set_popup_completion(True)
        # The role list is short and controlled, so filling in the rest of a
        # prefix is unambiguous and saves typing. Custom roles still work:
        # carrying on typing replaces the selected completion.
        completion.set_inline_completion(True)
        entry.set_completion(completion)

    def on_role_edited(self, _cell, path, new_text):
        if not new_text:
            return
        row = self.model[path]
        if row[COL_STATE] == STATE_DETACH:
            return
        row[COL_ROLE] = new_text
        self.update_status()

    def on_remove(self, _button):
        model, treeiter = self.tree.get_selection().get_selected()
        if not treeiter:
            return
        state = model[treeiter][COL_STATE]
        if state == STATE_NEW:
            model.remove(treeiter)
        elif state == STATE_DETACH:
            self._set_state(model[treeiter], STATE_EXISTING)
        else:
            self._set_state(model[treeiter], STATE_DETACH)
        self.refresh_completion()
        self.update_status()

    def on_revert(self, _button):
        if self.event is not None:
            self.load_participants()
            self.refresh_completion()
        self.update_status()

    def pending_counts(self):
        additions = detachments = role_changes = 0
        for row in self.model:
            if row[COL_STATE] == STATE_NEW:
                additions += 1
            elif row[COL_STATE] == STATE_DETACH:
                detachments += 1
            elif row[COL_ROLE] != row[COL_ORIG_ROLE]:
                role_changes += 1
        return additions, detachments, role_changes

    def update_status(self):
        additions, detachments, role_changes = self.pending_counts()
        parts = []
        if additions:
            parts.append(_("%d to add") % additions)
        if role_changes:
            parts.append(_("%d role change(s)") % role_changes)
        if detachments:
            parts.append(_("%d to detach") % detachments)
        self.status.set_text(", ".join(parts))
        self.apply_btn.set_sensitive(bool(parts) and self.event is not None)

    # ------------------------------------------------------------------
    # Apply
    # ------------------------------------------------------------------

    def on_apply(self, _button):
        if self.event is None:
            return
        additions, detachments, role_changes = self.pending_counts()
        if not (additions or detachments or role_changes):
            return

        db = self.dbstate.db
        ev_handle = self.event.get_handle()
        new_sort = self._sort_value(self.event)

        # Snapshot the model before touching the database, grouped by object
        # so each person or family is read, changed and committed exactly
        # once. An object holding two references to the same event produces
        # two rows, and those are told apart by COL_REFIDX rather than by
        # handle alone.
        by_object = {}
        for row in self.model:
            by_object.setdefault((row[COL_KIND], row[COL_HANDLE]), []).append(
                (row[COL_ROLE], row[COL_ORIG_ROLE], row[COL_STATE],
                 row[COL_REFIDX])
            )

        try:
            with DbTxn(_("Edit participants of event"), db) as trans:
                for (kind, handle), entries in by_object.items():
                    obj = self._get_object(kind, handle)
                    if obj is None:
                        continue

                    refs = list(obj.get_event_ref_list())
                    changed = False

                    # Roles first: they address a ref by its original index,
                    # so they must run before a detach shifts the list.
                    for role, orig_role, state, refidx in entries:
                        if state != STATE_EXISTING or role == orig_role:
                            continue
                        ref = self._ref_at(refs, refidx, ev_handle)
                        if ref is not None:
                            ref.set_role(EventRoleType(role))
                            changed = True

                    # Then detachments, highest index first for the same reason.
                    for refidx in sorted(
                        (entry[3] for entry in entries
                         if entry[2] == STATE_DETACH),
                        reverse=True,
                    ):
                        if self._ref_at(refs, refidx, ev_handle) is not None:
                            del refs[refidx]
                            changed = True

                    # Additions last, so _insert_index sees the final list.
                    for role, orig_role, state, refidx in entries:
                        if state != STATE_NEW:
                            continue
                        if any(ref.ref == ev_handle for ref in refs):
                            continue
                        eref = EventRef()
                        eref.set_reference_handle(ev_handle)
                        eref.set_role(EventRoleType(role))
                        refs.insert(self._insert_index(refs, new_sort), eref)
                        changed = True

                    if changed:
                        obj.set_event_ref_list(refs)
                        self._commit_object(kind, obj, trans)
        except Exception as err:
            # DbTxn.__exit__ has already aborted the transaction, so the
            # database is untouched. An exception raised from a GTK callback
            # would otherwise reach the user as nothing at all: keep the
            # pending edits on screen and say what went wrong.
            LOG.exception("Add Participants: applying changes failed")
            message = _("Could not apply changes: %s") % err
            self.status.set_text(message)
            self.uistate.push_message(self.dbstate, message)
            return

        # The event object itself never changed, so nothing has told the
        # Events view that its cached Main Participants column is stale.
        # That view does watch person-update, but its handler walks each
        # person's *current* event refs (plugins/view/eventview.py:156), so
        # it cannot see a reference we just removed. Nudge the row directly;
        # this covers additions, role changes and detachments alike.
        try:
            db.emit("event-update", ([ev_handle],))
        except Exception:
            LOG.debug("could not emit event-update", exc_info=True)

        self.load_participants()
        self.refresh_completion()
        self.update_status()
        self.status.set_text(
            _("Applied: +%d, %d role change(s), -%d")
            % (additions, role_changes, detachments)
        )

    @staticmethod
    def _ref_at(refs, refidx, ev_handle):
        """The ref a row stands for, or None if it no longer lines up."""
        if 0 <= refidx < len(refs) and refs[refidx].ref == ev_handle:
            return refs[refidx]
        return None

    def _get_object(self, kind, handle):
        if kind == "Person":
            return self._get_person(handle)
        if kind == "Family":
            return self._get_family(handle)
        return None

    def _commit_object(self, kind, obj, trans):
        db = self.dbstate.db
        if kind == "Person":
            db.commit_person(obj, trans)
        elif kind == "Family":
            db.commit_family(obj, trans)

    def _sort_value(self, event):
        date = event.get_date_object()
        return date.get_sort_value() if date else 0

    def _insert_index(self, refs, new_sort):
        """Chronological position. Undated events keep their place."""
        if not new_sort:
            return len(refs)
        for index, ref in enumerate(refs):
            event = self._get_event(ref.ref)
            if event is None:
                continue
            sort_value = self._sort_value(event)
            if sort_value and sort_value > new_sort:
                return index
        return len(refs)
