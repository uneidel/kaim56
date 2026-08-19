# Beispiel-Tool-Plugin: ein W6-Wurf. Konvention: DESC/PARAMS/REQUIRED + run().
DESC = "Einen Wuerfel werfen (1-6); optional anzahl (Standard 1)."
PARAMS = {"anzahl": {"type": "integer", "description": "wie viele Wuerfel"}}
REQUIRED = []

def run(anzahl=1):
    import random
    n = max(1, min(10, int(anzahl or 1)))
    wurf = [random.randint(1, 6) for _ in range(n)]
    return f"Wurf: {wurf} (Summe {sum(wurf)})"
