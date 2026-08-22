# Example tool plugin: a d6 roll. Convention: DESC/PARAMS/REQUIRED + run().
DESC = "Roll a die (1-6); optional count (default 1)."
PARAMS = {"count": {"type": "integer", "description": "how many dice"}}
REQUIRED = []

def run(count=1):
    import random
    n = max(1, min(10, int(count or 1)))
    roll = [random.randint(1, 6) for _ in range(n)]
    return f"Roll: {roll} (sum {sum(roll)})"
