"""In-process OCR backend based on RapidOCR.

The module keeps two engines:

* a detection + recognition engine for panels and full screens, which
  returns line and word boxes;
* a recognition-only engine for tight single-line crops such as timers,
  where the text detector is unreliable and unnecessary.

Both engines load their models lazily on first use. Calls are serialized per
engine because ``RapidOCR`` keeps the per-call ``use_det``/``use_rec`` flags as
instance state; the ONNX Runtime sessions themselves are thread-safe.
"""

import threading
from typing import List, Tuple

import numpy as np

from wosutil.utils import log_message

Box = Tuple[int, int, int, int]
Line = Tuple[str, Box]
WordLine = List[Tuple[str, Box]]

MIN_SCORE = 0.5

_init_lock = threading.Lock()
_det_engine = None
_rec_engine = None
_det_call_lock = threading.Lock()
_rec_call_lock = threading.Lock()


def _load_engine(engine_name: str):
    global _det_engine, _rec_engine
    from rapidocr import RapidOCR

    engine = RapidOCR(params={"Global.log_level": "warning"})
    if engine_name == "det":
        _det_engine = engine
    else:
        _rec_engine = engine
    return engine


def _get_det_engine():
    """Return the detection + recognition engine, loading it on first use."""
    if _det_engine is None:
        with _init_lock:
            if _det_engine is None:
                return _load_engine("det")
    return _det_engine


def _get_rec_engine():
    """Return the recognition-only engine, loading it on first use."""
    if _rec_engine is None:
        with _init_lock:
            if _rec_engine is None:
                return _load_engine("rec")
    return _rec_engine


def _quad_to_box(quad) -> Box:
    """Convert a four-point OCR quadrilateral to an axis-aligned (x, y, w, h) box."""
    xs = [float(point[0]) for point in quad]
    ys = [float(point[1]) for point in quad]
    x1, y1 = int(min(xs)), int(min(ys))
    x2, y2 = int(max(xs)), int(max(ys))
    return x1, y1, max(1, x2 - x1), max(1, y2 - y1)


def _has_ascii_alnum(text: str) -> bool:
    """Return True when the text contains at least one ASCII letter or digit."""
    return any(char.isascii() and char.isalnum() for char in text)


def _fallback_words(text: str, box: Box) -> WordLine:
    """Split a line into words with proportional boxes when word boxes are unavailable."""
    parts = [part for part in text.split() if _has_ascii_alnum(part)]
    if not parts:
        return []
    if len(parts) == 1:
        return [(parts[0], box)]
    x, y, width, height = box
    total_chars = sum(len(part) for part in parts)
    words: WordLine = []
    for part in parts:
        part_width = max(1, int(round(width * len(part) / total_chars)))
        words.append((part, (x, y, part_width, height)))
        x += part_width
    return words


def _fallback_chars(text: str, box: Box) -> WordLine:
    """Split a line into characters with proportional boxes when needed."""
    chars = [char for char in text if not char.isspace()]
    if not chars:
        return []
    x, y, width, height = box
    char_width = max(1, width // len(chars))
    return [(char, (x + index * char_width, y, char_width, height)) for index, char in enumerate(chars)]


def _detect(img_bgr: np.ndarray, single_char: bool = False) -> Tuple[List[Line], List[WordLine]]:
    """Run detection + recognition and return line and word boxes."""
    if img_bgr is None or img_bgr.size == 0:
        return [], []
    try:
        with _det_call_lock:
            result = _get_det_engine()(
                img_bgr,
                use_det=True,
                use_cls=False,
                use_rec=True,
                return_word_box=True,
                return_single_char_box=single_char,
            )
    except Exception as exc:
        log_message(f"OCR error while reading text lines: {exc}", level="error")
        return [], []

    texts = list(getattr(result, "txts", None) or ())
    boxes = getattr(result, "boxes", None)
    if not texts or boxes is None:
        return [], []

    word_results = getattr(result, "word_results", None)
    lines: List[Line] = []
    word_lines: List[WordLine] = []
    for index, text in enumerate(texts):
        if not text.strip() or index >= len(boxes) or not _has_ascii_alnum(text):
            continue
        box = _quad_to_box(boxes[index])
        lines.append((text, box))
        words: WordLine = []
        if word_results is not None and index < len(word_results) and word_results[index]:
            for word, score, quad in word_results[index]:
                if word.strip() and (single_char or _has_ascii_alnum(word)) and float(score) >= MIN_SCORE:
                    words.append((word, _quad_to_box(quad)))
        if not words:
            words = _fallback_chars(text, box) if single_char else _fallback_words(text, box)
        if words:
            word_lines.append(words)

    lines.sort(key=lambda item: (item[1][1], item[1][0]))
    word_lines.sort(key=lambda line: (min(box[1] for _, box in line), min(box[0] for _, box in line)))
    return lines, word_lines


def read_chars(img_bgr: np.ndarray) -> List[Line]:
    """Read every character of a BGR image, returning (char, (x, y, w, h)) pairs."""
    chars = [char for line in _detect(img_bgr, single_char=True)[1] for char in line]
    chars.sort(key=lambda item: (item[1][1], item[1][0]))
    return chars


def read_lines(img_bgr: np.ndarray) -> List[Line]:
    """Read the text lines of a BGR image, returning (text, (x, y, w, h)) pairs."""
    return _detect(img_bgr)[0]


def read_word_lines(img_bgr: np.ndarray) -> List[WordLine]:
    """Read the text lines of a BGR image, keeping each line's word boxes."""
    return _detect(img_bgr)[1]


def read_line(img_bgr: np.ndarray) -> Tuple[str, float]:
    """Read a single text line with recognition only, returning (text, confidence).

    This skips text detection, which is both faster and more reliable for the
    small fixed ROIs used by timers and the game clock.
    """
    if img_bgr is None or img_bgr.size == 0:
        return "", 0.0
    try:
        with _rec_call_lock:
            result = _get_rec_engine()(
                img_bgr,
                use_det=False,
                use_cls=False,
                use_rec=True,
                return_word_box=False,
            )
    except Exception as exc:
        log_message(f"OCR error while reading a text line: {exc}", level="error")
        return "", 0.0

    texts = []
    scores = []
    for text, score in zip(getattr(result, "txts", None) or (), getattr(result, "scores", None) or (), strict=False):
        if text.strip() and _has_ascii_alnum(text):
            texts.append(text)
            scores.append(float(score))
    if not texts:
        return "", 0.0
    return " ".join(texts), min(scores)
