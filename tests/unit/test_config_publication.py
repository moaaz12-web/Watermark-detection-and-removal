from pathlib import Path

import pytest

from scripts.publish_pipeline_configs import snapshot_metadata


def test_snapshot_metadata_validates_all_current_pipeline_configs() -> None:
    metadata = snapshot_metadata(
        Path("configs"),
        bucket="config-bucket",
        prefix="watermark-configs/",
        release="release-2026-08-05",
    )

    assert set(metadata) == {
        "watermark-v1",
        "watermark-bulk-v1",
        "watermark-v2",
        "watermark-bulk-v2",
    }
    assert metadata["watermark-v2"]["uri"].endswith(
        "/watermark-v2/release-2026-08-05.yaml"
    )
    assert len(metadata["watermark-v2"]["sha256"]) == 64


@pytest.mark.parametrize(
    "release",
    ["../unsafe", "", "release/with-slash"],
)
def test_snapshot_metadata_rejects_unsafe_release(release: str) -> None:
    with pytest.raises(ValueError, match="release"):
        snapshot_metadata(
            Path("configs"),
            bucket="config-bucket",
            prefix="watermark-configs/",
            release=release,
        )
