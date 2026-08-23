"""Structured pipeline compilation diagnostics."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PipelineCompilationDiagnostic:
    """One deterministic validation failure at a definition path."""

    code: str
    path: str
    message: str


class PipelineCompilationError(ValueError):
    """Raised when a pipeline definition cannot be compiled."""

    def __init__(self, diagnostics: tuple[PipelineCompilationDiagnostic, ...]) -> None:
        self.diagnostics = diagnostics
        summary = "; ".join(
            f"{diagnostic.path}: {diagnostic.message}" for diagnostic in diagnostics
        )
        super().__init__(summary)
