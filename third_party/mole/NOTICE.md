# Mole source attribution

Source: https://github.com/tw93/Mole

Pinned revision: `7b2a196061e1be7fae4ba422cd9102313fcb321d` (retrieved 2026-10-20).

Copyright belongs to tw93 and the Mole contributors. Licensed under GNU GPL
version 3; the complete license is included in `LICENSE`. Original source
comments and attribution are retained.

This directory includes Mole's cleanup, project purge, installer detection and
shared libraries. It is source reuse, not an external Mole installation.
The mactools distribution is licensed under GPL-3.0, including the adapter.
Corresponding source is supplied both here and inside `bin/mactools_mole.zip`.

Changes made by the mactools contributors on 2026-10-20:

- Isolated Mole state into `.config/mactools/mole`, `.cache/mactools/mole`
  and `Library/Logs/mactools/mole` to avoid mixing another installation's state.
- Installer entry point runs only when invoked directly, allowing safe sourcing.
- Added `mactools-bridge.sh`: selected cleanup functions run behind ma's fzf
  interface and confirmation. Administrator access is disabled. Trash, recent
  document lists, mail downloads and signed application version removal are
  disabled. Additional protected paths preserve saved state and mactools itself.
- Preview records path identities and exact owner commands in a private ledger;
  execution refuses new or replaced targets and retains upstream deletion guards.
- Limited deletion to home-owned paths. System maintenance, snapshots, application
  removal, OS staging and the unrelated optimize/status commands are not exposed.

Rebuild the reproducible bundled source with `python3 scripts/build_mole_bundle.py`.
The bundle's checksum is embedded in `bin/mactools_mole.py` and checked by both
the standalone installer and the runtime. No download happens during cleanup.
