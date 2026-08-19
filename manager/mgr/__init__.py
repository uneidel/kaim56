# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 Ulrich Neidel
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
# kAIm56-Manager als Paket. manager.py bleibt Einstiegspunkt (systemd) und
# Fassade: er importiert von hier und re-exportiert. Abhaengigkeitsrichtung:
# mgr-Module importieren NIE aus manager (keine Zyklen); was sie von dort
# brauchen, wird ihnen beim Start injiziert (siehe manager.py).
