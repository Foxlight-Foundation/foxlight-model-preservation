"""Web-seed policy checks that need no native BitTorrent binding."""

from __future__ import annotations

import pytest

from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.torrent import _web_seed  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize(
    "seed",
    [
        "http://mirror.example/models/",
        "http://192.168.1.10/models/",
        "http://10.0.0.5:8080/",
        "http://127.0.0.1.attacker.example/",
    ],
)
def test_local_http_grant_refuses_non_loopback_web_seeds(seed):
    with pytest.raises(PreservationError):
        _web_seed(seed, True)


@pytest.mark.parametrize(
    "seed", ["http://127.0.0.1:8000/", "http://localhost/m", "http://[::1]:9/"]
)
def test_local_http_grant_accepts_loopback_web_seeds(seed):
    assert _web_seed(seed, True).endswith("/")
    with pytest.raises(PreservationError):
        _web_seed(seed, False)
