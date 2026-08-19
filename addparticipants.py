#
# Add Participants - a Gramps gramplet
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
from gramps.gen.lib import EventRef, EventRoleType
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
    """Approximate SurnameBase.get_surname() from raw data."""
    parts = []
    for surname in name_data["surname_list"]:
        value = surname["surname"]
        prefix = surname["prefix"]
        if prefix and value:
            value = "%s %s" % (prefix, value)
        if value:
            parts.append(value)
    return " ".join(parts)


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
        self._completion_excluded = None
        self._index_id = 0          # idle source building the name index
        self._index_iter = None
        self._index_raw = False     # reading raw data rather than objects
        self._index_years = {}      # event handle -> year, for labels
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
        for handle in handles:
            self.people_labels.pop(handle, None)
            if not removed:
                person = self._get_person(handle)
                if person is not None:
                    self.people_labels[handle] = self._person_entry(person)
        if self._index_id:
            # A build is in flight; it publishes the sorted list when it
            # finishes, so skip the expensive re-sort but keep the change -
            # iter_people() may already be past this person.
            return
        self._sort_people_cache()

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
        try:
            self._index_years = self._build_year_map(db)
            with db.get_person_cursor() as cursor:
                rows = [data for _handle, data in cursor]
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
            try:
                handles = list(db.get_person_handles())
            except Exception:
                handles = [person.get_handle() for person in db.iter_people()]
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
            self._index_id = 0
            return False
        absorbed = 0
        for item in self._index_iter:
            if self._index_raw:
                self.people_labels[item["handle"]] = self._raw_person_entry(item)
            else:
                person = self._get_person(item)
                if person is not None:
                    self.people_labels[item] = self._person_entry(person)
            absorbed += 1
            if absorbed >= INDEX_CHUNK:
                self._show_index_progress(done=False)
                return True
        # Exhausted: publish the finished index.
        self._index_id = 0
        self._index_iter = None
        self._sort_people_cache()
        self._show_index_progress(done=True)
        return False

    def _build_year_map(self, db):
        """Event handle -> year, for every dated event, in a single query."""
        years = {}
        with db.get_event_cursor() as cursor:
            for _handle, data in cursor:
                date = data["date"]
                if not date:
                    continue
                dateval = date["dateval"]
                if not dateval or len(dateval) <= 2:
                    continue
                year = dateval[2]
                if year:
                    years[data["handle"]] = year
        return years

    def _raw_person_entry(self, data):
        """(label, folded search text) straight from stored person data."""
        label = self._raw_person_label(data)
        return label, self._raw_person_search_text(data, label)

    def _raw_person_label(self, data):
        """The object path's _person_label, without building a Person."""
        name = name_displayer.raw_display_name(data["primary_name"])
        primary_surname = _raw_surname(data["primary_name"])
        others = []
        for alt in data["alternate_names"]:
            surname = _raw_surname(alt)
            if surname and surname != primary_surname and surname not in others:
                others.append(surname)
        years = []
        refs = data["event_ref_list"]
        for key, marker in (("birth_ref_index", "b."),
                            ("death_ref_index", "d.")):
            index = data[key]
            if index is None or index < 0 or index >= len(refs):
                continue
            year = self._index_years.get(refs[index]["ref"])
            if year:
                years.append("%s %d" % (marker, year))
        label = name
        if others:
            label += " [%s]" % ", ".join(others)
        if years:
            label += " (%s)" % " ".join(years)
        return label

    def _raw_person_search_text(self, data, label):
        """The object path's _person_search_text, from stored data."""
        parts = [label]
        for name_data in [data["primary_name"]] + list(data["alternate_names"]):
            parts.append(name_displayer.raw_display_name(name_data))
            parts.append(name_data["first_name"])
            parts.append(_raw_surname(name_data))
            parts.append(name_data["call"])
            parts.append(name_data["nick"])
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

    def _person_label(self, person):
        """Primary name, any other surnames, then birth/death years."""
        name = name_displayer.display(person)
        primary = person.get_primary_name()
        primary_surname = primary.get_surname() if primary else ""
        others = []
        for alt in person.get_alternate_names():
            surname = alt.get_surname()
            if surname and surname != primary_surname and surname not in others:
                others.append(surname)
        years = []
        for ref, marker in (
            (person.get_birth_ref(), "b."),
            (person.get_death_ref(), "d."),
        ):
            year = ""
            if ref:
                event = self._get_event(ref.ref)
                if event:
                    date = event.get_date_object()
                    if date and date.get_year():
                        year = str(date.get_year())
            if year:
                years.append("%s %s" % (marker, year))
        label = name
        if others:
            label += " [%s]" % ", ".join(others)
        if years:
            label += " (%s)" % " ".join(years)
        return label

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
        return _fold(" ".join(part for part in parts if part))

    def _person_entry(self, person):
        """(display label, folded search text) for one person."""
        label = self._person_label(person)
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
        """Rebuild the type-ahead model, excluding anyone already listed.

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
        self.completion_model.clear()
        for label, handle, search in self.people_cache:
            if handle in listed:
                continue
            self.completion_model.append([label, handle, search])

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
        tokens = _fold(text).split()
        if not tokens:
            return
        matches = [
            (row[COMP_LABEL], row[COMP_HANDLE])
            for row in self.completion_model
            if all(token in row[COMP_SEARCH] for token in tokens)
        ]
        exact = [row for row in matches if _fold(row[0]) == _fold(text)]
        if exact:
            matches = exact
        if len(matches) == 1:
            self.stage_person(matches[0][0], matches[0][1])
            entry.set_text("")
        elif not matches:
            self.status.set_text(_("No match for '%s'") % text)
        else:
            self.status.set_text(
                _("%(count)d people match '%(text)s'")
                % {"count": len(matches), "text": text}
            )

    def stage_person(self, label, handle):
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
