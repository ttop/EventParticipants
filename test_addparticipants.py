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
Gtk=types.ModuleType("Gtk"); Gtk.ListStore=_ListStore
Pango=types.ModuleType("Pango")
Pango.Weight=type("W",(),{"NORMAL":400,"BOLD":700})
Pango.EllipsizeMode=type("E",(),{"END":3})
rep.Gtk, rep.Pango = Gtk, Pango; gi.repository=rep
sys.modules.update({"gi":gi,"gi.repository":rep,
                    "gi.repository.Gtk":Gtk,"gi.repository.Pango":Pango})

class HandleError(Exception): pass
def _mod(n,**a):
    m=types.ModuleType(n); m.__dict__.update(a); sys.modules[n]=m; return m

class EventRoleType:
    UNKNOWN=-1
    def __init__(self,v=None): self.v=v
    def __str__(self): return "Unknown" if self.v in (None,-1) else str(self.v)
    def __eq__(self,o): return str(self)==str(o)
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
_mod("gramps.gen.display")
_mod("gramps.gen.display.name",
     displayer=type("D",(),{"display":staticmethod(lambda p:p.name)})())
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
    def __init__(self,name,b=None,d=None,refs=None):
        self.name=name; self._b=b; self._d=d; self.refs=refs or []
    def get_birth_ref(self): return Ref(self._b) if self._b else None
    def get_death_ref(self): return Ref(self._d) if self._d else None
    def get_event_ref_list(self): return self.refs
    def set_event_ref_list(self,r): self.refs=r
    def get_handle(self): return self.handle
class FakeDb:
    def __init__(self): self.people={}; self.events={}; self.families={}
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
    g.event=None; g.last_status=None
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
g.build_people_cache()
check("built and sorted by label %r"%[l for l,_ in g.people_cache],
      [l for l,_ in g.people_cache]==["Amy","Zoe"])
reads=[]
_orig=db.get_person_from_handle
db.get_person_from_handle=lambda h:(reads.append(h), _orig(h))[1]
db.people["p1"].name="Zara"
g.on_people_changed(["p1"])
check("only the changed handle was re-read, got %r"%reads, reads==["p1"])
check("cache reflects new label %r"%[l for l,_ in g.people_cache],
      [l for l,_ in g.people_cache]==["Amy","Zara"])
g.on_people_deleted(["p2"])
check("delete drops it without a re-read %r"%[l for l,_ in g.people_cache],
      [l for l,_ in g.people_cache]==["Zara"])

print("\n[E] refresh_completion skips redundant rebuilds")
g,db=make()
g.people_cache=[("Amy","p1"),("Bob","p2")]
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
g.people_cache=[("Amy Smith","p1"),("Bob Smith","p2")]
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


print("\n" + ("ALL PASSED" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
