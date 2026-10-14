# Security Policy

## Supported versions

Pulsar is in early development. Only the latest commit on `main` receives
security fixes.

| Version | Supported |
| ------- | --------- |
| `main`  | Yes       |
| Anything older | No  |

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Report it privately via GitHub's private advisory form:

> https://github.com/Skomesh/pulsar/security/advisories/new

If that route is unavailable to you, open an issue titled `security: private
report requested` containing no technical detail, and say in the body that you
want a private channel. That flags the report without disclosing the issue.

Please include, where you can:

- What the issue is and which file or code path is involved
- Steps to reproduce
- The impact — what an attacker or a malformed input can achieve
- Your PulseAudio/PipeWire version and distribution

## What to expect

| Stage | Target |
| ----- | ------ |
| Acknowledgement | Within 72 hours |
| Triage (severity assessment) | Within 7 days |
| Fix or mitigation plan | Depends on severity |

Severity is assessed on impact, not on how the issue was found.

## Scope

In scope: Pulsar's own code — routing logic, preset handling, install and
uninstall scripts, and the Tkinter UI.

Out of scope: issues in PipeWire, PulseAudio, ALSA, BlueZ, or upstream
[pactl-gui](https://github.com/Skrappjaw/pactl-gui) — report those to their
projects.

## A note on privilege

Pulsar runs with the permissions of your user and drives system audio
subsystems. A user-local attacker who can already write to your preset or
config directory generally does not need a Pulsar vulnerability to get
there. Reports that depend on the reporter already having write access to
your home directory will usually be closed as not-a-vulnerability — but
please still report them; that judgement is mine to make, not yours.