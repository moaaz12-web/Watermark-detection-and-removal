from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from watermark_worker.config import (
    BackgroundRemovalConfig,
    DetectionConfig,
    InpaintingConfig,
    load_pipeline_config,
)
from watermark_worker.model_bundles import PreparedModelArtifact
from watermark_worker.model_registry import (
    CachedModelArtifact,
    ModelArtifact,
    load_model_manifest,
)
from watermark_worker.production_executor import (
    DetectionModelArtifacts,
    build_production_executor,
)
from watermark_worker.production_stages import (
    DetectionPrediction,
    RmbgPrediction,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class RecordingMaterializer:
    selected_ids: set[str] | None = None

    def materialize_selected(
        self, artifacts: list[ModelArtifact], artifact_ids: set[str]
    ) -> dict[str, CachedModelArtifact]:
        self.selected_ids = set(artifact_ids)
        return {
            artifact.artifact_id: CachedModelArtifact(
                artifact=artifact,
                path=Path("/verified") / artifact.filename,
            )
            for artifact in artifacts
            if artifact.artifact_id in artifact_ids
        }


@dataclass
class RecordingPreparer:
    prepared_ids: list[str] = field(default_factory=list)

    def prepare(self, cached: CachedModelArtifact) -> PreparedModelArtifact:
        self.prepared_ids.append(cached.artifact.artifact_id)
        return PreparedModelArtifact(
            artifact=cached.artifact,
            source_path=cached.path,
            runtime_path=Path("/prepared") / cached.artifact.artifact_id,
        )


class FakeRmbgRuntime:
    def predict(self, source: Image.Image) -> RmbgPrediction:
        return RmbgPrediction(
            detector_image=source.convert("RGB"),
            foreground_mask=Image.new("L", source.size, 255),
        )

    def close(self) -> None:
        pass


class FakeDetectionRuntime:
    def predict(self, image: Image.Image) -> tuple[DetectionPrediction, ...]:
        return ()

    def close(self) -> None:
        pass


class FakeLamaRuntime:
    def predict(self, source: Image.Image, mask: Image.Image) -> Image.Image:
        return source

    def close(self) -> None:
        pass


@dataclass
class RecordingRuntimeProvider:
    loaded: list[str] = field(default_factory=list)
    detection_artifacts: DetectionModelArtifacts | None = None

    def load_background_removal(
        self,
        artifact: PreparedModelArtifact,
        config: BackgroundRemovalConfig,
    ) -> FakeRmbgRuntime:
        self.loaded.append("background_removal")
        return FakeRmbgRuntime()

    def load_detection(
        self,
        artifacts: DetectionModelArtifacts,
        config: DetectionConfig,
    ) -> FakeDetectionRuntime:
        self.loaded.append("detection")
        self.detection_artifacts = artifacts
        return FakeDetectionRuntime()

    def load_inpainting(
        self,
        artifact: PreparedModelArtifact,
        config: InpaintingConfig,
    ) -> FakeLamaRuntime:
        self.loaded.append("inpainting")
        return FakeLamaRuntime()


def test_builder_prepares_only_enabled_models_and_loads_runtimes_lazily() -> None:
    config = load_pipeline_config(PROJECT_ROOT / "configs" / "watermark-v1.yaml").config
    manifest = load_model_manifest(PROJECT_ROOT / "model-manifest.json").manifest
    materializer = RecordingMaterializer()
    preparer = RecordingPreparer()
    provider = RecordingRuntimeProvider()

    executor = build_production_executor(
        config=config,
        manifest=manifest,
        materializer=materializer,
        preparer=preparer,
        runtime_provider=provider,
    )

    assert materializer.selected_ids == config.model_artifact_ids
    assert set(preparer.prepared_ids) == config.model_artifact_ids
    assert all("owlv2" not in artifact_id for artifact_id in preparer.prepared_ids)
    assert provider.loaded == []

    image = Image.new("RGB", (2, 2), "white")
    executor.background_removal.run(image)
    executor.background_removal.close()
    assert provider.loaded == ["background_removal"]

    executor.detection.run(image)
    executor.detection.close()
    assert provider.loaded == ["background_removal", "detection"]
    assert provider.detection_artifacts is not None
    assert provider.detection_artifacts.yolo is not None
    assert provider.detection_artifacts.grounding_dino is not None


def test_builder_keeps_disabled_detector_artifacts_out_of_runtime_contract() -> None:
    loaded = load_pipeline_config(PROJECT_ROOT / "configs" / "watermark-v1.yaml")
    config = loaded.config.model_copy(
        update={
            "detection": loaded.config.detection.model_copy(
                update={
                    "mode": "grounding_dino",
                    "yolo": loaded.config.detection.yolo.model_copy(
                        update={"enabled": False}
                    ),
                }
            )
        }
    )
    manifest = load_model_manifest(PROJECT_ROOT / "model-manifest.json").manifest
    provider = RecordingRuntimeProvider()
    executor = build_production_executor(
        config=config,
        manifest=manifest,
        materializer=RecordingMaterializer(),
        preparer=RecordingPreparer(),
        runtime_provider=provider,
    )

    executor.detection.run(Image.new("RGB", (1, 1), "white"))

    assert provider.detection_artifacts is not None
    assert provider.detection_artifacts.yolo is None
    assert provider.detection_artifacts.grounding_dino is not None
