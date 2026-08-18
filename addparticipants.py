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
from xml.sax.saxutils import escape as xml_escape

from gi.repository import Gtk, Pango

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
COL_STATE = 2      # display string: "", "new", "detach"
COL_HANDLE = 3
COL_KIND = 4       # "Person" or "Family"
COL_ORIG_ROLE = 5
COL_REFIDX = 6     # index into the object's event_ref_list, -1 for new
COL_WEIGHT = 7     # bold for staged additions

STATE_EXISTING = ""
STATE_NEW = "new"
STATE_DETACH = "detach"


class AddParticipants(Gramplet):
    """View and edit every participant of the active event."""

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def init(self):
        self.event = None
        self.event_handle = None
        self.people_cache = []      # list of (label, handle)
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
        self.completion_model = Gtk.ListStore(str, str)  # label, handle
        completion = Gtk.EntryCompletion()
        completion.set_model(self.completion_model)
        completion.set_text_column(0)
        completion.set_minimum_key_length(2)
        completion.set_match_func(self._match_func, None)
        completion.connect("match-selected", self.on_match_selected)

        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text(_("Type a name to add someone..."))
        self.entry.set_completion(completion)
        vbox.pack_start(self.entry, False, False, 0)

        # --- Participants ---
        self.model = Gtk.ListStore(str, str, str, str, str, str, int, int)
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
        role_col = Gtk.TreeViewColumn(_("Role"), role_cell, text=COL_ROLE)
        role_col.set_min_width(140)
        tree.append_column(role_col)

        state_col = Gtk.TreeViewColumn(
            "", Gtk.CellRendererText(), text=COL_STATE
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
        self.connect(self.dbstate.db, "person-delete", self.on_people_changed)
        self.connect_signal("Event", self.update)
        self.build_people_cache()
        self.build_role_model()

    def on_people_changed(self, *args):
        self.build_people_cache()
        self.refresh_completion()

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
        """Load every person once into memory as (label, handle)."""
        db = self.dbstate.db
        cache = []
        if db is None or not db.is_open():
            self.people_cache = cache
            return
        for person in db.iter_people():
            cache.append((self._person_label(person), person.get_handle()))
        cache.sort(key=lambda row: row[0])
        self.people_cache = cache

    def _person_label(self, person):
        """Name plus birth/death years for disambiguation."""
        name = name_displayer.display(person)
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
        if years:
            return "%s (%s)" % (name, " ".join(years))
        return name

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
        """Unknown, matching stock Gramps behaviour for shared events."""
        return str(EventRoleType(EventRoleType.UNKNOWN))

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
                     class_name, role, index, Pango.Weight.NORMAL]
                )

    def refresh_completion(self):
        """Rebuild the type-ahead model, excluding anyone already listed."""
        listed = {
            row[COL_HANDLE]
            for row in self.model
            if row[COL_KIND] == "Person" and row[COL_STATE] != STATE_DETACH
        }
        self.completion_model.clear()
        for label, handle in self.people_cache:
            if handle in listed:
                continue
            self.completion_model.append([label, handle])

    @staticmethod
    def _match_func(completion, key, treeiter, _data):
        model = completion.get_model()
        return key in model[treeiter][0].lower()

    # ------------------------------------------------------------------
    # Editing
    # ------------------------------------------------------------------

    def on_match_selected(self, completion, model, treeiter):
        label = model[treeiter][0]
        handle = model[treeiter][1]
        self.stage_person(label, handle)
        self.entry.set_text("")
        return True

    def stage_person(self, label, handle):
        for row in self.model:
            if row[COL_HANDLE] == handle and row[COL_KIND] == "Person":
                # refresh_completion() keeps detach-staged people in the
                # type-ahead, so picking one again has to mean "undo the
                # detach" rather than silently doing nothing.
                if row[COL_STATE] == STATE_DETACH:
                    row[COL_STATE] = STATE_EXISTING
                    self.refresh_completion()
                    self.update_status()
                return
        self.model.append(
            [label, self._default_role(), STATE_NEW, handle,
             "Person", "", -1, Pango.Weight.BOLD]
        )
        self.refresh_completion()
        self.update_status()

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
            model[treeiter][COL_STATE] = STATE_EXISTING
        else:
            model[treeiter][COL_STATE] = STATE_DETACH
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

        # Snapshot the model before touching the database.
        rows = [
            (row[COL_HANDLE], row[COL_KIND], row[COL_ROLE],
             row[COL_ORIG_ROLE], row[COL_STATE])
            for row in self.model
        ]

        try:
            with DbTxn(_("Edit participants of event"), db) as trans:
                for handle, kind, role, orig_role, state in rows:
                    obj = self._get_object(kind, handle)
                    if obj is None:
                        continue

                    refs = list(obj.get_event_ref_list())

                    if state == STATE_NEW:
                        if any(ref.ref == ev_handle for ref in refs):
                            continue
                        eref = EventRef()
                        eref.set_reference_handle(ev_handle)
                        eref.set_role(EventRoleType(role))
                        refs.insert(self._insert_index(refs, new_sort), eref)

                    elif state == STATE_DETACH:
                        refs = [ref for ref in refs if ref.ref != ev_handle]

                    elif role != orig_role:
                        for ref in refs:
                            if ref.ref == ev_handle:
                                ref.set_role(EventRoleType(role))
                    else:
                        continue

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

        self.load_participants()
        self.refresh_completion()
        self.update_status()
        self.status.set_text(
            _("Applied: +%d, %d role change(s), -%d")
            % (additions, role_changes, detachments)
        )

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
