# Reference seed collection

The initial collection exercises three small, publicly declared Apache-2.0 model
artifacts. Each recipe pins every file's SHA-256 and exact repository revision.
These are measured owner captures, with upstream/derivative README declarations
retained as notice files. They do not assert upstream signatures or registry trust.
An intake declaration records an operator's decision; it does not itself grant rights.

| Recipe | Declared closure | Logical bytes | Runtime needed separately |
| --- | --- | ---: | --- |
| `smollm2-gguf.json` | SmolLM2 135M Instruct Q4_K_M, derivative README, upstream notices | 105,482,658 | Compatible llama.cpp |
| `smollm-mlx.json` | SmolLM 135M Instruct 4-bit weights, config/index, complete tokenizer files, notices | 79,233,230 | Apple Silicon and compatible mlx-lm; conversion used 0.15.2 |
| `smolvlm-gguf.json` | SmolVLM 256M Q8_0, required matching Q8_0 projector, notices | 278,852,735 | llama.cpp with SmolVLM/mtmd support |

Exact inputs:

- [GGUF conversion](https://huggingface.co/bartowski/SmolLM2-135M-Instruct-GGUF/tree/09816acd5d99df7be770d85ea30822623dab342c),
  [upstream notice](https://huggingface.co/HuggingFaceTB/SmolLM2-135M-Instruct/blob/12fd25f77366fa6b3b4b768ec3050bf629380bac/README.md).
- [MLX conversion](https://huggingface.co/mlx-community/SmolLM-135M-Instruct-4bit/tree/642e06afe3fab57fd6cc518637c471af0a569e1e),
  [upstream notice](https://huggingface.co/HuggingFaceTB/SmolLM-135M-Instruct/blob/fcc320f490e08fdb4b99d935b2c58d40bf35b0d0/README.md).
- [Vision conversion and projector](https://huggingface.co/ggml-org/SmolVLM-256M-Instruct-GGUF/tree/b9e4379657e1450d04d02eec8e345667265b0a00),
  [upstream notice](https://huggingface.co/HuggingFaceTB/SmolVLM-256M-Instruct/blob/7e3e67edbbed1bf9888184d9df282b700a323964/README.md).

The measured intake accepts these as public seed candidates based on exact
upstream and conversion Apache-2.0 declarations. Required notices include both
READMEs and the complete unmodified Apache-2.0 text (the repository's `LICENSE`).
No additional NOTICE file was present in the selected repository trees. No model
repository Python is included or authorized to execute. Recheck any additional
artifacts or altered recipes as a new intake. Unknown or conflicting terms keep
an item out of public seeding. Small model size is useful for reproducing the
preservation contract; it is not a recommendation of model quality.

For the GGUF recipe, obtain these explicit inputs using the current `hf` CLI:

```sh
hf download bartowski/SmolLM2-135M-Instruct-GGUF \
  SmolLM2-135M-Instruct-Q4_K_M.gguf README.md \
  --revision 09816acd5d99df7be770d85ea30822623dab342c --local-dir ./inputs/gguf
hf download HuggingFaceTB/SmolLM2-135M-Instruct README.md \
  --revision 12fd25f77366fa6b3b4b768ec3050bf629380bac --local-dir ./inputs/notices
cp LICENSE ./inputs/notices/LICENSE
model-preserve pack --recipe examples/recipes/smollm2-gguf.json \
  --member weights=./inputs/gguf --member notices=./inputs/notices \
  --store ./preserved --key owner.pem
```

For MLX, select exactly the files listed in its recipe from its pinned conversion
revision. Obtain the pinned upstream README into a separate notices directory and
add `LICENSE` as above. For vision, both `weights` and `projector` can point to the
same conversion input directory; the explicit file selections remain distinct.
There is no wildcard copying of files into a bundle. Recipe expected hashes must
match, or pack refuses the capture.

The 2026-10-19 local drill packed and verified all three closures, stopped the
first HTTP supplier, downloaded to an empty store from a second local supplier,
and restored every declared member. All non-loopback DNS attempts were rejected
by the drill. Only `127.0.0.1` was requested. This demonstrates no hub/account
lookup during recovery. Both endpoints and copies belonged to one test operator;
they do not establish separate administration, hosting-provider diversity,
inference compatibility, long-term retention, or adversarial-network resistance.
