"""Differential ground truth: the planner and budget recommender, three versions side by side.

  pre19   — main before PR #19 (4bd8408, smartplate/ tree cd15aa2e)
  main    — main with PR #19 (33c6916, smartplate/ tree db0833e7)
  current — this working tree

Each version is extracted from git into its own importable package with its own
database and its own frozen clock, then fed the same random profiles. Every plan
difference must be explained by a documented, intentional change
(docs/testing/differential-pr19.md); anything else fails with the diff. The budget
recommender did not change in either step and must agree exactly.

CI checks out full history (fetch-depth: 0) so both baselines are available offline.
"""
import datetime as dt
import importlib
import io
import os
import pathlib
import subprocess
import sys
import tarfile
import tempfile

import pytest
from hypothesis import given, note, settings
from hypothesis import strategies as st

from gt_support import MONDAY_8AM, NAVRATRI_MONDAY
from test_gt_properties import profiles

REPO = pathlib.Path(__file__).resolve().parent.parent
BASELINES = {"pre19": "cd15aa2e4d395870abe3b1099869d1c7baf03cc0",     # git rev-parse 4bd8408:smartplate
             "main": "db0833e7fc9ca90bdfd782f7ae42c0492a82f637"}      # git rev-parse 33c6916:smartplate
EGG_LABELLED_VEG = {"Egg Puff + Chai"}       # seeded as veg=1 before this PR
MAX_ITEM_REPEAT, MAX_DELIVERY_CANDIDATES = 2, 6
INSTANTS = [MONDAY_8AM, dt.datetime(2026, 11, 4, 12, 0), NAVRATRI_MONDAY]


class Version:
    """One importable copy of the smartplate package, with its own DB and clock."""

    def __init__(self, name: str, package: str, root: pathlib.Path | None):
        if root is not None and str(root) not in sys.path:
            sys.path.insert(0, str(root))
        fd, self.db_path = tempfile.mkstemp(suffix=f"-{name}.db")
        os.close(fd)
        os.environ["SMARTPLATE_DB"] = self.db_path
        self.name, self.pkg = name, package
        self.mod = {m: importlib.import_module(f"{package}.{m}") for m in
                    ("config", "db", "seed", "clock", "everyday", "service", "ratelimit")}
        self.models = importlib.import_module(f"{package}.domain.models")
        self.recommender = importlib.import_module(f"{package}.kernel.recommender")
        self.at = MONDAY_8AM

    def activate(self, monkeypatch):
        cfg, clock = self.mod["config"], self.mod["clock"]
        monkeypatch.setattr(cfg, "DB_PATH", self.db_path)
        monkeypatch.setattr(cfg, "WEATHER_PROVIDER", "simulated")
        monkeypatch.setattr(cfg, "SWIGGY_PROVIDER", "simulated")
        monkeypatch.setattr(clock, "now", lambda: self.at)
        monkeypatch.setattr(clock, "today", lambda: self.at.date())
        self.mod["db"].init_db()
        self.mod["seed"].seed_all(optimize_starter=False)

    def plan(self, body: dict, at: dt.datetime) -> dict:
        self.at = at
        self.mod["ratelimit"].reset()
        view = self.mod["everyday"].create_profile(dict(body))
        user = self.models.get_user(view["user"]["id"])
        rec = self.recommender.recommend(user, [m for m in body["meals"]] * 7)
        cells = {}
        for day in view["grid"]:
            for meal, c in day["meals"].items():
                cells[f"{day['day']} {meal}"] = (c["kind"], c["item"], round(c["cost"], 2))
        return {"cells": cells, "spend": view["budget"]["spend"], "budget": view["budget"]["budget"],
                "recommendation": {k: (rec.get(k) or {}).get("total") if isinstance(rec.get(k), dict) else rec.get(k)
                                   for k in ("feasible", "floor", "usual", "variety", "suggested_rating_floor")}}


def _extract(tree: str, package: str) -> pathlib.Path:
    out = subprocess.run(["git", "archive", "--format=tar", tree], cwd=REPO, capture_output=True)
    if out.returncode != 0:
        pytest.fail(f"baseline tree {tree} for {package} is not in this clone ({out.stderr.decode()[:200]}). "
                    "Fetch full history: CI uses actions/checkout with fetch-depth: 0.")
    root = pathlib.Path(tempfile.mkdtemp(prefix=f"sp-{package}-"))
    with tarfile.open(fileobj=io.BytesIO(out.stdout)) as tar:
        tar.extractall(root / package, filter="data")
    return root


@pytest.fixture(scope="module")
def versions():
    saved = os.environ.get("SMARTPLATE_DB")
    out = {"pre19": Version("pre19", "smartplate_pre19", _extract(BASELINES["pre19"], "smartplate_pre19")),
           "main": Version("main", "smartplate_main", _extract(BASELINES["main"], "smartplate_main")),
           "current": Version("current", "smartplate", None)}
    if saved is not None:
        os.environ["SMARTPLATE_DB"] = saved
    yield out
    for v in out.values():
        if os.path.exists(v.db_path):
            os.unlink(v.db_path)


@pytest.fixture
def three(versions, monkeypatch):
    for v in versions.values():
        v.activate(monkeypatch)
    return versions


def _diff(a: dict, b: dict) -> dict:
    return {k: (a["cells"].get(k), b["cells"].get(k)) for k in sorted(set(a["cells"]) | set(b["cells"]))
            if a["cells"].get(k) != b["cells"].get(k)}


def _pr19_reasons(body: dict) -> list[str]:
    """PR #19 (planner side): home-cooking recipes obey allergen and medical rules, not
    just diet (egg curry has egg + gluten, masala oats gluten)."""
    out = []
    if {"egg", "gluten"} & set(body["allergens"]) or "celiac" in body["medical"]:
        out.append("PR19-D1 cook recipes obey allergies/celiac")
    return out


def _this_pr_reasons(body: dict, n_open: int) -> list[str]:
    out = []
    if body["diet"] in ("veg", "vegan"):
        out.append("GT-1 egg dishes are non-veg (Egg Puff relabelled)")
    if n_open > 2 * MAX_DELIVERY_CANDIDATES - 2:
        out.append("GT-2 enough distinct dishes offered to fill the week")
    if body["favourites"]:
        out.append("GT-3 variety cap never empties a meal the usual places can't cover")
    return out


@settings(max_examples=20)
@given(body=profiles(), at=st.sampled_from(INSTANTS))
def test_planner_differences_are_all_intentional(three, body, at):
    pre19, main, current = (three[k].plan(body, at) for k in ("pre19", "main", "current"))
    n_open = sum(1 for k, c in current["cells"].items() if c[0] not in ("past",))
    d19, dgt = _diff(pre19, main), _diff(main, current)
    note(f"pre19→main {d19}\nmain→current {dgt}")

    # the recommender is unchanged in both steps (its menu-safety call is shared code)
    assert pre19["recommendation"] == main["recommendation"], "recommender changed in PR #19"
    if body["diet"] == "nonveg":
        assert main["recommendation"] == current["recommendation"], "recommender changed in this PR"

    if d19:
        assert _pr19_reasons(body), f"unexplained PR #19 planner difference: {d19}"
    if dgt:
        assert _this_pr_reasons(body, n_open), f"unexplained planner difference in this PR: {dgt}"
    # direction of the intentional changes
    for k, (old, new) in dgt.items():
        if new and new[1] in EGG_LABELLED_VEG:
            assert body["diet"] == "nonveg", (k, new)
    assert all(c[1] not in EGG_LABELLED_VEG for c in current["cells"].values()) or body["diet"] == "nonveg"
    for k, (old, new) in d19.items():
        if new and new[0] == "cook" and old and old[0] == "cook":
            assert new[1] != old[1]                       # a different (safe) recipe, never the unsafe one
    if body["diet"] != "veg" and body["diet"] != "vegan" and not body["favourites"] and n_open <= 10:
        assert not dgt, f"this PR changed a plan it should not touch: {dgt}"
    assert current["spend"] <= current["budget"] + 0.011


def test_this_pr_fills_weeks_main_left_empty(three):
    """The headline intentional difference, pinned: a 21-meal vegetarian week with
    money to spare. main skipped 9 meals (6 dishes × 2 repeats = 12); now every meal
    is planned, still within budget, and no egg dish reaches a vegetarian."""
    body = {"name": "Diff", "diet": "veg", "allergens": ["peanut"], "weekly_budget": 3700,
            "meals": ["breakfast", "lunch", "dinner"], "cook": "never", "favourites": []}
    main, current = three["main"].plan(body, MONDAY_8AM), three["current"].plan(body, MONDAY_8AM)
    kinds = lambda p: [c[0] for c in p["cells"].values()]          # noqa: E731
    assert kinds(main).count("skip") == 9 and main["spend"] < 0.4 * main["budget"]
    assert kinds(current).count("skip") == 0 and current["spend"] <= current["budget"]
    assert not any(c[1] in EGG_LABELLED_VEG for c in current["cells"].values())
    # main offered the mislabelled Egg Puff to vegetarians; now it is non-veg
    veg = {"diet": "veg", "allergens": [], "medical": []}
    for name, expect_safe in (("main", True), ("current", False)):
        v = three[name]
        egg_puff = next(it for it in v.models.menu_for_city("Chennai") if it["name"] in EGG_LABELLED_VEG)
        violates = importlib.import_module(f"{v.pkg}.domain.allergens").violates(veg, egg_puff)
        assert (violates is None) is expect_safe, (name, violates)
