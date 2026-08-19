# kAIm56-Manager als Paket. manager.py bleibt Einstiegspunkt (systemd) und
# Fassade: er importiert von hier und re-exportiert. Abhaengigkeitsrichtung:
# mgr-Module importieren NIE aus manager (keine Zyklen); was sie von dort
# brauchen, wird ihnen beim Start injiziert (siehe manager.py).
