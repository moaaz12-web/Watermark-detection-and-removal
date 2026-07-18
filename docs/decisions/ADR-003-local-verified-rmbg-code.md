# ADR-003: Allow RMBG custom code only from its verified local bundle

## Status

Accepted for setup phase

## Date

2026-07-18

## Context

BRIA RMBG-1.4 uses custom Transformers model code. Vendoring and maintaining that code separately
adds work that is not required to establish the initial GPU worker. Allowing ordinary remote-code
loading would be mutable and could execute provider code that was not part of the approved model
artifact.

The existing RMBG tar bundle is stored in private GCS and identified by both an immutable object
generation and the SHA-256 checksum in `model-manifest.json`. The worker validates that checksum
before extracting the archive into its identity-pinned local bundle cache.

## Decision

- Load RMBG with `trust_remote_code=True` only from the extracted local bundle.
- Always combine it with `local_files_only=True`.
- Do not pass a Hugging Face repository ID or public URL to this loader.
- Do not fall back to a network download when custom source is absent from the bundle; fail model
  initialization instead.
- Continue pinning the complete RMBG tar by GCS generation and SHA-256 checksum. Any change to its
  weights or executable source creates a different artifact identity.
- Reconsider separately vendoring the model source before broader production rollout if security
  review, scanning, or maintainability requires it.

## Alternatives considered

### Vendor the RMBG model source immediately

Deferred. It gives clearer source review and patch ownership but adds setup work. The complete local
bundle is already immutable and checksum-verified, so separate vendoring is not required for the
current setup objective.

### Allow Transformers to fetch remote custom code at runtime

Rejected. A repository reference can change independently of the approved GCS artifact and adds a
network dependency during model initialization.

### Disable RMBG

Rejected. RMBG supplies the foreground mask required by the fail-closed product-overlap safety gate.

## Consequences

- Phase A no longer requires a separate RMBG source package or model-loader implementation.
- The RMBG tar must contain every custom Python file required by its Transformers configuration.
- The future dependency/container verification must prove the local bundle loads with networking
  unavailable.
- The executable source is pinned at bundle granularity rather than recorded as a separate upstream
  commit.
