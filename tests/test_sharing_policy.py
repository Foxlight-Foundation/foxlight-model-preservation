"""Licence families, opt-in upload limits and the standing intake policy."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import Capture

from foxlight_model_preservation.licences import (
    DEFAULT_LICENSES,
    KNOWN_LICENSES,
    UNIDENTIFIED,
    definition,
    definition_bytes,
    licence_status,
    normalize_license,
)
from foxlight_model_preservation.local_host.community import (
    CommunityMirror,
    CommunityPolicy,
)
from foxlight_model_preservation.mirror import (
    configure_intake,
    configure_mirror,
    withhold,
)
from foxlight_model_preservation.mirror_intake import retain_local
from foxlight_model_preservation.mirror_models import (
    IntakePolicy,
    MirrorState,
    SourceGrant,
    Terms,
)
from foxlight_model_preservation.mirror_state import (
    MirrorLedger,
    encode_state,
    grant_restart,
    policy_id,
    restart_allowed,
)
from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.signing import encode_record
from foxlight_model_preservation.storage import pack

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_TERMS = (Terms(license_id="test-only", terms_reference="LICENSE"),)


def cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - isolated test CLI, no shell
        [sys.executable, "-m", "foxlight_model_preservation.cli", *arguments],
        capture_output=True,
        text=True,
        timeout=60,
    )


def mirror(tmp_path: Path, **changes: object) -> MirrorLedger:
    config: dict[str, object] = {
        "name": "Policy fixture",
        "disk_bytes": 2 * 1024**3,
        "ingress_daily": 1024**3,
        "egress_daily": 1024**3,
        "egress_monthly": 1024**3,
        "port": 0,
    }
    config.update(changes)
    return MirrorLedger.initialize(tmp_path / "mirror", **config)


def grant(capture: Capture, licence: str = "test-only") -> SourceGrant:
    return SourceGrant(
        trust=capture.trust,
        terms=(Terms(license_id=licence, terms_reference="LICENSE"),),
    )


def intake(*licences: str, share: str = "eligible") -> IntakePolicy:
    return IntakePolicy.model_validate(
        {"share": share, "licenses": tuple(licences), "definition": 1}
    )


def another(capture: Capture, name: str) -> str:
    return pack(
        capture.recipe.model_copy(update={"name": name}),
        capture.sources,
        capture.store,
        (capture.key,),
    )


# Licence families --------------------------------------------------------------


def test_one_versioned_definition_with_hugging_face_style_defaults():
    loaded = definition()
    assert loaded.version == 1
    families = {family.id: family for family in loaded.families}
    assert list(families) == [
        "permissive",
        "attribution",
        "copyleft",
        "responsible-ai",
        "non-commercial",
        "custom",
    ]
    assert all(family.default for family in families.values())
    gated = {family for family, value in families.items() if value.acceptance}
    assert gated == {"responsible-ai", "non-commercial", "custom"}
    ids = {
        family: {licence.id for licence in value.licences}
        for family, value in families.items()
    }
    # Spellings as Hugging Face cards and Sovereign manifests use them.
    assert {"apache-2.0", "mit", "0bsd", "cc0-1.0", "zlib"} <= ids["permissive"]
    assert ids["attribution"] == {"cc-by-4.0", "cc-by-3.0"}
    assert {"gpl-3.0", "agpl-3.0", "lgpl-2.1", "mpl-2.0"} <= ids["copyleft"]
    assert "cc-by-sa-4.0" in ids["copyleft"]
    assert {"openrail", "openrail++", "bigcode-openrail-m"} <= ids["responsible-ai"]
    assert {"qwen-research", "cc-by-nc-4.0", "cc-by-nc-sa-4.0"} <= ids["non-commercial"]
    # Non-commercial says when to switch it off.
    assert "commercially" in families["non-commercial"].summary
    llama = {"llama2", "llama3", "llama3.1", "llama3.2", "llama3.3", "llama4"}
    assert llama | {"gemma", "qwen", "tongyi-qianwen"} <= ids["custom"]
    # Terms Hugging Face hosts and that allow redistribution are on by default.
    assert llama | {"gemma", "qwen", "tongyi-qianwen"} <= set(DEFAULT_LICENSES)
    assert "gpl-3.0" in DEFAULT_LICENSES and "cc-by-nc-4.0" in DEFAULT_LICENSES
    # Terms not confirmed to allow redistribution stay off, and say why.
    off = {
        licence.id: licence
        for family in loaded.families
        for licence in family.licences
        if licence.id not in DEFAULT_LICENSES
    }
    assert set(off) == {"grok2-community", "deepfloyd-if-license", "apple-amlr"}
    assert all(item.note and "redistribute" in item.note for item in off.values())
    # Every gated licence has a plain summary; EU and Gemma notes are explicit.
    for family in loaded.families:
        if family.acceptance:
            assert all(licence.summary for licence in family.licences)
    licences = {
        licence.id: licence for family in loaded.families for licence in family.licences
    }
    assert licences["llama3.2"].eu_subject == "the Llama 3.2 vision models"
    assert licences["llama4"].eu_subject == "Llama 4"
    assert licences["llama3.1"].eu_subject is None
    assert "Gemma 4 is Apache-2.0" in (licences["gemma"].note or "")
    assert KNOWN_LICENSES.isdisjoint(UNIDENTIFIED)


def test_the_library_ships_the_core_definition_byte_for_byte():
    copy = (
        ROOT / "capability/src/foxlight_preservation_library/licence_families_v1.json"
    )
    assert copy.read_bytes() == definition_bytes()


@pytest.mark.parametrize("value", ["", "unknown", "Other", " NOASSERTION ", "none"])
def test_unknown_other_and_blank_never_match(value: str):
    everything = [*KNOWN_LICENSES, value]
    assert licence_status([value], everything) == "no_license"
    assert licence_status(["mit", value], everything) == "no_license"
    with pytest.raises(ValueError, match="community_license_unidentified"):
        CommunityPolicy(licenses=("mit", value))
    with pytest.raises(ValueError, match="community_license_unidentified|normalized"):
        intake("mit", normalize_license(value))


def test_matching_is_exact_and_case_insensitive():
    assert licence_status(["GPL-3.0", "Apache 2.0"], DEFAULT_LICENSES) == "eligible"
    assert licence_status(["gpl-3.0-or-later"], DEFAULT_LICENSES) == (
        "license_not_selected"
    )
    assert licence_status(["Llama3.1"], DEFAULT_LICENSES) == "eligible"
    assert licence_status(["Grok2-Community"], DEFAULT_LICENSES) == (
        "license_not_selected"
    )
    assert licence_status(["llama3.1"], ["mit"]) == "license_not_selected"


# Opt-in upload limits ------------------------------------------------------------


def test_a_new_mirror_without_upload_caps_counts_but_never_refuses(tmp_path):
    ledger = mirror(tmp_path, egress_daily=None, egress_monthly=None)
    state = ledger.read()
    # Only the v6 profile can say "no cap"; older readers refuse it outright.
    assert state.schema_version == "foxlight.mirror-state.v6"
    assert json.loads(encode_state(state))["egress_daily"] is None
    assert ledger.reserve(50 * 1024**4) and ledger.reserve(50 * 1024**4)
    assert ledger.read().egress_month_used == 100 * 1024**4
    # The disk allowance and the download budget stay bounded.
    assert state.disk_bytes == 2 * 1024**3
    assert not ledger.reserve(2 * 1024**3, ingress=True)


def test_caps_still_refuse_when_set_and_can_be_switched_off(tmp_path):
    ledger = mirror(tmp_path)
    assert ledger.read().schema_version == "foxlight.mirror-state.v1"
    assert not ledger.reserve(2 * 1024**3)
    grant_restart(ledger, int(time.time()) + 600)
    state = configure_mirror(ledger, {"egress_daily": None, "egress_monthly": None})
    assert state.schema_version == "foxlight.mirror-state.v6"
    # Switching caps off changes the policy, so serving needs fresh consent.
    assert state.restart is None
    assert ledger.reserve(2 * 1024**3)


def test_no_cap_and_intake_need_the_v6_profile():
    base = json.loads(
        encode_state(
            MirrorState.model_validate_json(
                json.dumps(
                    {
                        "identity": {},
                        "name": "x",
                        "salt": "a" * 64,
                        "disk_bytes": 1024**3,
                        "ingress_daily": 1,
                        "egress_daily": 1,
                        "egress_monthly": 1,
                        "clock_floor": 0,
                    }
                )
            )
        )
    )
    for change in (
        {"egress_daily": None},
        {"egress_monthly": None},
        {"intake": {"share": "eligible", "licenses": ["mit"], "definition": 1}},
    ):
        for version in ("v1", "v3", "v5"):
            with pytest.raises(ValueError, match="state v6"):
                MirrorState.model_validate_json(
                    json.dumps(
                        {
                            **base,
                            **change,
                            "schema_version": "foxlight.mirror-state." + version,
                        }
                    )
                )
        MirrorState.model_validate_json(
            json.dumps({**base, **change, "schema_version": "foxlight.mirror-state.v6"})
        )


def test_released_ledgers_keep_their_bytes_and_policy_identity(tmp_path):
    ledger = mirror(tmp_path)
    state = ledger.read()
    raw = encode_state(state)
    assert b'"intake"' not in raw and b'"egress_daily":1073741824' in raw
    grant_restart(ledger, int(time.time()) + 600)
    # Reading with this release changes nothing a grant was bound to.
    assert restart_allowed(MirrorLedger(ledger.path).read())


def test_community_policy_limits_are_opt_in_and_old_files_keep_theirs():
    fresh = CommunityPolicy()
    assert fresh.daily_upload_bytes is None and fresh.monthly_upload_bytes is None
    assert fresh.schema_version == "sovereign.community-mirror.v2"
    assert "gpl-3.0" in fresh.licenses and "llama3" in fresh.licenses
    assert "grok2-community" not in fresh.licenses
    stored = CommunityPolicy.model_validate_json(
        json.dumps(
            {
                "schema_version": "sovereign.community-mirror.v1",
                "daily_upload_bytes": 20 * 1024**3,
                "monthly_upload_bytes": 300 * 1024**3,
                "licenses": ["mit"],
            }
        )
    )
    assert stored.daily_upload_bytes == 20 * 1024**3
    assert stored.licenses == ("mit",)
    with pytest.raises(ValueError, match="community_policy_version"):
        CommunityPolicy.model_validate_json(
            json.dumps(
                {
                    "schema_version": "sovereign.community-mirror.v1",
                    "daily_upload_bytes": None,
                }
            )
        )
    with pytest.raises(ValueError, match="community_upload_limit_invalid"):
        CommunityPolicy(daily_upload_bytes=10, monthly_upload_bytes=5)


def test_desktop_community_mirror_is_uncapped_until_the_owner_sets_limits(tmp_path):
    (tmp_path / "home").mkdir()
    community = CommunityMirror(tmp_path / "home", None)  # type: ignore[arg-type]
    state = community.ledger().read()
    assert state.egress_daily is None and state.egress_monthly is None
    assert state.schema_version == "foxlight.mirror-state.v6"
    read = community.read([])
    assert read["policy"]["daily_upload_bytes"] is None  # type: ignore[index]
    assert read["licence_definition"] == 1
    community.configure(
        {"daily_upload_bytes": 1024**3, "monthly_upload_bytes": 8 * 1024**3}
    )
    state = community.ledger().read()
    assert (state.egress_daily, state.egress_monthly) == (1024**3, 8 * 1024**3)
    community.configure({"daily_upload_bytes": None, "monthly_upload_bytes": None})
    assert community.ledger().read().egress_daily is None


def test_an_existing_desktop_policy_keeps_its_caps_and_licences(tmp_path):
    home = tmp_path / "home"
    (home / "sharing/community").mkdir(parents=True)
    stored = CommunityPolicy(
        schema_version="sovereign.community-mirror.v1",
        daily_upload_bytes=20 * 1024**3,
        monthly_upload_bytes=300 * 1024**3,
        licenses=("apache-2.0", "mit"),
    )
    (home / "sharing/community/policy.json").write_bytes(encode_record(stored))
    community = CommunityMirror(home, None)  # type: ignore[arg-type]
    assert community.policy() == stored
    state = community.ledger().read()
    assert (state.egress_daily, state.egress_monthly) == (20 * 1024**3, 300 * 1024**3)
    # A later change keeps the stored caps; it is saved in the v2 format.
    updated = community.configure({"port": 8744})
    assert updated.daily_upload_bytes == 20 * 1024**3
    assert updated.licenses == ("apache-2.0", "mit")
    assert updated.schema_version == "sovereign.community-mirror.v2"


# Standing intake ---------------------------------------------------------------


def test_matching_new_copies_join_under_the_same_grant(tmp_path, capture):
    ledger = mirror(tmp_path)
    configure_intake(ledger, intake("mit", "test-only"))
    expires = int(time.time()) + 3600
    grant_restart(ledger, expires)
    bound = ledger.read().restart
    assert bound is not None
    retain_local(ledger, capture.store, capture.identity, grant(capture))
    second = another(capture, "second public copy")
    retain_local(ledger, capture.store, second, grant(capture))
    state = ledger.read()
    # Admitting copies the policy covers keeps the grant and its identity.
    assert state.restart == bound and restart_allowed(state)
    assert {capture.identity, second} <= set(state.models)
    assert state.models[second].state == "retained"


def test_a_copy_beyond_the_policy_needs_fresh_consent(tmp_path, capture):
    ledger = mirror(tmp_path)
    configure_intake(ledger, intake("mit"))
    grant_restart(ledger, int(time.time()) + 3600)
    # Its licence is not selected: an explicit retain is new policy.
    retain_local(ledger, capture.store, capture.identity, grant(capture))
    assert ledger.read().restart is None
    assert not restart_allowed(ledger.read())


def test_chosen_mode_binds_each_copy_and_changing_the_policy_clears_the_grant(
    tmp_path, capture
):
    ledger = mirror(tmp_path)
    configure_intake(ledger, intake("test-only", share="chosen"))
    grant_restart(ledger, int(time.time()) + 3600)
    retain_local(ledger, capture.store, capture.identity, grant(capture))
    assert ledger.read().restart is None
    for change in (
        intake("test-only"),
        intake("test-only", "mit"),
        intake("mit"),
        None,
    ):
        grant_restart(ledger, int(time.time()) + 3600)
        before = policy_id(ledger.read())
        state = configure_intake(ledger, change)
        assert policy_id(state) != before
        assert state.restart is None, change
    # Setting the same policy again changes nothing.
    configure_intake(ledger, intake("mit"))
    grant_restart(ledger, int(time.time()) + 3600)
    assert configure_intake(ledger, intake("mit")).restart is not None


def test_withholding_narrows_without_re_scoping_the_grant(tmp_path, capture):
    ledger = mirror(tmp_path)
    configure_intake(ledger, intake("test-only"))
    retain_local(ledger, capture.store, capture.identity, grant(capture))
    grant_restart(ledger, int(time.time()) + 3600)
    bound = ledger.read().restart
    withhold(ledger, capture.identity, withheld=True)
    state = ledger.read()
    assert state.models[capture.identity].blocked
    assert state.restart == bound and restart_allowed(state)
    # A withheld copy that joins later is admitted blocked, still under the grant.
    second = another(capture, "withheld before it joined")
    withhold(ledger, second, withheld=True)
    retain_local(ledger, capture.store, second, grant(capture))
    state = ledger.read()
    assert state.models[second].blocked and restart_allowed(state)
    withhold(ledger, capture.identity, withheld=False)
    state = ledger.read()
    assert not state.models[capture.identity].blocked and restart_allowed(state)
    with pytest.raises(PreservationError, match="intake policy first"):
        (tmp_path / "other").mkdir()
        withhold(mirror(tmp_path / "other"), second, withheld=True)


def test_a_withheld_copy_a_followed_source_lists_is_admitted_blocked(tmp_path, capture):
    from test_mirror import cycle
    from test_mirror import mirror as source_mirror

    from foxlight_model_preservation.mirror import model_action, public_selection
    from foxlight_model_preservation.operations import OperationControl
    from foxlight_model_preservation.storage import manifest_path

    def served() -> bool:
        with ledger.service_lock():
            allowed, _ = public_selection(ledger, OperationControl())
        return "/" + manifest_path(capture.identity) in allowed

    ledger, _publisher = source_mirror(tmp_path, capture)
    configure_intake(ledger, intake("test-only"))
    withhold(ledger, capture.identity, withheld=True)
    cycle(ledger)
    held = ledger.read().models[capture.identity]
    # Selected like any listed copy, but blocked: not fetched, never served.
    assert held.origin == "source" and held.blocked
    assert held.state == "pending"
    assert not (ledger.path / "store" / manifest_path(capture.identity)).exists()
    assert not served()
    # A state that would serve a withheld copy is refused outright.
    with pytest.raises(ValueError, match="withheld mirror selection must stay blocked"):
        ledger.update(
            lambda state: state.model_copy(
                update={
                    "models": {
                        **state.models,
                        capture.identity: state.models[capture.identity].model_copy(
                            update={"blocked": False}
                        ),
                    }
                }
            )
        )
    # An explicit unblock is the owner sharing it again: it leaves the list too.
    model_action(ledger, capture.identity, "unblock")
    state = ledger.read()
    assert state.intake is not None and capture.identity not in state.intake.withheld
    cycle(ledger)
    assert ledger.read().models[capture.identity].state == "retained"
    assert served()


def test_existing_grants_are_not_silently_re_scoped(tmp_path, capture):
    """An existing mirror keeps its grant; adopting a standing policy needs consent."""
    ledger = mirror(tmp_path)
    retain_local(ledger, capture.store, capture.identity, grant(capture))
    grant_restart(ledger, int(time.time()) + 3600)
    before = ledger.read()
    assert before.intake is None and restart_allowed(before)
    # The new release reads it unchanged: same identity, grant still valid.
    assert policy_id(MirrorLedger(ledger.path).read()) == before.restart.policy_id
    # Adopting a policy that would admit new copies changes what the grant
    # covers, so the old grant is cleared and serving waits for consent.
    after = configure_intake(ledger, intake("test-only"))
    assert after.schema_version == "foxlight.mirror-state.v6"
    assert after.restart is None and not restart_allowed(after)
    grant_restart(ledger, int(time.time()) + 3600)
    assert restart_allowed(ledger.read())


def test_public_intake_only_still_applies(tmp_path, capture):
    ledger = mirror(tmp_path)
    configure_intake(ledger, intake("test-only"))
    private = capture.recipe.model_copy(
        update={
            "name": "private copy",
            "members": tuple(
                member.model_copy(
                    update={
                        "intake": member.intake.model_copy(
                            update={"audience": "private"}
                        )
                    }
                )
                for member in capture.recipe.members
            ),
        }
    )
    identity = pack(private, capture.sources, capture.store, (capture.key,))
    with pytest.raises(PreservationError):
        retain_local(ledger, capture.store, identity, grant(capture))
    assert identity not in ledger.read().models


def test_mirrors_serve_only_bundles_that_carry_their_licence_notices(tmp_path, capture):
    """Public intake binds every member to notice files, and serving includes them."""
    from foxlight_model_preservation.mirror import public_selection
    from foxlight_model_preservation.models import Intake
    from foxlight_model_preservation.operations import OperationControl
    from foxlight_model_preservation.storage import manifest_path, object_path

    with pytest.raises(ValueError):
        Intake(
            audience="public",
            license_id="llama3.1",
            terms_reference="LICENSE",
            decision="fixture",
            notice_paths=(),
        )
    # A notice that is not one of the member's files can't be packed.
    missing = capture.recipe.model_copy(
        update={
            "name": "notice not carried",
            "members": tuple(
                member.model_copy(
                    update={
                        "intake": member.intake.model_copy(
                            update={"notice_paths": ("NOT-CARRIED",)}
                        )
                    }
                )
                for member in capture.recipe.members
            ),
        }
    )
    with pytest.raises((ValueError, OSError)):
        pack(missing, capture.sources, capture.store, (capture.key,))
    ledger = mirror(tmp_path)
    configure_intake(ledger, intake("test-only"))
    manifest = retain_local(ledger, capture.store, capture.identity, grant(capture))
    allowed, _owners = public_selection(ledger, OperationControl())
    assert "/" + manifest_path(capture.identity) in allowed
    for member in manifest.members:
        notices = [
            file for file in member.files if file.path in member.intake.notice_paths
        ]
        assert notices, member.member_id
        for file in notices:
            assert "/" + object_path(file.sha256) in allowed


# Command line ----------------------------------------------------------------------


def test_cli_init_is_uncapped_and_config_sets_a_standing_policy(tmp_path):
    target = tmp_path / "mirror"
    init = cli("mirror", "init", "--dir", str(target), "--disk-bytes", "1G")
    assert init.returncode == 0, init.stderr
    state = MirrorLedger(target).read()
    assert state.egress_daily is None and state.egress_monthly is None
    assert state.disk_bytes == 1024**3 and state.ingress_daily == 50 * 1024**3
    configured = cli(
        "mirror",
        "config",
        "--dir",
        str(target),
        "--share",
        "all",
        "--licence-families",
        "permissive,copyleft",
        "--licenses",
        "Llama3.1",
    )
    assert configured.returncode == 0, configured.stderr
    assert json.loads(configured.stdout)["configured"] == ["intake"]
    state = MirrorLedger(target).read()
    assert state.intake is not None and state.intake.share == "eligible"
    assert {"mit", "gpl-3.0", "llama3.1"} <= set(state.intake.licenses)
    assert "cc-by-4.0" not in state.intake.licenses
    capped = cli(
        "mirror",
        "config",
        "--dir",
        str(target),
        "--egress-daily",
        "10G",
        "--egress-monthly",
        "100G",
    )
    assert capped.returncode == 0, capped.stderr
    assert MirrorLedger(target).read().egress_monthly == 100 * 1024**3
    uncapped = cli("mirror", "config", "--dir", str(target), "--egress-daily", "none")
    assert uncapped.returncode == 0, uncapped.stderr
    state = MirrorLedger(target).read()
    assert state.egress_daily is None and state.egress_monthly == 100 * 1024**3
    for wrong in (
        ("--licence-families", "everything"),
        ("--licenses", "unknown"),
        ("--clear-intake", "--share", "all"),
    ):
        refused = cli("mirror", "config", "--dir", str(target), *wrong)
        assert refused.returncode != 0
    assert (
        cli("mirror", "config", "--dir", str(target), "--clear-intake").returncode == 0
    )
    assert MirrorLedger(target).read().intake is None


def test_quickstart_is_uncapped_when_new_and_keeps_an_existing_mirrors_caps(
    tmp_path, capture
):
    from test_mirror_headless import collection

    path = collection(tmp_path, capture)
    target = tmp_path / "quick"
    base = (
        "mirror",
        "quickstart",
        "--dir",
        str(target),
        "--collection",
        str(path),
        "--name",
        "Quick",
        "--disk-bytes",
        "2G",
        "--yes-i-consent-to-public-serving",
    )
    first = cli(*base)
    assert first.returncode == 0, first.stderr
    assert "Upload cap:      none" in first.stderr
    assert "can use a lot of bandwidth" in first.stderr
    state = MirrorLedger(target).read()
    assert state.egress_daily is None and state.egress_monthly is None
    assert state.ingress_daily == 50 * 1024**3 and state.disk_bytes == 2 * 1024**3
    capped = cli(*base, "--egress-daily", "5G", "--egress-monthly", "50G")
    assert capped.returncode == 0, capped.stderr
    # A later start without limits keeps the caps this mirror already has.
    again = cli(*base)
    assert again.returncode == 0, again.stderr
    assert json.loads(again.stdout)["policy_changed"] is False
    state = MirrorLedger(target).read()
    assert (state.egress_daily, state.egress_monthly) == (5 * 1024**3, 50 * 1024**3)
    removed = cli(*base, "--egress-daily", "none", "--egress-monthly", "none")
    assert removed.returncode == 0, removed.stderr
    assert MirrorLedger(target).read().egress_monthly is None


def test_quickstart_and_config_preflight_caps_and_intake_before_writing(
    tmp_path, capture
):
    from test_mirror_headless import collection

    path = collection(tmp_path, capture)
    # A new mirror's settings, "no cap" included, validate before any directory.
    target = tmp_path / "new"
    refused = cli(
        "mirror",
        "quickstart",
        "--dir",
        str(target),
        "--collection",
        str(path),
        "--disk-bytes",
        "1M",
        "--yes-i-consent-to-public-serving",
    )
    assert refused.returncode != 0 and not target.exists()
    # An existing capped (v1) mirror switches caps off through the preflight.
    (tmp_path / "old").mkdir()
    capped = mirror(tmp_path / "old")
    assert capped.read().schema_version == "foxlight.mirror-state.v1"
    before = capped.read()
    # Settings, caps and intake are checked together; nothing is half applied.
    bad = cli(
        "mirror",
        "config",
        "--dir",
        str(capped.path),
        "--egress-daily",
        "none",
        "--share",
        "all",
        "--port",
        "70000",
    )
    assert bad.returncode != 0
    assert capped.read() == before
    good = cli(
        "mirror",
        "config",
        "--dir",
        str(capped.path),
        "--egress-daily",
        "none",
        "--egress-monthly",
        "none",
        "--share",
        "all",
    )
    assert good.returncode == 0, good.stderr
    after = capped.read()
    assert after.egress_daily is None and after.intake is not None
    assert after.schema_version == "foxlight.mirror-state.v6"
