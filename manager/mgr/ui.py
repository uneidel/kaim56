# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Manager web UI, assembled from its parts.

Was a single 2.100-line constant — CSS, markup, the architecture SVG and the
whole client script in one string nobody could navigate. The parts live in
ui_css / ui_html (ARCH_SVG separate, because the update cycle edits it) /
ui_js; this module keeps the public contract: ``PAGE``, consumed by
manager.py's render(). Pure data — logic stays in manager.py."""
from mgr.ui_css import CSS
from mgr.ui_html import HTML_TOP, ARCH_SVG, HTML_BOTTOM
from mgr.ui_js import JS

PAGE = CSS + HTML_TOP + ARCH_SVG + HTML_BOTTOM + JS
