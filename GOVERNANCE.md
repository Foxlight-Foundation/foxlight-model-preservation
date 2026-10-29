# Governance

Sovereign by Foxlight is developed in the open and published by
**FoxlightAI Inc.**, which holds the copyright together with the project's
contributors (see [NOTICE](NOTICE)). Foxlight Foundation is FoxlightAI's
internal governance for its open-source work; it is not a separate legal entity.

## Maintainers

The project is maintained by FoxlightAI Inc. Maintainers review and merge pull
requests, cut releases through the attested release workflow
([RELEASING.md](RELEASING.md)) and decide format changes.

## How changes are decided

- **Through pull requests.** All changes, including maintainers' own, go through
  ready pull requests with passing checks and review.
- **The format is a compatibility promise.** The v1 bundle format is frozen:
  existing identities and signatures keep verifying in future releases. Any
  incompatible change needs a new, separately versioned format and signing
  domain ([protocol](docs/protocol.md)). Drafts such as PMF are versioned
  separately and labelled as drafts.
- **The conformance vector is the referee.** Any implementation that passes the
  public vector for a format version interoperates with this one; the reference
  implementation gets no special authority over the format.
- **Security** reports follow [SECURITY.md](SECURITY.md).

## Contributing

Contributions are welcome under the Apache License 2.0 with a Developer
Certificate of Origin sign-off. There is no CLA: contributors keep their
copyright. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Names

The code is free to fork; the names are not. See [TRADEMARKS.md](TRADEMARKS.md).
