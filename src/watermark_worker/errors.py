from __future__ import annotations

from watermark_worker.contracts import ErrorCode


class WorkerError(Exception):
    """A classified worker failure with a stable machine-readable code."""

    def __init__(self, message: str, error_code: ErrorCode) -> None:
        super().__init__(message)
        self.error_code = error_code


class InvalidManifestError(WorkerError):
    def __init__(self, message: str) -> None:
        super().__init__(message, ErrorCode.INVALID_MANIFEST)


class InvalidConfigurationError(WorkerError):
    def __init__(self, message: str) -> None:
        super().__init__(message, ErrorCode.INVALID_CONFIGURATION)


class ModelValidationError(WorkerError):
    def __init__(self, message: str, error_code: ErrorCode = ErrorCode.MODEL_CHECKSUM_MISMATCH) -> None:
        super().__init__(message, error_code)


class GpuInitializationError(WorkerError):
    def __init__(self, message: str) -> None:
        super().__init__(message, ErrorCode.CUDA_UNAVAILABLE)


class PermanentStorageError(WorkerError):
    def __init__(self, message: str, error_code: ErrorCode = ErrorCode.STORAGE_PERMISSION_DENIED) -> None:
        super().__init__(message, error_code)


class StorageNotFoundError(PermanentStorageError):
    def __init__(self, message: str) -> None:
        super().__init__(message, ErrorCode.STORAGE_NOT_FOUND)


class RetryableStorageError(WorkerError):
    def __init__(self, message: str) -> None:
        super().__init__(message, ErrorCode.STORAGE_RETRYABLE)


class RetryableExternalError(WorkerError):
    def __init__(self, message: str) -> None:
        super().__init__(message, ErrorCode.EXTERNAL_PLATFORM_RETRYABLE)


class StaleOutputError(PermanentStorageError):
    def __init__(self, message: str) -> None:
        super().__init__(message, ErrorCode.STALE_OUTPUT)


class ConditionalWriteCollision(PermanentStorageError):
    def __init__(self, message: str) -> None:
        super().__init__(message, ErrorCode.CONDITIONAL_WRITE_COLLISION)
