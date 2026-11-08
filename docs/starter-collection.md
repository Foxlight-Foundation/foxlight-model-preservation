# The strict starter collection

The starter collection is a short, signed list of widely used open models that
a new mirror operator can choose to import in one step. It is **strict**: every
model is Apache-2.0 or MIT, both for the upstream base model and for the
converted files Sovereign carries. There are no Llama models, no Gemma 3 or
earlier, and no models under Qwen's research licence.

**It is a one-time import list, not a followed catalog.** When you import it,
each model becomes your own local selection, exactly as if you had run
`mirror retain` yourself. Nothing Foxlight publishes later can stop, change or
remove those copies: the mirror keeps no link to the list or its publisher. An
updated starter list is simply a new list that you may choose to import. Only
you stop serving a copy (`mirror block`) or delete it (`mirror prune`).

Operators are responsible for what they host and for the law where they run a
mirror. The licence notes below describe the terms in plain words; **they are
not legal advice.** A public mirror is not anonymous: anyone can see its
address, and the hosts it contacts see yours.

## What is in it

Sizes are the bytes each bundle downloads (decimal GB), including its licence
and notice files. Revisions are exact Hugging Face commits; follow the links
for the full 40-character IDs, which the recipes pin.

### Small tier (default): about 4.2 GB

| Model | Role | Files carried | Licence (resolved from) | Size |
| --- | --- | --- | --- | ---: |
| Qwen3.5 4B | Chat and vision | `Qwen3.5-4B-Q4_K_M.gguf` + `mmproj-F16.gguf` from [unsloth/Qwen3.5-4B-GGUF@e87f1764](https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/tree/e87f176479d0855a907a41277aca2f8ee7a09523) | Apache-2.0 ([Qwen/Qwen3.5-4B@851bf6e8](https://huggingface.co/Qwen/Qwen3.5-4B/tree/851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a) card and LICENSE) | 3.41 GB |
| Whisper large-v3-turbo | Speech to text | `ggml-large-v3-turbo-q5_0.bin` (whisper.cpp) from [ggerganov/whisper.cpp@5359861c](https://huggingface.co/ggerganov/whisper.cpp/tree/5359861c739e955e79d9a303bcbc70fb988958b1) | MIT ([openai/whisper-large-v3-turbo@41f01f3f](https://huggingface.co/openai/whisper-large-v3-turbo/tree/41f01f3fe87f28c78e2fbf8b568835947dd65ed9) card; OpenAI's MIT text from [openai/whisper@86098128](https://github.com/openai/whisper/blob/86098128c0b4f24f0e2aa2994de830614b474227/LICENSE)) | 0.57 GB |
| all-MiniLM-L6-v2 | English embeddings (tiny) | safetensors, tokenizer and sentence-transformers config from [sentence-transformers/all-MiniLM-L6-v2@1110a243](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/tree/1110a243fdf4706b3f48f1d95db1a4f5529b4d41) | Apache-2.0 (its own card); MIT base [nreimers/MiniLM-L6-H384-uncased@3276f0fa](https://huggingface.co/nreimers/MiniLM-L6-H384-uncased/tree/3276f0fac9d818781d7a1327b3ff818fc4e643c0) card and Microsoft's MIT text carried too | 0.09 GB |
| BGE small en v1.5 | English retrieval embeddings | safetensors, tokenizer and config from [BAAI/bge-small-en-v1.5@5c38ec7c](https://huggingface.co/BAAI/bge-small-en-v1.5/tree/5c38ec7c405ec4b44b94cc5a9bb96e735b38267a) | MIT (its own card; BAAI's MIT text from [FlagOpen/FlagEmbedding@897baa67](https://github.com/FlagOpen/FlagEmbedding/blob/897baa67c0ffddbfccf4e6776ff55310d67809d4/LICENSE)) | 0.13 GB |

### Standard tier (opt-in): about 55 GB in total

Standard includes everything in the small tier, plus:

| Model | Role | Files carried | Licence (resolved from) | Size |
| --- | --- | --- | --- | ---: |
| Qwen3.8 27B | Flagship dense chat and vision | `Qwen3.8-27B-Q4_K_M.gguf` + `mmproj-Qwen3.8-27B-BF16.gguf` from [ggml-org/Qwen3.8-27B-GGUF@a316ea52](https://huggingface.co/ggml-org/Qwen3.8-27B-GGUF/tree/a316ea527fde32c12487be7574e2db5ac2ad2a7e) | Apache-2.0 ([Qwen/Qwen3.8-27B@37561816](https://huggingface.co/Qwen/Qwen3.8-27B/tree/37561816e66b5b9153b1b64c10968c18c1e6f4a0) card and LICENSE) | 19.91 GB |
| gpt-oss 20B | Reasoning and tool use | `gpt-oss-20b-MXFP4.gguf` from [ggml-org/gpt-oss-20b-GGUF@15dcfc7e](https://huggingface.co/ggml-org/gpt-oss-20b-GGUF/tree/15dcfc7e7c608c6161507211ed43c5b590801b4b) | Apache-2.0 ([openai/gpt-oss-20b@6cee5e81](https://huggingface.co/openai/gpt-oss-20b/tree/6cee5e81ee83917806bbde320786a8fb61efebee) card, LICENSE and USAGE_POLICY) | 12.11 GB |
| Qwen3.5 9B | Mid-size chat and vision | `Qwen3.5-9B-Q4_K_M.gguf` + `mmproj-F16.gguf` from [unsloth/Qwen3.5-9B-GGUF@3885219b](https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/tree/3885219b6810b007914f3a7950a8d1b469d598a5) | Apache-2.0 ([Qwen/Qwen3.5-9B@c2022362](https://huggingface.co/Qwen/Qwen3.5-9B/tree/c202236235762e1c871ad0ccb60c8ee5ba337b9a) card and LICENSE) | 6.60 GB |
| Gemma 4 E4B instruct | On-device chat and vision | `gemma-4-E4B-it-Q4_K_M.gguf` + `mmproj-F16.gguf` from [unsloth/gemma-4-E4B-it-GGUF@bfc15c38](https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF/tree/bfc15c382204943c3a8fff0c750b94ae2364d7a3) | Apache-2.0 ([google/gemma-4-E4B-it@4ecc2ba6](https://huggingface.co/google/gemma-4-E4B-it/tree/4ecc2ba63bcac23ba4a767fc87025b1450f84c9c) card; the repository ships no LICENSE, so the Apache text is added) | 5.97 GB |
| Phi-4-mini instruct | Small reasoning model | `Phi-4-mini-instruct-Q4_K_M.gguf` from [unsloth/Phi-4-mini-instruct-GGUF@78eb92a4](https://huggingface.co/unsloth/Phi-4-mini-instruct-GGUF/tree/78eb92a46fc37e6b524df991ed9aca9bc6aa7b80) | MIT ([microsoft/Phi-4-mini-instruct@cfbefacb](https://huggingface.co/microsoft/Phi-4-mini-instruct/tree/cfbefacb99257ffa30c83adab238a50856ac3083) card, LICENSE and NOTICE.md) | 2.49 GB |
| BGE reranker v2 m3 | Multilingual reranker | safetensors and tokenizer from [BAAI/bge-reranker-v2-m3@953dc6f6](https://huggingface.co/BAAI/bge-reranker-v2-m3/tree/953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e) | Apache-2.0 (its own card); its MIT base [BAAI/bge-m3@5617a9f6](https://huggingface.co/BAAI/bge-m3/tree/5617a9f61b028005a4858fdac845db406aefb181) card and BAAI's MIT text carried too | 2.29 GB |
| Qwen3 Embedding 0.6B | Multilingual embeddings | safetensors, tokenizer and config from [Qwen/Qwen3-Embedding-0.6B@97b0c614](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B/tree/97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3) | Apache-2.0 (its own card; base [Qwen/Qwen3-0.6B-Base@da87bfb6](https://huggingface.co/Qwen/Qwen3-0.6B-Base/tree/da87bfb608c14b7cf20ba1ce41287e8de496c0cd) card and LICENSE) | 1.21 GB |
| nomic-embed-text v1.5 | English embeddings | `nomic-embed-text-v1.5.f16.gguf` from [nomic-ai/nomic-embed-text-v1.5-GGUF@0188c9bf](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5-GGUF/tree/0188c9bf409793f810680a5a431e7b899c46104c) | Apache-2.0 ([nomic-ai/nomic-embed-text-v1.5@e9b67630](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5/tree/e9b6763023c676ca8431644204f50c2b100d9aab) card; Apache text added) | 0.27 GB |

nomic-embed-text is carried as nomic's own f16 GGUF rather than safetensors:
its safetensors need model code from another repository (`trust_remote_code`),
while the GGUF runs in llama.cpp and Ollama with no repository code.

The recipes are in [`examples/recipes/starter/`](../examples/recipes/starter/)
and the plan that ties them to tiers and licence evidence is
[`examples/starter/plan.json`](../examples/starter/plan.json). No runtime is
bundled; each recipe names the runtime it needs. Being in the list is not a
claim about model quality, safety or fitness for any purpose.

### Left out

| Model | Why |
| --- | --- |
| hexgrad/Kokoro-82M | Its card declares `base_model: yl4579/StyleTTS2-LJSpeech`, which has no licence on Hugging Face, and StyleTTS 2's pre-trained models carry their own use condition (tell listeners that speech is synthesized). The base does not clearly resolve to Apache-2.0 or MIT, so the strict list leaves it out. |
| BAAI/bge-m3 | The pinned revision has no safetensors weights, only a pickle `pytorch_model.bin` and ONNX. |

## How licences were decided

- **From the base model, never the conversion's tag.** Each entry's licence
  is read from the base model's card at an exact revision, and its licence file
  where it has one. The conversion's own tag must name the same licence, but
  can never decide it: conversions are sometimes mis-tagged (for example
  `ggml-org/Qwen2.5-VL-3B-Instruct-GGUF` is tagged Apache-2.0 while Qwen2.5-VL
  3B is under Qwen's research licence). Every base model a card declares is
  checked too.
- **Licence texts travel with the weights.** Conversion repositories usually
  ship no LICENSE. Each bundle carries the base model's card, its LICENSE and
  any NOTICE or usage policy (Phi-4-mini's `NOTICE.md`, gpt-oss's
  `USAGE_POLICY`). Where a repository ships no licence file, the complete
  licence text is added from a pinned source: the Apache-2.0 text from this
  repository, or the MIT text with its copyright notice from the upstream
  project. Each of these is a separate, pinned bundle member.
- **Quantized files are modified works.** Apache-2.0 asks redistributors to
  mark changes; the conversion's own README, carried with the weights, says how
  the files were converted.

## Import it into a mirror

You need a [persistent mirror](persistent-mirrors.md), the signed list file and
its **list ID**: the SHA-256 identity of the exact list. Get the ID from a
source you trust separately from wherever you got the file, such as this
repository's release notes. The list file alone proves nothing; the ID pins it.

```sh
model-preserve mirror import-starter --dir ~/sovereign-mirrors/example \
  --list starter-list.json --expected-list-id LIST_SHA256 --tier small
```

Import refuses before it downloads anything unless:

- the list is exactly the one the ID names, and its signature meets the
  threshold of the signing keys inside it (add `--list-trust keys.json` to also
  require keys you enrolled independently);
- the new models fit the mirror's disk budget, with headroom;
- the mirror controller is stopped;
- you consent. The review lists every model with its licence, base model and
  size, the signing keys, the total download and the disk budget. Type
  `import`, or pass `--yes-i-accept-the-starter-list` after reading the review
  (`--plan-only` prints it and changes nothing).

Accepting the review accepts the list's model-signing keys **for the bundles in
that list only**. Each bundle is downloaded from the list's mirror hints (add
`--mirror URL_OR_STORE` to try your own source first, such as a copy on a USB
disk), verified against those keys, checked against its listed size and
licence terms, then kept as a local selection. Downloads go to a staging
directory beside the mirror (`--staging` to choose another on the same
filesystem) and are hard-linked into the mirror, so nothing is stored twice.
The download is an explicit one-off action, so it is not charged to the
mirror's daily ingress budget.

Running it again is safe. Models you already hold are skipped. A model you
removed is never imported again. A model whose earlier copy failed needs
`mirror retry` first. Importing a new selection clears the standing restart
grant (unless a [standing intake policy](persistent-mirrors.md#standing-intake-policy)
covers Apache-2.0 and MIT); grant it again after review.

### With quickstart or the container

`mirror quickstart` takes the same list as an option. It shows the starter
review with the mirror review, imports after you consent, then records the
restart grant so it covers the imported models. With a starter list the
collection file is optional; without one, no source is followed:

```sh
model-preserve mirror quickstart --dir ~/sovereign-mirrors/example \
  --starter-list starter-list.json --expected-starter-list-id LIST_SHA256 \
  --starter-tier small --disk-bytes 20G --public-url https://mirror.example.org
```

A model that fails to download is reported in the receipt; running quickstart
again retries it. In the [container](persistent-mirrors.md#run-in-a-container),
put the list in the collection directory and set
`SOVEREIGN_MIRROR_STARTER_LIST=/config/starter-list.json`,
`SOVEREIGN_MIRROR_STARTER_LIST_ID` and, for the larger tier,
`SOVEREIGN_MIRROR_STARTER_TIER=standard` (raise `SOVEREIGN_MIRROR_BUDGET` to
at least 60G). Each start imports only what is missing.

## The list format

The list is a DSSE envelope with payload type
`application/vnd.foxlight.sovereign.starter-list.v1+json`, signed with Ed25519
like every other Sovereign record. Its ID is the SHA-256 of the RFC 8785
payload, as for a catalog. The payload is strict JSON: unknown fields,
duplicate keys and other versions refuse.

| Field | Meaning |
| --- | --- |
| `schema_version` | Exactly `sovereign.starter-list.v1`. |
| `title`, `description`, `issued` | Shown in the review. |
| `bundle_trust` | The trust profile (keys and threshold) that signs the list and every bundle in it. |
| `entries` | 1–1000 entries, each listed below. |

Each entry extends a signed catalog entry (`entry_id`, `manifest`, `mirrors`):

| Field | Meaning |
| --- | --- |
| `entry_id` | Stable identifier, unique in the list. |
| `tier` | `small` or `standard`. Standard includes small. |
| `title`, `role` | What the model is for. |
| `manifest` | The exact bundle identity (manifest SHA-256). |
| `recipe`, `recipe_sha256` | The recipe name and the SHA-256 of its canonical JSON. |
| `size_bytes` | The bundle's total bytes; a different bundle refuses. |
| `licence` | Licence ID, a plain summary, and the base model (and conversion) with exact revisions it was resolved from. |
| `terms` | The exact licence and terms-reference pairs of every bundle member, kept as the selection's reviewed terms. |
| `mirrors` | 1–16 HTTP(S) addresses that hold the bundle. Hints only: you may use any source. |

## Build and publish it (maintainers)

[`scripts/build_starter_collection.py`](../scripts/build_starter_collection.py)
runs the pipeline. It needs network access to `huggingface.co` and
`raw.githubusercontent.com`; set `HF_TOKEN` only to raise Hub rate limits.

```sh
# Dry run: resolve every licence from its base model and check every pinned
# revision and SHA-256 (LFS files by the Hub's LFS oid: no weights download).
uv run python scripts/build_starter_collection.py check

# Download, pack, verify and sign. The publishing key is passed in, never stored.
uv run python scripts/build_starter_collection.py build --work /big/disk/starter \
  --key starter-publishing.pem --passphrase-file passphrase.txt \
  --mirror https://STARTER_BUCKET_ADDRESS --output starter-list.json

# Upload the bundles, the list and a fresh mirror card to the bucket.
uv run python scripts/build_starter_collection.py publish \
  --store /big/disk/starter/store --list starter-list.json --list-id LIST_SHA256 \
  --bucket-target bucket-target.json --card-key starter-mirror-card.pem
```

`build` refuses placeholder mirror hints, so the list always names a real
address. Its output is the signed list and the list ID to publish separately.
The starter mirror is a single [bucket mirror](bucket-mirrors.md) (Cloudflare
R2 on its own domain): no server and no open port, serving both tiers over
HTTPS. `publish` uploads only what the bucket is missing, puts the list at
`starter/LIST_ID.json` and publishes the bucket's signed mirror card in
`/.well-known/sovereign/peers.json`, so clients and other mirrors can find it.
The card is signed with a separate mirror key, never the publishing key, and
lasts 7 days: run `publish` again weekly to renew it. The bucket target is a
`sovereign.bucket-target.v1` file naming a keychain entry; the access keys stay
in the operating system's keychain.
