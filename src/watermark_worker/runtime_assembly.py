from __future__ import annotations

from pathlib import Path

from watermark_worker.config import PipelineConfig
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
) -> PipelineExecutor:
    """Assemble the production GPU path without runtime installs or public downloads."""
    model_storage = storage if isinstance(storage, GcsStorageAdapter) else GcsStorageAdapter()
    runtime_provider = LocalGpuRuntimeProvider()
    return build_production_executor(
        config=config,
        manifest=manifest,
        materializer=ModelArtifactCache(
            storage=model_storage,
            cache_root=work_root / "artifacts",
        ),
        preparer=ModelBundleCache(work_root / "bundles"),
        runtime_provider=runtime_provider,
    )
