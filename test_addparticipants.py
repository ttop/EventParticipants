"""Logic tests for the AddParticipants gramplet.

Gramps embeds libpython and ships no interpreter, so these stub out the
Gramps and GTK layers and exercise the plain logic: handle guards, the
apply transaction, the people cache and the completion model. They do not
test the GTK wiring - that still needs a real Gramps launch.

Run with:  python3 test_addparticipants.py
"""
import os, sys, types

class _ListStore:
    def __init__(self, *t): self.rows = []
    def append(self, row): self.rows.append(list(row))
    def clear(self): self.rows = []
    def remove(self, i): self.rows.pop(i)
    def __iter__(self): return iter(self.rows)
    def __getitem__(self, i): return self.rows[int(i)]
    def __len__(self): return len(self.rows)

gi=types.ModuleType("gi"); rep=types.ModuleType("gi.repository")
class _Entry:
    """Stands in for Gtk.Entry so isinstance() checks are meaningful."""
    def __init__(self): self.completion=None
    def set_completion(self,c): self.completion=c
    def set_width_chars(self,n): pass

class _EntryCompletion:
    def __init__(self):
        self.model=None; self.text_column=None; self.min_key=None
        self.popup=None; self.inline=None
    def set_model(self,m): self.model=m
    def set_text_column(self,c): self.text_column=c
    def set_minimum_key_length(self,n): self.min_key=n
    def set_popup_completion(self,b): self.popup=b
    def set_inline_completion(self,b): self.inline=b
    def set_match_func(self,f,d=None): pass
    def connect(self,*a): pass

Gtk=types.ModuleType("Gtk"); Gtk.ListStore=_ListStore
Gtk.Entry=_Entry; Gtk.EntryCompletion=_EntryCompletion
Pango=types.ModuleType("Pango")
Pango.Weight=type("W",(),{"NORMAL":400,"BOLD":700})
Pango.EllipsizeMode=type("E",(),{"END":3})
GLib=types.ModuleType("GLib")
GLib.PRIORITY_LOW=300
GLib._sources={}; GLib._next=[1]
def _idle_add(cb, priority=None):
    sid=GLib._next[0]; GLib._next[0]+=1; GLib._sources[sid]=cb; return sid
def _source_remove(sid): GLib._sources.pop(sid,None)
GLib.idle_add=_idle_add; GLib.source_remove=_source_remove

def drain(g, max_turns=10000):
    """Run a pending index build to completion; return the number of turns."""
    turns=0
    while g._index_id and turns<max_turns:
        cb=GLib._sources.get(g._index_id)
        if cb is None: break
        turns+=1
        if not cb(): break
    return turns

rep.Gtk, rep.Pango, rep.GLib = Gtk, Pango, GLib; gi.repository=rep
sys.modules.update({"gi":gi,"gi.repository":rep,
                    "gi.repository.Gtk":Gtk,"gi.repository.Pango":Pango,
                    "gi.repository.GLib":GLib})

class HandleError(Exception): pass
def _mod(n,**a):
    m=types.ModuleType(n); m.__dict__.update(a); sys.modules[n]=m; return m

class EventRoleType:
    UNKNOWN=-1; CUSTOM=0; PRIMARY=1; CELEBRANT=3; WITNESS=7; FAMILY=8
    _NAMES={-1:"Unknown",0:"Custom",1:"Primary",3:"Celebrant",7:"Witness",8:"Family"}
    def __init__(self,v=None): self.v=v
    def __str__(self):
        if isinstance(self.v,str): return self.v
        return self._NAMES.get(self.v,"Unknown")
    def __eq__(self,o): return str(self)==str(o)
    def is_primary(self): return self.v==self.PRIMARY
    def get_standard_names(self): return ["Primary","Witness","Unknown"]
class EventRef:
    def __init__(self): self.ref=None; self.role=None
    def set_reference_handle(self,h): self.ref=h
    def set_role(self,r): self.role=r
    def get_role(self): return self.role
class DbTxn:
    def __init__(self,msg,db): pass
    def __enter__(self): return self
    def __exit__(self,*a): return False

_mod("gramps"); _mod("gramps.gen")
_mod("gramps.gen.plug", Gramplet=type("Gramplet",(),{}))
_mod("gramps.gen.lib", EventRef=EventRef, EventRoleType=EventRoleType)
_mod("gramps.gen.db", DbTxn=DbTxn)
class Name:
    """Stands in for gramps.gen.lib.Name."""
    def __init__(self, given="", surname="", ntype=None, call="", nick="",
                 raw=None):
        self.given=given; self.surname=surname; self.ntype=ntype
        self.call=call; self.nick=nick; self.raw=raw
    def get_first_name(self): return self.given
    def get_surname(self): return self.surname
    def get_call_name(self): return self.call
    def get_nick_name(self): return self.nick
    def get_type(self): return self.ntype
    def display(self):
        if self.raw is not None: return self.raw
        # LNFN, the Gramps default: "Surname, Given"
        return ("%s, %s" % (self.surname, self.given)).strip().strip(",").strip()

class _Displayer:
    @staticmethod
    def display(person): return person.get_primary_name().display()
    @staticmethod
    def display_name(name): return name.display() if name else ""

_mod("gramps.gen.display")
_mod("gramps.gen.display.name", displayer=_Displayer())
_mod("gramps.gen.datehandler", get_date=lambda e:"1900")
_mod("gramps.gen.errors", HandleError=HandleError)
_mod("gramps.gen.const", GRAMPS_LOCALE=type("L",(),{"translation":
     type("T",(),{"gettext":staticmethod(lambda s:s)})()})())

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import addparticipants as ap

class Ref:
    def __init__(self,ref,role=None): self.ref=ref; self.role=role
    def set_role(self,r): self.role=r
class _Date:
    def __init__(self,s): self.s=s
    def get_sort_value(self): return self.s
    def get_year(self): return 1900
class Ev:
    def __init__(self,s): self._s=s
    def get_date_object(self): return _Date(self._s)
class Person:
    def __init__(self,name,b=None,d=None,refs=None,names=None):
        self.name=name; self._b=b; self._d=d; self.refs=refs or []
        self._names=names
    def get_primary_name(self):
        return self._names[0] if self._names else Name(raw=self.name)
    def get_alternate_names(self):
        return self._names[1:] if self._names else []
    def get_birth_ref(self): return Ref(self._b) if self._b else None
    def get_death_ref(self): return Ref(self._d) if self._d else None
    def get_event_ref_list(self): return self.refs
    def set_event_ref_list(self,r): self.refs=r
    def get_handle(self): return self.handle
class FakeDb:
    def __init__(self):
        self.people={}; self.events={}; self.families={}; self.emitted=[]
    def emit(self, signal, args): self.emitted.append((signal, args))
    def get_person_handles(self, sort_handles=False): return list(self.people)
    def is_open(self): return True
    def iter_people(self):
        for h,p in self.people.items(): p.handle=h; yield p
    def get_person_from_handle(self,h):
        if h not in self.people: raise HandleError(h)
        p=self.people[h]; p.handle=h; return p
    def get_event_from_handle(self,h):
        if h not in self.events: raise HandleError(h)
        return self.events[h]
    def get_family_from_handle(self,h):
        if h not in self.families: raise HandleError(h)
        return self.families[h]

def make():
    g=ap.AddParticipants.__new__(ap.AddParticipants)
    db=FakeDb()
    g.dbstate=type("S",(),{"db":db})()
    g.uistate=type("U",(),{"push_message":lambda s,d,t:None})()
    g.model=_ListStore(); g.completion_model=_ListStore()
    g.people_cache=[]; g.people_labels={}; g._completion_excluded=None
    g.commits=[]
    g._commit_object=lambda kind,obj,trans: g.commits.append((kind,obj))
    g.status=type("L",(),{"set_text":lambda s,t:setattr(g,"last_status",t)})()
    g.apply_btn=type("B",(),{"set_sensitive":lambda s,v:None})()
    g.entry=type("E",(),{"set_placeholder_text":
        lambda s,t: setattr(g,"placeholder",t)})()
    g._index_id=0; g._index_iter=None
    g.event=None; g.last_status=None; g.placeholder=None
    return g,db

def row(name,role,state,handle,kind,orig,refidx):
    return [name,role,state,handle,kind,orig,refidx,400,ap.STATE_TEXT[state]]

fails=[]
def check(n,c):
    print(("  PASS  " if c else "  FAIL  ")+n)
    if not c: fails.append(n)

print("\n[A] duplicate refs: role edit hits only the edited row")
g,db=make()
p=Person("Ann",refs=[Ref("E1","Witness"),Ref("E1","Witness"),Ref("E9","Primary")])
db.people["p1"]=p; db.events["E1"]=Ev(500)
g.event=Ev(500); g.event.get_handle=lambda:"E1"
g.model.append(row("Ann","Witness",ap.STATE_EXISTING,"p1","Person","Witness",0))
g.model.append(row("Ann","Celebrant",ap.STATE_EXISTING,"p1","Person","Witness",1))
g.load_participants=lambda:None; g.refresh_completion=lambda force=False:None
g.update_status=lambda:None
g.on_apply(None)
check("ref[0] role unchanged ('Witness'), got %r"%str(p.refs[0].role),
      str(p.refs[0].role)=="Witness")
check("ref[1] role changed ('Celebrant'), got %r"%str(p.refs[1].role),
      str(p.refs[1].role)=="Celebrant")
check("object committed exactly once (%d)"%len(g.commits), len(g.commits)==1)

print("\n[B] duplicate refs: detaching one row leaves the other")
g,db=make()
p=Person("Bea",refs=[Ref("E1","Witness"),Ref("E1","Primary")])
db.people["p1"]=p; db.events["E1"]=Ev(500)
g.event=Ev(500); g.event.get_handle=lambda:"E1"
g.model.append(row("Bea","Witness",ap.STATE_DETACH,"p1","Person","Witness",0))
g.model.append(row("Bea","Primary",ap.STATE_EXISTING,"p1","Person","Primary",1))
g.load_participants=lambda:None; g.refresh_completion=lambda force=False:None
g.update_status=lambda:None
g.on_apply(None)
check("one ref left (%d)"%len(p.refs), len(p.refs)==1)
check("survivor is the Primary one, got %r"%str(p.refs[0].role if p.refs else None),
      len(p.refs)==1 and str(p.refs[0].role)=="Primary")

print("\n[C] state column text is translated, token stays internal")
check("STATE_TEXT maps token -> display",
      ap.STATE_TEXT[ap.STATE_NEW]=="new" and ap.STATE_TEXT[ap.STATE_EXISTING]=="")
g,db=make()
g.model.append(row("Cy","Primary",ap.STATE_EXISTING,"p2","Person","Primary",0))
g._set_state(g.model[0], ap.STATE_DETACH)
check("_set_state updates both token and text",
      g.model[0][ap.COL_STATE]==ap.STATE_DETACH
      and g.model[0][ap.COL_STATE_TEXT]==ap.STATE_TEXT[ap.STATE_DETACH])

print("\n[D] incremental people cache")
g,db=make()
db.people["p1"]=Person("Zoe"); db.people["p2"]=Person("Amy")
g.build_people_cache(); drain(g)
check("built and sorted by label %r"%[r[0] for r in g.people_cache],
      [r[0] for r in g.people_cache]==["Amy","Zoe"])
reads=[]
_orig=db.get_person_from_handle
db.get_person_from_handle=lambda h:(reads.append(h), _orig(h))[1]
db.people["p1"].name="Zara"
g.on_people_changed(["p1"])
check("only the changed handle was re-read, got %r"%reads, reads==["p1"])
check("cache reflects new label %r"%[r[0] for r in g.people_cache],
      [r[0] for r in g.people_cache]==["Amy","Zara"])
g.on_people_deleted(["p2"])
check("delete drops it without a re-read %r"%[r[0] for r in g.people_cache],
      [r[0] for r in g.people_cache]==["Zara"])

print("\n[E] refresh_completion skips redundant rebuilds")
g,db=make()
g.people_cache=[("Amy","p1","amy"),("Bob","p2","bob")]
g.refresh_completion()
first=len(g.completion_model)
calls=[]
_ap=g.completion_model.append
g.completion_model.append=lambda r:(calls.append(r), _ap(r))[1]
g.refresh_completion()
check("built %d rows first time"%first, first==2)
check("second identical call did no work (%d appends)"%len(calls), len(calls)==0)
g.refresh_completion(force=True)
check("force=True rebuilds (%d appends)"%len(calls), len(calls)==2)

print("\n[F] Enter in the search box")
g,db=make()
g.people_cache=[("Amy Smith","p1","amy smith"),
                ("Bob Smith","p2","bob smith")]
g.refresh_completion()
g.stage_person=lambda l,h: g.__setattr__("staged",(l,h))
g.staged=None
ent=type("E",(),{"get_text":lambda s:"amy","set_text":lambda s,t:None})()
g.on_entry_activate(ent)
check("unique substring match stages %r"%(g.staged,), g.staged==("Amy Smith","p1"))
g.staged=None
ent2=type("E",(),{"get_text":lambda s:"smith","set_text":lambda s,t:None})()
g.on_entry_activate(ent2)
check("ambiguous match stages nothing", g.staged is None)
check("...and says so: %r"%g.last_status, "2" in str(g.last_status))
g.staged=None
ent3=type("E",(),{"get_text":lambda s:"zzz","set_text":lambda s,t:None})()
g.on_entry_activate(ent3)
check("no match reports it: %r"%g.last_status, "No match" in str(g.last_status))

print("\n[G] HandleError guards return None instead of raising")
g, db = make()
db.people["p1"] = Person("Alice")
check("_get_person(valid) -> object", g._get_person("p1") is not None)
check("_get_person(dangling) -> None", g._get_person("nope") is None)
check("_get_event(dangling) -> None", g._get_event("nope") is None)
check("_get_family(dangling) -> None", g._get_family("nope") is None)

print("\n[H] _person_label survives a dangling birth ref (was the load-time crash)")
g, db = make()
db.people["p1"] = Person("Bob", b="DELETED_EVENT")
try:
    label = g._person_label(db.people["p1"]); ok = (label == "Bob")
except HandleError:
    label, ok = "RAISED", False
check("label == 'Bob' (no year, no crash), got %r" % label, ok)

print("\n[I] _insert_index skips a dangling ref (was the in-transaction crash)")
g, db = make()
db.events["e_early"] = Ev(100)
db.events["e_late"] = Ev(900)
refs = [Ref("e_early"), Ref("GONE"), Ref("e_late")]
try:
    idx = g._insert_index(refs, 500); ok = (idx == 2)
except HandleError:
    idx, ok = "RAISED", False
check("inserts before e_late at index 2, got %r" % idx, ok)


print("\n[J] matching: word order and married names")

MARRIED = 3

def matcher(g, typed):
    """Everything the completion would offer for `typed`."""
    g.refresh_completion(force=True)
    comp = type("C",(),{"get_model":lambda s: g.completion_model})()
    out=[]
    for i in range(len(g.completion_model)):
        if ap.AddParticipants._match_func(comp, typed.casefold(), i, None):
            out.append(g.completion_model[i][0])
    return out

g,db=make()
db.people["p1"]=Person("x", names=[Name("John Mervyn","Joy")])
db.people["p2"]=Person("x", names=[
    Name("Jane","Doe"),
    Name("Jane","Smith",ntype=MARRIED),
])
db.people["p3"]=Person("x", names=[Name("Hans","M\u00fcller")])
g.build_people_cache(); drain(g)

check("'John Joy' finds 'Joy, John Mervyn' (order-independent)",
      any("Joy" in m for m in matcher(g,"John Joy")))
check("'joy john' finds it too",
      any("Joy" in m for m in matcher(g,"joy john")))
check("'Joy' alone still finds it",
      any("Joy" in m for m in matcher(g,"Joy")))
check("married name 'Smith' finds Jane Doe",
      any("Doe" in m for m in matcher(g,"Smith")))
check("'Jane Smith' finds her",
      any("Doe" in m for m in matcher(g,"Jane Smith")))
check("maiden name 'Jane Doe' still finds her",
      any("Doe" in m for m in matcher(g,"Jane Doe")))
check("'Muller' finds 'M\u00fcller' (accent-insensitive)",
      any("ller" in m for m in matcher(g,"Muller")))
check("unrelated text matches nothing",
      matcher(g,"Xavier Nobody")==[])

print("\n[K] the label shows an alternate surname")
lbl = g._person_label(db.people["p2"])
check("label mentions the married surname: %r" % lbl, "Smith" in lbl)
check("...and still leads with the primary name: %r" % lbl,
      lbl.startswith("Doe,"))

print("\n[L] the Role column completes as you type")
g,db=make()
g.role_model=_ListStore(str)
g.role_model.append(["Primary"]); g.role_model.append(["Witness"])

class FakeCombo:
    """The cell editable a CellRendererCombo hands to editing-started."""
    def __init__(self, child): self._child=child
    def get_child(self): return self._child

entry=Gtk.Entry()
g.on_role_editing_started(None, FakeCombo(entry), "0")
check("a completion is attached to the combo's entry",
      entry.completion is not None)
check("it is backed by the live role model",
      getattr(entry.completion,"model",None) is g.role_model)
check("it completes from the role column",
      getattr(entry.completion,"text_column",None)==0)
check("it pops up from the first character",
      getattr(entry.completion,"min_key",None)==1)

class OddEditable:
    def get_child(self): return object()
try:
    g.on_role_editing_started(None, OddEditable(), "0")
    check("an editable with no entry is ignored, not fatal", True)
except Exception as exc:
    check("an editable with no entry is ignored, not fatal (%r)"%exc, False)

print("\n[M] new participants default to Primary")
g,db=make()
check("_default_role() is 'Primary', got %r" % g._default_role(),
      g._default_role()=="Primary")
g.stage_person("Newcomer","pN")
check("a staged row carries Primary, got %r" % g.model[0][ap.COL_ROLE],
      g.model[0][ap.COL_ROLE]=="Primary")
check("...which is what the Main Participants column counts",
      EventRoleType(EventRoleType.PRIMARY).is_primary())

print("\n[N] Apply nudges the Events view to re-read the row")
g,db=make()
p=Person("Ann",refs=[])
db.people["p1"]=p; db.events["E1"]=Ev(500)
g.event=Ev(500); g.event.get_handle=lambda:"E1"
g.model.append(row("Ann","Primary",ap.STATE_NEW,"p1","Person","",-1))
g.load_participants=lambda:None; g.refresh_completion=lambda force=False:None
g.update_status=lambda:None
g.on_apply(None)
check("the reference was actually added", len(p.refs)==1)
check("event-update emitted for this event, got %r" % db.emitted,
      ("event-update",(["E1"],)) in db.emitted)

print("\n[O] the nudge also happens on detach, which stock Gramps misses")
g,db=make()
p=Person("Bea",refs=[Ref("E1","Primary")])
db.people["p1"]=p; db.events["E1"]=Ev(500)
g.event=Ev(500); g.event.get_handle=lambda:"E1"
g.model.append(row("Bea","Primary",ap.STATE_DETACH,"p1","Person","Primary",0))
g.load_participants=lambda:None; g.refresh_completion=lambda force=False:None
g.update_status=lambda:None
g.on_apply(None)
check("the reference was removed", len(p.refs)==0)
check("event-update still emitted, got %r" % db.emitted,
      ("event-update",(["E1"],)) in db.emitted)

print("\n[P] the name index builds in the background, not in one blocking pass")
g,db=make()
for i in range(250):
    db.people["p%d"%i]=Person("x", names=[Name("Given%d"%i,"Sur%d"%i)])
g.build_people_cache()
check("returns before the index is finished (%d of 250 so far)"
      % len(g.people_labels), len(g.people_labels) < 250)
check("the search box says why it is empty: %r" % g.placeholder,
      "Indexing" in str(g.placeholder))
turns=drain(g)
check("finished across several idle turns (%d)" % turns, turns > 1)
check("every person ended up indexed (%d)" % len(g.people_labels),
      len(g.people_labels)==250)
check("the placeholder goes back to normal: %r" % g.placeholder,
      "Indexing" not in str(g.placeholder))
check("and matching works once it is done",
      len([1 for r in g.people_cache if "Sur7" in r[0]]) >= 1)

print("\n[Q] a tree change abandons an in-flight index")
g,db=make()
for i in range(250):
    db.people["p%d"%i]=Person("x", names=[Name("A%d"%i,"B%d"%i)])
g.build_people_cache()
first=g._index_id
g.build_people_cache()            # e.g. another database-changed
check("the first build was cancelled", GLib._sources.get(first) is None
      or g._index_id != first)
drain(g)
check("the restarted build still indexes everyone (%d)" % len(g.people_labels),
      len(g.people_labels)==250)

print("\n[R] a person added mid-index is not lost")
g,db=make()
for i in range(250):
    db.people["p%d"%i]=Person("x", names=[Name("G%d"%i,"S%d"%i)])
g.build_people_cache()
cb=GLib._sources[g._index_id]; cb()          # one chunk only
db.people["late"]=Person("x", names=[Name("Late","Arrival")])
g.on_people_changed(["late"])                 # person-add during the build
check("kept while the build is still running",
      "late" in g.people_labels)
drain(g)
check("still present once the build publishes",
      "late" in g.people_labels)
check("and reachable in the sorted index",
      any(h=="late" for _l,h,_s in g.people_cache))

print("\n" + ("ALL PASSED" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
