from __future__ import annotations

from pathlib import Path

from watermark_worker.config import PipelineConfig
from watermark_worker.contracts import ArtifactProfile
from watermark_worker.gpu_runtime.acceptance import GpuAcceptanceRecorder
from watermark_worker.gpu_runtime.provider import LocalGpuRuntimeProvider
from watermark_worker.model_bundles import ModelBundleCache
from watermark_worker.model_registry import ModelArtifactCache, ModelManifest
from watermark_worker.pipeline import PipelineExecutor
from watermark_worker.production_executor import build_production_executor
from watermark_worker.storage import GcsStorageAdapter, StorageAdapter


def build_default_production_executor(
    *,
    config: PipelineConfig,
    manifest: ModelManifest,
    storage: StorageAdapter,
    work_root: Path,
    artifact_profile: ArtifactProfile = ArtifactProfile.FULL,
) -> PipelineExecutor:
    """Assemble the production GPU path without runtime installs or public downloads."""
    model_storage = storage if isinstance(storage, GcsStorageAdapter) else GcsStorageAdapter()
    runtime_provider = LocalGpuRuntimeProvider()
    acceptance = GpuAcceptanceRecorder(
        torch_module=runtime_provider.torch_module,
        device=runtime_provider.cuda_device,
        work_root=work_root,
    )
    acceptance.start()
    return build_production_executor(
        config=config,
        manifest=manifest,
        materializer=ModelArtifactCache(
            storage=model_storage,
            cache_root=work_root / "artifacts",
        ),
        preparer=ModelBundleCache(work_root / "bundles"),
        runtime_provider=runtime_provider,
        artifact_profile=artifact_profile,
        on_stage_lifecycle=acceptance.record,
    )
