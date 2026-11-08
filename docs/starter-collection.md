# The starter collection

The starter collection is a signed list of the open models the community
actually runs, chosen by **sustained interest**. It uses Hugging Face downloads
of the base models and of the GGUF/MLX conversions people use locally, and
Ollama and LM Studio pull counts. A mirror operator can import it in one
reviewed step. It comes in three tiers; each tier includes the smaller ones.

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

## What is in it (2026-Q4)

Sizes are the bytes each bundle downloads (decimal GB), including its licence
and notice files. Links go to the exact pinned revisions. A **gate** means the
import asks you to accept that licence's terms first (see
[licences that need your acceptance](#licences-that-need-your-acceptance)).
The evidence for each pick (download and pull figures, the sustained ratio)
is in [`examples/starter/signals/`](../examples/starter/signals/) and in the
research report the list was derived from.

### Small tier (default)

| Model | Weights from | Licence (from base) | Gate | Size |
| --- | --- | --- | --- | ---: |
| Qwen3.5 4B (Q4_K_M GGUF + vision projector) | [unsloth/Qwen3.5-4B-GGUF@e87f1764](https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/tree/e87f176479d0855a907a41277aca2f8ee7a09523) | Apache-2.0 ([Qwen/Qwen3.5-4B@851bf6e8](https://huggingface.co/Qwen/Qwen3.5-4B/tree/851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a)) | none | 3.41 GB |
| Gemma 4 E4B instruct (Q4_K_M GGUF + projector) | [unsloth/gemma-4-E4B-it-GGUF@bfc15c38](https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF/tree/bfc15c382204943c3a8fff0c750b94ae2364d7a3) | Apache-2.0 ([google/gemma-4-E4B-it@4ecc2ba6](https://huggingface.co/google/gemma-4-E4B-it/tree/4ecc2ba63bcac23ba4a767fc87025b1450f84c9c)) | none | 5.97 GB |
| Whisper large-v3-turbo (whisper.cpp q5_0) | [ggerganov/whisper.cpp@5359861c](https://huggingface.co/ggerganov/whisper.cpp/tree/5359861c739e955e79d9a303bcbc70fb988958b1) | MIT ([openai/whisper-large-v3-turbo@41f01f3f](https://huggingface.co/openai/whisper-large-v3-turbo/tree/41f01f3fe87f28c78e2fbf8b568835947dd65ed9)) | none | 0.57 GB |
| Kokoro 82M | [hexgrad/Kokoro-82M@f3ff3571](https://huggingface.co/hexgrad/Kokoro-82M/tree/f3ff3571791e39611d31c381e3a41a3af07b4987) | Apache-2.0 ([hexgrad/Kokoro-82M@f3ff3571](https://huggingface.co/hexgrad/Kokoro-82M/tree/f3ff3571791e39611d31c381e3a41a3af07b4987)) | none | 0.36 GB |
| nomic-embed-text v1.5 (f16 GGUF) | [nomic-ai/nomic-embed-text-v1.5-GGUF@0188c9bf](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5-GGUF/tree/0188c9bf409793f810680a5a431e7b899c46104c) | Apache-2.0 ([nomic-ai/nomic-embed-text-v1.5@e9b67630](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5/tree/e9b6763023c676ca8431644204f50c2b100d9aab)) | none | 0.27 GB |

The small tier is about **10.6 GB**.

### Standard tier

Includes the small tier, plus:

| Model | Weights from | Licence (from base) | Gate | Size |
| --- | --- | --- | --- | ---: |
| Qwen3.8 27B (UD-Q4_K_M GGUF + vision projector) | [unsloth/Qwen3.8-27B-GGUF@4ca72078](https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/tree/4ca720788d1e01f1bff70c033e0d0028fd02e502) | Apache-2.0 ([Qwen/Qwen3.8-27B@37561816](https://huggingface.co/Qwen/Qwen3.8-27B/tree/37561816e66b5b9153b1b64c10968c18c1e6f4a0)) | none | 17.39 GB |
| gpt-oss 20B (MXFP4 GGUF) | [ggml-org/gpt-oss-20b-GGUF@15dcfc7e](https://huggingface.co/ggml-org/gpt-oss-20b-GGUF/tree/15dcfc7e7c608c6161507211ed43c5b590801b4b) | Apache-2.0 ([openai/gpt-oss-20b@6cee5e81](https://huggingface.co/openai/gpt-oss-20b/tree/6cee5e81ee83917806bbde320786a8fb61efebee)) | none | 12.11 GB |
| Gemma 4 12B instruct (Q4_K_M GGUF + projector) | [unsloth/gemma-4-12b-it-GGUF@fc034cff](https://huggingface.co/unsloth/gemma-4-12b-it-GGUF/tree/fc034cfff751157913579611efad8462ac1be606) | Apache-2.0 ([google/gemma-4-12B-it@43c9d014](https://huggingface.co/google/gemma-4-12B-it/tree/43c9d014b231474fa7212dbb48a762566c2b255d)) | none | 7.30 GB |
| Qwen3.5 9B (Q4_K_M GGUF + vision projector) | [unsloth/Qwen3.5-9B-GGUF@3885219b](https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/tree/3885219b6810b007914f3a7950a8d1b469d598a5) | Apache-2.0 ([Qwen/Qwen3.5-9B@c2022362](https://huggingface.co/Qwen/Qwen3.5-9B/tree/c202236235762e1c871ad0ccb60c8ee5ba337b9a)) | none | 6.60 GB |
| Qwen2.5 Coder 7B instruct (Q4_K_M GGUF) | [unsloth/Qwen2.5-Coder-7B-Instruct-GGUF@0ecf1185](https://huggingface.co/unsloth/Qwen2.5-Coder-7B-Instruct-GGUF/tree/0ecf11859560b2bf42e703207f9371186d02245f) | Apache-2.0 ([Qwen/Qwen2.5-Coder-7B-Instruct@c03e6d35](https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct/tree/c03e6d358207e414f1eca0bb1891e29f1db0e242)) | none | 4.68 GB |
| Llama 3.2 3B instruct (Q4_K_M GGUF) | [unsloth/Llama-3.2-3B-Instruct-GGUF@e7d0997e](https://huggingface.co/unsloth/Llama-3.2-3B-Instruct-GGUF/tree/e7d0997e49c9cb00d88b4c1a6a16aa894b0bbc31) | llama3.2 ([meta-llama/Llama-3.2-3B-Instruct@0cb88a4f](https://huggingface.co/meta-llama/Llama-3.2-3B-Instruct/tree/0cb88a4f764b7a12671c53f0838cd831a0843b95)) | accept Llama 3.2 licence + AUP | 2.02 GB |
| GLM-OCR (bf16 safetensors) | [zai-org/GLM-OCR@2e85a628](https://huggingface.co/zai-org/GLM-OCR/tree/2e85a62840ccac27daa451df36c736c4636b8628) | MIT ([zai-org/GLM-OCR@2e85a628](https://huggingface.co/zai-org/GLM-OCR/tree/2e85a62840ccac27daa451df36c736c4636b8628)) | none | 2.66 GB |
| Qwen3-TTS 12Hz 1.7B base (safetensors) | [Qwen/Qwen3-TTS-12Hz-1.7B-Base@fd4b2543](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base/tree/fd4b254389122332181a7c3db7f27e918eec64e3) | Apache-2.0 ([Qwen/Qwen3-TTS-12Hz-1.7B-Base@fd4b2543](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base/tree/fd4b254389122332181a7c3db7f27e918eec64e3)) | none | 4.54 GB |
| BGE reranker v2 m3 (safetensors) | [BAAI/bge-reranker-v2-m3@953dc6f6](https://huggingface.co/BAAI/bge-reranker-v2-m3/tree/953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e) | Apache-2.0 ([BAAI/bge-reranker-v2-m3@953dc6f6](https://huggingface.co/BAAI/bge-reranker-v2-m3/tree/953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e)) | none | 2.29 GB |
| Qwen3 Embedding 0.6B (Q8_0 GGUF) | [Qwen/Qwen3-Embedding-0.6B-GGUF@370f27d7](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF/tree/370f27d7550e0def9b39c1f16d3fbaa13aa67728) | Apache-2.0 ([Qwen/Qwen3-Embedding-0.6B@97b0c614](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B/tree/97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3)) | none | 0.64 GB |
| all-MiniLM-L6-v2 (safetensors) | [sentence-transformers/all-MiniLM-L6-v2@1110a243](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/tree/1110a243fdf4706b3f48f1d95db1a4f5529b4d41) | Apache-2.0 ([sentence-transformers/all-MiniLM-L6-v2@1110a243](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/tree/1110a243fdf4706b3f48f1d95db1a4f5529b4d41)) | none | 0.09 GB |
| Granite Embedding small English R2 (safetensors) | [ibm-granite/granite-embedding-small-english-r2@2ab6fa8e](https://huggingface.co/ibm-granite/granite-embedding-small-english-r2/tree/2ab6fa8ea2d674564defd37171ae19079b864b33) | Apache-2.0 ([ibm-granite/granite-embedding-small-english-r2@2ab6fa8e](https://huggingface.co/ibm-granite/granite-embedding-small-english-r2/tree/2ab6fa8ea2d674564defd37171ae19079b864b33)) | none | 0.10 GB |
| Z-Image Turbo (Q4_K_M GGUF + Qwen3-4B encoder + VAE) | [unsloth/Z-Image-Turbo-GGUF@b40f712c](https://huggingface.co/unsloth/Z-Image-Turbo-GGUF/tree/b40f712c569ecfff42f2307dcc43399ae1d71318) | Apache-2.0 ([Tongyi-MAI/Z-Image-Turbo@f332072a](https://huggingface.co/Tongyi-MAI/Z-Image-Turbo/tree/f332072aa78be7aecdf3ee76d5c247082da564a6)) | none | 6.68 GB |

The standard tier (with small) is about **78 GB**. Its speech to text is
Whisper large-v3-turbo from the small tier; Parakeet is in the Apple add-on.

### Large tier

Includes the standard tier, plus:

| Model | Weights from | Licence (from base) | Gate | Size |
| --- | --- | --- | --- | ---: |
| Qwen3.6 35B-A3B (UD-Q4_K_M GGUF + vision projector) | [unsloth/Qwen3.6-35B-A3B-GGUF@a483e9e6](https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF/tree/a483e9e6cbd595906af30beda3187c2663a1118c) | Apache-2.0 ([Qwen/Qwen3.6-35B-A3B@345d467b](https://huggingface.co/Qwen/Qwen3.6-35B-A3B/tree/345d467b117d1fd5eda9b879a5988de9ae5f47a7)) | none | 23.03 GB |
| Gemma 4 26B-A4B instruct (UD-Q4_K_M GGUF + projector) | [unsloth/gemma-4-26B-A4B-it-GGUF@c099eb48](https://huggingface.co/unsloth/gemma-4-26B-A4B-it-GGUF/tree/c099eb48e663fd284577b04978a94ffccb261841) | Apache-2.0 ([google/gemma-4-26B-A4B-it@4d7ae498](https://huggingface.co/google/gemma-4-26B-A4B-it/tree/4d7ae4984b7db7de8f8457170b3f1a419ee76d52)) | none | 18.14 GB |
| Gemma 4 31B instruct (Q4_K_M GGUF + projector) | [unsloth/gemma-4-31B-it-GGUF@c1ac76e9](https://huggingface.co/unsloth/gemma-4-31B-it-GGUF/tree/c1ac76e99d5513b141e8adde7288b85c3f9c32ec) | Apache-2.0 ([google/gemma-4-31B-it@842da379](https://huggingface.co/google/gemma-4-31B-it/tree/842da3794eaa0b77d5f08bae87a17459d91ff475)) | none | 19.52 GB |
| Muse Glimmer 30B (Q4_K_M GGUF + projector + DFlash drafter) | [meta-models/Muse-Glimmer-30B-GGUF@b44f1072](https://huggingface.co/meta-models/Muse-Glimmer-30B-GGUF/tree/b44f1072cf75e918a1b82a3a17b45c4389b076e7) | Apache-2.0 ([meta-models/Muse-Glimmer-30B@cec94fcf](https://huggingface.co/meta-models/Muse-Glimmer-30B/tree/cec94fcfd6512f2cfe708138c32533b346ea4f69)) | none | 19.79 GB |
| Qwen3 Coder 30B-A3B instruct (Q4_K_M GGUF) | [unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF@b17cb02d](https://huggingface.co/unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF/tree/b17cb02dd882d5b6ab62fc777ad2995f19668350) | Apache-2.0 ([Qwen/Qwen3-Coder-30B-A3B-Instruct@b2cff646](https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct/tree/b2cff646eb4bb1d68355c01b18ae02e7cf42d120)) | none | 18.56 GB |
| gpt-oss 120B (MXFP4 GGUF) | [ggml-org/gpt-oss-120b-GGUF@7200b74b](https://huggingface.co/ggml-org/gpt-oss-120b-GGUF/tree/7200b74bea78b0a2964ef9f3897eefa401e890ff) | Apache-2.0 ([openai/gpt-oss-120b@b5c939de](https://huggingface.co/openai/gpt-oss-120b/tree/b5c939de8f754692c1647ca79fbf85e8c1e70f8a)) | none | 63.39 GB |
| GLM-4.7-Flash (Q4_K_M GGUF) | [unsloth/GLM-4.7-Flash-GGUF@0d32489e](https://huggingface.co/unsloth/GLM-4.7-Flash-GGUF/tree/0d32489ecb9db6d2a4fc93bd27ef01519f95474d) | MIT ([zai-org/GLM-4.7-Flash@7dd20894](https://huggingface.co/zai-org/GLM-4.7-Flash/tree/7dd20894a642a0aa287e9827cb1a1f7f91386b67)) | none | 18.31 GB |
| Qwen3-VL 8B instruct (Q4_K_M GGUF + projector) | [Qwen/Qwen3-VL-8B-Instruct-GGUF@f982a075](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct-GGUF/tree/f982a07559d4a2f6c8744d840bf6fccab30eea96) | Apache-2.0 ([Qwen/Qwen3-VL-8B-Instruct@0c351dd0](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct/tree/0c351dd01ed87e9c1b53cbc748cba10e6187ff3b)) | none | 6.19 GB |
| Qwen3 Embedding 8B (Q8_0 GGUF) | [Qwen/Qwen3-Embedding-8B-GGUF@69d0e58a](https://huggingface.co/Qwen/Qwen3-Embedding-8B-GGUF/tree/69d0e58a13e463cd99a9b83e3f5fee7c10265fab) | Apache-2.0 ([Qwen/Qwen3-Embedding-8B@1d8ad4ca](https://huggingface.co/Qwen/Qwen3-Embedding-8B/tree/1d8ad4ca9b3dd8059ad90a75d4983776a23d44af)) | none | 8.05 GB |
| DeepSeek-R1-0528 Qwen3 8B (Q4_K_M GGUF) | [unsloth/DeepSeek-R1-0528-Qwen3-8B-GGUF@eb48357c](https://huggingface.co/unsloth/DeepSeek-R1-0528-Qwen3-8B-GGUF/tree/eb48357c179d34dbf515983f798dfb8752a0f261) | MIT ([deepseek-ai/DeepSeek-R1-0528-Qwen3-8B@6e8885a6](https://huggingface.co/deepseek-ai/DeepSeek-R1-0528-Qwen3-8B/tree/6e8885a6ff5c1dc5201574c8fd700323f23c25fa)) | none | 5.03 GB |
| Mistral 7B instruct v0.3 (Q4_K_M GGUF) | [lmstudio-community/Mistral-7B-Instruct-v0.3-GGUF@29a78541](https://huggingface.co/lmstudio-community/Mistral-7B-Instruct-v0.3-GGUF/tree/29a785419661afc70b5cd91b5023a835b0092281) | Apache-2.0 ([mistralai/Mistral-7B-Instruct-v0.3@c170c708](https://huggingface.co/mistralai/Mistral-7B-Instruct-v0.3/tree/c170c708c41dac9275d15a8fff4eca08d52bab71)) | none | 4.37 GB |
| Llama 3.1 8B instruct (Q4_K_M GGUF) | [unsloth/Llama-3.1-8B-Instruct-GGUF@600b0020](https://huggingface.co/unsloth/Llama-3.1-8B-Instruct-GGUF/tree/600b0020115fd6b17f0752848fe7b5a1be686bcd) | llama3.1 ([meta-llama/Llama-3.1-8B-Instruct@0e9e39f2](https://huggingface.co/meta-llama/Llama-3.1-8B-Instruct/tree/0e9e39f249a16976918f6564b8830bc894c89659)) | accept Llama 3.1 licence + AUP | 4.92 GB |
| Gemma 3 4B instruct (Q4_K_M GGUF + projector) | [unsloth/gemma-3-4b-it-GGUF@5a3566e7](https://huggingface.co/unsloth/gemma-3-4b-it-GGUF/tree/5a3566e716d80f709ed7b79817eaf7733d2a1fce) | gemma ([google/gemma-3-4b-it@093f9f38](https://huggingface.co/google/gemma-3-4b-it/tree/093f9f388b31de276ce2de164bdc2081324b9767)) | accept Gemma ToU + Prohibited Use Policy | 3.34 GB |
| Chatterbox (English) | [ResembleAI/chatterbox@5bb1f6ee](https://huggingface.co/ResembleAI/chatterbox/tree/5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18) | MIT ([ResembleAI/chatterbox@5bb1f6ee](https://huggingface.co/ResembleAI/chatterbox/tree/5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18)) | none | 3.19 GB |
| XTTS v2 | [coqui/XTTS-v2@6c2b0d75](https://huggingface.co/coqui/XTTS-v2/tree/6c2b0d75eae4b7047358e3b6bd9325f857d43f77) | coqui-public-model-license ([coqui/XTTS-v2@6c2b0d75](https://huggingface.co/coqui/XTTS-v2/tree/6c2b0d75eae4b7047358e3b6bd9325f857d43f77)) | accept CPML: non-commercial only | 2.09 GB |
| FLUX.2 klein 4B (Q8_0 GGUF + Qwen3-4B encoder + VAE) | [unsloth/FLUX.2-klein-4B-GGUF@a09d5bb5](https://huggingface.co/unsloth/FLUX.2-klein-4B-GGUF/tree/a09d5bb51ac4000ccd9643d10e9db3ed14200508) | Apache-2.0 ([black-forest-labs/FLUX.2-klein-4B@e7b7dc27](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B/tree/e7b7dc27f91deacad38e78976d1f2b499d76a294)) | none | 7.13 GB |
| Stable Diffusion XL base 1.0 (fp16 single file) | [stabilityai/stable-diffusion-xl-base-1.0@46216598](https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/tree/462165984030d82259a11f4367a4eed129e94a7b) | openrail++ ([stabilityai/stable-diffusion-xl-base-1.0@46216598](https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/tree/462165984030d82259a11f4367a4eed129e94a7b)) | accept OpenRAIL++-M use restrictions | 6.94 GB |

The large tier (with standard and small) is about **310 GB**. The Z-Image and FLUX.2 bundles each carry the
same Qwen3-4B text encoder: it is counted in both sizes but stored once.

### Apple MLX add-on (opt-in)

Tiers carry only builds that run on any platform by default. Builds that run
only on Apple silicon form an **opt-in add-on**: the MLX 4-bit builds of
Qwen3.8 27B and Gemma 4 E4B (for those two models Apple users download more MLX
than GGUF) and the MLX checkpoint of Parakeet. Choose formats `gguf,mlx`
(`--formats` on `import-starter`,
`--starter-formats` on quickstart, `SOVEREIGN_MIRROR_STARTER_FORMATS` in the
container). An MLX build is imported only when its tier is selected and `mlx`
is among the formats; the review always shows the add-on's size.

| Model | Weights from | Licence (from base) | Gate | Size |
| --- | --- | --- | --- | ---: |
| Gemma 4 E4B instruct (MLX 4-bit, Apple add-on) [small] | [lmstudio-community/gemma-4-E4B-it-MLX-4bit@ce713a00](https://huggingface.co/lmstudio-community/gemma-4-E4B-it-MLX-4bit/tree/ce713a0013bb7090305edfb8e3773a07ccae35c1) | Apache-2.0 ([google/gemma-4-E4B-it@4ecc2ba6](https://huggingface.co/google/gemma-4-E4B-it/tree/4ecc2ba63bcac23ba4a767fc87025b1450f84c9c)) | none | 6.86 GB |
| Qwen3.8 27B (MLX 4-bit, Apple add-on) [standard] | [lmstudio-community/Qwen3.8-27B-MLX-4bit@6067b15c](https://huggingface.co/lmstudio-community/Qwen3.8-27B-MLX-4bit/tree/6067b15cf581666a4aecf6af3afaba4bb5efc20c) | Apache-2.0 ([Qwen/Qwen3.8-27B@37561816](https://huggingface.co/Qwen/Qwen3.8-27B/tree/37561816e66b5b9153b1b64c10968c18c1e6f4a0)) | none | 16.08 GB |
| Parakeet TDT 0.6B v3 (MLX, Apple add-on) [standard] | [mlx-community/parakeet-tdt-0.6b-v3@ed2b7e8c](https://huggingface.co/mlx-community/parakeet-tdt-0.6b-v3/tree/ed2b7e8c15f9aaa0b5772e2efb986255eaef7e15) | cc-by-4.0 ([nvidia/parakeet-tdt-0.6b-v3@541d1f99](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3/tree/541d1f99c6b0c3cd0b11a95167540bb8edefd82b)) | none | 2.51 GB |

The add-on adds about 6.9 GB to the small tier and 25.5 GB to standard or
large. `check` refuses an entry whose recipe has MLX members unless it is in
the add-on, so no Apple-only build can enter a default tier. Builds that need one particular runtime but run on
any platform (Kokoro, Qwen3-TTS, Chatterbox, XTTS v2, GLM-OCR and the
safetensors embeddings, all on PyTorch) stay in their tiers; each recipe names
its runtime.

### Notes on specific models

- **Kokoro 82M** is included because its own card declares Apache-2.0. Its
  declared base, StyleTTS 2, asks that listeners be told speech is
  synthesized; the review shows this note.
- **Muse Glimmer 30B** is Meta's Apache-2.0 model, with its usage policy
  carried. It is under three months old, so it is on the watch list and is
  re-scored at the February 2027 review.
- **Llama 3.2 3B and Llama 3.1 8B** carry Meta's licence and Acceptable Use
  Policy (from `meta-llama/llama-models`), their NOTICE line and
  "Built with Llama". They are text-only, so the Llama EU restriction on
  multimodal models does not apply.
- **Gemma 3 4B** carries the Gemma Terms of Use, Google's Prohibited Use
  Policy and the required NOTICE line. Accepting its gate binds you to the
  Prohibited Use Policy, and you must pass it on as an enforceable term to
  anyone you share it with.
- **XTTS v2** is under the Coqui Public Model License: **non-commercial use
  only**. It is not in Sovereign's licence list, so the import always asks.
- **SDXL base 1.0** carries the OpenRAIL++-M licence with its use
  restrictions (Attachment A).
- **Parakeet** (CC BY 4.0, in the Apple add-on) carries an attribution file,
  the licence text and
  a note of changes.

### Left out

| Model | Why |
| --- | --- |
| pyannote/speaker-diarization-community-1 | Skipped for now. The repository is gated on Hugging Face, so its small files cannot be pinned without an account that accepted the gate. It is a candidate for the next quarterly review. |
| BAAI/bge-m3 | No safetensors weights at the pinned revision (only a pickle `.bin` and ONNX). |
| Qwen/Qwen3-Reranker-4B (Q8_0 GGUF) | No Q8_0 GGUF from Qwen or the reviewed conversion organizations; only individual uploads. |
| openai/whisper-large-v3 (whisper.cpp) | The base card says Apache-2.0 but the conversion repository is tagged MIT: the tags disagree. |
| nomic-embed-text v1.5 safetensors | They need model code from another repository; the f16 GGUF is carried instead. |
| Llama 3.2 Vision, Llama 4 | Kept out of every default tier: low, falling interest, and their licences are not granted to people or companies in the EU. |
| Microsoft Muse (WHAM) | Its licence forbids distributing the model. |

### Extra-large add-ons: optional, not packaged

For data-centre operators the research also lists GLM-5.3-Flash (MIT,
about 195 GB), DeepSeek-V4.1-Flash (MIT, about 460 GB), Qwen3.8-Flash-Next
(Qwen Community 1.0, about 108 GB; its model-as-a-service clause needs a
separate licence from Alibaba), Qwen3.5-122B-A10B (Apache-2.0, about 75 GB)
and Muse Spark Open 816B-A42B (Muse Model License, about 490 GB). None is
packaged. Muse Spark Open's licence must be checked against its full text
before anyone carries it: it sits behind Hugging Face's gate and was
summarised from press coverage.

## How licences were decided

- **From the base model, never the conversion's tag.** Each entry's licence
  is read from the base model's card at an exact revision. The conversion's
  own tag must name the same licence but never decides it; a disagreement is a
  stop. Every base model a card declares is checked too, and the one reviewed
  exception (Kokoro) is recorded in the plan with its reason. Pipelines check
  each component (text encoder, VAE) the same way.
- **Licence texts travel with the weights.** Each bundle carries the base
  model's card and its LICENSE, NOTICE or usage policy. Where a repository
  ships none, the text is added from a pinned source: the Apache-2.0 text from
  this repository, the SPDX MIT or CC BY 4.0 text, the upstream project's
  licence, or the notice files in
  [`examples/starter/notices/`](../examples/starter/notices/).
- **Quantized files are modified works.** The conversion's README carried
  with the weights says how the files were converted, and the Gemma and Llama
  NOTICE files say so too.

## Import it into a mirror

You need a [persistent mirror](persistent-mirrors.md), the signed list file and
its **list ID**: the SHA-256 identity of the exact list. Get the ID from a
source you trust separately from wherever you got the file, such as this
repository's release notes. The list file alone proves nothing; the ID pins it.

```sh
model-preserve mirror import-starter --dir ~/sovereign-mirrors/example \
  --list starter-list.json --expected-list-id LIST_SHA256 --tier small
```

Add `--formats gguf,mlx` to include the Apple MLX add-on for the chosen tiers.

Import refuses before it downloads anything unless:

- the list is exactly the one the ID names, and its signature meets the
  threshold of the signing keys inside it (add `--list-trust keys.json` to also
  require keys you enrolled independently);
- the new models fit the mirror's disk budget, with headroom. Each missing
  file counts once: files shared between bundles, already in the mirror for
  models you hold, or already copied by an interrupted import cost nothing.
  The review shows the download size and an upper estimate of the disk
  needed. After consent the import fetches only the signed file lists and
  decides exactly, before any weights download, against both the mirror's
  budget and the free space on the staging volume. The peak is one copy of
  each missing file: staging starts with hard links to files the mirror
  already holds (so they are neither downloaded nor stored again), downloads
  are renamed into place rather than copied, retaining hard-links them into
  the mirror, and each model's staging is removed once it is kept. A
  `--staging` directory on another volume cannot be hard-linked, so it needs
  a second copy's worth of space there. With quickstart (and the container),
  a bundle whose file list can't be fetched, or a budget refusal, is reported
  in the receipt and the mirror starts serving what it holds;
- the mirror controller is stopped;
- you consent. The review lists every model with its licence, base model and
  size, the signing keys, the total download and the disk budget. Type
  `import`, or pass `--yes-i-accept-the-starter-list` after reading the review
  (`--plan-only` prints it and changes nothing).

### Licences that need your acceptance

A starter list may also carry models whose licences have use restrictions or
conditions to pass on: non-commercial licences, responsible-AI licences and
makers' own terms (for example the Gemma Terms of Use or a Llama licence).
Five licences in this list are gated: Llama 3.1, Llama 3.2, the Gemma Terms of
Use, OpenRAIL++-M and the Coqui Public Model License. The import handles them
the same way the core's terms gate does, as Hugging Face's gated models do.

Which licences need acceptance is decided by Sovereign's own
[licence definition](sovereign-desktop.md#help-keep-models-available), never by
the list. The review marks each such model ("terms to accept") and lists,
under **Terms you must accept before importing**, each licence's name, a plain
summary, a link to its full text, and any warning or EU notice the definition
attaches. Import then refuses with `terms_acceptance_required`, before anything
downloads, unless you accept **exactly** those licences: type
`I accept these terms` when asked, or pass `--accept-terms gemma,llama3.1`
(`--accept-starter-terms` on quickstart, `SOVEREIGN_MIRROR_STARTER_ACCEPT_TERMS`
in the container). Your acceptance is recorded under the mirror's private
`scratch/starter-acceptance/` before the import starts. Every licence the
models still to download need must be accepted. A rerun may keep the same
acceptance: names of licences for models you already hold are allowed, so an
unchanged container configuration restarts cleanly, while a licence that
nothing in the import uses still refuses. Serving such a
model publicly may also be limited by its terms; deciding that is yours.

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
removed is never imported again. A model whose earlier copy failed or was
interrupted needs `mirror retry --manifest ...` first; the next import then
resumes it (the review marks it "resumed") under the same acceptance check as a
new model. Importing a new selection clears the standing restart
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
`SOVEREIGN_MIRROR_STARTER_LIST_ID` and, for a larger tier,
`SOVEREIGN_MIRROR_STARTER_TIER=standard` or `large`
(`SOVEREIGN_MIRROR_STARTER_MIRROR` names an extra source tried first, such as a
mounted local copy). Raise
`SOVEREIGN_MIRROR_BUDGET` to fit: at least 20G for small, 120G for standard
and 350G for large. Each start imports only what is missing.

## How the list stays community-driven

A new list is derived every quarter (February, May, August and November) and
published as its own versioned list (`starter-YYYY-Qn`) with a diff against the
previous one. **Publishing it changes nothing on any mirror.** You import a
new list only if you choose to, and you can import, ignore or cherry-pick it.
Foxlight has no remote authority over what you hold.

[`scripts/derive_starter_signals.py`](../scripts/derive_starter_signals.py)
recomputes the signals from public APIs, with no page scraping:

- Hugging Face 30-day and all-time downloads of each base model;
- 30-day downloads summed across the conversion organizations (unsloth,
  bartowski, lmstudio-community, ggml-org, mlx-community);
- the sustained ratio: 30-day downloads divided by the lifetime monthly
  average. At least 0.7 after three months reads as sustained;
- Ollama monthly pulls, from two operator-supplied pull-count snapshots
  (`--ollama` and `--ollama-previous`), since Ollama has no statistics API.

```sh
uv run python scripts/derive_starter_signals.py --output examples/starter/signals \
  --proposals proposals.json --previous-diff examples/starter/signals/diff-2026-11-07.json
```

It writes `signals-DATE.json` and `.csv` and a ranked `diff-DATE.json` with
these rules:

- **Score.** Each model's median rank across the available signals, within
  its tier. Likes only break ties; leaderboards are never an input.
- **Promote** a candidate only when it is at least three months old, ranks in
  its tier's top N on at least two signals, and has a sustained ratio of at
  least 0.7.
- **Demote** a listed model only after two consecutive weak quarters. One weak
  quarter puts it on the watch list.
- **Proposals add candidates.** Anyone can propose a model with its repository,
  tier and licence; proposals join the scoring run and never override it.
- **Operators choose.** The diff is a proposal for the next list. Each new
  list is checked, packed and signed like this one, and imported only by
  operators who choose to.

The 2026-11-07 baseline is in `examples/starter/signals/`. It keeps 29 entries,
and puts seven on the watch list: Llama 3.2 3B, Llama 3.1 8B, GLM-OCR, Gemma 4
26B-A4B, gpt-oss 120B, GLM-4.7-Flash and Muse Glimmer.

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
| `tier` | `small`, `standard` or `large`. Each tier includes the smaller ones. |
| `format` | `gguf` (the default builds: GGUF, and the whisper.cpp, safetensors or PyTorch files a model's runtime uses) or `mlx` (the opt-in Apple add-on). Any other value refuses. |
| `title`, `role` | What the model is for. |
| `manifest` | The exact bundle identity (manifest SHA-256). |
| `recipe`, `recipe_sha256` | The recipe name and the SHA-256 of its canonical JSON. |
| `size_bytes` | The bundle's total bytes; a different bundle refuses. |
| `licence` | Licence ID, a plain summary, and the base model (and conversion) with exact revisions it was resolved from. |
| `terms` | The exact licence and terms-reference pairs of every bundle member, kept as the selection's reviewed terms. Whether a licence needs acceptance comes from Sovereign's licence definition, not from the list. |
| `mirrors` | 1–16 HTTP(S) addresses that hold the bundle. Hints only: you may use any source. |

## Build and publish it (maintainers)

[`scripts/build_starter_collection.py`](../scripts/build_starter_collection.py)
runs the pipeline. It needs network access to `huggingface.co` and
`raw.githubusercontent.com`; set `HF_TOKEN` only to raise Hub rate limits.
Every download is HTTPS, including redirects. The token is sent only to
`huggingface.co` itself, as a header urllib never copies to a redirect, and any
Authorization header is dropped when a redirect is followed. Recipe origins
are parsed exactly (a Hub `owner/name`, or `github.com/owner/repo`), never
matched by substring.

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

The plan's `licence_policy` is `known` for this list. `strict` (the
default) admits Apache-2.0 or MIT only;
`known` (any licence in Sovereign's licence definition; `check` reports
which need acceptance, and operators accept them at import). Either way the
licence is resolved from the base model and a conversion tag must match it.

`build` always runs `check` first, so nothing is signed whose licences,
revisions and file hashes were not verified in that run. `check` also binds
each recipe member to that evidence: every model member must declare exactly
the licence resolved for its model and come from the exact base or conversion
revision whose licence was resolved, and a notices member may only declare a
licence resolved for that entry. `build` refuses placeholder mirror hints, so the list always names a real
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
