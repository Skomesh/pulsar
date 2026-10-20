# Pulsar -- RPM spec for Fedora and derivatives.
#
# NOT BUILT AND NOT VERIFIED BY rpmbuild. rpmbuild/rpm are not installed on the
# machine this spec was written on, so no `rpmbuild -ba` was run and no binary
# RPM exists. The %%prep/%%build/%%install shell bodies were extracted and checked
# with `bash -n` only, which catches syntax errors but proves nothing about
# whether the files land where %%files expects them. Build it before trusting it.
#
# WHY pyproject-rpm-macros AND NOT `pip install` IN %%install:
#
# %%pyproject_wheel + %%pyproject_install (from pyproject-rpm-macros, a
# dependency of python3-devel in Fedora) are the distribution-standard route
# for a PEP 517 package. They install the wheel into %{buildroot}, then
# %%py3_shebang_fix rewrites the console script's shebang from the build
# interpreter to /usr/bin/python3 -- which is exactly what is needed here,
# because Pulsar's /usr/bin/pulsar-gui comes from the [project.scripts] entry
# point and would otherwise be unusable after install. It also cleans the
# stray __pycache__ pip leaves in bindir.
#
# Installing to the system site-packages is acceptable for the same reason it
# is on Debian: the wheel ships exactly one top-level package, pulsar. Earlier
# versions also installed utils, ui and config as top-level modules, which
# would collide with unrelated packages in site-packages.
#
# Version is read from pyproject.toml by %%pyproject_buildrequires? No --
# deliberately not. BuildRequires: is written out by hand below so that the
# built RPM's dependency metadata is reviewable in this file rather than
# generated at build time. %%prep asserts pyproject.toml's version matches
# %%version, so a bump in one place and not the other fails the build instead
# of producing an RPM whose filename and metadata disagree.

Name:           pulsar
Version:        0.1.0
Release:        1%{?dist}
Summary:        Graphical audio routing manager for PulseAudio and PipeWire

License:        MIT
URL:            https://github.com/Skomesh/pulsar
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch

# python3-devel pulls in pyproject-rpm-macros and the build backend.
# python3-installer / python3-setuptools / python3-wheel are named explicitly
# because pyproject.toml's [build-system].requires asks for them and the
# wheel build runs with --no-isolation: nothing is fetched at build time, so
# anything the backend imports must already be installed here.
BuildRequires:  python3-devel >= 3.8
BuildRequires:  python3-setuptools
BuildRequires:  python3-wheel
BuildRequires:  python3-installer

# Requires, not Suggests:
#   python3-tkinter  -- tkinter is imported at module scope by both
#                       pulsar.__main__ and pulsar.ui.main_window, so without
#                       it the app cannot start at all.
#   pulseaudio-utils -- provides pactl, used for all stream/sink control.
#   pipewire-utils   -- provides pw-dump, pw-top and pw-record, used for
#                       server introspection, diagnostics and the exclusive
#                       app-audio routing path.
# Note the Fedora names differ from Debian's: pw-dump/pw-top/pw-record live in
# pipewire-utils here, and in the pipewire package on Debian/Ubuntu.
Requires:       python3 >= 3.8
Requires:       python3-tkinter
Requires:       pulseaudio-utils
Requires:       pipewire-utils

# gdbus is used to enumerate PipeWire clients when building the app picker.
# The code treats it as optional (shutil.which("gdbus") is None is handled),
# and the feature degrades rather than failing, so Recommends is right:
# on Fedora it comes from glib2.
Recommends:     glib2

%description
Pulsar is a Tkinter desktop application for routing audio between
applications, sinks and sources on a PulseAudio or PipeWire server. It can
list and inspect playback and capture streams, move an application to a
different output exclusively (so its audio leaves the previous device rather
than being duplicated there), create and connect sinks, and save routing setups
as named presets and profiles.

Pulsar shells out to the audio server's own command line tools rather than
linking against a client library.

Saved presets and profiles are written to $XDG_DATA_HOME/pulsar/presets
(~/.local/share/pulsar/presets by default), never under /usr. No writable
directory is created in the package's install prefix, so this package remains
fully relocatable and does not need a %post script.

%prep
%autosetup -p %{name}-%{version}

# Fail loudly if pyproject.toml and the spec header disagree. Version is
# maintained in pyproject.toml; this keeps the two in step the same way
# debian/rules does for the Debian package.
#
# This check MUST come after the source-unpacking line above. The tarball is
# not unpacked until then, so running this first reads a pyproject.toml that
# does not exist and fails the build with a misleading "version does not
# match" -- on every platform, including Fedora.
#
# One more RPM trap, learned the hard way while writing this very comment:
# RPM expands macros everywhere in a section, including inside comment text.
# Naming a macro in prose expands it to its shell script, and the remainder
# of that comment line then executes as shell. Refer to macros by description
# ("the source-unpacking line"), never by their literal name in a comment.
if [ "$(sed -n 's/^version[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' pyproject.toml | head -1)" != "%{version}" ]; then
    echo "ERROR: version in pyproject.toml does not match %%version (%{version})" >&2
    exit 1
fi

%build
# Pure Python: no compilation, no extension modules. pyproject.toml's
# [tool.setuptools.packages.find] is scoped to src/ with include = ["pulsar*"],
# so automatic discovery cannot pick up src/utils, src/ui or src/config as
# top-level modules. That constraint is load-bearing for a distro package.
%pyproject_wheel

%install
%pyproject_install
# Consume the RECORD-generated file list into %%{pyproject_files} so the
# %%files section can read it with `-f`.
#
# The argument is a MODULE NAME GLOB -- "requests*", "kerberos" -- matched
# against the importable module names in the RECORD, NOT a filesystem path.
# The dist-info directory is derived from the modules it finds. Two traps,
# both hit here first time round:
#
# 1. Passing a path instead. A path carries dots, and parse_varargs rejects
#    any glob containing one because it assumes a namespaced package:
#        ValueError: Attempted to use a namespaced package with . in the
#        glob: .../pulsar-0.1.0.dist-info. That is not (yet) supported.
#
# 2. Passing a flag. The flag set is +auto/-l/-L/-M/-D and every one of them
#    is boolean except -D, so a path after any of them is silently treated
#    as a positional glob -- which then trips (1). With no glob left over,
#    you get:
#        ValueError: At least one module glob needs to be provided to
#        %%pyproject_save_files.
#
# Bare "pulsar", not "pulsar*". A glob here is expanded by the shell that
# runs the expanded macro body, in the %%install working directory -- the
# unpacked source tree, which has src/pulsar/ but no pulsar* entry. No
# match means the shell silently drops the word, and the macro is called
# with no argument at all, which reports as:
#
#     ValueError: At least one module glob needs to be provided to
#     %%pyproject_save_files.
#
# Reading as if the macro ignored its argument. The macro does not: the
# shell ate it. A bare name has no metacharacters, so it survives intact.
%pyproject_save_files pulsar

# Nothing writable under /usr/share/pulsar: user data resolves to
# $XDG_DATA_HOME/pulsar/presets, so a presets directory in the install prefix
# would be dead weight the application never reads. The built-in profiles ship
# inside the module as package data (pulsar/utils/presets/builtin_profiles.json)
# and are read-only.

# Desktop entry. The source pulsar.desktop carries
# Exec=PLACEHOLDER_EXEC_PATH, which install.sh substitutes for a git
# checkout. An RPM must ship a launcher that already works, so the placeholder
# is replaced here with the path the console script is installed to. %%py3_shebang_fix
# (inside %%pyproject_install) has by this point rewritten the script's shebang,
# so /usr/bin/pulsar-gui is a working interpreter invocation.
install -D -m 0644 pulsar.desktop %{buildroot}%{_datadir}/applications/pulsar.desktop
sed -i -e 's|PLACEHOLDER_EXEC_PATH|/usr/bin/pulsar-gui|g' \
    %{buildroot}%{_datadir}/applications/pulsar.desktop

# Icons into the hicolor theme layout, each named pulsar.png / pulsar.svg as
# the theme lookup requires. install(1) because the source files are named
# pulsar-<size>.png and RPM cannot rename either.
for size in 16 24 32 48 64 128 256 512; do
    install -D -m 0644 assets/icons/pulsar-$size.png \
        %{buildroot}%{_datadir}/icons/hicolor/$size"x"$size/apps/pulsar.png
done
install -D -m 0644 assets/icons/pulsar.svg \
    %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/pulsar.svg

%check
# desktop-file-validate catches the class of error that matters here: a broken
# Exec= line, which is what shipping the un-substituted placeholder would be.
desktop-file-validate %{buildroot}%{_datadir}/applications/pulsar.desktop
# Import smoke test: proves the module and its package data resolve from the
# installed layout rather than from the source tree. No display is required
# because nothing instantiates Tk here.
PYTHONPATH=%{buildroot}%{python3_sitelib} \
    %{__python3} -c "import pulsar, pulsar.utils.paths, pulsar.utils.profile_manager"

# -f %{pyproject_files} reads the RECORD-generated file list. A bare macro
# name here is a PATH, not a list, so RPM looks for that absolute path
# inside the buildroot and reports it missing:
#
#     error: File not found: .../BUILDROOT/...-pyproject-files
#
%files -f %{pyproject_files}
%license LICENSE NOTICE
%doc README.md CHANGELOG.md
# Both console scripts. `pulsar` is the command-line interface and
# `pulsar-gui` the windowed app; the desktop entry execs the latter.
%{_bindir}/pulsar
%{_bindir}/pulsar-gui
%{_datadir}/applications/pulsar.desktop
%{_datadir}/icons/hicolor/16x16/apps/pulsar.png
%{_datadir}/icons/hicolor/24x24/apps/pulsar.png
%{_datadir}/icons/hicolor/32x32/apps/pulsar.png
%{_datadir}/icons/hicolor/48x48/apps/pulsar.png
%{_datadir}/icons/hicolor/64x64/apps/pulsar.png
%{_datadir}/icons/hicolor/128x128/apps/pulsar.png
%{_datadir}/icons/hicolor/256x256/apps/pulsar.png
%{_datadir}/icons/hicolor/512x512/apps/pulsar.png
%{_datadir}/icons/hicolor/scalable/apps/pulsar.svg

%changelog
* Fri Oct 16 2026 Skomesh <skomesh@users.noreply.github.com> - 0.1.0-1
- Initial RPM packaging of Pulsar 0.1.0.
- Built with %%pyproject_wheel/%%pyproject_install; installs the pulsar module
  into %%{python3_sitelib} and the /usr/bin/pulsar-gui launcher into bindir.
- Desktop entry ships with Exec already set to /usr/bin/pulsar-gui rather than the
  PLACEHOLDER_EXEC_PATH the source template carries.
- Requires python3-tkinter, pulseaudio-utils and pipewire-utils.