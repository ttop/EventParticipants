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
    @staticmethod
    def raw_display_name(raw):
        """Same LNFN formatting, but from stored data instead of a Name."""
        surname=" ".join(x["surname"] for x in raw["surname_list"] if x["surname"])
        return ("%s, %s" % (surname, raw["first_name"])).strip().strip(",").strip()

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
    def __init__(self,s,year=0): self.s=s; self.year=year
    def get_sort_value(self): return self.s
    def get_year(self): return self.year
class Ev:
    def __init__(self,s,year=0): self._s=s; self._y=year
    def get_date_object(self): return _Date(self._s,self._y)
class Person:
    def __init__(self,name,b=None,d=None,refs=None,names=None):
        self.name=name; self._b=b; self._d=d; self.refs=refs or []
        self._names=names; self.handle=None
    def get_primary_name(self):
        return self._names[0] if self._names else Name(raw=self.name)
    def get_alternate_names(self):
        return self._names[1:] if self._names else []
    def get_birth_ref(self): return Ref(self._b) if self._b else None
    def get_death_ref(self): return Ref(self._d) if self._d else None
    def get_event_ref_list(self): return self.refs
    def set_event_ref_list(self,r): self.refs=r
    def get_handle(self): return self.handle
def _raw_name(n):
    """A Name stub rendered as the dict the database actually stores."""
    if n.raw is not None:
        return {"display_as":0,"first_name":"","call":"","nick":"",
                "surname_list":[{"surname":n.raw,"prefix":""}]}
    return {"display_as":0,"first_name":n.given,"call":n.call,"nick":n.nick,
            "surname_list":[{"surname":n.surname,"prefix":""}]}

class _Cursor:
    """Mimics gen.db.generic.Cursor: yields (handle, raw data)."""
    def __init__(self, rows): self.rows=rows
    def __enter__(self): return self
    def __exit__(self,*a): return False
    def __iter__(self):
        for d in self.rows: yield (d["handle"], d)

class FakeDb:
    def __init__(self):
        self.people={}; self.events={}; self.families={}; self.emitted=[]
        self.raw_families=[]
    def _ref_index(self, person, handle):
        for i,r in enumerate(person.refs):
            if r.ref==handle: return i
        return -1
    def _raw_person(self, handle, p):
        names = p._names or [Name(raw=p.name)]
        return {"handle":handle,
                "primary_name":_raw_name(names[0]),
                "alternate_names":[_raw_name(n) for n in names[1:]],
                "event_ref_list":[{"ref":r.ref} for r in p.refs],
                "birth_ref_index":self._ref_index(p,p._b),
                "death_ref_index":self._ref_index(p,p._d)}
    def get_person_cursor(self):
        return _Cursor([self._raw_person(h,p) for h,p in self.people.items()])
    def get_family_cursor(self):
        return _Cursor(getattr(self, "raw_families", []))
    def get_event_cursor(self):
        return _Cursor([{"handle":h,
                         "date":{"dateval":[0,0,
                                 e.get_date_object().get_year(),False]}}
                        for h,e in self.events.items()])
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
    g.entry=type("E",(),{
        "set_placeholder_text": lambda s,t: setattr(g,"placeholder",t),
        "get_text": lambda s: getattr(g,"typed",""),
        "set_text": lambda s,t: setattr(g,"typed",t)})()
    g._index_id=0; g._index_iter=None; g._index_spouses={}
    g._completion_excluded=frozenset(); g._matches=[]; g.typed=""
    g._index_lifespan={}; g._not_living=0
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

print("\n[E] refresh_completion skips redundant work")
g,db=make()
g.people_cache=[("Amy","p1","amy"),("Bob","p2","bob")]
calls=[]
g._update_completion=lambda *a: calls.append(1)
g.refresh_completion()
check("nothing to do while the excluded set is unchanged (%d)"%len(calls),
      len(calls)==0)
g.model.append(row("Amy","Primary",ap.STATE_EXISTING,"p1","Person","Primary",0))
g.refresh_completion()
check("someone becoming a participant refreshes it (%d)"%len(calls),
      len(calls)==1)
g.refresh_completion()
check("but only once (%d)"%len(calls), len(calls)==1)
g.refresh_completion(force=True)
check("force=True refreshes anyway (%d)"%len(calls), len(calls)==2)

g,db=make()
g.people_cache=[("Amy","p1","amy"),("Bob","p2","bob")]
g.typed="am"; g._update_completion()
check("the model holds only what was typed for (%d row)"
      % len(g.completion_model), len(g.completion_model)==1)
check("...and it is the right one", g.completion_model[0][0]=="Amy")
g.typed=""; g._update_completion()
check("an empty box offers nothing", len(g.completion_model)==0)

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
    """Everything the completion would offer for `typed`, best first."""
    return [label for label, _h, _s in g._ranked_matches(typed)]

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

print("\n[S] raw indexing and the object path agree exactly")
names=[Name("Jane","Doe"), Name("Jane","Smith",ntype=3)]

def indexed(force_object):
    g,db=make()
    db.events["E1"]=Ev(0,1901); db.events["E2"]=Ev(0,1980)
    db.people["p1"]=Person("x", names=names, b="E1", d="E2",
                           refs=[Ref("E1"),Ref("E2")])
    if force_object:
        def boom(): raise RuntimeError("no cursor here")
        db.get_person_cursor=boom
    g.build_people_cache(); drain(g)
    return g, g.people_labels.get("p1")

g_raw, raw_entry = indexed(force_object=False)
g_obj, obj_entry = indexed(force_object=True)
check("raw path used a cursor", g_raw._index_raw is True)
check("object path fell back", g_obj._index_raw is False)
check("both produced an entry", raw_entry is not None and obj_entry is not None)
check("labels identical: %r" % (raw_entry[0],), raw_entry[0]==obj_entry[0])
check("search text identical", raw_entry[1]==obj_entry[1])
check("years came through the year map", 
      "1901" in raw_entry[0] and "1980" in raw_entry[0])
check("married surname still annotated", "[Smith]" in raw_entry[0])

print("\n[T] a broken raw layout degrades instead of emptying the index")
g,db=make()
db.people["p1"]=Person("x", names=[Name("Ann","Lee")])
db.get_person_cursor=lambda: _Cursor([{"handle":"p1","primary_name":None}])
g.build_people_cache(); drain(g)
check("fell back to the object API", g._index_raw is False)
check("and still indexed the person (%d)" % len(g.people_labels),
      len(g.people_labels)==1)

print("\n[U] a surname reached by marriage is searchable")
# Louisa Heitt married Ernest Reyman. Her record carries no married name -
# that is how nearly every tree stores it - so "Louisa Reyman" has to be
# found through the family.
g,db=make()
db.people["lou"]=Person("x", names=[Name("Louisa","Heitt")])
db.people["ern"]=Person("x", names=[Name("Ernest August","Reyman")])
db.raw_families=[{"handle":"f1","father_handle":"ern","mother_handle":"lou"}]
g.build_people_cache(); drain(g)

def hits(typed):
    return [label for label, _h, _s in g._ranked_matches(typed)]

check("no married name is stored on her",
      db.people["lou"].get_alternate_names()==[])
check("'Louisa Reyman' finds her: %r" % hits("Louisa Reyman"),
      any("Heitt" in m for m in hits("Louisa Reyman")))
check("'Louisa Heitt' still finds her",
      any("Heitt" in m for m in hits("Louisa Heitt")))
check("her label says who she married: %r" % g.people_labels["lou"][0],
      "Reyman" in g.people_labels["lou"][0])
check("and it reads as a marriage, not as her own surname",
      "m. Reyman" in g.people_labels["lou"][0])
check("'Ernest Heitt' does NOT find him - the surname travels one way",
      not any("Reyman" in m for m in hits("Ernest Heitt")))
check("his own label carries no married surname: %r"
      % g.people_labels["ern"][0], "m. " not in g.people_labels["ern"][0])
check("'Ernest Reyman' still finds him by his own name",
      any("Reyman" in m for m in hits("Ernest Reyman")))
check("unrelated names still match nobody", hits("Zebedee Nobody")==[])

print("\n[V] spouse surnames survive the object fallback")
g,db=make()
db.people["lou"]=Person("x", names=[Name("Louisa","Heitt")])
db.people["ern"]=Person("x", names=[Name("Ernest","Reyman")])
db.raw_families=[{"handle":"f1","father_handle":"ern","mother_handle":"lou"}]
db.get_person_cursor=lambda: (_ for _ in ()).throw(RuntimeError("no cursor"))
db.get_person_handles=lambda sort_handles=False: list(db.people)
g.build_people_cache(); drain(g)
check("fell back to the object API", g._index_raw is False)
check("still learned the spouse surname",
      "Reyman" in g.people_labels["lou"][0])

print("\n[W] the best matches come first")
g,db=make()
db.people["a"]=Person("x", names=[Name("John Mervyn","Joy")])
db.people["b"]=Person("x", names=[Name("Bonnie E.","Johnson")])
db.people["c"]=Person("x", names=[Name("Daniel John","Joy")])
# Bonnie married a Joy, so she matches "Joy" through her married surname
db.raw_families=[{"handle":"f1","father_handle":"a","mother_handle":"b"}]
g.build_people_cache(); drain(g)

order=[l for l,_h,_s in g._ranked_matches("John Joy")]
check("all three still match: %r" % order, len(order)==3)
check("a real 'John Joy' is first: %r" % order[0], order[0].startswith("Joy,"))
check("both Joys outrank the Johnson",
      all(x.startswith("Joy,") for x in order[:2]))
check("the married-surname match sinks to last: %r" % order[-1],
      "Johnson" in order[-1])

# a whole word beats the start of a longer one
order=[l for l,_h,_s in g._ranked_matches("John")]
check("exact word 'John' outranks the 'Johnson' prefix: %r" % order[0],
      "Johnson" not in order[0])

print("\n[X] the popup is capped")
g,db=make()
for i in range(120):
    db.people["p%d"%i]=Person("x", names=[Name("Test%d"%i,"Common")])
g.build_people_cache(); drain(g)
g.typed="common"; g._update_completion()
check("all 120 are matches", len(g._matches)==120)
check("but only %d are offered" % ap.COMPLETION_LIMIT,
      len(g.completion_model)==ap.COMPLETION_LIMIT)

print("\n[Y] people who were not alive then are left out")

def tree_with_event(event_year):
    g,db=make()
    db.events["b1"]=Ev(0,1850); db.events["d1"]=Ev(0,1900)   # died 1900
    db.events["b2"]=Ev(0,1920)                                # born 1920
    db.people["dead"]=Person("x", names=[Name("Sarah","Fisher")],
                             b="b1", d="d1", refs=[Ref("b1"),Ref("d1")])
    db.people["later"]=Person("x", names=[Name("Sarah","Fisher")],
                              b="b2", refs=[Ref("b2")])
    db.people["nodates"]=Person("x", names=[Name("Sarah","Fisher")])
    g.build_people_cache(); drain(g)
    g.event = Ev(0, event_year) if event_year else None
    if event_year:
        g.event.get_handle=lambda:"E"
    return g

g = tree_with_event(1950)
order=[h for _l,h,_s in g._ranked_matches("Sarah Fisher")]
check("the one who died in 1900 is gone: %r" % (order,), "dead" not in order)
check("the living ones remain", set(order)=={"later","nodates"})
check("and the gramplet counted what it dropped (%d)" % g._not_living,
      g._not_living==1)
check("someone with no dates at all is kept", "nodates" in order)
check("_alive_at says so outright", g._alive_at("dead",1950) is False)
check("...and stays silent when there are no dates",
      g._alive_at("nodates",1950) is None)

g = tree_with_event(1900)
check("a death in the event year is still plausible",
      g._alive_at("dead",1900) is True)
check("burial two years later is tolerated",
      g._alive_at("dead",1902) is True)
check("but not a decade later", g._alive_at("dead",1910) is False)
check("someone born in 1920 was not there in 1900",
      g._alive_at("later",1900) is False)
check("and a 1920 birth with no death is fine in 1950",
      tree_with_event(1950)._alive_at("later",1950) is True)
check("a birth with no death is assumed ended after %d years"
      % ap.MAX_LIFESPAN,
      tree_with_event(2080)._alive_at("later",2080) is False)
check("...and still alive just inside it",
      tree_with_event(2019)._alive_at("later",2019) is True)

g = tree_with_event(None)
order=[h for _l,h,_s in g._ranked_matches("Sarah Fisher")]
check("an undated event excludes nobody: %r" % (order,),
      len(order)==3 and g._event_year()==0)

# a death with no birth is inferred backwards the same way
g,db=make()
db.events["d9"]=Ev(0,1900)
db.people["oldster"]=Person("x", names=[Name("Mary","Stone")],
                            d="d9", refs=[Ref("d9")])
g.build_people_cache(); drain(g)
g.event=Ev(0,1750); g.event.get_handle=lambda:"E"
check("a death in 1900 with no birth rules out 1750",
      g._alive_at("oldster",1750) is False)
g.event=Ev(0,1850); g.event.get_handle=lambda:"E"
check("...but not 1850", g._alive_at("oldster",1850) is True)

# Enter says why nothing came back
g,db=make()
db.events["b1"]=Ev(0,1850); db.events["d1"]=Ev(0,1900)
db.people["dead"]=Person("x", names=[Name("Sarah","Fisher")],
                         b="b1", d="d1", refs=[Ref("b1"),Ref("d1")])
g.build_people_cache(); drain(g)
g.event=Ev(0,1990); g.event.get_handle=lambda:"E"
g.stage_person=lambda l,h: g.__setattr__("staged",(l,h)); g.staged=None
ent=type("E",(),{"get_text":lambda s:"Sarah Fisher",
                 "set_text":lambda s,t:None})()
g.on_entry_activate(ent)
check("Enter stages nobody", g.staged is None)
check("and explains the omission: %r" % g.last_status,
      "not living" in str(g.last_status))

print("\n[Z] staging uses the indexed label, not whatever was displayed")
g,db=make()
db.people["p1"]=Person("x", names=[Name("Ann","Lee")])
g.build_people_cache(); drain(g)
g.stage_person("Ann Lee -- DECORATED", "p1")
check("participant row shows the real label: %r" % g.model[0][ap.COL_NAME],
      g.model[0][ap.COL_NAME]=="Lee, Ann")

print("\n" + ("ALL PASSED" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
