#
# Add Participants - a Gramps gramplet for attaching one event to many people
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
    # BETA, not UNSTABLE: PluginRegister drops UNSTABLE plugins
    # outright when stable_only is set (_pluginreg.py:1481), which
    # would hide the addon rather than just labelling it.
    status=BETA,
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
    authors=["Todd Wells"],
    authors_email=["todd@wellshub.com"],
)
