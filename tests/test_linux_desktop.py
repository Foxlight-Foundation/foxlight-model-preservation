"""Linux ticket confinement and ambient-runtime separation without a GUI import."""

import importlib.util
from pathlib import Path

import pytest

SHELL = Path(__file__).resolve().parents[1] / "desktop/linux/sovereign-linux.py"
spec = importlib.util.spec_from_file_location("sovereign_linux", SHELL)
assert spec is not None and spec.loader is not None
shell = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shell)
BASE = "http://127.0.0.1:43123/s/" + "a" * 64 + "/"


@pytest.mark.parametrize(
    "address",
    [BASE, BASE + "api/library?offset=0", BASE + "#library"],
)
def test_navigation_keeps_exact_origin_and_ticket(address):
    assert shell.permitted(BASE, address)


@pytest.mark.parametrize(
    "address",
    [
        "https://example.invalid/",
        BASE.replace(":43123", ":43124"),
        BASE.replace("127.0.0.1", "localhost"),
        BASE.replace("127.0.0.1", "127.0.0.1.example.invalid"),
        BASE.replace("/s/", "/another/"),
        BASE[:-1] + "suffix",
        BASE.replace("a" * 64, "b" * 64),
        BASE + "../other-ticket/",
        BASE + "%2e%2e/other-ticket/",
        BASE + "api%2f..%2fother/",
        BASE + "api%5c..%5cother/",
        BASE.replace("http://", "http://attacker@"),
        BASE.replace(":43123", ":70000"),
        "file:///etc/passwd",
        "javascript:alert(1)",
    ],
)
def test_other_origins_tickets_and_schemes_refuse(address):
    assert not shell.permitted(BASE, address)


def test_child_omits_ambient_python_proxy_and_loader(monkeypatch):
    for name in ("PYTHONPATH", "PYTHONHOME", "LD_PRELOAD", "HTTPS_PROXY"):
        monkeypatch.setenv(name, "/untrusted")
    monkeypatch.setenv("XDG_DATA_HOME", "/owned/data")
    result = shell.owner_environment()
    assert result["XDG_DATA_HOME"] == "/owned/data"
    assert result["PATH"] == "/usr/bin:/bin"
    for name in ("PYTHONPATH", "PYTHONHOME", "LD_PRELOAD", "HTTPS_PROXY"):
        assert name not in result


def test_installer_cache_paths_and_stale_stub_records_are_not_packaged(
    tmp_path, monkeypatch
):
    monkeypatch.syspath_prepend(str(SHELL.parents[2] / "scripts"))
    from build_linux import normalize_installer_metadata

    site = tmp_path / "site-packages"
    metadata = site / "example-1.dist-info"
    metadata.mkdir(parents=True)
    (site / "bin").mkdir()
    (site / "bin/example").write_text("#!/temporary/build/python\n")
    for name in ("uv_cache.json", "direct_url.json"):
        (metadata / name).write_text('{"build_specific":true}')
    (metadata / "RECORD").write_text(
        "example.py,sha256=preserve,12\n"
        "example-1.dist-info/uv_cache.json,,\n"
        "example-1.dist-info/direct_url.json,,\n"
        "../bin/example,,\n"
        "bin/another-stub,,\n"
        "example-1.dist-info/RECORD,,\n"
    )
    normalize_installer_metadata(site)
    assert not (site / "bin").exists()
    assert {path.name for path in metadata.iterdir()} == {"RECORD"}
    assert (metadata / "RECORD").read_text() == (
        "example.py,sha256=preserve,12\nexample-1.dist-info/RECORD,,\n"
    )
