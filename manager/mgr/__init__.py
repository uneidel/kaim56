# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
# The kAIm56 manager as a package. manager.py stays the entry point (systemd)
# and facade: it imports from here and re-exports. Dependency direction:
# mgr modules NEVER import from manager (no cycles); what they need from there
# is injected at startup (see manager.py).
