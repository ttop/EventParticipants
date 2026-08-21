# Event Participants

A [Gramps](https://gramps-project.org) gramplet for attaching one event to many people at once, from the Events view.

Sharing a single event — a census, a christening, a Christmas dinner — with everyone who took part means opening each person's editor and adding/sharing the same event by hand, one at a time. This gramplet does the whole batch in one pass from the Event view.

It also inserts each new reference chronologically into the person's event list rather than appending it to the end, which is what Gramps does by default.

![The gramplet in the Events view bottombar](docs/event-participants.png)

Requires Gramps 6.0. GPL v2 (see [LICENSE](LICENSE)).

---

## Installing

### Through the Addon Manager

1. **Edit → Addon Manager...**
2. On the **Projects** tab, press **+** and fill in:

   | Project name | `Event Participants` |
   |---|---|
   | URL | `https://raw.githubusercontent.com/ttop/EventParticipants/main/gramps60` |

3. Back on the **Addons** tab, change the Filter from "Stable" to "Beta," find **Event Participants** and press **Install**.
4. Restart Gramps.

* Note: This PlugIn seems stable and unlikely to be problematic, but I'd like there to be reports of successful usage before I mark it as Stable.

### From source

Or copy `eventparticipants.py` and `eventparticipants.gpr.py` into a directory of their own under your Gramps user plugins folder:

| Platform | Plugins folder |
|---|---|
| macOS | `~/Library/Application Support/gramps/gramps60/plugins/` |
| Linux | `~/.local/share/gramps/gramps60/plugins/` |
| Windows | `%APPDATA%\gramps\gramps60\plugins\` |


```bash
mkdir -p ~/.local/share/gramps/gramps60/plugins/EventParticipants
cp eventparticipants.py eventparticipants.gpr.py \
   ~/.local/share/gramps/gramps60/plugins/EventParticipants/
```

### After Installation

Restart Gramps fully — it does not pick up new plugin code otherwise. Then go to the **Events** view and click the **▼** at the right-hand end of the bottombar tab strip → **Add a gramplet** → **Event Participants**.

## Using it

Select an event in the Events view. The header shows its type, date and description, and the list fills with everyone already taking part.

### Adding people

Type into the search box. Matching is word by word, in any order, against every form of the name — "john joy" finds `Joy, John Mervyn`, and so does "joy john". "Flo Joy" finds Florence Reyman who married John Joy. Results are ranked best-first, so the person you meant is at the top rather than wherever the alphabet put them.

Use the arrow keys to navigate to a result and Enter to select it, or click any row in the drop-down. 

The search matches more than the person's default name, it will also match on married names, nicknames, alternate surnames, etc.

It also narrows the list and removes people who probably could not have been alive at the time of the event, with a 2-year grace period for events after a person's death (for burial, probate, etc).

### Removing people

Select a row and press **Remove**. For someone you just staged this unstages them; for an existing participant it marks the row `detach`. It toggles — pressing **Remove** on a row already marked `detach` takes the mark off again. Nothing is written until you apply.

### Applying

**Apply** writes the changes.

**Revert** discards all pending changes and reloads from the database. A single **Edit → Undo** reverses an entire applied batch.
