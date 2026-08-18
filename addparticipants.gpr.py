#
# Add Participants - a Gramps gramplet for attaching one event to many people
#
# This file registers the addon with Gramps.
#

register(
    GRAMPLET,
    id="Add Participants",
    name=_("Add Participants"),
    description=_(
        "Attach the selected event to multiple people at once, "
        "with type-ahead search and chronological insertion."
    ),
    status=STABLE,
    fname="addparticipants.py",
    height=300,
    detached_width=600,
    detached_height=450,
    expand=True,
    gramplet="AddParticipants",
    gramplet_title=_("Add Participants"),
    version="1.0.0",
    gramps_target_version="6.0",
    navtypes=["Event"],
    authors=["Todd"],
    authors_email=[""],
)
