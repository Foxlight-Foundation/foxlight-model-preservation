"""Quarterly starter signals: scoring and hysteresis, with fixtures only."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pytest


def _script(name: str):
    path = Path(__file__).parents[1] / "scripts" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


signals = _script("derive_starter_signals")
TODAY = date(2026, 11, 7)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the signals tests must not use the network")

    monkeypatch.setattr("urllib.request.urlopen", refuse)


def test_sustained_ratio_compares_this_month_with_the_lifetime_average():
    # 12M all-time over about 12 months is 1M a month; 1M now is sustained.
    ratio = signals.sustained_ratio(1_000_000, 12_000_000, date(2025, 11, 7), TODAY)
    assert ratio == pytest.approx(1.0, abs=0.01)
    declining = signals.sustained_ratio(300_000, 12_000_000, date(2025, 11, 7), TODAY)
    assert declining is not None and declining < 0.4
    assert signals.sustained_ratio(5, 0, date(2026, 1, 1), TODAY) is None


def test_ollama_rate_is_monthly_and_ignores_new_or_reset_tags():
    rates = signals.ollama_rate(
        {"qwen3.8": 6_000_000, "new": 10, "reset": 5},
        {"qwen3.8": 3_200_000, "reset": 50},
        days=34,
    )
    assert rates == {"qwen3.8": pytest.approx(2_800_000 * 30.44 / 34, rel=1e-3)}
    assert signals.ollama_rate({"a": 1}, {"a": 0}, days=0) == {}


MODELS = {
    # Listed and strong.
    "org/listed-strong": {
        "downloads": 9_000_000,
        "downloadsAllTime": 50_000_000,
        "likes": 900,
        "createdAt": "2026-03-01T00:00:00Z",
    },
    # Listed and weak (declining, few downloads).
    "org/listed-weak": {
        "downloads": 10_000,
        "downloadsAllTime": 9_000_000,
        "likes": 5,
        "createdAt": "2024-01-01T00:00:00Z",
    },
    # A proposal: strong, old enough and sustained.
    "org/rising": {
        "downloads": 8_000_000,
        "downloadsAllTime": 40_000_000,
        "likes": 700,
        "createdAt": "2026-04-01T00:00:00Z",
    },
    # A proposal: strong but younger than three months.
    "org/young": {
        "downloads": 7_000_000,
        "downloadsAllTime": 9_000_000,
        "likes": 100,
        "createdAt": "2026-09-20T00:00:00Z",
    },
    # A proposal: strong on one signal only.
    "org/one-signal": {
        "downloads": 6_000_000,
        "downloadsAllTime": 300_000_000,
        "likes": 1,
        "createdAt": "2023-01-01T00:00:00Z",
    },
}
CONVERSIONS = {
    "listed-strong": 20_000_000,
    "listed-weak": 1_000,
    "rising": 15_000_000,
    "young": 12_000_000,
    "one-signal": 0,
}
PLAN = {
    "title": "fixture",
    "entries": [
        {
            "entry_id": "listed-strong",
            "tier": "small",
            "licence": {"base_model": "org/listed-strong"},
        },
        {
            "entry_id": "listed-strong-mlx",
            "tier": "small",
            "licence": {"base_model": "org/listed-strong"},
        },
        {
            "entry_id": "listed-weak",
            "tier": "small",
            "licence": {"base_model": "org/listed-weak"},
        },
    ],
}
PROPOSALS = [
    {"base_model": "org/rising", "tier": "small"},
    {"base_model": "org/young", "tier": "small"},
    {"base_model": "org/one-signal", "tier": "small"},
    # A proposal for a listed model never overrides its scoring.
    {"base_model": "org/listed-weak", "tier": "large"},
]


def _rows():
    candidates = signals.candidates_from(PLAN, PROPOSALS)
    return signals.measure(
        candidates,
        TODAY,
        model=lambda repo: MODELS[repo],
        conversions=lambda name: CONVERSIONS[name],
    )


def test_candidates_dedupe_base_models_and_proposals_only_add():
    candidates = signals.candidates_from(PLAN, PROPOSALS)
    by_base = {item.base_model: item for item in candidates}
    assert len(candidates) == 5
    assert (
        by_base["org/listed-weak"].listed and by_base["org/listed-weak"].tier == "small"
    )
    with pytest.raises(ValueError, match="tier"):
        signals.candidates_from(PLAN, [{"base_model": "org/x"}])


def test_rules_promote_keep_watch_and_demote_with_hysteresis():
    rows = _rows()
    diff = signals.decide(rows)
    keys = {kind: [item["key"] for item in items] for kind, items in diff.items()}
    assert keys["promote"] == ["rising"]
    assert keys["keep"] == ["listed-strong"]
    # One weak quarter only puts a listed model on the watch list.
    assert keys["watch"] == ["listed-weak"] and keys["demote"] == []
    not_yet = {item["key"]: item["reason"] for item in diff["not_yet"]}
    assert "younger than 3 months" in not_yet["young"]
    assert "fewer than two top-N signals" in not_yet["one-signal"]
    # A second consecutive weak quarter demotes it.
    again = signals.decide(rows, previous_weak=["listed-weak"])
    assert [item["key"] for item in again["demote"]] == ["listed-weak"]
    assert again["demote"][0]["reason"] == "two consecutive weak quarters"


def test_score_is_the_median_rank_and_likes_only_break_ties():
    rows = {row.key: row for row in _rows()}
    strong = rows["listed-strong"]
    assert strong.ranks == {"conversions_30d": 1, "base_30d": 1}
    assert strong.score == 1
    tied = [
        signals.Signals("a", "o/a", "small", False, base_30d=5, likes=1),
        signals.Signals("b", "o/b", "small", False, base_30d=5, likes=9),
    ]
    signals.rank(tied)
    assert tied[1].ranks["base_30d"] == 1 and tied[0].ranks["base_30d"] == 2


def test_ollama_signal_counts_when_supplied():
    candidates = [
        signals.Candidate("x", "org/one-signal", "small", False, ollama_tag="x"),
        signals.Candidate("s", "org/listed-strong", "small", True, ollama_tag="s"),
        signals.Candidate("w", "org/listed-weak", "small", True, ollama_tag="w"),
        signals.Candidate("r", "org/rising", "small", False, ollama_tag="r"),
    ]
    rows = signals.measure(
        candidates,
        TODAY,
        model=lambda repo: MODELS[repo],
        conversions=lambda name: CONVERSIONS[name],
        ollama={"x": 5_000_000.0, "s": 1.0, "w": 2.0, "r": 3.0},
    )
    one = next(row for row in rows if row.key == "x")
    # Without Ollama it had one top-N signal; Ollama supplies the second.
    assert one.ranks["ollama_monthly"] == 1
    assert one.strong_signals == 2 and one.qualifies


def test_main_writes_dated_signals_csv_and_diff(tmp_path, monkeypatch):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(PLAN))
    proposals = tmp_path / "proposals.json"
    proposals.write_text(json.dumps(PROPOSALS))
    monkeypatch.setattr(signals, "hub_model", lambda repo: MODELS[repo])
    monkeypatch.setattr(signals, "hub_conversions", lambda name: CONVERSIONS[name])
    out = tmp_path / "out"
    assert (
        signals.main(
            [
                "--plan",
                str(plan),
                "--proposals",
                str(proposals),
                "--output",
                str(out),
                "--date",
                "2026-11-07",
            ]
        )
        == 0
    )
    data = json.loads((out / "signals-2026-11-07.json").read_text())
    assert {row["key"] for row in data["signals"]} >= {"listed-strong", "rising"}
    assert (out / "signals-2026-11-07.csv").read_text().startswith("key,")
    diff = json.loads((out / "diff-2026-11-07.json").read_text())
    assert [item["key"] for item in diff["promote"]] == ["rising"]
    assert "operators choose" in diff["note"]
