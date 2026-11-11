"""OCR abstraction (Phase 3): interface + result types + exceptions."""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from enum import Enum


class ExtractionSource(str, Enum):
    EMBEDDED_TEXT = "embedded_text"
    OCR = "ocr"


class OCRStatus(str, Enum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


@dataclass
class OCRWord:
    """One recognized word and its location within an OCR page."""

    text: str
    confidence: float | None
    left: int
    top: int
    width: int
    height: int
    block_number: int | None = None
    paragraph_number: int | None = None
    line_number: int | None = None
    word_number: int | None = None


@dataclass
class OCRPageData:
    """Structured result for OCR performed on a single image/page."""

    text: str
    confidence: float | None = None
    words: list[OCRWord] = field(default_factory=list)


@dataclass
class PageResult:
    page_number: int
    text: str
    source: ExtractionSource
    confidence: float | None = None  # None when not meaningfully available (e.g. embedded text)
    warning: str | None = None
    words: list[OCRWord] = field(default_factory=list)


@dataclass
class OCRResult:
    status: OCRStatus
    engine: str
    pages: list[PageResult] = field(default_factory=list)
    processing_duration_ms: float = 0.0
    warnings: list[str] = field(default_factory=list)
    dependency_missing: bool = False

    @property
    def full_text(self) -> str:
        return "\n\n".join(p.text for p in self.pages if p.text)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def average_confidence(self) -> float | None:
        scored = [p.confidence for p in self.pages if p.confidence is not None]
        return (sum(scored) / len(scored)) if scored else None


class OCRDependencyError(Exception):
    """Raised when a required external OCR dependency (e.g. Tesseract) is unavailable."""


class OCRProcessingError(Exception):
    """Raised when OCR/text extraction fails for reasons other than a missing dependency."""


class OCREngine(abc.ABC):
    name: str

    @abc.abstractmethod
    def is_available(self) -> bool: ...

    @abc.abstractmethod
    def extract_text_from_image_bytes(self, image_bytes: bytes) -> tuple[str, float | None]: ...

    def extract_structured_text_from_image_bytes(self, image_bytes: bytes) -> OCRPageData:
        """Return structured OCR data when supported.

        The default implementation preserves compatibility with existing OCR engines:
        engines only implementing the original tuple-returning method continue to work,
        but provide no word-level layout metadata until they override this method.
        """
        text, confidence = self.extract_text_from_image_bytes(image_bytes)
        return OCRPageData(text=text, confidence=confidence)
