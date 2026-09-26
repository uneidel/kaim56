# kAIm56 — Code flow app: tests for validate_flow.py (the agent's checker).
# SPDX-License-Identifier: AGPL-3.0-or-later
# Run: python3 -m unittest discover apps/codeflow/tests   (writes py-results.json for the JS parity test)
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import validate_flow as vf  # noqa: E402

FX = json.load(open(os.path.join(HERE, "fixtures.json")))


def apply(g, ops):
    for op, path, *val in ops:
        keys = path.split(".")
        obj = g
        for k in keys[:-1]:
            obj = obj[int(k)] if isinstance(obj, list) else obj[k]
        last = keys[-1]
        tgt = obj[int(last)] if isinstance(obj, list) and last.isdigit() else (obj.get(last) if isinstance(obj, dict) else None)
        if op == "set":
            if isinstance(obj, list):
                obj[int(last)] = val[0]
            else:
                obj[last] = val[0]
        elif op == "del":
            del obj[last]
        elif op == "append":
            tgt.append(val[0])
        elif op == "pop":
            tgt.pop()
    return g


def build(case):
    """A workspace with src/ and flow/ for one case."""
    ws = tempfile.mkdtemp(prefix="codeflow-")
    for f, body in FX["files"].items():
        p = os.path.join(ws, "src", f)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").write(body)
    os.makedirs(os.path.join(ws, "flow"))
    graphs = {"overview": copy.deepcopy(FX["overview"]), **copy.deepcopy(FX["details"])}
    tgt = case["target"]
    for name, g in graphs.items():
        if name == tgt and case.get("absent"):
            continue
        path = os.path.join(ws, "flow", f"{name}.json")
        if name == tgt and "raw" in case:
            open(path, "w").write(case["raw"])
        else:
            json.dump(apply(g, case.get("ops", [])) if name == tgt else g, open(path, "w"))
    return ws


class ValidateFlow(unittest.TestCase):
    def test_cases_and_write_results_for_js(self):
        results = {}
        for case in FX["cases"]:
            res = vf.validate_tree(build(case))
            results[case["name"]] = res["errors"]
            if case["expect"]:
                self.assertFalse(res["ok"], case["name"])
                self.assertIn(case["expect"], " | ".join(res["errors"]), case["name"])
            else:
                self.assertEqual((res["ok"], res["graphs"], res["nodes"]), (True, 2, 5), res["errors"])
        json.dump(results, open(os.path.join(HERE, "py-results.json"), "w"), indent=1)

    def test_cli_and_helpers(self):
        ws = build(FX["cases"][0])
        ok = subprocess.run([sys.executable, vf.__file__, ws], capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0, ok.stdout); self.assertIn("OK: 2 graph(s)", ok.stdout)
        self.assertEqual(vf.find(ws, "save_order")[:2], [(0, "db.py", 1, "def save_order(order):"), (1, "app/api.py", 9, "save_order(order)")])
        self.assertEqual(vf.show(ws, "app/api.py", 3, 4), [(3, "def create_order(req):"), (4, "line 4")])
        self.assertIn("app/api.py  (40)", vf.repo_map(ws))
        for cmd, want in ((["find", "create_order"], "app/api.py:3: def create_order(req):"), (["map"], "db.py  (10)"),
                          (["show", "db.py", "1", "1"], "    1  def save_order(order):")):
            out = subprocess.run([sys.executable, vf.__file__, *cmd], cwd=ws, capture_output=True, text=True).stdout
            self.assertIn(want, out, cmd)
        bad = build(FX["cases"][-2])
        r = subprocess.run([sys.executable, vf.__file__, bad], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1); self.assertIn("not valid JSON", r.stdout)


if __name__ == "__main__":
    unittest.main()
