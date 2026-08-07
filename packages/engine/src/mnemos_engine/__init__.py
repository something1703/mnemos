"""Mnemos engine — the Fabric.

Every memory read and write lives here. This package deliberately contains no
destructive operation: `make no-delete-in-engine` scans for destructive SQL and
fails the build if any appears. Destruction is the Warden's job alone
(packages/warden), which is invariant 1.

See PHASE_03_MEMORY_ENGINE.md.
"""

__version__ = "0.1.0"
