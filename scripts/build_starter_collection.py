#!/usr/bin/env python3
"""Check, pack, sign and publish Sovereign's starter collection.

The plan (``examples/starter/plan.json``) names each entry's tier, recipe and
the base model its licence is resolved from. Recipes pin exact revisions and
every file's SHA-256. Three steps:

``check``
    Dry run. Resolves every licence from the BASE model's card at its pinned
    revision (a conversion's tag must match it and never decides it), checks
    declared ancestors, and confirms every recipe file exists at its pinned
    revision with the pinned SHA-256: large (LFS) files by the Hub's LFS
    ``oid``, so no weights are downloaded; small text files are downloaded and
    hashed. Nothing is written.

``build``
    Downloads each pinned file (streamed, hash-checked), packs one bundle per
    recipe with the operator's Foxlight starter publishing key, verifies every
    bundle, then writes the signed starter list (``sovereign.starter-list.v1``).
    The key is passed in by the operator and never stored by this script.

``publish``
    Uploads the bundles and the signed list to a bucket mirror (for example
    Cloudflare R2), then publishes the bucket's signed mirror card and peers
    document. The card lasts at most 14 days (7 by default): re-run
    ``publish`` (it uploads only what is missing) to renew it.

This script needs network access to huggingface.co and
raw.githubusercontent.com for ``check`` and ``build``. Set ``HF_TOKEN`` to
raise Hub rate limits; it is only sent to huggingface.co.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO, Literal

from pydantic import Field

from foxlight_model_preservation.bucket import (
    BucketClient,
    BucketTarget,
    load_credentials,
    publish_peers,
    publish_to_bucket,
)
from foxlight_model_preservation.licences import (
    KNOWN_LICENSES,
    UNIDENTIFIED,
    definition,
    normalize_license,
)
from foxlight_model_preservation.mirror_cards import build_peers, issue_card
from foxlight_model_preservation.mirror_models import Terms
from foxlight_model_preservation.models import (
    PreservationError,
    Recipe,
    Record,
    TrustProfile,
)
from foxlight_model_preservation.signing import (
    canonical_bytes,
    digest,
    load_key,
    public_record,
    strict_json,
)
from foxlight_model_preservation.starter import (
    StarterEntry,
    StarterLicence,
    StarterList,
    list_identity,
    sign_starter_list,
    verify_starter_list,
)
from foxlight_model_preservation.storage import pack, verify_store

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PLAN = ROOT / "examples" / "starter" / "plan.json"
# The strict policy: Apache-2.0 or MIT only, for the base model and the conversion.
STRICT = {"apache-2.0": "Apache-2.0", "mit": "MIT"}
SMALL_FILE = 16 * 1024**2
CHUNK = 1024**2
PLACEHOLDER = re.compile(r"\.invalid(?::\d+)?(?:/|$)")


class LicenceRefused(PreservationError):
    """A model whose licence does not clearly resolve to Apache-2.0 or MIT."""


class Excluded(Record):
    """A model considered and left out, with the reason."""

    model: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=1000)


class Component(Record):
    """Another model inside one bundle (a text encoder, a VAE) and its licence."""

    members: tuple[str, ...] = Field(min_length=1, max_length=16)
    licence: StarterLicence


class PlanEntry(Record):
    """One starter entry before packing: its recipe and licence evidence."""

    entry_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    tier: Literal["small", "standard", "large"]
    title: str = Field(min_length=1, max_length=200)
    role: str = Field(min_length=1, max_length=200)
    recipe: str = Field(min_length=1, max_length=200)
    licence: StarterLicence
    # Further models a pipeline bundles; each licence is resolved the same way.
    components: tuple[Component, ...] = ()
    # Declared ancestors accepted despite their own card, each with the reason.
    ancestor_waivers: dict[str, str] = Field(default_factory=dict)
    # A reviewed, named licence outside Sovereign's definition (always gated).
    custom_licence: bool = False


class Plan(Record):
    """The reviewed input to the starter pipeline."""

    schema_version: Literal["sovereign.starter-plan.v1"]
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    recipes: str = Field(min_length=1, max_length=200)
    mirrors: tuple[str, ...] = Field(min_length=1, max_length=16)
    entries: tuple[PlanEntry, ...] = Field(min_length=1, max_length=1000)
    excluded: tuple[Excluded, ...] = ()
    # strict: Apache-2.0 or MIT only. known: any licence in Sovereign's licence
    # definition; gated ones need the operator's acceptance at import.
    licence_policy: Literal["strict", "known"] = "strict"


def load_plan(path: Path) -> tuple[Plan, dict[str, Recipe]]:
    """Strictly parse the plan and every recipe it names."""
    plan = Plan.model_validate_json(canonical_bytes(strict_json(path.read_bytes())))
    folder = (path.parent / plan.recipes).resolve()
    recipes: dict[str, Recipe] = {}
    for entry in plan.entries:
        recipe_path = (folder / entry.recipe).resolve()
        if recipe_path.parent != folder:
            raise PreservationError("plan recipe must be a file in its recipe folder")
        recipes[entry.entry_id] = Recipe.model_validate_json(
            canonical_bytes(strict_json(recipe_path.read_bytes()))
        )
    if len(recipes) != len(plan.entries):
        raise PreservationError("duplicate plan entry")
    return plan, recipes


def recipe_sha256(recipe: Recipe) -> str:
    """The recipe identity recorded in the list: SHA-256 of its canonical form."""
    return digest(canonical_bytes(recipe.model_dump(mode="json")))


# Licence resolution --------------------------------------------------------


def _card_licence(info: dict[str, Any]) -> str:
    card = info.get("cardData") or {}
    value = card.get("license")
    if isinstance(value, list):
        value = value[0] if len(value) == 1 else None  # several: not clear
    if value == "other" and isinstance(card.get("license_name"), str):
        value = card["license_name"]  # A named custom licence.
    return normalize_license(value) if isinstance(value, str) else ""


def card_bases(info: dict[str, Any]) -> tuple[str, ...]:
    """The base models a card declares (``base_model``), if any."""
    value = (info.get("cardData") or {}).get("base_model")
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        return tuple(item for item in value if isinstance(item, str))
    return ()


def allowed_licences(policy: str) -> frozenset[str]:
    """Normalized licence identifiers a plan policy admits."""
    return frozenset(STRICT) if policy == "strict" else KNOWN_LICENSES


def spelled(licence: str) -> str:
    """The identifier as recipes and lists spell it."""
    return STRICT.get(licence, licence)


def requires_acceptance(licence: str) -> bool:
    """Whether Sovereign's definition gates this licence behind acceptance."""
    for family in definition().families:
        if any(item.id == licence for item in family.licences):
            return family.acceptance
    return True


def licence_text(origin: str, path: str) -> bool:
    """Whether a carried file is a licence text (by name, or an SPDX text)."""
    name = path.rsplit("/", 1)[-1].upper()
    return (
        "LICENSE" in name
        or "TERMS_OF_USE" in name
        or origin == "github.com/spdx/license-list-data"
    )


def resolve_licence(
    base_info: dict[str, Any],
    conversion_info: dict[str, Any] | None,
    policy: str = "strict",
    *,
    allowed: frozenset[str] | None = None,
) -> str:
    """Resolve the licence from the BASE model's card; never from a conversion.

    The base card must name a licence the policy admits: Apache-2.0 or MIT
    for ``strict``, any licence in Sovereign's definition for ``known`` (gated
    ones are then accepted by the operator at import). A conversion's own tag
    must name the same licence: a missing, different or contradicting tag
    refuses (it is not "clearly" the same terms), but it can never widen or
    replace what the base model's card says.
    """
    base = _card_licence(base_info)
    if base not in (allowed if allowed is not None else allowed_licences(policy)):
        raise LicenceRefused(
            f"base model licence {base or 'unidentified'!r} is not "
            + ("Apache-2.0 or MIT" if policy == "strict" else "a known licence")
        )
    if conversion_info is not None:
        tagged = _card_licence(conversion_info)
        if tagged != base:
            raise LicenceRefused(
                f"conversion licence tag {tagged or 'missing'!r} does not match "
                f"the base model's {base!r}"
            )
    return spelled(base)


# Hub access -----------------------------------------------------------------


@dataclass
class Hub:
    """Read-only access to the Hugging Face Hub and pinned GitHub files."""

    hub: str = "https://huggingface.co"
    raw: str = "https://raw.githubusercontent.com"
    token: str | None = None

    def _request(self, url: str) -> urllib.request.Request:
        headers = {"User-Agent": "foxlight-starter-collection"}
        if self.token and url.startswith(self.hub + "/"):
            headers["Authorization"] = f"Bearer {self.token}"
        return urllib.request.Request(url, headers=headers)  # noqa: S310 - https

    def json(self, url: str) -> Any:
        """Fetch one bounded JSON document, retrying transient failures twice."""
        for attempt in range(3):
            try:
                with urllib.request.urlopen(  # noqa: S310 - fixed https hosts
                    self._request(url), timeout=60
                ) as response:
                    return json.loads(response.read(64 * 1024**2))
            except urllib.error.HTTPError as error:
                if error.code < 500 or attempt == 2:
                    raise
            except urllib.error.URLError:
                if attempt == 2:
                    raise
        raise AssertionError("unreachable")  # pragma: no cover

    def info(self, repository: str, revision: str) -> dict[str, Any]:
        """Model metadata (card data included) at an exact revision."""
        value = self.json(f"{self.hub}/api/models/{repository}/revision/{revision}")
        if not isinstance(value, dict) or (
            re.fullmatch(r"[0-9a-f]{40}", revision) and value.get("sha") != revision
        ):
            raise PreservationError(f"{repository}@{revision} did not resolve")
        return value

    def tree(self, repository: str, revision: str) -> dict[str, dict[str, Any]]:
        """Every file at an exact revision, with size and LFS object identity."""
        items = self.json(
            f"{self.hub}/api/models/{repository}/tree/{revision}?recursive=1"
        )
        return {item["path"]: item for item in items if item.get("type") == "file"}

    def url(self, origin: str, revision: str, path: str) -> str:
        """The download address of one pinned file."""
        if origin.startswith("github.com/"):
            return f"{self.raw}/{origin.removeprefix('github.com/')}/{revision}/{path}"
        return f"{self.hub}/{origin}/resolve/{revision}/{path}"

    @contextmanager
    def open(self, origin: str, revision: str, path: str) -> Iterator[BinaryIO]:
        """Stream one pinned file."""
        request = self._request(self.url(origin, revision, path))
        with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
            yield response


def _hash(stream: BinaryIO, output: BinaryIO | None = None) -> tuple[str, int]:
    hasher = hashlib.sha256()
    size = 0
    while chunk := stream.read(CHUNK):
        hasher.update(chunk)
        size += len(chunk)
        if output is not None:
            output.write(chunk)
    return hasher.hexdigest(), size


# check ----------------------------------------------------------------------


def check(
    plan: Plan,
    recipes: dict[str, Recipe],
    hub: Hub,
    *,
    say: Callable[[str], None] = print,
) -> list[dict[str, Any]]:
    """Dry-run every entry; return per-entry evidence, raising on any mismatch.

    No weights are downloaded: LFS files are checked by the Hub's LFS ``oid``
    (their SHA-256). Small non-LFS files are downloaded and hashed.
    """
    report: list[dict[str, Any]] = []
    problems: list[str] = []
    allowed = allowed_licences(plan.licence_policy)
    for entry in plan.entries:
        recipe = recipes[entry.entry_id]
        licence = entry.licence
        evidence: dict[str, Any] = {"entry_id": entry.entry_id, "tier": entry.tier}
        try:
            permitted = set(allowed)
            resolved = ""
            ancestors: dict[str, str] = {}
            evidences = [(licence, recipe.root_member)] + [
                (component.licence, component.members[0])
                for component in entry.components
            ]
            for item, member_id in evidences:
                base = hub.info(item.base_model, item.base_revision)
                conversion = (
                    hub.info(item.conversion, item.conversion_revision)
                    if item.conversion and item.conversion_revision
                    else None
                )
                if entry.custom_licence:
                    custom = _card_licence(base)
                    if custom in UNIDENTIFIED or not custom:
                        raise LicenceRefused("custom licence is unidentified")
                    permitted.add(custom)
                found_licence = resolve_licence(
                    base, conversion, "known", allowed=frozenset(permitted)
                )
                if normalize_license(found_licence) != normalize_license(
                    item.license_id
                ):
                    raise LicenceRefused(
                        f"plan says {item.license_id}, base resolves to {found_licence}"
                    )
                origin = next(
                    m.origin_repository
                    for m in recipe.members
                    if m.member_id == member_id
                )
                if origin not in {item.conversion, item.base_model}:
                    raise PreservationError(
                        f"{member_id} comes from neither its base nor conversion"
                    )
                for ancestor in card_bases(base):
                    if ancestor in entry.ancestor_waivers:
                        ancestors[ancestor] = (
                            "waived: " + entry.ancestor_waivers[ancestor]
                        )
                        continue
                    found = _card_licence(hub.info(ancestor, "main"))
                    if found not in permitted:
                        raise LicenceRefused(
                            f"declared base {ancestor} is {found or 'unidentified'!r}"
                        )
                    ancestors[ancestor] = spelled(found)
                resolved = resolved or found_licence
            base = hub.info(licence.base_model, licence.base_revision)
            conversion = (
                hub.info(licence.conversion, licence.conversion_revision)
                if licence.conversion and licence.conversion_revision
                else None
            )
            declared = {normalize_license(m.intake.license_id) for m in recipe.members}
            if not declared <= permitted:
                raise LicenceRefused(
                    "a recipe member declares a licence the plan policy refuses"
                )
            if not any(
                licence_text(member.origin_repository, file.path)
                for member in recipe.members
                for file in member.files
            ):
                raise LicenceRefused("no licence text is carried")
            total = 0
            for member in recipe.members:
                origin, revision = member.origin_repository, member.origin_revision
                listing = (
                    None
                    if origin.startswith("github.com/")
                    else hub.tree(origin, revision)
                )
                for file in member.files:
                    expected = file.expected_sha256
                    if listing is not None:
                        item = listing.get(file.path)
                        if item is None:
                            raise PreservationError(
                                f"{origin}@{revision[:12]} has no {file.path}"
                            )
                        lfs = item.get("lfs")
                        size = int(item["size"])
                        if lfs:
                            found = str(lfs.get("oid", ""))
                        elif size <= SMALL_FILE:
                            with hub.open(origin, revision, file.path) as stream:
                                found, size = _hash(stream)
                        else:
                            raise PreservationError(f"{file.path} is large and not LFS")
                    else:
                        with hub.open(origin, revision, file.path) as stream:
                            found, size = _hash(stream)
                    if found != expected:
                        raise PreservationError(
                            f"{origin}@{revision[:12]}/{file.path} SHA-256 differs"
                        )
                    total += size
            evidence.update(
                licence=resolved,
                base=f"{licence.base_model}@{licence.base_revision}",
                base_card_licence=_card_licence(base),
                conversion=f"{licence.conversion}@{licence.conversion_revision}"
                if conversion is not None
                else None,
                conversion_card_licence=_card_licence(conversion)
                if conversion is not None
                else None,
                ancestors=ancestors,
                components={
                    "+".join(c.members): f"{c.licence.base_model}@"
                    f"{c.licence.base_revision} ({c.licence.license_id})"
                    for c in entry.components
                },
                requires_acceptance=sorted(
                    item for item in declared if requires_acceptance(item)
                ),
                logical_bytes=total,
                ok=True,
            )
            say(f"ok   {entry.entry_id:28} {resolved:10} {total:>16,} bytes")
        except (ValueError, OSError) as error:
            evidence.update(ok=False, error=str(error))
            problems.append(f"{entry.entry_id}: {error}")
            say(f"FAIL {entry.entry_id:28} {error}")
        report.append(evidence)
    if problems:
        raise PreservationError("starter check failed: " + "; ".join(problems))
    return report


# build ----------------------------------------------------------------------


def download(
    hub: Hub, recipe: Recipe, inputs: Path, *, say: Callable[[str], None] = print
) -> dict[str, Path]:
    """Download each member's pinned files into its own input directory."""
    sources: dict[str, Path] = {}
    for member in recipe.members:
        folder = inputs / recipe.name / member.member_id
        sources[member.member_id] = folder
        for file in member.files:
            target = folder / file.path
            if target.is_file():
                with target.open("rb") as stream:
                    if _hash(stream)[0] == file.expected_sha256:
                        continue
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            partial = target.with_name(target.name + ".part")
            say(f"get  {member.origin_repository}/{file.path}")
            with (
                hub.open(
                    member.origin_repository, member.origin_revision, file.path
                ) as stream,
                partial.open("wb") as output,
            ):
                found, _size = _hash(stream, output)
            if found != file.expected_sha256:
                partial.unlink()
                raise PreservationError(f"{file.path} SHA-256 differs from its recipe")
            partial.replace(target)
    return sources


def unique_terms(recipe: Recipe) -> tuple[Terms, ...]:
    """Every member's exact (licence, reference) pair, once each."""
    seen = dict.fromkeys(
        (member.intake.license_id, member.intake.terms_reference)
        for member in recipe.members
    )
    return tuple(Terms(license_id=a, terms_reference=b) for a, b in seen)


def build(
    plan: Plan,
    recipes: dict[str, Recipe],
    hub: Hub,
    *,
    work: Path,
    key_path: Path,
    password: bytes | None,
    mirrors: tuple[str, ...],
    issued: str,
    output: Path,
    allow_placeholder: bool = False,
    say: Callable[[str], None] = print,
) -> str:
    """Download, pack, verify and sign; write the list and return its ID."""
    if any(PLACEHOLDER.search(mirror) for mirror in mirrors) and not allow_placeholder:
        raise PreservationError(
            "mirror hints are placeholders: pass --mirror with the real address"
        )
    if output.exists():
        raise PreservationError("list output already exists")
    key = load_key(key_path, password)
    trust = TrustProfile(
        schema_version="foxlight.model-preservation.trust.v1",
        keys=(public_record(key),),
        threshold=1,
        as_of=issued,
    )
    store = work / "store"
    entries: list[StarterEntry] = []
    for entry in plan.entries:
        recipe = recipes[entry.entry_id]
        sources = download(hub, recipe, work / "inputs", say=say)
        identity = pack(recipe, sources, store, (key,))
        manifest = verify_store(store, identity, trust)
        say(f"pack {entry.entry_id:28} {identity}")
        entries.append(
            StarterEntry(
                entry_id=entry.entry_id,
                tier=entry.tier,
                title=entry.title,
                role=entry.role,
                manifest=identity,
                recipe=recipe.name,
                recipe_sha256=recipe_sha256(recipe),
                size_bytes=manifest.total_bytes,
                licence=entry.licence,
                terms=unique_terms(recipe),
                mirrors=mirrors,
            )
        )
    starter = StarterList(
        schema_version="sovereign.starter-list.v1",
        title=plan.title,
        description=plan.description,
        issued=issued,
        bundle_trust=trust,
        entries=tuple(entries),
    )
    envelope = sign_starter_list(starter, (key,))
    list_id = list_identity(starter)
    verify_starter_list(envelope, list_id)
    with output.open("xb") as stream:
        stream.write(envelope)
    (work / "trust.json").write_bytes(canonical_bytes(trust.model_dump(mode="json")))
    return list_id


# publish --------------------------------------------------------------------


def publish(
    client: BucketClient,
    store: Path,
    list_path: Path,
    list_id: str,
    *,
    card_key_path: Path,
    card_password: bytes | None,
    say: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Upload every bundle, then the list, then a fresh signed mirror card."""
    data = list_path.read_bytes()
    starter = verify_starter_list(data, list_id)
    for entry in starter.entries:
        sent = publish_to_bucket(store, entry.manifest, starter.bundle_trust, client)
        say(f"put  {entry.entry_id:28} {sent} new objects")
    client.put_bytes(f"starter/{list_id}.json", data, "application/json")
    card = issue_card(
        load_key(card_key_path, card_password),
        (client.target.public_url,),
        {entry.manifest for entry in starter.entries},
    )
    publish_peers(client, build_peers(card, ()))
    return {
        "list_id": list_id,
        "list_path": f"starter/{list_id}.json",
        "bundles": len(starter.entries),
        "public_url": client.target.public_url,
    }


# CLI ------------------------------------------------------------------------


def _password(path: Path | None) -> bytes | None:
    if path is None:
        return None
    return path.read_bytes().rstrip(b"\r\n") or None


def main(argv: list[str] | None = None) -> int:
    """Run one pipeline step and print a JSON receipt."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    steps = parser.add_subparsers(dest="step", required=True)
    for name in ("check", "build"):
        step = steps.add_parser(name)
        step.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
        step.add_argument("--only", action="append", default=[], help="entry ID")
    build_step = steps.choices["build"]
    build_step.add_argument("--work", type=Path, required=True)
    build_step.add_argument("--key", type=Path, required=True)
    build_step.add_argument("--passphrase-file", type=Path)
    build_step.add_argument("--output", type=Path, required=True)
    build_step.add_argument("--mirror", action="append", default=[])
    build_step.add_argument("--issued", default=datetime.now(UTC).date().isoformat())
    build_step.add_argument("--allow-placeholder-mirrors", action="store_true")
    build_step.add_argument("--skip-check", action="store_true")
    publish_step = steps.add_parser("publish")
    publish_step.add_argument("--store", type=Path, required=True)
    publish_step.add_argument("--list", dest="list_path", type=Path, required=True)
    publish_step.add_argument("--list-id", required=True)
    publish_step.add_argument("--bucket-target", type=Path, required=True)
    publish_step.add_argument("--card-key", type=Path, required=True)
    publish_step.add_argument("--card-passphrase-file", type=Path)
    arguments = parser.parse_args(argv)
    say: Callable[[str], None] = lambda line: print(line, file=sys.stderr)  # noqa: E731
    try:
        if arguments.step == "publish":
            target = BucketTarget.model_validate_json(
                arguments.bucket_target.read_bytes()
            )
            client = BucketClient(target, load_credentials(target.key_name))
            result: Any = publish(
                client,
                arguments.store,
                arguments.list_path,
                arguments.list_id,
                card_key_path=arguments.card_key,
                card_password=_password(arguments.card_passphrase_file),
                say=say,
            )
        else:
            plan, recipes = load_plan(arguments.plan)
            if arguments.only:
                wanted = set(arguments.only)
                plan = plan.model_copy(
                    update={
                        "entries": tuple(
                            e for e in plan.entries if e.entry_id in wanted
                        )
                    }
                )
            hub = Hub(token=os.environ.get("HF_TOKEN") or None)
            if arguments.step == "check" or not arguments.skip_check:
                report = check(plan, recipes, hub, say=say)
            if arguments.step == "check":
                result = {"checked": report, "downloaded_weights": False}
            else:
                list_id = build(
                    plan,
                    recipes,
                    hub,
                    work=arguments.work,
                    key_path=arguments.key,
                    password=_password(arguments.passphrase_file),
                    mirrors=tuple(arguments.mirror) or plan.mirrors,
                    issued=arguments.issued,
                    output=arguments.output,
                    allow_placeholder=arguments.allow_placeholder_mirrors,
                    say=say,
                )
                result = {"list_id": list_id, "list": str(arguments.output)}
    except (ValueError, OSError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
