# Try preservation without a model download

This walkthrough uses synthetic files from the public conformance vector. It
needs no GPU, model weights, hub account or signing key. Run it from an
authenticated checkout on Linux or macOS with Python 3.11+ and `uv` installed.
The fixture is a teaching example, not an inference model.

## Prepare the example

```sh
uv sync --locked
uv run python - <<'PY'
import base64
import json
from pathlib import Path

vector = json.loads(Path("examples/conformance/v1.json").read_bytes())
root = Path("preservation-demo")
root.mkdir()  # Refuse to overwrite a previous walkthrough.
origin = root / "origin"
(origin / "manifests").mkdir(parents=True)
for file_id, encoded in vector["objects_base64"].items():
    target = origin / "objects" / "sha256" / file_id
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(base64.b64decode(encoded))
(origin / "manifests" / (vector["manifest"] + ".json")).write_text(
    json.dumps(vector["envelope"])
)
(root / "fixture-trust.json").write_text(json.dumps(vector["trust"]))
(root / "manifest-id.txt").write_text(vector["manifest"] + "\n")
print("Synthetic store prepared at", origin)
PY
preservation_demo_id=$(cat preservation-demo/manifest-id.txt)
```

The fixture's public trust material is checked in with its test data. That is
suitable for this demonstration only because the example itself comes from your
authenticated software checkout. Its private signing key was discarded. For real
models, independently authenticate the signer and exact reference; never let an
unknown mirror nominate its own trusted key.

## Verify, receive, restore

```sh
uv run model-preserve verify "$preservation_demo_id" \
  --trust preservation-demo/fixture-trust.json --store preservation-demo/origin

uv run model-preserve fetch "$preservation_demo_id" \
  --trust preservation-demo/fixture-trust.json \
  --mirror ./preservation-demo/unavailable \
  --mirror ./preservation-demo/origin --store preservation-demo/received

uv run model-preserve restore "$preservation_demo_id" \
  --trust preservation-demo/fixture-trust.json \
  --store preservation-demo/received --output preservation-demo/restored
```

The first source deliberately does not exist. Fetch tries the second permitted
source and publishes completion only after checking all objects. The commands
print JSON receipts with the exact manifest ID, verified logical byte count and
trust snapshot label. Restored member folders contain only selected files.
Receiving does not start a server.

For a second terminal session, set `preservation_demo_id` again from its file.
Rerunning restore requires a new output directory; existing outputs are refused.
Do not put `preservation-demo/` in a source commit or a public server's root.

## Serve the same copy explicitly

In the first terminal:

```sh
uv run model-preserve seed --store preservation-demo/origin \
  --trust preservation-demo/fixture-trust.json --manifest "$preservation_demo_id" \
  --allow-http --max-upload-bytes 1048576
```

It listens on loopback port 8125. In a second terminal, from the same checkout:

```sh
preservation_demo_id=$(cat preservation-demo/manifest-id.txt)
uv run model-preserve fetch "$preservation_demo_id" \
  --trust preservation-demo/fixture-trust.json --mirror http://127.0.0.1:8125 \
  --allow-http --allow-private --store preservation-demo/received-over-http
```

Plain HTTP and private-address access are explicit exceptions for this local
exercise. Ordinary receiving uses HTTPS and rejects non-global addresses by
default. Stop the seed with Ctrl-C. Stopping it does not delete retained copies.
A real public mirror should follow the [mirror guide](mirrors.md).

## Observe corruption being rejected

After stopping the server, make a separate disposable copy and alter one object:

```sh
uv run python - <<'PY'
import json
import shutil
from pathlib import Path

root = Path("preservation-demo")
shutil.copytree(root / "received", root / "corrupt")
vector = json.loads(Path("examples/conformance/v1.json").read_bytes())
file_id = next(iter(vector["objects_base64"]))
(root / "corrupt" / "objects" / "sha256" / file_id).write_bytes(b"changed")
PY
uv run model-preserve verify "$preservation_demo_id" \
  --trust preservation-demo/fixture-trust.json --store preservation-demo/corrupt
```

The final command is expected to exit with an error. The previously verified
`received` store remains intact. Changing an envelope, signer or expected identity
also fails; the [conformance tests](../tests/test_integrity.py) exercise these cases.

For real model capture, use a new encrypted owner key and an explicit recipe.
Continue with the [seed collection](seed-collection.md),
[trust and recovery](trust-and-recovery.md), and [operating guide](operations.md).
