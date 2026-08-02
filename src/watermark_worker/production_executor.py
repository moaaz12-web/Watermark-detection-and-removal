from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from watermark_worker.config import (
    BackgroundRemovalConfig,
    DetectionConfig,
    InpaintingConfig,
    PipelineConfig,
    SegmentationConfig,
)
from watermark_worker.contracts import ArtifactProfile
from watermark_worker.errors import ModelValidationError
from watermark_worker.model_bundles import PreparedModelArtifact
from watermark_worker.model_registry import (
    CachedModelArtifact,
    ModelArtifact,
    ModelManifest,
    validate_model_bindings,
)
from watermark_worker.pipeline import PipelineExecutor, StageLifecycleEvent
from watermark_worker.production_stages import (
    DetectionRuntime,
    LamaRuntime,
    ProductionBackgroundRemovalStage,
    ProductionDetectionStage,
    ProductionInpaintingStage,
    RmbgRuntime,
)


class ModelArtifactMaterializer(Protocol):
    def materialize_selected(
        self, artifacts: list[ModelArtifact], artifact_ids: set[str]
    ) -> dict[str, CachedModelArtifact]: ...


class ModelArtifactPreparer(Protocol):
    def prepare(self, cached: CachedModelArtifact) -> PreparedModelArtifact: ...


@dataclass(frozen=True)
class DetectionModelArtifacts:
    sam2: PreparedModelArtifact
    yolo: PreparedModelArtifact | None
    grounding_dino: PreparedModelArtifact | None


class ProductionRuntimeProvider(Protocol):
    """Heavyweight runtime boundary implemented by the future GPU image."""

    def load_background_removal(
        self,
        artifact: PreparedModelArtifact,
        config: BackgroundRemovalConfig,
    ) -> RmbgRuntime: ...

    def load_detection(
        self,
        artifacts: DetectionModelArtifacts,
        config: DetectionConfig,
        segmentation_config: SegmentationConfig,
    ) -> DetectionRuntime: ...

    def load_inpainting(
        self,
        artifact: PreparedModelArtifact,
        config: InpaintingConfig,
    ) -> LamaRuntime: ...


def build_production_executor(
    *,
    config: PipelineConfig,
    manifest: ModelManifest,
    materializer: ModelArtifactMaterializer,
    preparer: ModelArtifactPreparer,
    runtime_provider: ProductionRuntimeProvider,
    artifact_profile: ArtifactProfile = ArtifactProfile.FULL,
    on_stage_lifecycle: Callable[[StageLifecycleEvent], None] | None = None,
) -> PipelineExecutor:
    """Prepare immutable artifacts and assemble one all-resident pipeline executor."""

    validate_model_bindings(config, manifest)
    selected_ids = config.model_artifact_ids
    cached = materializer.materialize_selected(manifest.artifacts, selected_ids)
    _require_exact_artifact_set("materialized", selected_ids, set(cached))

    prepared: dict[str, PreparedModelArtifact] = {}
    for artifact_id in sorted(selected_ids):
        item = preparer.prepare(cached[artifact_id])
        if item.artifact.artifact_id != artifact_id:
            raise ModelValidationError(
                f"prepared model artifact identity mismatch for {artifact_id}"
            )
        prepared[artifact_id] = item

    rmbg = prepared[config.background_removal.model_artifact_id]
    sam2 = prepared[config.segmentation.model_artifact_id]
    lama = prepared[config.inpainting.model_artifact_id]
    yolo = (
        prepared[config.detection.yolo.model_artifact_id] if config.detection.yolo.enabled else None
    )
    grounding_dino = (
        prepared[config.detection.grounding_dino.model_artifact_id]
        if config.detection.grounding_dino.enabled
        else None
    )
    detection_artifacts = DetectionModelArtifacts(
        sam2=sam2,
        yolo=yolo,
        grounding_dino=grounding_dino,
    )

    return PipelineExecutor(
        background_removal=ProductionBackgroundRemovalStage(
            lambda: runtime_provider.load_background_removal(rmbg, config.background_removal),
            artifact_profile,
        ),
        detection=ProductionDetectionStage(
            lambda: runtime_provider.load_detection(
                detection_artifacts, config.detection, config.segmentation
            ),
            artifact_profile,
        ),
        inpainting=ProductionInpaintingStage(
            lambda: runtime_provider.load_inpainting(lama, config.inpainting),
            config.inpainting,
        ),
        safety_gate=config.safety_gate,
        on_stage_lifecycle=on_stage_lifecycle,
    )


def _require_exact_artifact_set(state: str, expected: set[str], actual: set[str]) -> None:
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    if missing or unexpected:
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unexpected:
            details.append("unexpected " + ", ".join(unexpected))
        raise ModelValidationError(
            f"{state} model artifact set does not match the configuration: " + "; ".join(details)
        )
