#!/usr/bin/env python3
"""Recompute the starter collection's community-interest signals each quarter.

The starter list is a one-time import list. This script only *proposes* a next
quarter's list: it never changes a published list or any mirror. Operators
choose what to import.

Signals, all from public APIs (no page scraping):

- Hugging Face 30-day and all-time downloads of each entry's base repository;
- the 30-day downloads summed across the conversion organizations
  (unsloth, bartowski, lmstudio-community, ggml-org, mlx-community), matched by
  repository-name prefix;
- the sustained ratio: 30-day downloads / (all-time downloads / months since
  the repository was created). At least 0.7 after 3 months reads as sustained;
- Ollama monthly pulls, when an operator supplies a pull-count snapshot
  (``--ollama tags.csv``: ``tag,pulls``) and the previous one: Ollama has no
  public statistics API, so nothing is scraped.

Rules (the same as docs/starter-collection.md):

- Score: the median of an entry's ranks across the available signals; likes
  only break ties.
- Promote a candidate only when it is at least 3 months old, ranks in its
  tier's top N on at least two signals and its sustained ratio is at least 0.7.
- Demote a listed entry only after two consecutive weak quarters; one weak
  quarter puts it on the watch list.
- Proposals (``--proposals``) add candidates to the scoring run; they never
  override it.

Output: ``signals-YYYY-MM-DD.json`` and ``.csv`` in ``--output``, plus a ranked
candidate diff ``diff-YYYY-MM-DD.json`` against the current plan.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

CONVERSION_ORGS = (
    "unsloth",
    "bartowski",
    "lmstudio-community",
    "ggml-org",
    "mlx-community",
)
MIN_AGE_DAYS = 90
SUSTAINED = 0.7
SIGNALS = ("conversions_30d", "base_30d", "ollama_monthly")
TIERS = ("small", "standard", "large")
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PLAN = ROOT / "examples" / "starter" / "plan.json"


@dataclass
class Candidate:
    """One model scored this quarter: a listed entry or a proposal."""

    key: str
    base_model: str
    tier: str
    listed: bool
    ollama_tag: str | None = None


@dataclass
class Signals:
    """The measured signals for one candidate on one date."""

    key: str
    base_model: str
    tier: str
    listed: bool
    base_30d: int | None = None
    base_all_time: int | None = None
    conversions_30d: int | None = None
    created_at: str | None = None
    age_days: int | None = None
    sustained_ratio: float | None = None
    ollama_monthly: float | None = None
    likes: int | None = None
    ranks: dict[str, int] = field(default_factory=dict)
    score: float | None = None
    strong_signals: int = 0
    qualifies: bool = False


def sustained_ratio(
    downloads_30d: int, all_time: int, created: date, today: date
) -> float | None:
    """30-day downloads against the lifetime monthly average."""
    months = max((today - created).days / 30.44, 1.0)
    if all_time <= 0:
        return None
    return round(downloads_30d / (all_time / months), 3)


def ollama_rate(
    current: dict[str, int], previous: dict[str, int], days: int
) -> dict[str, float]:
    """Monthly pull rate per tag between two operator-supplied snapshots."""
    if days <= 0:
        return {}
    return {
        tag: round((pulls - previous[tag]) * 30.44 / days, 1)
        for tag, pulls in current.items()
        if tag in previous and pulls >= previous[tag]
    }


def rank(rows: list[Signals]) -> None:
    """Rank each available signal within each tier and take the median rank.

    A tier's top N is the number of entries the current list has in it (at
    least 3), so a candidate must beat a listed model to qualify.
    """
    for tier in TIERS:
        pool = [row for row in rows if row.tier == tier]
        top = max(3, sum(1 for row in pool if row.listed))
        for signal in SIGNALS:
            measured = [row for row in pool if getattr(row, signal) is not None]
            measured.sort(key=lambda row: (-getattr(row, signal), -(row.likes or 0)))
            for position, row in enumerate(measured, start=1):
                row.ranks[signal] = position
        for row in pool:
            row.score = statistics.median(row.ranks.values()) if row.ranks else None
            row.strong_signals = sum(1 for value in row.ranks.values() if value <= top)
            row.qualifies = bool(
                row.strong_signals >= 2
                and row.sustained_ratio is not None
                and row.sustained_ratio >= SUSTAINED
                and (row.age_days or 0) >= MIN_AGE_DAYS
            )


def decide(
    rows: list[Signals], previous_weak: Iterable[str] = ()
) -> dict[str, list[dict[str, Any]]]:
    """Apply promotion and demotion hysteresis; a proposal, never an action.

    ``previous_weak`` names the listed entries that did not qualify last
    quarter (from the previous diff). A listed entry is demoted only when it
    fails again; a first weak quarter only puts it on the watch list.
    """
    weak_before = set(previous_weak)
    diff: dict[str, list[dict[str, Any]]] = {
        "promote": [],
        "keep": [],
        "watch": [],
        "demote": [],
        "not_yet": [],
    }
    for row in sorted(rows, key=lambda r: (TIERS.index(r.tier), r.score or 1e9)):
        item = {
            "key": row.key,
            "base_model": row.base_model,
            "tier": row.tier,
            "score": row.score,
            "strong_signals": row.strong_signals,
            "sustained_ratio": row.sustained_ratio,
            "age_days": row.age_days,
        }
        if row.listed:
            if row.qualifies:
                diff["keep"].append(item)
            elif row.key in weak_before:
                diff["demote"].append(
                    item | {"reason": "two consecutive weak quarters"}
                )
            else:
                diff["watch"].append(item | {"reason": "first weak quarter"})
        elif row.qualifies:
            diff["promote"].append(item)
        else:
            reasons = []
            if (row.age_days or 0) < MIN_AGE_DAYS:
                reasons.append("younger than 3 months")
            if row.strong_signals < 2:
                reasons.append("fewer than two top-N signals")
            if row.sustained_ratio is None or row.sustained_ratio < SUSTAINED:
                reasons.append("sustained ratio below 0.7")
            diff["not_yet"].append(item | {"reason": "; ".join(reasons)})
    return diff


# Data sources ----------------------------------------------------------------


def _json(url: str) -> Any:
    request = urllib.request.Request(  # noqa: S310 - fixed https host
        url, headers={"User-Agent": "foxlight-starter-signals"}
    )
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
        return json.loads(response.read(32 * 1024**2))


def hub_model(repository: str) -> dict[str, Any]:
    """Downloads (30-day and all-time), likes and creation date of one repo."""
    expand = "&".join(
        "expand[]=" + item
        for item in ("downloads", "downloadsAllTime", "likes", "createdAt")
    )
    return _json(f"https://huggingface.co/api/models/{repository}?{expand}")


def hub_conversions(name: str) -> int:
    """30-day downloads summed across the conversion orgs, by name prefix."""
    total = 0
    for org in CONVERSION_ORGS:
        query = urllib.parse.urlencode(
            {"author": org, "search": name, "limit": 100, "expand[]": "downloads"}
        )
        for item in _json(f"https://huggingface.co/api/models?{query}"):
            repo = str(item.get("id", "")).split("/", 1)[-1].casefold()
            if repo.startswith(name.casefold()):
                total += int(item.get("downloads") or 0)
    return total


def measure(
    candidates: list[Candidate],
    today: date,
    *,
    model: Callable[[str], dict[str, Any]] | None = None,
    conversions: Callable[[str], int] | None = None,
    ollama: dict[str, float] | None = None,
) -> list[Signals]:
    """Collect every candidate's signals (data sources are injectable)."""
    model = model or hub_model
    conversions = conversions or hub_conversions
    rows: list[Signals] = []
    for item in candidates:
        row = Signals(item.key, item.base_model, item.tier, item.listed)
        try:
            info = model(item.base_model)
        except (OSError, ValueError) as error:
            print(f"skip {item.base_model}: {error}", file=sys.stderr)
            info = {}
        if info:
            row.base_30d = int(info.get("downloads") or 0)
            row.base_all_time = int(info.get("downloadsAllTime") or 0)
            row.likes = int(info.get("likes") or 0)
            created = info.get("createdAt")
            if isinstance(created, str):
                row.created_at = created
                made = datetime.fromisoformat(created.replace("Z", "+00:00")).date()
                row.age_days = (today - made).days
                row.sustained_ratio = sustained_ratio(
                    row.base_30d, row.base_all_time, made, today
                )
        try:
            row.conversions_30d = conversions(item.base_model.split("/", 1)[-1])
        except (OSError, ValueError) as error:
            print(f"skip conversions of {item.base_model}: {error}", file=sys.stderr)
        if ollama is not None and item.ollama_tag:
            row.ollama_monthly = ollama.get(item.ollama_tag)
        rows.append(row)
    rank(rows)
    return rows


def candidates_from(
    plan: dict[str, Any], proposals: list[dict[str, Any]]
) -> list[Candidate]:
    """Listed entries (one per base model) plus proposed candidates."""
    seen: dict[str, Candidate] = {}
    for entry in plan["entries"]:
        base = entry["licence"]["base_model"]
        if base not in seen:
            seen[base] = Candidate(
                entry["entry_id"], base, entry["tier"], True, entry.get("ollama_tag")
            )
    for proposal in proposals:
        base = proposal["base_model"]
        if base in seen:
            continue  # A proposal adds candidates; it never overrides scoring.
        if proposal.get("tier") not in TIERS:
            raise ValueError(f"proposal {base} needs a tier")
        seen[base] = Candidate(
            proposal.get("key") or base.split("/")[-1].casefold(),
            base,
            proposal["tier"],
            False,
            proposal.get("ollama_tag"),
        )
    return list(seen.values())


def _snapshot(path: Path) -> dict[str, int]:
    with path.open(newline="") as stream:
        return {row["tag"]: int(row["pulls"]) for row in csv.DictReader(stream)}


def main(argv: list[str] | None = None) -> int:
    """Measure, score and write the dated signals and the candidate diff."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--proposals", type=Path, help="JSON list of candidates")
    parser.add_argument("--ollama", type=Path, help="this quarter's tag,pulls CSV")
    parser.add_argument("--ollama-previous", type=Path)
    parser.add_argument("--ollama-days", type=int, default=91)
    parser.add_argument("--previous-diff", type=Path, help="last quarter's diff")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--date", default=datetime.now(UTC).date().isoformat())
    arguments = parser.parse_args(argv)
    today = date.fromisoformat(arguments.date)
    plan = json.loads(arguments.plan.read_text())
    proposals = (
        json.loads(arguments.proposals.read_text()) if arguments.proposals else []
    )
    rates = (
        ollama_rate(
            _snapshot(arguments.ollama),
            _snapshot(arguments.ollama_previous),
            arguments.ollama_days,
        )
        if arguments.ollama and arguments.ollama_previous
        else None
    )
    rows = measure(candidates_from(plan, proposals), today, ollama=rates)
    previous_weak: list[str] = []
    if arguments.previous_diff:
        last = json.loads(arguments.previous_diff.read_text())
        previous_weak = [
            item["key"] for item in last.get("watch", []) + last.get("demote", [])
        ]
    diff = decide(rows, previous_weak)
    arguments.output.mkdir(parents=True, exist_ok=True)
    stem = arguments.date
    records = [asdict(row) for row in rows]
    (arguments.output / f"signals-{stem}.json").write_text(
        json.dumps({"date": stem, "signals": records}, indent=2) + "\n"
    )
    with (arguments.output / f"signals-{stem}.csv").open("w", newline="") as stream:
        columns = [k for k in records[0] if k != "ranks"] if records else []
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
    (arguments.output / f"diff-{stem}.json").write_text(
        json.dumps(
            {
                "date": stem,
                "plan": plan.get("title"),
                "note": "A proposal only. Publishing a new list changes no mirror; "
                "operators choose what to import.",
                **diff,
            },
            indent=2,
        )
        + "\n"
    )
    print(json.dumps({key: len(value) for key, value in diff.items()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
