#!/usr/bin/env python3
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
"""Build this addon into a Gramps "project" so the Addon Manager can install it.

A project is any URL serving two things, which is all the Addon Manager
knows how to ask for (`gen/plug/utils.py:229` and `gui/plug/_windows.py:325`):

    <url>/listings/addons-<lang>.json    the metadata, one entry per addon
    <url>/download/<z>                   the archive each entry names in "z"

Run `python3 build_addon.py` to regenerate both under `gramps60/`, then commit
them: it is the committed files that raw.githubusercontent.com serves.

The metadata is read out of the .gpr.py rather than repeated here, by running
it with a stand-in `register()` — the same trick Gramps itself uses to inspect
an addon before installing it (`FakeRegistrar` in `gen/plug/utils.py`). That
way the version, status and description can never drift from the registration.
"""

import gzip
import io
import json
import os
import tarfile

# Mirrors gramps/gen/plug/_pluginreg.py. The .gpr.py is evaluated with these
# in scope, so whatever it passes to register() arrives already numeric and
# the listing cannot disagree with it.
PTYPE = {
    "REPORT": 0, "QUICKREPORT": 1, "TOOL": 2, "IMPORT": 3, "EXPORT": 4,
    "DOCGEN": 5, "GENERAL": 6, "MAPSERVICE": 7, "VIEW": 8, "RELCALC": 9,
    "GRAMPLET": 10, "SIDEBAR": 11, "DATABASE": 12, "RULE": 13, "THUMBNAILER": 14,
}
STATUS = {"UNSTABLE": 0, "EXPERIMENTAL": 1, "BETA": 2, "STABLE": 3}
AUDIENCE = {"EVERYONE": 0, "EXPERT": 1, "DEVELOPER": 2}

GPR = "eventparticipants.gpr.py"
PLUGIN_DIR = "EventParticipants"          # the directory name inside plugins/
ARCHIVE = "EventParticipants.addon.tgz"   # the listing's "z"
FILES = ["eventparticipants.py", "eventparticipants.gpr.py"]

# Gramps 6.0 looks under a version-named directory, matching the layout of the
# official addons repo. VERSION_DIR_NAME is "gramps%s%s" % major, minor.
OUT_DIR = "gramps60"

# A fixed timestamp keeps rebuilds byte-identical, so an unchanged addon does
# not show up as a changed binary in every diff. 1980-01-01 rather than 0,
# which renders as 1969 west of UTC and reads like a bug in `tar tzvf`.
EPOCH = 315532800


def read_registration(path):
    """Evaluate a .gpr.py with a stand-in register() and return what it got."""
    captured = []

    def register(ptype, **kwargs):
        kwargs["ptype"] = ptype
        captured.append(kwargs)

    env = dict(PTYPE)
    env.update(STATUS)
    env.update(AUDIENCE)
    env["register"] = register
    env["_"] = lambda text: text
    env["MODULE_VERSION"] = "6.0"

    with open(path, encoding="utf-8") as handle:
        exec(compile(handle.read(), path, "exec"), env)  # noqa: S102

    if not captured:
        raise SystemExit("%s registered nothing" % path)
    if len(captured) > 1:
        raise SystemExit("%s registers %d plugins; expected 1" % (path, len(captured)))
    return captured[0]


def build_archive(dest):
    """Write the .tgz. Members are paths relative to the user plugins dir,
    because Gramps installs one with extractall(USER_PLUGINS)."""
    raw = io.BytesIO()
    # mtime=0 on the gzip header as well, or the wrapper changes every run
    # even when the tar inside it does not.
    with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=EPOCH) as gz:
        with tarfile.open(fileobj=gz, mode="w") as tar:
            for name in FILES:
                info = tar.gettarinfo(name, arcname="%s/%s" % (PLUGIN_DIR, name))
                info.mtime = EPOCH
                info.uid = info.gid = 0
                info.uname = info.gname = "root"
                with open(name, "rb") as handle:
                    tar.addfile(info, handle)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as handle:
        handle.write(raw.getvalue())
    return len(raw.getvalue())


def build_listing(reg, dest):
    """Write listings/addons-en.json — the keys are the short ones the Addon
    Manager reads in gui/plug/_windows.py:226-271."""
    entry = {
        "i": reg["id"],                          # id, matched against installed plugins
        "n": reg["name"],                        # displayed name
        "d": reg.get("description", ""),         # displayed description
        "v": reg["version"],                     # compared for update checks
        "t": reg["ptype"],                       # plugin type, for the Type lozenge
        "a": reg.get("audience", AUDIENCE["EVERYONE"]),
        "s": reg.get("status", STATUS["STABLE"]),
        "h": reg.get("help_url", "") or "",      # wiki button, hidden when empty
        "z": ARCHIVE,                            # fetched from <url>/download/<z>
    }
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8") as handle:
        json.dump([entry], handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    return entry


def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    reg = read_registration(GPR)

    if reg.get("gramps_target_version") != "6.0":
        # Gramps refuses an addon whose target is not exactly the running
        # major.minor, so a mismatch here means OUT_DIR is wrong too.
        raise SystemExit(
            "gramps_target_version is %r but this builds into %s/"
            % (reg.get("gramps_target_version"), OUT_DIR)
        )

    size = build_archive(os.path.join(OUT_DIR, "download", ARCHIVE))
    entry = build_listing(reg, os.path.join(OUT_DIR, "listings", "addons-en.json"))

    print("%s/download/%s  (%d bytes)" % (OUT_DIR, ARCHIVE, size))
    print("%s/listings/addons-en.json" % OUT_DIR)
    print("  %s %s  type=%d status=%d audience=%d"
          % (entry["i"], entry["v"], entry["t"], entry["s"], entry["a"]))


if __name__ == "__main__":
    main()
