# Mole source attribution

Source: https://github.com/tw93/Mole

Pinned revision: `7b2a196061e1be7fae4ba422cd9102313fcb321d` (retrieved 2026-10-20).

Copyright belongs to tw93 and the Mole contributors. Licensed under GNU GPL
version 3; the complete license is included in `LICENSE`. Original source
comments and attribution are retained.

This directory includes Mole's cleanup, project purge, installer detection, application uninstall, optimization tasks and
shared libraries. It is source reuse, not an external Mole installation.
The mactools distribution is licensed under GPL-3.0, including the adapter.
Corresponding source is supplied both here and inside `bin/mactools_mole.zip`.

Changes made by the mactools contributors on 2026-10-20:

- Isolated Mole state into `.config/mactools/mole`, `.cache/mactools/mole`
  and `Library/Logs/mactools/mole` to avoid mixing another installation's state.
- Installer entry point runs only when invoked directly, allowing safe sourcing.
- Added `mactools-bridge.sh`: selected cleanup functions run behind ma's fzf
  interface and confirmation. Shared cache categories disable unrelated data
  cleanup; dedicated categories expose Trash, recent items, Mail downloads,
  saved state, old app versions and guarded system cleanup explicitly.
- Preview records path identities and exact owner commands in a private ledger;
  execution refuses new or replaced targets and retains upstream deletion guards.
  Administrator access is requested only during confirmed execution when needed.
- System snapshots, OS update staging and large files remain review-only.
  Selected periodic maintenance and opt-in filesystem verification reuse upstream
  optimize tasks. Nix and simulator deletion is limited to reviewed instances.
- Added `mactools-apps.sh` and an import-safe uninstall entry point for ma remove.
  App removal previews exact bundles and leftovers, preserves personal data unless
  explicitly selected, and validates owning Homebrew executable and full cask
  identity instead of treating same-name apps as interchangeable installations.
- Added `mactools-optimize.sh` for ma optimize / maco. Only explicitly selected
  catalog tasks execute after preview and confirmation; native task conditions,
  VPN checks, running-application guards and disk-verification opt-in remain.
  Administrative commands are restricted to the selected task and acquire
  credentials only when an approved operation actually needs them.

Rebuild the reproducible bundled source with `python3 scripts/build_mole_bundle.py`.
The bundle's checksum is embedded in `bin/mactools_mole.py` and checked by both
the standalone installer and the runtime. No download happens during cleanup.
