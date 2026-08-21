#
# Event Participants - a Gramps gramplet
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
"""Logic tests for the EventParticipants gramplet.

Gramps embeds libpython and ships no interpreter, so these stub out the
Gramps and GTK layers and exercise the plain logic: handle guards, the
apply transaction, the people cache and the completion model. They do not
test the GTK wiring - that still needs a real Gramps launch.

Run with:  python3 test_eventparticipants.py
"""
import os, sys, types

class _ListStore:
    def __init__(self, *t): self.rows = []
    def append(self, row): self.rows.append(list(row))
    def clear(self): self.rows = []
    def remove(self, i): self.rows.pop(i)
    # Gtk.TreeModel.__delitem__ turns an int into an iter and removes it
    # (gi/overrides/Gtk.py:932), which is how _drop_covered_staged deletes.
    def __delitem__(self, i): self.rows.pop(int(i))
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
    """Mirrors GrampsType's string handling (gen/lib/grampstype.py:203): a
    known name maps to its code, anything else becomes a CUSTOM role that
    keeps the string - and a CUSTOM role is not primary."""
    UNKNOWN=-1; CUSTOM=0; PRIMARY=1; CELEBRANT=3; WITNESS=7; FAMILY=8
    _NAMES={-1:"Unknown",0:"Custom",1:"Primary",3:"Celebrant",7:"Witness",8:"Family"}
    _VALUES={n:v for v,n in _NAMES.items()}
    def __init__(self,v=None):
        self.s=""
        if isinstance(v,str):
            self.v=self._VALUES.get(v,self.CUSTOM)
            if self.v==self.CUSTOM: self.s=v
        else:
            self.v=self.UNKNOWN if v is None else v
    def __str__(self):
        if self.v==self.CUSTOM and self.s: return self.s
        return self._NAMES.get(self.v,"Unknown")
    def __eq__(self,o): return str(self)==str(o)
    def is_primary(self): return self.v==self.PRIMARY
    def is_custom(self): return self.v==self.CUSTOM
    def get_standard_names(self): return ["Primary","Witness","Unknown"]

class EventType:
    """Only the codes and the two fallback predicates the gramplet reads."""
    MARRIAGE=1; BIRTH=12; DEATH=13; BAPTISM=15; BURIAL=19; CAUSE_DEATH=20
    CHRISTEN=22; CREMATION=24; PROBATE=39; STILLBIRTH=45
    _NAMES={1:"Marriage",12:"Birth",13:"Death",15:"Baptism",19:"Burial",
            20:"Cause Of Death",22:"Christening",24:"Cremation",
            39:"Probate",45:"Stillbirth"}
    def __init__(self,v=None): self.v=self.BIRTH if v is None else v
    def __str__(self): return self._NAMES.get(self.v,"Unknown")
    def get_map(self): return self._NAMES
    def is_birth_fallback(self):
        return self.v in (self.STILLBIRTH,self.BAPTISM,self.CHRISTEN)
    def is_death_fallback(self):
        return self.v in (self.STILLBIRTH,self.BURIAL,self.CREMATION,
                          self.CAUSE_DEATH,self.PROBATE)

class EventRef:
    def __init__(self): self.ref=None; self.role=None
    def set_reference_handle(self,h): self.ref=h
    def set_role(self,r): self.role=r
    def get_role(self): return self.role
class Date:
    """Enough of gramps.gen.lib.Date to exercise the calendar conversion.

    Hebrew years run about 3760 ahead of Gregorian ones. The exact offset
    does not matter here, only that a year in another calendar must not be
    taken at face value.
    """
    CAL_GREGORIAN=0; CAL_HEBREW=2
    _POS_YR=2
    _OFFSET={0:0, 2:3760}
    def __init__(self, source=None):
        if source is None:
            self.calendar=0; self.dateval=(0,0,0,False); self.quality=0
            self.modifier=0; self.text=""; self.newyear=0; self.sortval=0
        else:
            self.calendar=source.calendar; self.dateval=tuple(source.dateval)
            self.quality=source.quality; self.modifier=source.modifier
            self.text=source.text; self.newyear=source.newyear
            self.sortval=source.sortval
    def set(self, quality=None, modifier=None, calendar=None, value=None,
            text=None, newyear=0):
        if quality is not None: self.quality=quality
        if modifier is not None: self.modifier=modifier
        if calendar is not None: self.calendar=calendar
        if value is not None: self.dateval=tuple(value)
        if text is not None: self.text=text
        self.newyear=newyear
    def get_calendar(self): return self.calendar
    def get_year(self): return self.dateval[self._POS_YR]
    def get_sort_value(self): return self.sortval
    def convert_calendar(self, calendar, known_valid=True):
        year=self.dateval[self._POS_YR]
        if year:
            year=year - self._OFFSET[self.calendar] + self._OFFSET[calendar]
        self.dateval=(self.dateval[0], self.dateval[1], year, False)
        self.calendar=calendar

class DbTxn:
    def __init__(self,msg,db): pass
    def __enter__(self): return self
    def __exit__(self,*a): return False

_mod("gramps"); _mod("gramps.gen")
_mod("gramps.gen.plug", Gramplet=type("Gramplet",(),{}))
def gregorian(date):
    """gramps.gen.lib.date.gregorian: convert without touching the original."""
    if date.get_calendar() != Date.CAL_GREGORIAN:
        date = Date(date)
        date.convert_calendar(Date.CAL_GREGORIAN)
    return date

_mod("gramps.gen.lib", Date=Date, EventRef=EventRef,
     EventRoleType=EventRoleType, EventType=EventType)
_mod("gramps.gen.lib.date", Date=Date, gregorian=gregorian)
_mod("gramps.gen.db", DbTxn=DbTxn)
def _format_surnames(parts):
    """SurnameBase.get_surname() over (surname, prefix, connector) triples.

    Mirrors gramps/gen/lib/surnamebase.py:180. Both halves of the stub go
    through this, so neither the object nor the raw side of the parity test
    is allowed to define the answer by calling the gramplet's own reader.
    """
    totalsurn = ""
    for surname, prefix, connector in parts:
        fsurn = "%s %s" % (prefix, surname) if prefix else surname
        fsurn = fsurn.strip()
        if connector:
            fsurn = "%s %s" % (fsurn, connector)
        totalsurn = "%s %s" % (totalsurn, fsurn.strip())
    return totalsurn.strip()

class Name:
    """Stands in for gramps.gen.lib.Name."""
    def __init__(self, given="", surname="", ntype=None, call="", nick="",
                 raw=None, surnames=None):
        self.given=given; self.ntype=ntype
        self.call=call; self.nick=nick; self.raw=raw
        # (surname, prefix, connector) triples, as Gramps stores them
        self.parts=list(surnames) if surnames is not None else [(surname,"","")]
    def get_first_name(self): return self.given
    def get_surname(self): return _format_surnames(self.parts)
    def get_call_name(self): return self.call
    def get_nick_name(self): return self.nick
    def get_type(self): return self.ntype
    def display(self):
        if self.raw is not None: return self.raw
        # LNFN, the Gramps default: "Surname, Given"
        return ("%s, %s" % (self.get_surname(), self.given)
                ).strip().strip(",").strip()

class _Displayer:
    @staticmethod
    def display(person): return person.get_primary_name().display()
    @staticmethod
    def display_name(name): return name.display() if name else ""
    @staticmethod
    def raw_display_name(raw):
        """Same LNFN formatting, but from stored data instead of a Name."""
        surname=_format_surnames([(x["surname"],x["prefix"],x["connector"])
                                  for x in raw["surname_list"]])
        return ("%s, %s" % (surname, raw["first_name"])).strip().strip(",").strip()

_mod("gramps.gen.display")
_mod("gramps.gen.display.name", displayer=_Displayer())
_mod("gramps.gen.datehandler", get_date=lambda e:"1900")
_mod("gramps.gen.errors", HandleError=HandleError)
_mod("gramps.gen.const", GRAMPS_LOCALE=type("L",(),{"translation":
     type("T",(),{"gettext":staticmethod(lambda s:s)})()})())

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eventparticipants as ap

class Ref:
    """An EventRef. A reference defaults to Primary, which is what a birth,
    death, christening or burial reference normally is."""
    def __init__(self,ref,role="Primary"): self.ref=ref; self.role=role
    def set_role(self,r): self.role=r
    def get_role(self):
        return self.role if isinstance(self.role,EventRoleType) \
            else EventRoleType(self.role)
class Ev:
    def __init__(self,s,year=0,calendar=0,etype=EventType.BIRTH):
        self._s=s; self._y=year; self._c=calendar; self._t=etype
    def get_type(self): return EventType(self._t)
    def get_description(self): return ""
    def get_date_object(self):
        date=Date()
        date.set(calendar=self._c, value=(0,0,self._y,False))
        date.sortval=self._s
        return date
class _Iter(int):
    """Stands in for a Gtk.TreeIter: indexes a row like a number but, like a
    real iter, is never falsy - row 0 is a real row."""
    def __bool__(self): return True

class Family:
    def __init__(self,father=None,mother=None,handle=None,refs=None):
        self._father=father; self._mother=mother; self.handle=handle
        self.refs=refs or []
    def get_handle(self): return self.handle
    def get_father_handle(self): return self._father
    def get_mother_handle(self): return self._mother
    def get_gramps_id(self): return self.handle or "F?"
    def get_event_ref_list(self): return self.refs
    def set_event_ref_list(self,r): self.refs=r
class Person:
    """Models Gramps' positional birth/death pointers, because that is the
    whole point of the indices tests: gen/lib/person.py keeps
    birth_ref_index / death_ref_index as offsets into event_ref_list, and
    get_birth_ref() hands back *that list element*, not a copy. Identity is
    what lets the gramplet put the pointers back after the list moves."""
    def __init__(self,name,b=None,d=None,refs=None,names=None,families=None):
        self.name=name; self._b=b; self._d=d; self.refs=refs or []
        self._names=names; self.handle=None
        self.family_list=families or []
        self.birth_ref_index=self._find(b)
        self.death_ref_index=self._find(d)
        # Older fixtures name a birth/death event without listing its ref.
        # Those keep the old shim below; one that WAS listed gets Gramps'
        # real semantics, including -1 meaning "no such event" once the
        # reference is detached.
        self._b_listed=self.birth_ref_index!=-1
        self._d_listed=self.death_ref_index!=-1
    def _find(self,handle):
        for i,r in enumerate(self.refs):
            if r.ref==handle: return i
        return -1
    def get_primary_name(self):
        return self._names[0] if self._names else Name(raw=self.name)
    def get_alternate_names(self):
        return self._names[1:] if self._names else []
    def get_birth_ref(self):
        if 0 <= self.birth_ref_index < len(self.refs):
            return self.refs[self.birth_ref_index]
        return Ref(self._b) if self._b and not self._b_listed else None
    def get_death_ref(self):
        if 0 <= self.death_ref_index < len(self.refs):
            return self.refs[self.death_ref_index]
        return Ref(self._d) if self._d and not self._d_listed else None
    def get_event_ref_list(self): return self.refs
    def get_primary_event_ref_list(self):
        return [r for r in self.refs if r.get_role().is_primary()]
    def set_event_ref_list(self,r): self.refs=r
    def get_handle(self): return self.handle
def _raw_name(n):
    """A Name stub rendered as the dict the database actually stores."""
    if n.raw is not None:
        return {"display_as":0,"first_name":"","call":"","nick":"",
                "surname_list":[{"surname":n.raw,"prefix":"","connector":""}]}
    return {"display_as":0,"first_name":n.given,"call":n.call,"nick":n.nick,
            "surname_list":[{"surname":s,"prefix":p,"connector":c}
                            for s,p,c in n.parts]}

class _Cursor:
    """Mimics gen.db.generic.Cursor: yields (handle, raw data)."""
    def __init__(self, rows): self.rows=rows
    def __enter__(self): return self
    def __exit__(self,*a): return False
    def __iter__(self):
        for d in self.rows: yield (d["handle"], d)

class FakeDb:
    """One store per object type.

    Families used to live in two: a list of raw dicts for the cursor and a
    dict of objects for get_family_from_handle(). A test that filled only one
    of them silently asserted nothing, because the build-time entry it meant
    to overwrite had never existed. Both views are derived from `families`
    here so that cannot happen again.
    """
    def __init__(self):
        self.people={}; self.events={}; self.families={}
        self.committed_events=[]
    def _ref_index(self, person, handle):
        for i,r in enumerate(person.refs):
            if r.ref==handle: return i
        return -1
    def _raw_person(self, handle, p):
        names = p._names or [Name(raw=p.name)]
        return {"handle":handle,
                "primary_name":_raw_name(names[0]),
                "alternate_names":[_raw_name(n) for n in names[1:]],
                "event_ref_list":[{"ref":r.ref,
                                   "role":{"value":r.get_role().v}}
                                  for r in p.refs],
                "birth_ref_index":self._ref_index(p,p._b),
                "death_ref_index":self._ref_index(p,p._d)}
    def get_person_cursor(self):
        return _Cursor([self._raw_person(h,p) for h,p in self.people.items()])
    def get_family_cursor(self):
        return _Cursor([{"handle":h,
                         "father_handle":f.get_father_handle(),
                         "mother_handle":f.get_mother_handle()}
                        for h,f in self.families.items()])
    def get_event_cursor(self):
        rows=[]
        for h,e in self.events.items():
            d=e.get_date_object()
            rows.append({"handle":h,
                         "type":{"value":e._t},
                         "date":{"dateval":list(d.dateval),
                                 "calendar":d.calendar,"quality":d.quality,
                                 "modifier":d.modifier,"text":d.text,
                                 "newyear":d.newyear,"sortval":d.sortval}})
        return _Cursor(rows)
    def find_backlink_handles(self, handle, include_classes=None):
        """(class, handle) for everything referencing an event."""
        if include_classes is None or "Person" in include_classes:
            for h,p in self.people.items():
                if any(r.ref==handle for r in p.refs):
                    yield ("Person", h)
        if include_classes is None or "Family" in include_classes:
            for h,f in self.families.items():
                if any(r.ref==handle for r in f.refs):
                    f.handle=h
                    yield ("Family", h)
    def commit_event(self, event, trans, change_time=None):
        self.committed_events.append(event)
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
        f=self.families[h]; f.handle=h; return f

def make():
    g=ap.EventParticipants.__new__(ap.EventParticipants)
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
    g._index_lifespan={}; g._index_forms={}
    g._not_living=0; g._already_listed=0
    g._index_years={}; g._index_fallbacks={}; g._index_mothers={}
    g._rebuild_id=0; g._applying=False; g._notice=""; g._family_spouses={}
    g._recache_pending=set(); g._recache_id=0
    g.event=None; g.event_handle=None
    g.last_status=None; g.placeholder=None
    g.updates=0
    g.update=lambda: setattr(g,"updates",g.updates+1)
    return g,db

def run_idle(sid):
    """Run one pending GLib idle callback, the way the main loop would."""
    cb=GLib._sources.get(sid)
    if cb is None: return False
    while cb(): pass
    GLib._sources.pop(sid,None)
    return True

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
g.last_status="SENTINEL"          # so an absent message cannot pass vacuously
g.on_entry_activate(ent2)
check("ambiguous match stages nothing", g.staged is None)
# and says nothing: the drop-down is already showing the matches
check("...silently, leaving the label alone: %r"%g.last_status,
      g.last_status=="SENTINEL")
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
# The event is committed inside the transaction, which makes Gramps emit
# event-update itself - and replay it on undo and redo, which emitting it
# by hand afterwards never did.
g,db=make()
p=Person("Ann",refs=[])
db.people["p1"]=p; db.events["E1"]=Ev(500)
g.event=Ev(500); g.event.get_handle=lambda:"E1"
g.model.append(row("Ann","Primary",ap.STATE_NEW,"p1","Person","",-1))
g.load_participants=lambda:None; g.refresh_completion=lambda force=False:None
g.update_status=lambda:None
g.on_apply(None)
check("the reference was actually added", len(p.refs)==1)
check("the event was committed in the same transaction, got %r"
      % db.committed_events, db.committed_events==[db.events["E1"]])

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
check("the event was committed here too, got %r" % db.committed_events,
      db.committed_events==[db.events["E1"]])

print("\n[O2] Apply reports what it actually did")
g,db=make()
p=Person("Cal",refs=[Ref("E1","Primary")])
db.people["p1"]=p; db.events["E1"]=Ev(500)
g.event=Ev(500); g.event.get_handle=lambda:"E1"
# A row whose reference has gone from under us: the record now holds one
# reference to this event, but the model believes it holds two.
g.model.append(row("Cal","Witness",ap.STATE_EXISTING,"p1","Person","Primary",0))
g.model.append(row("Cal","Witness",ap.STATE_EXISTING,"p1","Person","Primary",1))
g.load_participants=lambda:None; g.refresh_completion=lambda force=False:None
g.update_status=lambda:None
g.on_apply(None)
check("the surviving reference was re-roled: %r" % str(p.refs[0].role),
      str(p.refs[0].role)=="Witness")
check("one change was applied, not two: %r" % g.last_status,
      "1 role change" in str(g.last_status))
check("and the one that no longer matched is owned up to: %r" % g.last_status,
      "no longer matched" in str(g.last_status))

# the row's identity is which reference to *this* event it is, so an
# unrelated reference appearing before it does not misdirect the edit
g,db=make()
p=Person("Dot",refs=[Ref("E1","Primary"),Ref("E9","Primary")])
db.people["p1"]=p; db.events["E1"]=Ev(500); db.events["E9"]=Ev(100)
g.event=Ev(500); g.event.get_handle=lambda:"E1"
g.model.append(row("Dot","Witness",ap.STATE_EXISTING,"p1","Person","Primary",0))
p.refs.insert(0, Ref("E7","Primary"))       # added elsewhere since the load
g.load_participants=lambda:None; g.refresh_completion=lambda force=False:None
g.update_status=lambda:None
g.on_apply(None)
check("the edit landed on the right reference: %r"
      % [(r.ref,str(r.role)) for r in p.refs],
      str(p.refs[1].role)=="Witness" and str(p.refs[0].role)=="Primary")
check("and it was counted as applied: %r" % g.last_status,
      "1 role change" in str(g.last_status)
      and "no longer matched" not in str(g.last_status))

print("\n[O2b] a vanished object only counts its actual changes as skipped")
g,db=make()
db.events["E1"]=Ev(500)
g.event=Ev(500); g.event.get_handle=lambda:"E1"
# Two untouched rows and one real detachment, on a person deleted since the
# list was loaded. Only the detachment was ever a pending change.
g.model.append(row("Gone","Primary",ap.STATE_EXISTING,"p9","Person","Primary",0))
g.model.append(row("Gone","Witness",ap.STATE_EXISTING,"p9","Person","Witness",1))
g.model.append(row("Gone","Primary",ap.STATE_DETACH,"p9","Person","Primary",2))
g.load_participants=lambda:None; g.refresh_completion=lambda force=False:None
g.update_status=lambda:None
g.on_apply(None)
check("only the one real change is reported skipped: %r" % g.last_status,
      "1 change(s) no longer matched" in str(g.last_status))

print("\n[O3] Apply refuses to write to an event that has gone")
g,db=make()
p=Person("Eve",refs=[])
db.people["p1"]=p                              # no db.events["E1"]
g.event=Ev(500); g.event.get_handle=lambda:"E1"
g.model.append(row("Eve","Primary",ap.STATE_NEW,"p1","Person","",-1))
g.load_participants=lambda:None; g.refresh_completion=lambda force=False:None
g.update_status=lambda:None
g.on_apply(None)
check("no reference was written", len(p.refs)==0)
check("nothing was committed", g.commits==[] and db.committed_events==[])
check("and it says why: %r" % g.last_status,
      "no longer exists" in str(g.last_status))

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
# Every kind of name that can make someone match has to read the same from
# the stored dicts and from a built Person, or the search box behaves
# differently depending on which path the index happened to take.

def parity(title, names, spouse=None, calendar=0, years=(1901,1980)):
    """Index one person both ways; insist the two agree byte for byte."""
    entries=[]; forms=[]
    for force_object in (False, True):
        g,db=make()
        db.events["E1"]=Ev(0,years[0],calendar)
        db.events["E2"]=Ev(0,years[1],calendar)
        db.people["p1"]=Person("x", names=names, b="E1", d="E2",
                               refs=[Ref("E1"),Ref("E2")])
        if spouse is not None:
            db.people["sp"]=Person("x", names=[spouse])
            db.families["f1"]=Family(father="sp", mother="p1")
        if force_object:
            def boom(): raise RuntimeError("no cursor here")
            db.get_person_cursor=boom
        g.build_people_cache(); drain(g)
        check("%s: the %s path indexed the person"
              % (title, "object" if force_object else "raw"),
              g._index_raw is not force_object and "p1" in g.people_labels)
        entries.append(g.people_labels.get("p1"))
        forms.append(g._index_forms.get("p1"))
    raw, obj = entries
    ok = raw is not None and obj is not None
    check("%s: labels identical: %r" % (title, ok and raw[0]),
          ok and raw[0]==obj[0])
    check("%s: search text identical" % title, ok and raw[1]==obj[1])
    check("%s: Enter's name forms identical" % title,
          forms[0] is not None and forms[0]==forms[1])
    return raw[0] if ok else ""

lab = parity("plain", [Name("Jane","Doe"), Name("Jane","Smith",ntype=3)])
check("years came through the year map: %r" % lab,
      "1901" in lab and "1980" in lab)
check("married surname still annotated", "[Smith]" in lab)

lab = parity("connector", [Name("Jean", surnames=[("Rossi","de","y"),
                                                  ("Pardo","","")])])
check("the connector survives both readers: %r" % lab, "y" in lab.split())
check("...and so do both surname parts",
      "Rossi" in lab and "Pardo" in lab and "de" in lab.split())

lab = parity("prefix only", [Name("Willem", surnames=[("","van der","")])])
check("a surname that is only a prefix is not dropped: %r" % lab,
      "van der" in lab)

lab = parity("aka", [Name("Lura Ruth","Casey"), Name("Loretta","")])
check("an alternate given name is annotated the same way: %r" % lab,
      "aka Loretta" in lab)

lab = parity("married", [Name("Louisa","Heitt")],
             spouse=Name("Ernest","Reyman"))
check("a surname reached by marriage is annotated the same way: %r" % lab,
      "m. Reyman" in lab)

lab = parity("nick and call",
             [Name("John Mervyn","Joy",nick="Buster",call="Mervyn"),
              Name("Jack","Joy",call="Sonny")])
check("the nickname is shown and marked: %r" % lab, "nicknamed Buster" in lab)
check("a call name that is not one of the given names is shown: %r" % lab,
      "called Sonny" in lab)
check("...but one that is adds no clutter: %r" % lab, "called Mervyn" not in lab)

# suppression is by whole word, case-folded: "Ann" is not "Annette"
lab = parity("call name inside a given name",
             [Name("Annette","Bell",call="Ann")])
check("a call name that only looks like a prefix is shown: %r" % lab,
      "called Ann" in lab)
lab = parity("call name in another case",
             [Name("John Mervyn","Joy",call="mervyn")])
check("...and case alone does not make it a different name: %r" % lab,
      "called mervyn" not in lab)

lab = parity("hebrew calendar", [Name("Chaim","Levi")],
             calendar=Date.CAL_HEBREW, years=(5661,5740))
check("both readers convert to the Gregorian year: %r" % lab,
      "1901" in lab and "1980" in lab)
check("...and neither shows the calendar-local one: %r" % lab,
      "5661" not in lab and "5740" not in lab)

print("\n[T] a broken raw layout degrades instead of emptying the index")
g,db=make()
db.people["p1"]=Person("x", names=[Name("Ann","Lee")])
db.get_person_cursor=lambda: _Cursor([{"handle":"p1","primary_name":None}])
g.build_people_cache(); drain(g)
check("fell back to the object API", g._index_raw is False)
check("and still indexed the person (%d)" % len(g.people_labels),
      len(g.people_labels)==1)

print("\n[T2] one unusable row does not strand the whole index")
# build_people_cache() only proves the raw layout on the first row. A bad
# row further in used to raise inside the GLib idle callback, which kills
# the idle source: _index_id stayed set, "Indexing names..." stuck forever
# and the sorted index was never published.
g,db=make()
for i in range(3):
    db.people["p%d"%i]=Person("x", names=[Name("G%d"%i,"S%d"%i)])
_good=db.get_person_cursor
def _mixed():
    rows=[]
    for _h,d in _good():
        d=dict(d)
        # Unreadable, but only once _raw_person_entry gets to it: the probe
        # in build_people_cache never sees this row.
        if d["handle"]=="p1": d["alternate_names"]=None
        rows.append(d)
    return _Cursor(rows)
db.get_person_cursor=_mixed
g.build_people_cache(); drain(g)
check("the build finished instead of hanging", g._index_id==0)
check("the placeholder stopped saying 'Indexing': %r" % g.placeholder,
      "Indexing" not in str(g.placeholder))
check("everyone is indexed (%d of 3)" % len(g.people_labels),
      len(g.people_labels)==3)
check("...including the row that could not be read raw: %r"
      % (g.people_labels.get("p1"),),
      "S1" in (g.people_labels.get("p1") or ("",))[0])
check("and the sorted index was published (%d)" % len(g.people_cache),
      len(g.people_cache)==3)

# the object path is guarded too: an unreadable person is skipped, not fatal
g,db=make()
db.people["ok"]=Person("x", names=[Name("Ann","Lee")])
db.people["bad"]=Person("x", names=[Name("Bad","Row")])
def _boom(): raise RuntimeError("no cursor here")
db.get_person_cursor=_boom
_real=g._person_entry
def _entry(person):
    if person is db.people["bad"]: raise RuntimeError("unreadable person")
    return _real(person)
g._person_entry=_entry
g.build_people_cache(); drain(g)
check("only the unreadable person was skipped (%d of 2)"
      % len(g.people_labels), len(g.people_labels)==1)
check("the build still published", g._index_id==0 and len(g.people_cache)==1)

print("\n[U] a surname reached by marriage is searchable")
# Louisa Heitt married Ernest Reyman. Her record carries no married name -
# that is how nearly every tree stores it - so "Louisa Reyman" has to be
# found through the family.
g,db=make()
db.people["lou"]=Person("x", names=[Name("Louisa","Heitt")])
db.people["ern"]=Person("x", names=[Name("Ernest August","Reyman")])
db.families["f1"]=Family(father="ern", mother="lou")
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
db.families["f1"]=Family(father="ern", mother="lou")
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
db.families["f1"]=Family(father="a", mother="b")
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

print("\n[AA] an alternate given name is visible, not just matchable")
# Lura Ruth Casey is also recorded as Loretta, with no surname of its own.
# Searching "Loretta" found her but the label gave no hint why.
g,db=make()
db.people["lura"]=Person("x", names=[Name("Lura Ruth","Casey"),
                                     Name("Loretta","")])
g.build_people_cache(); drain(g)
label=g.people_labels["lura"][0]
check("'Loretta' still finds her",
      any(h=="lura" for _l,h,_s in g._ranked_matches("Loretta")))
check("and the label now says why: %r" % label, "Loretta" in label)
check("marked as an alias, not as a surname: %r" % label, "aka Loretta" in label)
check("her own name still leads: %r" % label, label.startswith("Casey, Lura Ruth"))
check("searching her real name still works",
      any(h=="lura" for _l,h,_s in g._ranked_matches("Lura Casey")))

# an alternate that only repeats the primary given name adds nothing
g,db=make()
db.people["p1"]=Person("x", names=[Name("Ann","Lee"), Name("Ann","Lee")])
g.build_people_cache(); drain(g)
check("a duplicate alternate adds no clutter: %r" % g.people_labels["p1"][0],
      g.people_labels["p1"][0]=="Lee, Ann")

# all three annotation kinds can coexist and stay distinguishable
g,db=make()
db.people["w"]=Person("x", names=[Name("Jane","Doe"), Name("Janie","Smith")])
db.people["h"]=Person("x", names=[Name("Bob","Brown")])
db.families["f1"]=Family(father="h", mother="w")
g.build_people_cache(); drain(g)
lab=g.people_labels["w"][0]
check("surname, alias and marriage all shown: %r" % lab,
      "Smith" in lab and "aka Janie" in lab and "m. Brown" in lab)

print("\n[AD] a year in another calendar is converted before it is used")
# One Hebrew-calendar event reads as year 5686. Compared as a Gregorian
# year it postdates everybody, so _alive_at() would rule out the whole tree.
g,db=make()
db.events["b1"]=Ev(0,5686,Date.CAL_HEBREW)          # 1926 Gregorian
db.people["p1"]=Person("x", names=[Name("Chaim","Levi")],
                       b="b1", refs=[Ref("b1")])
g.build_people_cache(); drain(g)
check("the label carries the Gregorian year: %r" % g.people_labels["p1"][0],
      "1926" in g.people_labels["p1"][0]
      and "5686" not in g.people_labels["p1"][0])
check("so does the lifespan the alive filter reads: %r"
      % (g._index_lifespan["p1"],), g._index_lifespan["p1"][0]==1926)
g.event=Ev(0,5686,Date.CAL_HEBREW); g.event.get_handle=lambda:"E"
check("the event's own year is converted too (%d)" % g._event_year(),
      g._event_year()==1926)
check("...so a 1926 birth is not ruled out by a 1926 event",
      g._alive_at("p1", g._event_year()) is True)
check("and someone born in 1926 is still offered: %r"
      % [h for _l,h,_s in g._ranked_matches("Chaim Levi")],
      [h for _l,h,_s in g._ranked_matches("Chaim Levi")]==["p1"])

print("\n[AE] a christening stands in for a birth, a burial for a death")
# Neither event sets birth_ref_index or death_ref_index, so reading only
# those left these people with no years at all - no label years, and nothing
# for the alive filter to exclude them by.

def fallback_tree(force_object):
    g,db=make()
    db.events["chr"]=Ev(0,1840,etype=EventType.CHRISTEN)
    db.events["bur"]=Ev(0,1890,etype=EventType.BURIAL)
    db.people["p1"]=Person("x", names=[Name("Ada","Stone")],
                           refs=[Ref("chr"),Ref("bur")])
    if force_object:
        def boom(): raise RuntimeError("no cursor here")
        db.get_person_cursor=boom
    g.build_people_cache(); drain(g)
    return g

for _force in (False, True):
    g=fallback_tree(_force); path="object" if _force else "raw"
    check("%s: took the raw path? %r" % (path, g._index_raw),
          g._index_raw is not _force)
    check("%s: both years reach the label: %r" % (path, g.people_labels["p1"][0]),
          "1840" in g.people_labels["p1"][0]
          and "1890" in g.people_labels["p1"][0])
    check("%s: and the lifespan: %r" % (path, g._index_lifespan["p1"]),
          g._index_lifespan["p1"]==(1840,1890,ap.BIRTH_GRACE))
    check("%s: so a 1990 event rules her out" % path,
          g._alive_at("p1",1990) is False)
    check("%s: and an 1860 one does not" % path,
          g._alive_at("p1",1860) is True)
check("both paths produce the same entry",
      fallback_tree(False).people_labels["p1"]
      == fallback_tree(True).people_labels["p1"])
check("both paths agree on the grace too",
      fallback_tree(False)._index_lifespan["p1"]
      == fallback_tree(True)._index_lifespan["p1"])

# a christening follows a birth, so it is a lower bound set slightly late
for _force in (False, True):
    g,db=make(); path="object" if _force else "raw"
    db.events["chr"]=Ev(0,1842,etype=EventType.CHRISTEN)
    db.people["p1"]=Person("x", names=[Name("Ada","Stone")], refs=[Ref("chr")])
    if _force:
        def boom(): raise RuntimeError("no cursor here")
        db.get_person_cursor=boom
    g.build_people_cache(); drain(g)
    check("%s: an 1841 census does not rule out a child christened in 1842"
          % path, g._alive_at("p1",1841) is True)
    check("%s: ...but 1830 still does" % path,
          g._alive_at("p1",1830) is False)
    check("%s: the grace is recorded, not baked into the year: %r"
          % (path, g._index_lifespan["p1"]),
          g._index_lifespan["p1"]==(1842,0,ap.BIRTH_GRACE))
    check("%s: and the label shows the real christening year: %r"
          % (path, g.people_labels["p1"][0]),
          "1842" in g.people_labels["p1"][0])

# a real birth year gets no grace at all - that would be hedging
for _force in (False, True):
    g,db=make(); path="object" if _force else "raw"
    db.events["b1"]=Ev(0,1842,etype=EventType.BIRTH)
    db.people["p1"]=Person("x", names=[Name("Ada","Stone")],
                           b="b1", refs=[Ref("b1")])
    if _force:
        def boom(): raise RuntimeError("no cursor here")
        db.get_person_cursor=boom
    g.build_people_cache(); drain(g)
    check("%s: a recorded 1842 birth still rules out 1841" % path,
          g._alive_at("p1",1841) is False)
    check("%s: and carries no grace: %r" % (path, g._index_lifespan["p1"]),
          g._index_lifespan["p1"]==(1842,0,0))

# an undated primary event is no use as a year, so it must not block a
# dated fallback from supplying one
for _force in (False, True):
    g,db=make(); path="object" if _force else "raw"
    db.events["b1"]=Ev(0,0,etype=EventType.BIRTH)          # birth, no date
    db.events["chr"]=Ev(0,1842,etype=EventType.CHRISTEN)
    db.events["d1"]=Ev(0,0,etype=EventType.DEATH)          # death, no date
    db.events["bur"]=Ev(0,1900,etype=EventType.BURIAL)
    db.people["p1"]=Person("x", names=[Name("Ada","Stone")],
                           b="b1", d="d1",
                           refs=[Ref("b1"),Ref("chr"),Ref("d1"),Ref("bur")])
    if _force:
        def boom(): raise RuntimeError("no cursor here")
        db.get_person_cursor=boom
    g.build_people_cache(); drain(g)
    check("%s: the dated christening and burial won through: %r"
          % (path, g._index_lifespan["p1"]),
          g._index_lifespan["p1"]==(1842,1900,ap.BIRTH_GRACE))
    check("%s: so the years reach the label: %r"
          % (path, g.people_labels["p1"][0]),
          "1842" in g.people_labels["p1"][0]
          and "1900" in g.people_labels["p1"][0])
    check("%s: and a 1990 event rules her out" % path,
          g._alive_at("p1",1990) is False)

# a witness at someone else's burial has not died
for _force in (False, True):
    g,db=make(); path="object" if _force else "raw"
    db.events["bur"]=Ev(0,1890,etype=EventType.BURIAL)
    db.people["p1"]=Person("x", names=[Name("Ida","Stone")],
                           refs=[Ref("bur","Witness")])
    if _force:
        def boom(): raise RuntimeError("no cursor here")
        db.get_person_cursor=boom
    g.build_people_cache(); drain(g)
    check("%s: a non-primary role is not a fallback: %r"
          % (path, g._index_lifespan["p1"]),
          g._index_lifespan["p1"]==(0,0,0))
    check("%s: so she is never ruled out" % path,
          g._alive_at("p1",1990) is None)

print("\n[AF] an event edited elsewhere is noticed")
g,db=make()
db.events["b1"]=Ev(0,1850)
db.people["p1"]=Person("x", names=[Name("Sarah","Fisher")],
                       b="b1", refs=[Ref("b1")])
g.build_people_cache(); drain(g)
check("indexed with the year it had: %r" % g.people_labels["p1"][0],
      "1850" in g.people_labels["p1"][0])
db.events["b1"]=Ev(0,1860)                     # the birth date was corrected
g.on_events_changed(["b1"])
check("the year map followed the edit (%r)" % g._index_years.get("b1"),
      g._index_years.get("b1")==1860)
check("so did the label: %r" % g.people_labels["p1"][0],
      "1860" in g.people_labels["p1"][0])
check("and the lifespan the alive filter reads: %r" % (g._index_lifespan["p1"],),
      g._index_lifespan["p1"]==(1860,0,0))

# the selected event's own date moving refreshes the view
g,db=make()
db.events["E1"]=Ev(500,1900)
g.event=db.events["E1"]; g.event_handle="E1"
g.on_events_changed(["E1"])
check("the active event's change triggers a refresh (%d)" % g.updates,
      g.updates==1)
check("...and the list is reloaded, since nothing was staged",
      g.event_handle is None)

# but not at the cost of staged edits
g,db=make()
db.events["E1"]=Ev(500,1900)
g.event=db.events["E1"]; g.event_handle="E1"
g.model.append(row("Ann","Primary",ap.STATE_NEW,"p1","Person","",-1))
g.on_events_changed(["E1"])
check("a staged addition survives the refresh", g.event_handle=="E1")
check("and the gramplet says the event moved: %r" % g._notice,
      "changed elsewhere" in g._notice)

# our own apply does not re-enter this
g,db=make()
g._applying=True
g.on_events_changed(["E1"])
check("the apply transaction's own signals are ignored", g.updates==0)

print("\n[AG] the active event being deleted clears the view")
g,db=make()
db.events["E1"]=Ev(500,1900)
g.event=db.events["E1"]; g.event_handle="E1"
g._index_years["E1"]=1900
g.model.append(row("Ann","Primary",ap.STATE_EXISTING,"p1","Person","Primary",0))
del db.events["E1"]
g.on_events_deleted(["E1"])
check("the selection is dropped", g.event is None and g.event_handle is None)
check("the participant list is emptied (%d)" % len(g.model), len(g.model)==0)
check("the year map forgets it", "E1" not in g._index_years)
check("and the view is refreshed (%d)" % g.updates, g.updates==1)

print("\n[AH] a family edit keeps married surnames honest")
g,db=make()
db.people["hus"]=Person("x", names=[Name("Ernest","Reyman")], families=["f1"])
db.people["wife"]=Person("x", names=[Name("Louisa","Heitt")], families=["f1"])
db.families["f1"]=Family(father="hus", mother="wife")
g.build_people_cache(); drain(g)
check("she is searchable by his surname: %r" % g.people_labels["wife"][0],
      "m. Reyman" in g.people_labels["wife"][0])
check("the family's wife was remembered", g._index_mothers.get("f1")=="wife")
# the husband is deleted: Gramps commits the family, never the wife, so no
# person-update ever names her
del db.people["hus"]
db.families["f1"]=Family(father=None, mother="wife")
db.people["wife"]=Person("x", names=[Name("Louisa","Heitt")], families=["f1"])
g.on_families_changed(["f1"])
check("the stale married surname is gone: %r" % g.people_labels["wife"][0],
      "m. Reyman" not in g.people_labels["wife"][0])
check("and 'Louisa Reyman' no longer finds her",
      not any(h=="wife" for _l,h,_s in g._ranked_matches("Louisa Reyman")))
check("but her own name still does",
      any(h=="wife" for _l,h,_s in g._ranked_matches("Louisa Heitt")))

# a family deleted outright: the wife can only be found through the old map
g,db=make()
db.people["hus"]=Person("x", names=[Name("Ernest","Reyman")], families=["f1"])
db.people["wife"]=Person("x", names=[Name("Louisa","Heitt")], families=["f1"])
db.families["f1"]=Family(father="hus", mother="wife")
g.build_people_cache(); drain(g)
del db.families["f1"]
db.people["wife"]=Person("x", names=[Name("Louisa","Heitt")], families=[])
g.on_families_changed(["f1"])
check("a deleted family drops her married surname too: %r"
      % g.people_labels["wife"][0],
      "m. Reyman" not in g.people_labels["wife"][0])

print("\n[AI] a bulk rebuild reindexes the tree, once")
g,db=make()
db.people["p1"]=Person("x", names=[Name("Ann","Lee")])
g.build_people_cache(); drain(g)
db.people["p2"]=Person("x", names=[Name("Imported","Person")])
# an importer disables the signals and only calls request_rebuild(), which
# fires person-, family- and event-rebuild one after another
g.on_tree_rebuilt(); g.on_tree_rebuilt(); g.on_tree_rebuilt()
check("the three signals coalesced into one pending rebuild",
      g._rebuild_id != 0)
sid=g._rebuild_id
run_idle(sid)
drain(g)
check("the imported person is searchable (%d indexed)" % len(g.people_labels),
      "p2" in g.people_labels)
check("the rebuild source was cleared", g._rebuild_id==0)

print("\n[AJ] a family participant does not speak for its spouses")
# A marriage is referenced by the Family, and the Events view counts both
# spouses through it. Offering one of them again wrote a second, personal
# reference at Primary and had the Main Participants column count them twice.

def married_tree():
    g,db=make()
    db.events["E1"]=Ev(500,1900,etype=EventType.MARRIAGE)
    db.people["h"]=Person("x", names=[Name("Bob","Brown")])
    db.people["w"]=Person("x", names=[Name("Jane","Brown")])
    db.families["f1"]=Family(father="h", mother="w",
                             refs=[Ref("E1","Family")])
    g.build_people_cache(); drain(g)
    g.event=db.events["E1"]; g.event.get_handle=lambda:"E1"
    g.event_handle="E1"
    g.load_participants()
    return g,db

def select(g, index):
    """Point the tree's selection at one row, the way a click would."""
    sel=type("S",(),{"get_selected":lambda s:(g.model,_Iter(index))})()
    g.tree=type("T",(),{"get_selection":lambda s:sel})()

g,db=married_tree()
check("the family is the participant (%d rows)" % len(g.model),
      len(g.model)==1 and g.model[0][ap.COL_KIND]=="Family")
# A listed family does NOT speak for its spouses in the offer. Their family
# reference does stand for them in Main Participants, so adding them
# personally means being named twice there - that is the user's call, and
# refusing the edit was second-guessing them over one duplicated name.
g.refresh_completion()
check("a listed family excludes nobody: %r" % (g._completion_excluded,),
      g._completion_excluded==frozenset())
check("its spouses are offered: %r" % [h for _l,h,_s in g._ranked_matches("Brown")],
      sorted(h for _l,h,_s in g._ranked_matches("Brown"))==["h","w"])
g.stage_person("Brown, Bob","h")
check("and staging one adds it (%d rows)" % len(g.model),
      len(g.model)==2)
check("...with no complaint: %r" % g.last_status,
      "through a family" not in str(g.last_status))

print("\n[AJ2] a spouse staged personally survives the family coming back")
# Detach F, stage a spouse personally, then un-detach F. Both staying is now
# the point: the duplicate is the user\'s to make, so nothing is unstaged
# behind their back.
g,db=married_tree()
select(g, 0)
g.on_remove(None)                       # detach the family
check("the family is staged for detachment",
      g.model[0][ap.COL_STATE]==ap.STATE_DETACH)
g.stage_person("Brown, Bob","h")
check("its husband can be staged personally (%d rows)" % len(g.model),
      len(g.model)==2)
g.on_remove(None)                       # un-detach the family
check("the family is attached again",
      g.model[0][ap.COL_STATE]==ap.STATE_EXISTING)
check("and the staged spouse is still there (%d rows)" % len(g.model),
      len(g.model)==2)
check("...unstaged behind nobody\'s back: %r" % g.last_status,
      "covered by a family" not in str(g.last_status))

# and the transaction writes it rather than skipping it
g,db=married_tree()
g.model.append(row("Brown, Bob","Primary",ap.STATE_NEW,"h","Person","",-1))
g.load_participants=lambda:None; g.refresh_completion=lambda force=False:None
g.update_status=lambda:None
g.on_apply(None)
check("the personal reference was written: %r" % db.people["h"].refs,
      len(db.people["h"].refs)==1)
check("the family reference is untouched (%d)" % len(db.families["f1"].refs),
      len(db.families["f1"].refs)==1)
check("and the apply counts it: %r" % g.last_status,
      "+1" in str(g.last_status) and "no longer matched" not in str(g.last_status))

print("\n[AJ3] an active family row follows family updates and deletes")
g,db=married_tree()
db.people["w2"]=Person("x", names=[Name("Jill","Brown")])
db.families["f1"]=Family(father="h", mother="w2",
                         refs=[Ref("E1","Family")])
g.on_families_changed(["f1"])
check("a relinked family row shows the new spouse: %r" % g.model[0][ap.COL_NAME],
      "Jill" in g.model[0][ap.COL_NAME] and "Jane" not in g.model[0][ap.COL_NAME])
check("and no one is excluded on account of it: %r" % (g._completion_excluded,),
      g._completion_excluded==frozenset())

g,db=married_tree()
del db.families["f1"]
g.on_families_changed(["f1"])
check("a deleted family row vanishes from the active event (%d rows)" % len(g.model),
      len(g.model)==0)

print("\n[AK] a role typed in the wrong case is snapped to the real one")
g,db=make()
g.role_model=_ListStore(str)
for _n in ("Primary","Witness","Celebrant"): g.role_model.append([_n])
g.model.append(row("Ann","Primary",ap.STATE_EXISTING,"p1","Person","Primary",0))
g.update_status=lambda:None
g.on_role_edited(None,"0","  primary ")
check("'  primary ' becomes 'Primary': %r" % g.model[0][ap.COL_ROLE],
      g.model[0][ap.COL_ROLE]=="Primary")
check("...which is what the Main Participants column counts",
      EventRoleType(g.model[0][ap.COL_ROLE]).is_primary())
g.on_role_edited(None,"0","WITNESS")
check("'WITNESS' becomes 'Witness': %r" % g.model[0][ap.COL_ROLE],
      g.model[0][ap.COL_ROLE]=="Witness")
g.on_role_edited(None,"0","Pallbearer")
check("a genuinely new role is still allowed through: %r"
      % g.model[0][ap.COL_ROLE], g.model[0][ap.COL_ROLE]=="Pallbearer")
check("...and Gramps makes it a custom role",
      EventRoleType("Pallbearer").is_custom())
g.on_role_edited(None,"0","   ")
check("blank input leaves the role alone: %r" % g.model[0][ap.COL_ROLE],
      g.model[0][ap.COL_ROLE]=="Pallbearer")

print("\n[AL] re-picking a doubly detached person restores both rows")
g,db=make()
db.people["p1"]=Person("x", names=[Name("Ann","Lee")])
g.build_people_cache(); drain(g)
g.model.append(row("Lee, Ann","Witness",ap.STATE_DETACH,"p1","Person","Witness",0))
g.model.append(row("Lee, Ann","Primary",ap.STATE_DETACH,"p1","Person","Primary",1))
g.stage_person("Lee, Ann","p1")
check("both detachments were undone: %r"
      % [r[ap.COL_STATE] for r in g.model],
      all(r[ap.COL_STATE]==ap.STATE_EXISTING for r in g.model))
check("and no third row was invented (%d)" % len(g.model), len(g.model)==2)

print("\n[AM] Enter says something true about why nothing was staged")
g,db=make()
db.events["b1"]=Ev(0,1900); db.events["d1"]=Ev(0,1980)
db.people["p1"]=Person("x", names=[Name("Amy","Smith")],
                       b="b1", d="d1", refs=[Ref("b1"),Ref("d1")])
db.people["p2"]=Person("x", names=[Name("Amy Jane","Smithson")])
g.build_people_cache(); drain(g)
g.refresh_completion()
g.stage_person=lambda l,h: setattr(g,"staged",(l,h)); g.staged=None

def enter(typed):
    g.staged=None; g.last_status=None
    g.on_entry_activate(type("E",(),{"get_text":lambda s:typed,
                                     "set_text":lambda s,t:None})())

# (a) the exact-match test used to compare the typed text against the
# decorated label, so anyone with dates could never be an exact match
enter("Amy Smith")
check("a full name picks its owner out of the partial matches: %r"
      % (g.staged,), g.staged is not None and g.staged[1]=="p1")
enter("Smith")
check("a bare surname is still ambiguous: %r" % (g.staged,),
      g.staged is None)
check("...and Enter says nothing about it: %r" % g.last_status,
      not str(g.last_status or ""))

# (b) a search that only turns up people already listed says so
g,db=make()
db.people["p1"]=Person("x", names=[Name("Amy","Smith")])
g.build_people_cache(); drain(g)
g.model.append(row("Smith, Amy","Primary",ap.STATE_EXISTING,
                   "p1","Person","Primary",0))
g.refresh_completion()
g.stage_person=lambda l,h: setattr(g,"staged",(l,h)); g.staged=None
enter("Amy Smith")
check("nothing is staged", g.staged is None)
check("and it does not claim there is no such person: %r" % g.last_status,
      "already a participant" in str(g.last_status))

# (c) an Enter during the index build says the index is still filling
g,db=make()
for i in range(300):
    db.people["p%d"%i]=Person("x", names=[Name("G%d"%i,"S%d"%i)])
g.build_people_cache()                       # started, not finished
g.stage_person=lambda l,h: setattr(g,"staged",(l,h)); g.staged=None
enter("G299 S299")
check("it says the index is still filling: %r" % g.last_status,
      "indexing" in str(g.last_status).lower())
drain(g)
enter("G299 S299")
check("...and once it is done the same text stages the person: %r"
      % (g.staged,), g.staged is not None and g.staged[1]=="p299")

print("\n[AN] moving to another event says what it discarded")
g,db=make()
db.events["E1"]=Ev(500,1900); db.events["E2"]=Ev(600,1910)
g.get_active=lambda t:"E2"
g.header=type("H",(),{"set_markup":lambda s,m:None})()
g.load_participants=lambda: g.model.clear()
g.refresh_completion=lambda force=False:None
g.event_handle="E1"
g.model.append(row("Ann","Primary",ap.STATE_NEW,"p1","Person","",-1))
g.model.append(row("Bea","Witness",ap.STATE_DETACH,"p2","Person","Primary",0))
g.main()
check("the new event is loaded", g.event_handle=="E2")
check("the staged edits are gone (%d rows)" % len(g.model), len(g.model)==0)
check("but they were not thrown away in silence: %r" % g.last_status,
      "Discarded 2" in str(g.last_status))

# deselecting the event entirely says it too
g,db=make()
g.get_active=lambda t:None
g.header=type("H",(),{"set_markup":lambda s,m:None})()
g.event_handle="E1"
g.model.append(row("Ann","Primary",ap.STATE_NEW,"p1","Person","",-1))
g.main()
check("deselecting reports it as well: %r" % g.last_status,
      "Discarded 1" in str(g.last_status))

# and an event with nothing staged says nothing
g,db=make()
db.events["E2"]=Ev(600,1910)
g.get_active=lambda t:"E2"
g.header=type("H",(),{"set_markup":lambda s,m:None})()
g.load_participants=lambda:None
g.refresh_completion=lambda force=False:None
g.event_handle="E1"
g.main()
check("nothing staged, nothing said: %r" % g.last_status,
      not str(g.last_status or ""))

# the active event being deleted counts the edits before the model is cleared
g,db=make()
db.events["E1"]=Ev(500,1900)
g.event=db.events["E1"]; g.event_handle="E1"
g.model.append(row("Ann","Primary",ap.STATE_NEW,"p1","Person","",-1))
g.model.append(row("Bea","Primary",ap.STATE_NEW,"p2","Person","",-1))
del db.events["E1"]
g.on_events_deleted(["E1"])
check("a deleted active event owns up to what went with it: %r" % g.last_status,
      "Discarded 2" in str(g.last_status))

# a notice is shown next to the counts, not instead of them
g,db=make()
g.model.append(row("Ann","Primary",ap.STATE_NEW,"p1","Person","",-1))
g._notice="Something happened"
g.update_status()
check("both the notice and the counts reach the label: %r" % g.last_status,
      "Something happened" in str(g.last_status)
      and "1 to add" in str(g.last_status))
g.update_status()
check("but the notice is one-shot: %r" % g.last_status,
      "Something happened" not in str(g.last_status)
      and "1 to add" in str(g.last_status))

# an event edited elsewhere with edits staged says so, and is not swallowed
g,db=make()
db.events["E1"]=Ev(500,1900)
g.event=db.events["E1"]; g.event_handle="E1"
g.model.append(row("Ann","Primary",ap.STATE_NEW,"p1","Person","",-1))
g.on_events_changed(["E1"])
g.update_status()
check("the 'changed elsewhere' notice survives to the label: %r" % g.last_status,
      "changed elsewhere" in str(g.last_status)
      and "1 to add" in str(g.last_status))

print("\n[AN3] a big re-cache goes onto idle turns, not the signal handler")
# Correcting a shared event's date names every participant. A census with
# hundreds of them costs a read each plus their birth, death and family
# reads - the query-per-person freeze the raw index exists to avoid.
g,db=make()
db.events["E1"]=Ev(0,1900)
count=ap.RECACHE_CHUNK*2+5
for i in range(count):
    db.people["p%d"%i]=Person("x", names=[Name("G%d"%i,"Census")],
                              b="E1", refs=[Ref("E1")])
g.build_people_cache(); drain(g)
reads=[]
_orig=db.get_person_from_handle
db.get_person_from_handle=lambda h:(reads.append(h), _orig(h))[1]
db.events["E1"]=Ev(0,1910)                       # the date was corrected
g.on_events_changed(["E1"])
check("the handler returned without reading anybody (%d reads)" % len(reads),
      reads==[])
check("...having queued them all instead (%d of %d)"
      % (len(g._recache_pending), count), len(g._recache_pending)==count)
turns=0
while g._recache_id and turns<100:
    cb=GLib._sources.get(g._recache_id)
    if cb is None: break
    turns+=1
    if not cb(): break
check("it took several idle turns (%d)" % turns, turns>1)
check("every label caught up: %r" % g.people_labels["p0"][0],
      all("1910" in g.people_labels["p%d"%i][0] for i in range(count)))
check("and the queue is empty", not g._recache_pending and g._recache_id==0)

# a small batch is still done there and then, as it always was
g,db=make()
db.events["E1"]=Ev(0,1900)
db.people["p1"]=Person("x", names=[Name("Ann","Lee")], b="E1", refs=[Ref("E1")])
g.build_people_cache(); drain(g)
db.events["E1"]=Ev(0,1910)
g.on_events_changed(["E1"])
check("one participant is re-cached immediately: %r" % g.people_labels["p1"][0],
      "1910" in g.people_labels["p1"][0])

print("\n[AN4] one corrupt record does not abandon the rest of a batch")
# Callback.emit swallows an exception from a signal handler with nothing but
# a log line (gen/utils/callback.py:427), so a handler that dies half way
# leaves stale labels with no sign at all.
g,db=make()
db.people["good1"]=Person("x", names=[Name("Ann","Lee")])
db.people["bad"]=Person("x", names=[Name("Bad","Row")])
db.people["good2"]=Person("x", names=[Name("Zoe","Vale")])
g.build_people_cache(); drain(g)
db.people["good1"]=Person("x", names=[Name("Anne","Leigh")])
db.people["good2"]=Person("x", names=[Name("Zoey","Vail")])
_real=g._person_entry
def _entry(person):
    if person is db.people["bad"]: raise RuntimeError("corrupt record")
    return _real(person)
g._person_entry=_entry
try:
    g.on_people_changed(["good1","bad","good2"])
    survived=True
except Exception as exc:
    survived=False
check("the handler did not blow up (%r)" % (survived,), survived)
check("the people either side of the bad one were re-cached: %r"
      % [g.people_labels[h][0] for h in ("good1","good2")],
      "Leigh" in g.people_labels["good1"][0]
      and "Vail" in g.people_labels["good2"][0])

# and a family nothing can read is skipped, not fatal
g,db=make()
db.people["wife"]=Person("x", names=[Name("Louisa","Heitt")], families=["f1"])
g.build_people_cache(); drain(g)
def _boom_family(h): raise RuntimeError("corrupt family")
g._get_family=_boom_family
try:
    g.on_people_changed(["wife"])
    survived=True
except Exception as exc:
    survived=False
check("an unreadable family is survivable (%r)" % (survived,), survived)

print("\n[AN2] a queued bulk rebuild does not outlive its tree")
g,db=make()
db.people["p1"]=Person("x", names=[Name("Ann","Lee")])
g.build_people_cache(); drain(g)
g.on_tree_rebuilt()
sid=g._rebuild_id
check("a rebuild is queued", sid != 0)
g._cancel_rebuild()
check("cancelling clears the handle", g._rebuild_id==0)
check("...and drops the idle source", GLib._sources.get(sid) is None)

print("\n[AO] a nickname that finds someone is visible on their row")
g,db=make()
db.people["p1"]=Person("x", names=[Name("John Mervyn","Joy",nick="Buster")])
db.people["p2"]=Person("x", names=[Name("Ann","Lee",call="Nancy")])
g.build_people_cache(); drain(g)
check("'Buster' finds him",
      any(h=="p1" for _l,h,_s in g._ranked_matches("Buster")))
check("and the label says why: %r" % g.people_labels["p1"][0],
      "nicknamed Buster" in g.people_labels["p1"][0])
check("'Nancy' finds her",
      any(h=="p2" for _l,h,_s in g._ranked_matches("Nancy")))
check("and the label says why: %r" % g.people_labels["p2"][0],
      "called Nancy" in g.people_labels["p2"][0])
check("her own name still leads: %r" % g.people_labels["p2"][0],
      g.people_labels["p2"][0].startswith("Lee, Ann"))

print("\n[AP] letters that do not decompose, and elided apostrophes")
g,db=make()
db.people["p1"]=Person("x", names=[Name("Søren","Kjær")])
db.people["p2"]=Person("x", names=[Name("Sean","O'Brien")])
db.people["p3"]=Person("x", names=[Name("Stanisław","Bałka")])
g.build_people_cache(); drain(g)
def hit(typed): return [h for _l,h,_s in g._ranked_matches(typed)]
check("'Soren' finds 'Søren'", "p1" in hit("Soren"))
check("'Kjaer' finds 'Kjær'", "p1" in hit("Kjaer"))
check("'Søren' still finds himself", "p1" in hit("Søren"))
check("'Stanislaw Balka' finds the barred Ls", "p3" in hit("Stanislaw Balka"))
check("'obrien' finds \"O'Brien\"", "p2" in hit("obrien"))
check("\"Sean O'Brien\" still finds him", "p2" in hit("Sean O'Brien"))
check("and so does 'o brien'", "p2" in hit("o brien"))
check("_fold keeps the split and the elided form: %r" % ap._fold("O'Brien"),
      ap._fold("O'Brien")=="o brien obrien")
check("a plain name is untouched: %r" % ap._fold("Doe, Jane"),
      ap._fold("Doe, Jane")=="doe jane")

# a precomposed letter decomposes to a stroked one, so the translation has
# to come after NFKD or the stroke survives into the index
check("'Soren' finds the precomposed 'ǿren' too: %r" % ap._fold("Sǿren"),
      ap._fold("Sǿren")=="soren")
check("...and 'Kjaer' the precomposed one: %r" % ap._fold("Kjǽr"),
      ap._fold("Kjǽr")=="kjaer")
g,db=make()
db.people["p4"]=Person("x", names=[Name("Sǿren","Kjǽr")])
g.build_people_cache(); drain(g)
check("and they are searchable by the plain spelling",
      any(h=="p4" for _l,h,_s in g._ranked_matches("Soren Kjaer")))

print("\n[AQ] every spelling of an apostrophe name can match exactly")
g,db=make()
db.people["p1"]=Person("x", names=[Name("Sean","O'Brien")])
db.people["p2"]=Person("x", names=[Name("Sean Patrick","O'Brienson")])
g.build_people_cache(); drain(g)
g.stage_person=lambda l,h: setattr(g,"staged",(l,h)); g.staged=None
def enter_ap(typed):
    g.staged=None; g.last_status=None
    g.on_entry_activate(type("E",(),{"get_text":lambda s:typed,
                                     "set_text":lambda s,t:None})())
for spelling in ("Sean O'Brien", "Sean O Brien", "Sean OBrien"):
    enter_ap(spelling)
    check("%r stages the right man: %r" % (spelling, g.staged),
          g.staged is not None and g.staged[1]=="p1")
enter_ap("Sean")
check("a given name alone is still ambiguous: %r" % (g.staged,),
      g.staged is None)

print("\n[AR] a person's birth/death pointers survive the list moving")
# Gramps addresses birth and death by POSITION in event_ref_list, and they
# are the only two things in the data model that work that way. Inserting a
# reference chronologically shifts every later entry down one, so the stored
# positions must move too - otherwise the pointer names whatever slid into
# its slot and a Residence event becomes somebody's death. This corrupted 32
# people in the real tree before it was caught.
def apply_only(g):
    g.load_participants=lambda:None; g.refresh_completion=lambda force=False:None
    g.update_status=lambda:None
    g.on_apply(None)

# (a) a new reference inserted BEFORE the death pointer
g,db=make()
birth=Ref("B1"); death=Ref("D1")
p=Person("Nell", b="B1", d="D1", refs=[birth, death])
db.people["p1"]=p
db.events["B1"]=Ev(100,1900); db.events["D1"]=Ev(900,1990)
db.events["E1"]=Ev(500,1950)
g.event=db.events["E1"]; g.event.get_handle=lambda:"E1"
check("death starts at index 1", p.death_ref_index==1)
g.model.append(row("Nell","Primary",ap.STATE_NEW,"p1","Person","",-1))
apply_only(g)
check("the new ref landed chronologically in the middle (%d refs)" % len(p.refs),
      len(p.refs)==3 and p.refs[1].ref=="E1")
check("birth pointer still names the birth: %r" % p.birth_ref_index,
      p.get_birth_ref() is birth)
check("death pointer FOLLOWED the shift: %r" % p.death_ref_index,
      p.death_ref_index==2 and p.get_death_ref() is death)

# (b) the mirror case: detaching a reference that sits before the death
g,db=make()
birth=Ref("B1"); mid=Ref("E1"); death=Ref("D1")
p=Person("Owen", b="B1", d="D1", refs=[birth, mid, death])
db.people["p1"]=p
db.events["B1"]=Ev(100,1900); db.events["D1"]=Ev(900,1990)
db.events["E1"]=Ev(500,1950)
g.event=db.events["E1"]; g.event.get_handle=lambda:"E1"
check("death starts at index 2", p.death_ref_index==2)
g.model.append(row("Owen","Primary",ap.STATE_DETACH,"p1","Person","Primary",0))
apply_only(g)
check("the detached ref is gone (%d refs)" % len(p.refs), len(p.refs)==2)
check("death pointer came back down with it: %r" % p.death_ref_index,
      p.death_ref_index==1 and p.get_death_ref() is death)

# (c) detaching the death event itself: Gramps' own answer is -1, not a
# pointer at whatever took its place
g,db=make()
birth=Ref("B1"); death=Ref("E1")
p=Person("Pearl", b="B1", d="E1", refs=[birth, death, Ref("Z1")])
db.people["p1"]=p
db.events["B1"]=Ev(100,1900); db.events["E1"]=Ev(900,1990); db.events["Z1"]=Ev(950,1995)
g.event=db.events["E1"]; g.event.get_handle=lambda:"E1"
check("death starts at index 1", p.death_ref_index==1)
g.model.append(row("Pearl","Primary",ap.STATE_DETACH,"p1","Person","Primary",0))
apply_only(g)
check("the death reference is gone (%d refs)" % len(p.refs), len(p.refs)==2)
check("and the pointer says 'none' rather than naming its neighbour: %r"
      % p.death_ref_index, p.death_ref_index==-1 and p.get_death_ref() is None)

# (d) a person with no death recorded keeps -1 rather than acquiring one
g,db=make()
p=Person("Quinn", refs=[Ref("Z1")])
db.people["p1"]=p; db.events["Z1"]=Ev(100,1900); db.events["E1"]=Ev(500,1950)
g.event=db.events["E1"]; g.event.get_handle=lambda:"E1"
g.model.append(row("Quinn","Primary",ap.STATE_NEW,"p1","Person","",-1))
apply_only(g)
check("no death is invented: %r" % p.death_ref_index, p.death_ref_index==-1)

# (e) families have no such pointers; the restore must not touch them
g,db=make()
f=Family(father="h", mother="w", refs=[Ref("E1","Family")])
db.families["f1"]=f
db.events["E1"]=Ev(500,1950)
g.event=db.events["E1"]; g.event.get_handle=lambda:"E1"
g.model.append(row("Family: A & B","Family",ap.STATE_DETACH,"f1","Family","Family",0))
apply_only(g)
check("a family row applies without reaching for birth/death (%d refs)"
      % len(f.refs), len(f.refs)==0)

print("\n[AB] a change that lands during the index build is not clobbered")
# The raw build walks a snapshot of the table taken at build_people_cache time.
# A person whose name changed after that snapshot must keep the fresh name
# rather than being written back from the stale copy; a deleted person must
# stay deleted instead of being resurrected by the snapshot.
g,db=make()
db.people["p1"]=Person("x", names=[Name("Old","Name")])
g.build_people_cache()                    # snapshot taken (p1 = Old); build not yet run
db.people["p1"]=Person("x", names=[Name("New","Name")])
g.on_people_changed(["p1"])               # person-update arrives mid-build
drain(g)
check("a rename mid-build keeps the fresh name: %r" % g.people_labels["p1"][0],
      "New" in g.people_labels["p1"][0])
check("and the stale name is gone: %r" % g.people_labels["p1"][0],
      "Old" not in g.people_labels["p1"][0])

g,db=make()
db.people["p1"]=Person("x", names=[Name("Ghost","Name")])
g.build_people_cache()
del db.people["p1"]                       # the person is removed
g.on_people_deleted(["p1"])               # person-delete arrives mid-build
drain(g)
check("a deletion mid-build stays deleted",
      "p1" not in g.people_labels)
check("...and is out of the searchable index",
      not any(h=="p1" for _l,h,_s in g.people_cache))

# the ordinary (post-build) recache still works, unaffected by the guard
g,db=make()
db.people["p1"]=Person("x", names=[Name("Old","Name")])
g.build_people_cache(); drain(g)
db.people["p1"]=Person("x", names=[Name("New","Name")])
g.on_people_changed(["p1"])
check("a rename after the build is applied: %r" % g.people_labels["p1"][0],
      "New" in g.people_labels["p1"][0])

print("\n[AC] a married surname is picked up when the person arrives mid-session")
# The married name lives on the family, not the person. A wife who already
# existed is handled by the full build; the gap was a person added (or renamed)
# after the build, whose surname she married into had not yet been learned.

def hits(g, typed):
    return [label for label, _h, _s in g._ranked_matches(typed)]

# (a) a new wife: her husband's surname becomes searchable without a rebuild
g,db=make()
db.people["hus"]=Person("x", names=[Name("Ernest","Reyman")])
g.build_people_cache(); drain(g)
db.families["f1"]=Family(father="hus", mother="wife")
db.people["wife"]=Person("x", names=[Name("Louisa","Heitt"), ],
                          families=["f1"])
db.people["hus"]=Person("x", names=[Name("Ernest","Reyman")],
                         families=["f1"])
g.on_people_changed(["wife"])
check("her label now carries the married surname: %r" % g.people_labels["wife"][0],
      "m. Reyman" in g.people_labels["wife"][0])
check("'Louisa Reyman' finds her: %r" % hits(g, "Louisa Reyman"),
      any("Heitt" in m for m in hits(g, "Louisa Reyman")))
check("'Louisa Heitt' still finds her",
      any("Heitt" in m for m in hits(g, "Louisa Heitt")))

# (b) a new husband: his existing wife becomes searchable by his surname
g,db=make()
db.people["wife"]=Person("x", names=[Name("Louisa","Heitt")])
g.build_people_cache(); drain(g)
check("she has no married surname yet", "m. " not in g.people_labels["wife"][0])
db.families["f1"]=Family(father="hus", mother="wife")
db.people["hus"]=Person("x", names=[Name("Ernest","Reyman")],
                         families=["f1"])
db.people["wife"]=Person("x", names=[Name("Louisa","Heitt")],
                          families=["f1"])
g.on_people_changed(["hus"])
check("her label now carries his surname: %r" % g.people_labels["wife"][0],
      "m. Reyman" in g.people_labels["wife"][0])
check("'Louisa Reyman' finds her: %r" % hits(g, "Louisa Reyman"),
      any("Heitt" in m for m in hits(g, "Louisa Reyman")))
check("he himself is NOT findable by her maiden name",
      not any("Heitt" in m for m in hits(g, "Ernest Heitt")))

# (c) renaming an existing husband replaces the entry the build already made
g,db=make()
db.people["hus"]=Person("x", names=[Name("Ernest","Reyman")],
                         families=["f1"])
db.people["wife"]=Person("x", names=[Name("Louisa","Heitt")],
                          families=["f1"])
db.families["f1"]=Family(father="hus", mother="wife")
g.build_people_cache(); drain(g)
check("the build itself gave her his surname: %r" % g.people_labels["wife"][0],
      "m. Reyman" in g.people_labels["wife"][0])
# _spouse_dependents walks the husband's families to find his wives, and
# _spouse_surnames_for then walks hers to find her husbands - the same
# family both times, so the two share one memo
fam_reads=[]
_origf=db.get_family_from_handle
db.get_family_from_handle=lambda h:(fam_reads.append(h), _origf(h))[1]
per_reads=[]
_origp=db.get_person_from_handle
db.get_person_from_handle=lambda h:(per_reads.append(h), _origp(h))[1]
g.on_people_changed(["hus"])
check("the family was read once, not twice: %r" % fam_reads,
      fam_reads==["f1"])
check("and each person once: %r" % sorted(per_reads),
      sorted(per_reads)==["hus","wife"])
db.get_family_from_handle=_origf; db.get_person_from_handle=_origp
db.people["hus"]=Person("x", names=[Name("Ernest","Newname")],
                         families=["f1"])
g.on_people_changed(["hus"])
check("the wife follows the rename: %r" % g.people_labels["wife"][0],
      "m. Newname" in g.people_labels["wife"][0])
check("...and the old surname is gone: %r" % g.people_labels["wife"][0],
      "Reyman" not in g.people_labels["wife"][0])

print("\n" + ("ALL PASSED" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
