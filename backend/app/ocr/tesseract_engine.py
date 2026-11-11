"""Tesseract-based OCR engine with conservative image preprocessing."""
import io

import pytesseract
from PIL import Image, ImageEnhance, ImageOps

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.ocr.base import (
    OCRDependencyError,
    OCREngine,
    OCRPageData,
    OCRProcessingError,
    OCRWord,
)

logger = get_logger(__name__)

# A modest upscale helps Tesseract read small invoice-table characters.
_UPSCALE_FACTOR = 2
_CONTRAST_FACTOR = 1.25
_TESSERACT_CONFIG = "--oem 3 --psm 4"


class TesseractOCREngine(OCREngine):
    name = "tesseract"

    def __init__(self) -> None:
        settings = get_settings()
        if settings.TESSERACT_CMD:
            pytesseract.pytesseract.tesseract_cmd = settings.TESSERACT_CMD

    def is_available(self) -> bool:
        try:
            pytesseract.get_tesseract_version()
            return True
        except (pytesseract.TesseractNotFoundError, OSError):
            return False

    @staticmethod
    def _preprocess_image(image: Image.Image) -> Image.Image:
        """Normalize orientation and improve text visibility without cropping."""
        image = ImageOps.exif_transpose(image)
        image = ImageOps.grayscale(image)
        image = ImageOps.autocontrast(image, cutoff=1)

        resampling = getattr(Image, "Resampling", Image)
        image = image.resize(
            (image.width * _UPSCALE_FACTOR, image.height * _UPSCALE_FACTOR),
            resample=resampling.LANCZOS,
        )
        image = ImageEnhance.Contrast(image).enhance(_CONTRAST_FACTOR)
        return image

    def extract_text_from_image_bytes(self, image_bytes: bytes) -> tuple[str, float | None]:
        """Keep the original public interface for existing callers."""
        result = self.extract_structured_text_from_image_bytes(image_bytes)
        return result.text, result.confidence

    def extract_structured_text_from_image_bytes(self, image_bytes: bytes) -> OCRPageData:
        """Run Tesseract once and return text, aggregate confidence, and word geometry."""
        if not self.is_available():
            raise OCRDependencyError(
                "Tesseract OCR executable not found. Install it and/or set TESSERACT_CMD."
            )

        try:
            with Image.open(io.BytesIO(image_bytes)) as source_image:
                # Preserve the current OCR behavior: correct EXIF orientation, but
                # do not apply resizing/contrast preprocessing to this pass.
                image = ImageOps.exif_transpose(source_image).copy()

            data = pytesseract.image_to_data(
                image,
                config=_TESSERACT_CONFIG,
                output_type=pytesseract.Output.DICT,
            )

            line_groups: dict[
                tuple[int, int, int, int],
                list[tuple[int, int, str]],
            ] = {}
            confidences: list[float] = []
            words: list[OCRWord] = []

            for index, raw_text in enumerate(data.get("text", [])):
                word_text = raw_text.strip()
                if not word_text:
                    continue

                try:
                    page_number = int(data["page_num"][index])
                    block_number = int(data["block_num"][index])
                    paragraph_number = int(data["par_num"][index])
                    line_number = int(data["line_num"][index])
                    word_number = int(data["word_num"][index])
                    left = int(data["left"][index])
                    top = int(data["top"][index])
                    width = int(data["width"][index])
                    height = int(data["height"][index])
                    raw_confidence = float(data["conf"][index])
                except (KeyError, IndexError, TypeError, ValueError):
                    # Skip malformed entries rather than failing the entire page.
                    continue

                confidence_value = raw_confidence if raw_confidence >= 0 else None
                line_key = (page_number, block_number, paragraph_number, line_number)
                line_groups.setdefault(line_key, []).append(
                    (left, word_number, word_text)
                )

                if confidence_value is not None:
                    confidences.append(confidence_value)

                words.append(
                    OCRWord(
                        text=word_text,
                        confidence=confidence_value,
                        left=left,
                        top=top,
                        width=width,
                        height=height,
                        block_number=block_number,
                        paragraph_number=paragraph_number,
                        line_number=line_number,
                        word_number=word_number,
                    )
                )

            text_lines: list[str] = []
            for key in sorted(line_groups):
                line_words = sorted(
                    line_groups[key],
                    key=lambda item: (item[0], item[1]),
                )
                text_lines.append(" ".join(word for _, _, word in line_words))

            text_result = "\n".join(text_lines)
            confidence = (
                sum(confidences) / len(confidences)
                if confidences else None
            )
            return OCRPageData(
                text=text_result,
                confidence=confidence,
                words=words,
            )

        except pytesseract.TesseractNotFoundError as exc:
            raise OCRDependencyError("Tesseract OCR executable not found.") from exc
        except Exception as exc:  # noqa: BLE001
            logger.error("Tesseract OCR failed: %s", exc)
            raise OCRProcessingError(f"OCR failed: {exc}") from exc
