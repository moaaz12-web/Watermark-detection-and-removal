# Bulk watermark limit: 30,000 images

Both bulk pipeline YAMLs now allow 30,000 items. Five-image manual pipelines,
model choices, and image processing policies are unchanged.

The limit is enforced by `validate_manifest` using the loaded configuration.
Tests cover 30,000 valid unique items and rejection of 30,001 for both bulk versions.

## Production configuration publication

The existing `scripts/publish_pipeline_configs.py` helper published immutable
snapshots with release `2026-11-24-bulk-30k` to the private watermark-data bucket.
The backend's `scripts/watermark_pipeline_config_refs.json` selects the two new
bulk snapshots for new jobs. The previous small-job references remain selected.
Only `limits.maximum_items` differs from the previous API-selected bulk policies.

The backend master deployment promotes these references and the 30,000-image
setting to the API and watermark Jobs. This configuration-only worker release
uses existing external-policy loading and does not need a GPU image rebuild.
Existing jobs retain their saved configuration references. No processing job is
started solely to verify the new allowance.

Published bulk identities:

| Pipeline | Generation | SHA-256 |
| --- | --- | --- |
| watermark-bulk-v1 | 1795524602280993 | a85f106f9e41cb6f9bc73760d07de047b8239a3370d12fb448318f77100bd962 |
| watermark-bulk-v2 | 1795524603634130 | 96fc2bdc777e1f8a4c550c5fa80cec928e18ad1c51c0d2320b9feee0c9f6ec2b |

Object paths are `watermark-configs/<pipeline>/2026-11-24-bulk-30k.yaml` in
`project-5909a738-2fdc-451e-ad6-watermark-data`.

## Rollback

Restore the prior backend-selected bulk references and 25,000 maximum through
the normal backend deployment, with compatible UI/API validation. Account for
queued jobs above 25,000 before reducing the allowance; preserve their pinned
configuration objects and saved identities. This limit change does not establish
a 30,000-image throughput or completion-time guarantee.
