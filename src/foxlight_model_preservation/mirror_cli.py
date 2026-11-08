"""Operator-only mirror controls; ordinary receiving remains receive-only."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Literal, cast

from pydantic import JsonValue, TypeAdapter

from .licences import (
    DEFAULT_LICENSES,
    LICENCE_DEFINITION_VERSION,
    definition,
    identified_licences,
)
from .mirror import (
    configure_http,
    configure_intake,
    configure_mirror,
    derived_model_limit,
    follow,
    model_action,
    plan,
    retry_selection,
    run_mirror,
    withhold,
)
from .mirror_announce import DISCLOSURE, mirror_key_id
from .mirror_cards import base_url
from .mirror_intake import retain_local
from .mirror_models import (
    AnnouncePolicy,
    IntakePolicy,
    MirrorObservation,
    MirrorState,
    SourceGrant,
    Terms,
    upgrade_profile,
)
from .mirror_native import configure, export_reference, write_reference
from .mirror_prune import prune
from .mirror_quickstart import (
    CONSENT_FLAG,
    QuickstartSettings,
    apply_quickstart,
    default_name,
    load_collection,
    parse_duration,
    parse_size,
    review,
)
from .mirror_recovery import (
    export_selection_kit,
    inspect_selection,
    recover_selection,
    selection_status,
)
from .mirror_state import MAX_STATE, MirrorLedger, grant_restart
from .models import PreservationError
from .operations import OperationControl
from .signing import load_trust
from .starter import (
    STARTER_CONSENT_FLAG,
    TIERS,
    ImportOptions,
    StarterList,
    StarterPlan,
    Tier,
    import_starter,
    plan_import,
    review_import,
    select_tier,
    verify_starter_list,
)


class MirrorArguments(argparse.Namespace):
    """Typed standalone mirror subcommands, with no implicit hosted dependencies."""

    mirror_action: str
    mirror_dir: Path
    mirror_name: str | None
    disk_bytes: int | None
    ingress_daily: int | None
    egress_daily: int | Literal["none"] | None
    egress_monthly: int | Literal["none"] | None
    intake_share: Literal["all", "chosen"] | None
    intake_licenses: str | None
    licence_families: str | None
    clear_intake: bool
    mirror_bind: str | None
    mirror_port: int | None
    serve_start: int | None
    serve_end: int | None
    refresh_seconds: int | None
    follow_withdrawals: str | None
    public_url: str | None
    clear_public_url: bool
    contact: str | None
    announce_nostr: bool | None
    announce_dht: bool | None
    confirm_public_announcement: bool
    collection: Path | None
    starter_list: Path | None
    starter_tier: Tier
    expected_list_id: str | None
    list_trust: Path | None
    starter_mirror: list[str]
    accept_starter: bool
    starter_staging: Path | None
    resume_for: int | None
    plan_only: bool
    consent_public_serving: bool
    run_after: bool
    adopt_source_grant: bool
    source_file: Path
    local_store: Path
    expected_bootstrap_id: str
    mirror_trust: Path
    terms_file: Path
    confirm_redistribution: bool
    confirm_reintake: bool
    http_enabled: bool
    observe_mirror: bool
    allow_http: bool
    allow_private: bool
    allow_local: bool
    model_max_bytes: int | None
    public: bool
    resume: bool
    seconds: int
    mirror_manifest: str
    confirm_manifest: str
    resume_expires: int | None
    confirm_resume: bool
    ipfs_binary: str | None
    ipfs_sha256: str | None
    torrent: bool
    bootstrap: list[str]
    allow_private_peers: bool
    max_active: int
    session_seconds: int
    session_bytes: int
    upload_rate: int
    export_kind: str
    export_output: Path


def _size(text: str) -> int:
    try:
        return parse_size(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


UNCAPPED = ("none", "off", "unlimited")


def _cap(text: str) -> int | Literal["none"]:
    """An upload cap: a size, or "none" for no cap (uploads are still counted)."""
    if text.strip().casefold() in UNCAPPED:
        return "none"
    return _size(text)


def _resolved_cap(value: int | Literal["none"] | None) -> int | None:
    """None and "none" both mean no cap where nothing else applies."""
    return None if value is None or value == "none" else value


def _duration(text: str) -> int:
    try:
        return parse_duration(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


SIZE_HELP = "bytes, or a binary size such as 500G or 2T (1G = 1024^3 bytes)"
# The disk budget is always bounded, and the download budget protects the
# operator's own connection. Upload caps are opt-in: none unless one is given.
BUDGETS = (
    ("disk-bytes", 100 * 1024**3, "total retained-byte budget"),
    ("ingress-daily", 50 * 1024**3, "download reservation per UTC day"),
)
UPLOAD_CAPS = (
    ("egress-daily", "optional upload cap per UTC day"),
    ("egress-monthly", "optional upload cap per UTC month"),
)
CAP_HELP = (
    'a size such as 50G, or "none" for no cap (the default for a new mirror; '
    "uploads are still counted, and uncapped serving can use a lot of "
    "bandwidth, which costs money on metered hosting)"
)
BINDS = ["127.0.0.1", "::1", "0.0.0.0", "::"]  # noqa: S104 - explicit choices


def _policy_arguments(command: argparse.ArgumentParser, *, config: bool) -> None:
    """Shared listener, budget and announcement flags; config defaults to keep."""
    command.add_argument(
        "--name",
        dest="mirror_name",
        default=None,
        help="mirror label (default: this host's name)",
    )
    for flag, default, purpose in BUDGETS:
        command.add_argument(
            "--" + flag,
            type=_size,
            default=None if config else default,
            help=f"{purpose}; {SIZE_HELP}",
        )
    for flag, purpose in UPLOAD_CAPS:
        command.add_argument(
            "--" + flag,
            type=_cap,
            default=None,
            help=f"{purpose}; {CAP_HELP}"
            + ("; unchanged unless given" if config else ""),
        )
    command.add_argument(
        "--bind",
        dest="mirror_bind",
        choices=BINDS,
        default=None if config else "127.0.0.1",
        help="listener address; loopback behind a TLS reverse proxy is intended",
    )
    command.add_argument(
        "--port", dest="mirror_port", type=int, default=None if config else 8080
    )
    command.add_argument(
        "--public-url", help="announced HTTPS base URL of this mirror (http:// warns)"
    )
    command.add_argument("--contact", help="optional contact in the mirror card")
    nostr = command.add_mutually_exclusive_group()
    nostr.add_argument(
        "--announce-nostr",
        dest="announce_nostr",
        action="store_true",
        default=None,
        help="also publish the mirror card to public Nostr relays",
    )
    dht = command.add_mutually_exclusive_group()
    dht.add_argument(
        "--announce-dht",
        dest="announce_dht",
        action="store_true",
        default=None,
        help="also announce a plain-HTTP public URL on the BitTorrent DHT",
    )
    if config:
        nostr.add_argument(
            "--no-announce-nostr", dest="announce_nostr", action="store_false"
        )
        dht.add_argument("--no-announce-dht", dest="announce_dht", action="store_false")
        command.add_argument(
            "--clear-public-url",
            action="store_true",
            help="stop announcing and remove this mirror's peers document",
        )
    command.add_argument(
        "--confirm-public-announcement",
        action="store_true",
        help="acknowledge that Nostr/DHT announcing publishes this address",
    )


def register_mirror_commands(
    add_parser: Callable[..., argparse.ArgumentParser],
) -> None:
    """Register explicit init/follow/run/retention and separate resume-grant actions."""
    mirror = add_parser("mirror", help="operate a standalone persistent HTTP mirror")
    actions = mirror.add_subparsers(dest="mirror_action", required=True)
    for name in (
        "init",
        "config",
        "quickstart",
        "follow",
        "retain",
        "withhold",
        "release",
        "http",
        "plan",
        "status",
        "run",
        "pause",
        "resume",
        "leave",
        "grant-resume",
        "revoke-resume",
        "pin",
        "unpin",
        "block",
        "unblock",
        "retry",
        "acknowledge",
        "unwithdraw",
        "prune",
        "transports",
        "export",
        "selection",
        "inspect",
        "export-kit",
        "recover",
        "import-starter",
    ):
        command = actions.add_parser(
            name,
            help={
                "acknowledge": "acknowledge a catalog withdrawal advisory and "
                "keep serving the copy (block it to stop serving)",
                "unwithdraw": "deprecated alias of acknowledge",
                "import-starter": "one-time import of a signed starter list's "
                "bundles as your own local selections (no follow relationship)",
            }.get(name),
        )
        command.add_argument("--dir", dest="mirror_dir", type=Path, required=True)
        if name == "recover":
            command.add_argument("--progress", action="store_true")
        if name == "http":
            http = command.add_mutually_exclusive_group(required=True)
            http.add_argument("--enable", dest="http_enabled", action="store_true")
            http.add_argument("--disable", dest="http_enabled", action="store_false")
        if name == "export":
            command.add_argument(
                "--kind", dest="export_kind", choices=["peer", "torrent"], required=True
            )
            command.add_argument(
                "--output", dest="export_output", type=Path, required=True
            )
        if name == "export-kit":
            command.add_argument(
                "--output", dest="export_output", type=Path, required=True
            )
        if name == "transports":
            command.add_argument("--ipfs", dest="ipfs_binary")
            command.add_argument("--ipfs-sha256")
            command.add_argument("--torrent", action="store_true")
            command.add_argument("--bootstrap", action="append", default=[])
            command.add_argument("--allow-private-peers", action="store_true")
            command.add_argument("--max-active", type=int, default=2)
            command.add_argument("--session-seconds", type=int, default=3600)
            command.add_argument("--session-bytes", type=int, default=64 * 1024**2)
            command.add_argument("--upload-rate", type=int, default=1024**2)
            command.add_argument(
                "--confirm-public-transports", action="store_true", required=True
            )
        if name in ("init", "config", "quickstart"):
            _policy_arguments(command, config=name == "config")
        if name == "config":
            command.add_argument(
                "--share",
                dest="intake_share",
                choices=["all", "chosen"],
                default=None,
                help="standing public intake: every licence-eligible local copy "
                "joins under the restart grant (all), or each copy is consented "
                "to separately (chosen); changing it clears the restart grant",
            )
            command.add_argument(
                "--licenses",
                "--licences",
                dest="intake_licenses",
                default=None,
                help="comma-separated licence identifiers the standing intake "
                "admits (default: the default families)",
            )
            command.add_argument(
                "--licence-families",
                "--license-families",
                dest="licence_families",
                default=None,
                help="comma-separated licence families to include: "
                + ", ".join(family.id for family in definition().families),
            )
            command.add_argument(
                "--clear-intake",
                action="store_true",
                help="remove the standing intake policy (clears the restart grant)",
            )
        if name in ("init", "config"):
            command.add_argument(
                "--serve-start",
                type=int,
                default=None if name == "config" else 0,
                help="UTC hour inclusive",
            )
            command.add_argument(
                "--serve-end",
                type=int,
                default=None if name == "config" else 0,
                help="UTC hour exclusive; equal means all day",
            )
            command.add_argument(
                "--refresh-seconds",
                type=int,
                default=None if name == "config" else 21600,
            )
        if name in ("config", "quickstart"):
            command.add_argument(
                "--follow-withdrawals",
                choices=["on", "off"],
                default=None,
                help="on: stop serving copies a followed catalog withdrew (your "
                "choice, never a publisher's); off (default): withdrawal is only "
                "an advisory in status. Changing it clears the restart grant"
                + (
                    "; unchanged unless given"
                    if name == "config"
                    else "; an existing mirror keeps its setting unless given"
                ),
            )
        if name == "quickstart":
            command.add_argument(
                "--collection",
                type=Path,
                default=None,
                help="reviewable collection file (sovereign.mirror-collection.v1); "
                "optional with --starter-list",
            )
            command.add_argument(
                "--resume-for",
                type=_duration,
                default=180 * 86400,
                help="standing restart grant, at most 366d (default 180d)",
            )
            command.add_argument(
                "--max-bytes",
                dest="model_max_bytes",
                type=_size,
                default=None,
                help="per-model limit (default: the collection's, else disk budget)",
            )
            command.add_argument(
                "--plan-only",
                action="store_true",
                help="print the review and change nothing",
            )
            command.add_argument(
                CONSENT_FLAG,
                dest="consent_public_serving",
                action="store_true",
                help="consent non-interactively to the printed review",
            )
            command.add_argument(
                "--run",
                dest="run_after",
                action="store_true",
                help="then serve under the new restart grant until stopped",
            )
        if name in ("quickstart", "import-starter"):
            _starter_arguments(command, quickstart=name == "quickstart")
        if name == "import-starter":
            command.add_argument(
                "--plan-only",
                action="store_true",
                help="print the review and change nothing",
            )
            command.add_argument(
                STARTER_CONSENT_FLAG,
                dest="accept_starter",
                action="store_true",
                help="consent non-interactively to the printed import review",
            )
            for flag in ("allow-http", "allow-private"):
                command.add_argument(
                    "--" + flag,
                    action="store_true",
                    help="permit plaintext or private-address mirror hints",
                )
            command.add_argument(
                "--staging",
                dest="starter_staging",
                type=Path,
                default=None,
                help="download staging directory on the mirror's filesystem "
                "(default: beside the mirror directory)",
            )
        if name in ("follow", "retain"):
            command.add_argument(
                "--bundle-trust", dest="mirror_trust", type=Path, required=True
            )
            command.add_argument("--terms-file", type=Path, required=True)
            command.add_argument(
                "--confirm-redistribution", action="store_true", required=True
            )
            command.add_argument(
                "--max-bytes",
                dest="model_max_bytes",
                type=_size,
                default=None,
                help="per-model limit (default: disk budget less headroom); "
                + SIZE_HELP,
            )
        if name == "follow":
            command.add_argument("--source-file", type=Path, required=True)
            command.add_argument("--expected-bootstrap-id", required=True)
            for flag in ("allow-http", "allow-private", "allow-local"):
                command.add_argument("--" + flag, action="store_true")
        if name == "retain":
            command.add_argument(
                "--store", dest="local_store", type=Path, required=True
            )
            command.add_argument(
                "--confirm-reintake",
                action="store_true",
                help="separately authorize copying a removed exact selection again",
            )
        if name == "run":
            choice = command.add_mutually_exclusive_group(required=True)
            choice.add_argument(
                "--public",
                action="store_true",
                help="fresh manual public grant, at most 24 hours",
            )
            choice.add_argument(
                "--resume",
                action="store_true",
                help="require the separate standing resume grant",
            )
            command.add_argument("--seconds", type=int, default=3600)
            command.add_argument(
                "--observe",
                dest="observe_mirror",
                action="store_true",
                help="emit bounded live controller activity without peer history",
            )
        if name in (
            "pin",
            "unpin",
            "block",
            "unblock",
            "withhold",
            "release",
            "retry",
            "acknowledge",
            "unwithdraw",
            "prune",
            "export",
            "retain",
            "selection",
            "inspect",
            "export-kit",
            "recover",
        ):
            command.add_argument("--manifest", dest="mirror_manifest", required=True)
        if name == "prune":
            command.add_argument("--confirm-manifest", required=True)
        if name == "retry":
            command.add_argument(
                "--adopt-source-grant",
                action="store_true",
                help="replace this selection's keys/terms with its source's current "
                "grant (after re-following); stop the controller first",
            )
        if name == "grant-resume":
            expiry = command.add_mutually_exclusive_group(required=True)
            expiry.add_argument(
                "--expires",
                dest="resume_expires",
                type=int,
                help="UTC Unix seconds, within 366 days",
            )
            expiry.add_argument(
                "--for",
                dest="resume_for",
                type=_duration,
                help="duration such as 365d, at most 366d",
            )
            command.add_argument("--confirm-resume", action="store_true", required=True)


def _starter_arguments(command: argparse.ArgumentParser, *, quickstart: bool) -> None:
    """The signed starter list, its tier and its independently obtained ID."""
    prefix = "starter-" if quickstart else ""
    command.add_argument(
        "--starter-list" if quickstart else "--list",
        dest="starter_list",
        type=Path,
        required=not quickstart,
        default=None,
        help="signed starter list (sovereign.starter-list.v1); a one-time import"
        + (", optional" if quickstart else ""),
    )
    command.add_argument(
        f"--{prefix}tier",
        dest="starter_tier",
        choices=list(TIERS),
        default="small",
        help="small (default) or standard (includes small)",
    )
    command.add_argument(
        "--expected-starter-list-id" if quickstart else "--expected-list-id",
        dest="expected_list_id",
        required=not quickstart,
        default=None,
        help="the list's SHA-256 identity, obtained independently",
    )
    command.add_argument(
        f"--{prefix}list-trust",
        dest="list_trust",
        type=Path,
        default=None,
        help="optional independently enrolled keys the list signature must meet",
    )
    command.add_argument(
        f"--{prefix}mirror",
        dest="starter_mirror",
        action="append",
        default=[],
        help="extra source for the bundles (URL or local store), tried first",
    )


def _load_starter(arguments: MirrorArguments) -> tuple[StarterList, str] | None:
    """Authenticate the selected starter list against its expected ID."""
    if arguments.starter_list is None:
        if arguments.expected_list_id is not None:
            raise PreservationError("an expected starter list ID needs a list")
        return None
    if arguments.expected_list_id is None:
        raise PreservationError("a starter list needs its expected list ID")
    trust = (
        load_trust(_input(arguments.list_trust))
        if arguments.list_trust is not None
        else None
    )
    starter = verify_starter_list(
        _input(arguments.starter_list), arguments.expected_list_id, trust=trust
    )
    return starter, arguments.expected_list_id


def _import_starter(
    arguments: MirrorArguments, control: OperationControl
) -> dict[str, JsonValue]:
    """Review a verified starter list, require consent, then import once."""
    loaded = _load_starter(arguments)
    if loaded is None:  # pragma: no cover - argparse requires the list
        raise PreservationError("a starter list is required")
    starter, list_id = loaded
    ledger = MirrorLedger(arguments.mirror_dir)
    text, summary = review_import(
        starter, plan_import(ledger, starter, list_id, arguments.starter_tier)
    )
    print(text, file=sys.stderr, flush=True)
    if arguments.plan_only:
        return {"review": summary, "consented": False, "changed": False}
    consented = arguments.accept_starter
    if not consented and sys.stdin.isatty():
        print(
            "\nType 'import' to import these models as reviewed: ",
            end="",
            file=sys.stderr,
            flush=True,
        )
        consented = sys.stdin.readline().strip() == "import"
    receipt = import_starter(
        ledger,
        starter,
        list_id,
        arguments.starter_tier,
        consented=consented,
        options=ImportOptions(
            mirrors=tuple(arguments.starter_mirror),
            allow_http=arguments.allow_http,
            allow_private=arguments.allow_private,
            staging=arguments.starter_staging,
        ),
        control=control,
    )
    return {"review": summary, "consented": True, **receipt}


def _input(path: Path) -> bytes:
    with path.open("rb") as stream:
        data = stream.read(MAX_STATE + 1)
    if len(data) > MAX_STATE:
        raise PreservationError("mirror input exceeds its bound")
    return data


def _observation(status: MirrorObservation) -> None:
    print(
        json.dumps(
            {"event": "mirror_status", "status": status.model_dump(mode="json")}
        ),
        flush=True,
    )


def _prepared(port: int | None) -> None:
    receipt: dict[str, JsonValue] = {"ready": True, "port": port, "anonymous": False}
    if port is None:
        receipt["http_enabled"] = False
    print(json.dumps(receipt), flush=True)


def _announce(
    arguments: MirrorArguments, current: AnnouncePolicy | None
) -> AnnouncePolicy | None:
    """Combine announcement flags with the current policy; opt-ins need a URL."""
    if getattr(arguments, "clear_public_url", False):
        if (
            arguments.public_url
            or arguments.contact
            or arguments.announce_nostr
            or arguments.announce_dht
        ):
            raise PreservationError("clearing the public URL takes no other announce")
        return None
    url = base_url(arguments.public_url) if arguments.public_url else None
    if current is None and url is None:
        if arguments.contact or arguments.announce_nostr or arguments.announce_dht:
            raise PreservationError("announcement options need --public-url")
        return None
    previous = current or AnnouncePolicy(public_url=url or "")
    nostr = (
        previous.nostr if arguments.announce_nostr is None else arguments.announce_nostr
    )
    dht = previous.dht if arguments.announce_dht is None else arguments.announce_dht
    newly_public = (nostr and not previous.nostr) or (dht and not previous.dht)
    if current is None:
        newly_public = nostr or dht
    if newly_public and not arguments.confirm_public_announcement:
        raise PreservationError(
            DISCLOSURE + " Add --confirm-public-announcement to proceed."
        )
    return AnnouncePolicy(
        public_url=url or previous.public_url,
        contact=arguments.contact if arguments.contact else previous.contact,
        nostr=nostr,
        dht=dht,
    )


def _warnings(announce: AnnouncePolicy | None) -> list[JsonValue]:
    if announce is not None and announce.public_url.startswith("http://"):
        return ["public URL uses plain http://; prefer https:// behind a TLS proxy"]
    return []


def _intake(
    arguments: MirrorArguments, current: IntakePolicy | None
) -> IntakePolicy | None:
    """Combine intake flags with the current policy; withheld copies are kept."""
    if arguments.clear_intake:
        if (
            arguments.intake_share
            or arguments.intake_licenses
            or (arguments.licence_families)
        ):
            raise PreservationError("clearing the intake policy takes no other choice")
        return None
    if not (
        arguments.intake_share
        or arguments.intake_licenses
        or arguments.licence_families
    ):
        return current
    licences: list[str] = []
    if arguments.licence_families:
        families = {family.id: family for family in definition().families}
        for name in arguments.licence_families.split(","):
            family = families.get(name.strip())
            if family is None:
                raise PreservationError(f"unknown licence family: {name.strip()}")
            licences += [licence.id for licence in family.licences]
    if arguments.intake_licenses:
        licences += [
            item for item in arguments.intake_licenses.split(",") if item.strip()
        ]
    if not licences:
        licences = list(current.licenses if current else DEFAULT_LICENSES)
    share = (
        ("eligible" if arguments.intake_share == "all" else "chosen")
        if arguments.intake_share
        else current.share
        if current
        else "eligible"
    )
    return IntakePolicy(
        share=share,
        licenses=identified_licences(licences),
        definition=LICENCE_DEFINITION_VERSION,
        withheld=current.withheld if current else (),
    )


def _model_limit(arguments: MirrorArguments, ledger: MirrorLedger) -> int:
    """An explicit per-model limit, else what the disk budget could ever admit."""
    if arguments.model_max_bytes is not None:
        return arguments.model_max_bytes
    return derived_model_limit(ledger.read().disk_bytes)


def _quickstart(
    arguments: MirrorArguments, control: OperationControl
) -> dict[str, JsonValue]:
    """Review one collection and/or starter list, require consent, then set up.

    A starter list is imported once after setup (it downloads), then the
    restart grant is recorded again so it covers the imported selections. An
    entry that fails to import is reported; re-running quickstart retries it.
    """
    if arguments.collection is None and arguments.starter_list is None:
        raise PreservationError("quickstart needs --collection or --starter-list")
    collection, document = (
        load_collection(arguments.collection)
        if arguments.collection is not None
        else (None, None)
    )
    starter = _load_starter(arguments)
    # Quickstart states the whole policy: no public URL means no announcement.
    announce = _announce(arguments, None)
    # Upload caps are opt-in. An existing mirror keeps the caps it has unless
    # a value (or "none") is given; a new one has none unless one is given.
    existing = (
        MirrorLedger(arguments.mirror_dir).read()
        if (arguments.mirror_dir / "mirror.json").exists()
        else None
    )
    daily, monthly = (
        _resolved_cap(arguments.egress_daily)
        if arguments.egress_daily is not None or existing is None
        else existing.egress_daily,
        _resolved_cap(arguments.egress_monthly)
        if arguments.egress_monthly is not None or existing is None
        else existing.egress_monthly,
    )
    settings = QuickstartSettings(
        name=arguments.mirror_name or default_name(),
        disk_bytes=arguments.disk_bytes or BUDGETS[0][1],
        ingress_daily=arguments.ingress_daily or BUDGETS[1][1],
        egress_daily=daily,
        egress_monthly=monthly,
        bind=arguments.mirror_bind or "127.0.0.1",
        port=8080 if arguments.mirror_port is None else arguments.mirror_port,
        announce=announce,
        resume_seconds=arguments.resume_for or 180 * 86400,
        max_bytes=arguments.model_max_bytes,
        # Off unless chosen; an existing mirror keeps the choice it has.
        follow_withdrawals=follow == "on"
        if (follow := getattr(arguments, "follow_withdrawals", None)) is not None
        else existing is not None and existing.follow_withdrawals,
    )
    text, summary = review(collection, document, settings)
    if starter is not None:
        starter_text, starter_summary = _starter_review(
            arguments, existing, starter, settings.disk_bytes
        )
        text += "\n\n" + starter_text
        summary["starter"] = starter_summary
    print(text, file=sys.stderr, flush=True)
    if arguments.plan_only:
        return {
            "review": summary,
            "consented": False,
            "changed": False,
            "network_started": False,
        }
    consented = arguments.consent_public_serving
    if not consented and sys.stdin.isatty():
        print(
            "\nType 'serve' to consent to public serving as reviewed: ",
            end="",
            file=sys.stderr,
            flush=True,
        )
        consented = sys.stdin.readline().strip() == "serve"
    ledger, receipt = apply_quickstart(
        arguments.mirror_dir, collection, document, settings, consented=consented
    )
    result: dict[str, JsonValue] = {
        "review": summary,
        "consented": True,
        **receipt,
        "mirror_key": mirror_key_id(ledger) if announce else None,
    }
    if starter is not None:
        result["starter"] = import_starter(
            ledger,
            starter[0],
            starter[1],
            arguments.starter_tier,
            consented=True,
            options=ImportOptions(
                mirrors=tuple(arguments.starter_mirror), keep_going=True
            ),
            control=control,
        )
        # The import adds selections; grant again so the grant covers them.
        expires = receipt["restart_expires"]
        if isinstance(expires, int):
            grant_restart(ledger, expires)
    if arguments.run_after:
        result["run"] = run_mirror(
            ledger, resume=True, control=control, prepared=_prepared
        )
    return result


def _starter_review(
    arguments: MirrorArguments,
    existing: MirrorState | None,
    starter: tuple[StarterList, str],
    disk_bytes: int,
) -> tuple[str, dict[str, JsonValue]]:
    """Review a starter import against the disk budget quickstart will set."""
    if existing is not None:
        ledger = MirrorLedger(arguments.mirror_dir)
        plan = plan_import(ledger, starter[0], starter[1], arguments.starter_tier)
        plan = StarterPlan(
            list_id=plan.list_id,
            tier=plan.tier,
            entries=plan.entries,
            status=plan.status,
            new_bytes=plan.new_bytes,
            usage=plan.usage,
            disk_bytes=disk_bytes,
        )
    else:
        entries = select_tier(starter[0], arguments.starter_tier)
        plan = StarterPlan(
            list_id=starter[1],
            tier=arguments.starter_tier,
            entries=entries,
            status={entry.entry_id: "new" for entry in entries},
            new_bytes=sum(entry.size_bytes for entry in entries),
            usage=0,
            disk_bytes=disk_bytes,
        )
    if not plan.fits:
        raise PreservationError(
            "starter import exceeds the disk budget; raise --disk-bytes or "
            "choose a smaller tier"
        )
    return review_import(starter[0], plan)


def run_mirror_command(
    arguments: MirrorArguments, control: OperationControl
) -> dict[str, JsonValue]:
    """Execute exactly one local mirror action; init/follow/plan never connect."""
    action = arguments.mirror_action
    if action == "init":
        announce = _announce(arguments, None)
        ledger = MirrorLedger.initialize(
            arguments.mirror_dir,
            name=arguments.mirror_name or default_name(),
            disk_bytes=arguments.disk_bytes,
            ingress_daily=arguments.ingress_daily,
            egress_daily=_resolved_cap(arguments.egress_daily),
            egress_monthly=_resolved_cap(arguments.egress_monthly),
            bind=arguments.mirror_bind,
            port=arguments.mirror_port,
            serve_start=arguments.serve_start,
            serve_end=arguments.serve_end,
            refresh_seconds=arguments.refresh_seconds,
            announce=announce,
        )
        return {
            "initialized": True,
            "network_started": False,
            "warnings": _warnings(announce),
            "policy": plan(ledger),
        }
    if action == "quickstart":
        return _quickstart(arguments, control)
    if action == "import-starter":
        return _import_starter(arguments, control)
    ledger = MirrorLedger(arguments.mirror_dir)
    if action == "config":
        before = ledger.read()
        announce = _announce(arguments, before.announce)
        changes: dict[str, object] = {
            key: value
            for key, value in {
                "name": arguments.mirror_name,
                "disk_bytes": arguments.disk_bytes,
                "ingress_daily": arguments.ingress_daily,
                "egress_daily": arguments.egress_daily,
                "egress_monthly": arguments.egress_monthly,
                "bind": arguments.mirror_bind,
                "port": arguments.mirror_port,
                "serve_start": arguments.serve_start,
                "serve_end": arguments.serve_end,
                "refresh_seconds": arguments.refresh_seconds,
                "follow_withdrawals": None
                if arguments.follow_withdrawals is None
                else arguments.follow_withdrawals == "on",
            }.items()
            if value is not None
        }
        for key in ("egress_daily", "egress_monthly"):
            if changes.get(key) == "none":
                changes[key] = None  # Explicitly no upload cap.
        if announce != before.announce:
            changes["announce"] = announce
        intake = _intake(arguments, before.intake)
        # Preflight: settings, caps and the intake policy validate together
        # before anything is written; a later refusal restores the ledger.
        MirrorState.model_validate(
            upgrade_profile(
                before.model_copy(update={**changes, "intake": intake})
            ).model_dump()
        )
        try:
            after = configure_mirror(ledger, changes) if changes else before
            if intake != before.intake:
                after = configure_intake(ledger, intake)
                changes["intake"] = True
        except BaseException:
            if ledger.read() != before:
                restored = before
                with ledger.service_lock():
                    ledger.update(lambda _current: restored)
            raise
        return {
            "configured": [cast(JsonValue, key) for key in sorted(changes)],
            "network_started": False,
            "restart_grant_invalidated": before.restart is not None
            and after.restart is None,
            "warnings": _warnings(after.announce),
            "policy": plan(ledger),
        }
    if action in ("plan", "status"):
        return plan(ledger)
    if action == "selection":
        selected = selection_status(ledger, arguments.mirror_manifest)
        return {
            "manifest": arguments.mirror_manifest,
            "selected": selected is not None,
            "state": selected.state if selected is not None else None,
            "origin": selected.origin if selected is not None else None,
            "pinned": selected.pinned if selected is not None else False,
            "blocked": selected.blocked if selected is not None else False,
            "network_started": False,
        }
    if action == "inspect":
        metadata = inspect_selection(ledger, arguments.mirror_manifest)
        return {
            "manifest": arguments.mirror_manifest,
            "metadata": metadata.model_dump(mode="json"),
            "payload_bytes_verified": False,
            "network_started": False,
        }
    if action == "export-kit":
        kit = export_selection_kit(
            ledger,
            arguments.mirror_manifest,
            arguments.export_output,
            control=control,
        )
        return {
            "manifest": arguments.mirror_manifest,
            "members": len(kit.manifest.members),
            "kit_bytes": kit.kit_bytes,
            "exported": True,
            "network_started": False,
            "grants_imported": False,
        }
    if action == "recover":
        manifest = recover_selection(ledger, arguments.mirror_manifest, control=control)
        return {
            "manifest": arguments.mirror_manifest,
            "members": len(manifest.members),
            "state": "retained",
            "payload_bytes_verified": True,
            "network_started": False,
            "grants_imported": False,
        }
    if action == "http":
        state = configure_http(ledger, enabled=arguments.http_enabled)
        return {
            "http_enabled": state.http_enabled,
            "network_started": False,
            "restart_grant_invalidated": True,
        }
    if action == "retain":
        manifest = retain_local(
            ledger,
            arguments.local_store,
            arguments.mirror_manifest,
            SourceGrant(
                trust=load_trust(_input(arguments.mirror_trust)),
                terms=TypeAdapter(tuple[Terms, ...]).validate_json(
                    _input(arguments.terms_file)
                ),
                max_bytes=_model_limit(arguments, ledger),
            ),
            control=control,
            confirm_reintake=arguments.confirm_reintake,
        )
        return {
            "manifest": arguments.mirror_manifest,
            "members": len(manifest.members),
            "retained": True,
            "network_started": False,
            "grants_imported": False,
        }
    if action == "follow":
        terms = TypeAdapter(tuple[Terms, ...]).validate_json(
            _input(arguments.terms_file)
        )
        source = follow(
            ledger,
            _input(arguments.source_file),
            arguments.expected_bootstrap_id,
            SourceGrant(
                trust=load_trust(_input(arguments.mirror_trust)),
                terms=terms,
                max_bytes=_model_limit(arguments, ledger),
                allow_http=arguments.allow_http,
                allow_private=arguments.allow_private,
                allow_local=arguments.allow_local,
            ),
        )
        return {
            "source_id": source,
            "network_started": False,
            "model_authority": "mirror only",
        }
    if action == "run":
        return run_mirror(
            ledger,
            public=arguments.public,
            resume=arguments.resume,
            seconds=arguments.seconds,
            control=control,
            prepared=_prepared,
            observe=_observation if arguments.observe_mirror else None,
        )
    if action == "transports":
        configure(
            ledger,
            ipfs_binary=arguments.ipfs_binary,
            ipfs_sha256=arguments.ipfs_sha256,
            torrent=arguments.torrent,
            bootstrap=tuple(arguments.bootstrap),
            allow_private_peers=arguments.allow_private_peers,
            max_active=arguments.max_active,
            session_seconds=arguments.session_seconds,
            session_bytes=arguments.session_bytes,
            upload_rate=arguments.upload_rate,
        )
        return {
            "configured": True,
            "network_started": False,
            "restart_grant_invalidated": True,
        }
    if action == "export":
        data = export_reference(
            ledger, arguments.mirror_manifest, arguments.export_kind
        )
        write_reference(arguments.export_output, data)
        return {"exported": True, "network_started": False, "grants_imported": False}
    if action == "grant-resume":
        expires = (
            arguments.resume_expires
            if arguments.resume_expires is not None
            else int(time.time()) + (arguments.resume_for or 0)
        )
        state = grant_restart(ledger, expires)
        return {
            "action": action,
            "restart_expires": expires,
            "policy_id": state.restart.policy_id if state.restart else None,
            "retained_bytes_deleted": False,
            "recorded_at": int(time.time()),
        }
    if action == "retry":
        retry_selection(
            ledger,
            arguments.mirror_manifest,
            adopt_source_grant=arguments.adopt_source_grant,
        )
    elif action == "revoke-resume":
        ledger.update(lambda s: s.model_copy(update={"restart": None}))
    elif action in ("pause", "resume", "leave"):
        ledger.update(
            lambda s: s.model_copy(
                update={
                    "paused": action != "resume",
                    "left": action == "leave",
                    "restart": None if action == "leave" else s.restart,
                }
            )
        )
    elif action in ("withhold", "release"):
        withhold(ledger, arguments.mirror_manifest, withheld=action == "withhold")
    elif action == "prune":
        return TypeAdapter(dict[str, JsonValue]).validate_python(
            prune(ledger, arguments.mirror_manifest, arguments.confirm_manifest)
        )
    else:
        model_action(ledger, arguments.mirror_manifest, action)
    receipt: dict[str, JsonValue] = {
        "action": action,
        "retained_bytes_deleted": action == "prune",
        "recorded_at": int(time.time()),
    }
    if action == "unwithdraw":
        receipt["deprecated"] = "use 'mirror acknowledge'; withdrawal is advisory"
    return receipt
