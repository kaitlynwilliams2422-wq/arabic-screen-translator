# -*- coding: utf-8 -*-
"""
Mia's Arabic Screen Translator
================================
Pick an app window (or the whole screen), and this app watches it.
When it sees Arabic text, it reads the text (OCR), writes it in
English letters (transliteration), and translates it into English —
live, in the app and in a little overlay window. Screen Translate
mode goes further: it reads ALL the Arabic in the watched window
and draws an English label right on top of each line, in place.

Honest notes:
- Reading the screen needs the free Tesseract program with Arabic
  language data installed (see README.txt). Without it, the app
  tells you exactly what to install — it never makes text up.
- Translating needs the internet (it uses Google Translate through
  the free deep-translator library).
- It works on Arabic text it can SEE: Modern Standard Arabic and
  dialects like Egyptian. Dialect translation quality can vary.

This file can be imported without opening the window, so the
translation / transliteration helpers can be tested on their own.
"""

import os
import sys
import time
import queue
import shutil
import threading

# tkinter is only needed for the window itself. Guard the import so
# the helper functions below can be imported and tested anywhere.
try:
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
except Exception:  # pragma: no cover - no display / no tk
    tk = None
    ttk = None
    messagebox = None
    filedialog = None

APP_TITLE = "Mia's Arabic Screen Translator"
SAMPLE_SENTENCE = "أَهْلًا، كَيْفَ حَالُكَ الْيَوْمَ؟"
WHOLE_SCREEN = "SCREEN"  # sentinel hwnd value for the whole screen

BG = "#0e0f13"
PANEL = "#17181d"
CARD = "#1f2129"
TEXT = "#e8eaf2"
MUTED = "#9aa0ae"
TEAL = "#14b8a6"
TEAL_TEXT = "#06231f"
GOOD = "#4ade80"
WARN = "#fbbf24"


# ---------------------------------------------------------------------
# Window listing / rectangles (Windows, via ctypes — no extra installs)
# ---------------------------------------------------------------------

def list_open_windows():
    """Return [(hwnd, title), ...] for visible windows with a title.

    Windows only. On any other system (or on any error) returns [].
    """
    if sys.platform != "win32":
        return []
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        windows = []

        enum_proc_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _callback(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value.strip()
            if title:
                windows.append((int(hwnd), title))
            return True

        user32.EnumWindows(enum_proc_type(_callback), 0)
        return windows
    except Exception:
        return []


def sort_windows(windows):
    """Sort a [(hwnd, title)] window list for the picker.

    Any window whose title contains "Roblox" is pinned to the top
    and marked with a game emoji — this app is mostly used beside
    Roblox. All other windows keep their original order.
    Returns [(hwnd, display_title), ...].
    """
    roblox = []
    others = []
    for hwnd, title in windows:
        if "roblox" in title.lower():
            roblox.append((hwnd, "🎮 " + title))
        else:
            others.append((hwnd, title))
    return roblox + others


def chat_crop_box(rect):
    """The Roblox chat area inside a window rectangle.

    Chat sits in the top-left: full height is not needed.
    left/top stay; right = left + 45% of width;
    bottom = top + 50% of height. All ints.
    """
    left, top, right, bottom = rect
    width = right - left
    height = bottom - top
    return (int(left), int(top),
            int(left + 0.45 * width), int(top + 0.50 * height))


def capture_box_for(rect, chat_mode):
    """The box to screenshot: chat crop when chat mode is on,
    otherwise the full window rectangle."""
    if rect is None:
        return None
    if chat_mode:
        return chat_crop_box(rect)
    return rect


def get_window_rect(hwnd):
    """Return (left, top, right, bottom) for a window, or None."""
    if sys.platform != "win32" or hwnd in (None, WHOLE_SCREEN):
        return None
    try:
        import ctypes
        from ctypes import wintypes

        rect = wintypes.RECT()
        ok = ctypes.windll.user32.GetWindowRect(
            wintypes.HWND(hwnd), ctypes.byref(rect))
        if not ok:
            return None
        if rect.right <= rect.left or rect.bottom <= rect.top:
            return None
        return (rect.left, rect.top, rect.right, rect.bottom)
    except Exception:
        return None


# ---------------------------------------------------------------------
# Screen capture + OCR
# ---------------------------------------------------------------------

def capture_screen(bbox=None):
    """Grab a screenshot (PIL Image) of bbox, or the whole screen.

    Returns (image, error). image is None when capture failed.
    """
    try:
        from PIL import ImageGrab
    except Exception:
        return None, "Pillow is not installed yet — run run_translator.bat once."
    try:
        img = ImageGrab.grab(bbox=bbox)
        if img is None:
            return None, "The screenshot came back empty."
        return img.convert("RGB"), None
    except Exception as exc:
        return None, "Could not take a screenshot: %s" % exc


def preprocess_for_ocr(image, clear=False):
    """Make a screenshot easier for OCR: 2x bigger + grayscale.

    With clear=True ("Clear text mode") it also boosts contrast
    and turns the image black-or-white only (threshold), which
    helps a lot on game chat text.
    """
    try:
        from PIL import Image, ImageEnhance
        w, h = image.size
        big = image.resize((max(1, w * 2), max(1, h * 2)), Image.LANCZOS)
        gray = big.convert("L")
        if clear:
            gray = ImageEnhance.Contrast(gray).enhance(2.0)
            gray = gray.point(lambda p: 255 if p > 140 else 0)
        return gray
    except Exception:
        return image


def filter_low_confidence(data, min_conf=40):
    """Clean pytesseract image_to_data output.

    Keeps only words whose confidence is >= min_conf, in reading
    order, grouped back into lines. Returns the cleaned text.
    """
    try:
        words = data.get("text", [])
        confs = data.get("conf", [])
        lines = {}
        order = []
        for i, word in enumerate(words):
            if word is None or not str(word).strip():
                continue
            try:
                conf = float(confs[i])
            except Exception:
                conf = -1
            if conf < min_conf:
                continue
            if "line_num" in data:
                key = (data.get("block_num", [0] * len(words))[i],
                       data.get("par_num", [0] * len(words))[i],
                       data.get("line_num", [0] * len(words))[i])
            else:
                key = (0, 0, 0)
            if key not in lines:
                lines[key] = []
                order.append(key)
            lines[key].append(str(word))
        return "\n".join(" ".join(lines[k]) for k in order)
    except Exception:
        return ""


TESSERACT_HELP = (
    "Reading Arabic text needs one extra free program: Tesseract OCR "
    "with Arabic language data.\n"
    "1) Download the Tesseract Windows installer from UB Mannheim "
    "(link in README.txt).\n"
    "2) During setup, on the 'Choose Components' page, tick "
    "'Additional language data' -> 'Arabic'.\n"
    "3) Finish the install, then reopen this app and press Refresh."
)


# ---------------------------------------------------------------------
# Finding the Tesseract program
#
# The Windows installer (UB Mannheim) puts tesseract.exe in
# C:\Program Files\Tesseract-OCR\ and does NOT add it to the PATH,
# so just asking the system path is not enough — look in the usual
# install folders too, and remember a folder the user picks by hand.
# ---------------------------------------------------------------------

TESSERACT_SETTINGS_FILENAME = "tesseract_settings.json"


def default_tesseract_settings_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        TESSERACT_SETTINGS_FILENAME)


def load_tesseract_setting(path=None):
    """Return the saved tesseract.exe path, or None. Never raises."""
    import json
    path = path or default_tesseract_settings_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        cmd = data.get("tesseract_cmd")
        if cmd:
            return str(cmd)
    except Exception:
        pass
    return None


def save_tesseract_setting(path_str, settings_path=None):
    """Save the tesseract.exe path to the settings file.

    Returns the settings file path.
    """
    import json
    settings_path = settings_path or default_tesseract_settings_path()
    with open(settings_path, "w", encoding="utf-8") as fh:
        json.dump({"tesseract_cmd": str(path_str)}, fh,
                  ensure_ascii=False, indent=1)
    return settings_path


def tesseract_candidate_paths():
    """The usual Windows install locations for tesseract.exe.

    Built from environment variables so tests can fake them.
    """
    candidates = []
    program_files = os.environ.get("ProgramFiles")
    program_files_x86 = os.environ.get("ProgramFiles(x86)")
    local_app_data = os.environ.get("LOCALAPPDATA")
    if program_files:
        candidates.append(os.path.join(
            program_files, "Tesseract-OCR", "tesseract.exe"))
    if program_files_x86:
        candidates.append(os.path.join(
            program_files_x86, "Tesseract-OCR", "tesseract.exe"))
    if local_app_data:
        candidates.append(os.path.join(
            local_app_data, "Programs", "Tesseract-OCR",
            "tesseract.exe"))
        candidates.append(os.path.join(
            local_app_data, "Tesseract-OCR", "tesseract.exe"))
    if program_files:
        candidates.append(os.path.join(
            program_files, "Tesseract-OCR", "bin", "tesseract.exe"))
    return candidates


def find_tesseract_cmd():
    """Find tesseract.exe. Returns its path, or None.

    Search order:
    a) the path saved by the "Find Tesseract" button,
    b) "tesseract" on the system PATH,
    c) the usual Windows install folders.
    Never raises.
    """
    try:
        saved = load_tesseract_setting()
        if saved and os.path.isfile(saved):
            return saved
    except Exception:
        pass
    try:
        on_path = shutil.which("tesseract")
        if on_path:
            return on_path
    except Exception:
        pass
    for candidate in tesseract_candidate_paths():
        try:
            if os.path.isfile(candidate):
                return candidate
        except Exception:
            continue
    return None


def apply_tesseract_cmd():
    """Point pytesseract at the found tesseract.exe.

    Returns the path used, or None if Tesseract was not found.
    Never raises.
    """
    cmd = find_tesseract_cmd()
    if cmd:
        try:
            import pytesseract
            pytesseract.pytesseract.tesseract_cmd = cmd
        except Exception:
            pass
    return cmd


def ocr_status():
    """Check whether screen-reading (OCR) is ready.

    Returns (ok, message). Never raises.
    """
    try:
        import pytesseract
    except Exception:
        return False, ("pytesseract is not installed yet — run "
                       "run_translator.bat once, it installs it.")
    # Find the Tesseract program itself (saved setting, PATH, or one
    # of the usual install folders) and point pytesseract at it.
    cmd = apply_tesseract_cmd()
    if not cmd:
        return False, (
            "Tesseract program not found. I looked on the system "
            "path and in the usual install folders (like "
            "C:\\Program Files\\Tesseract-OCR). Press the "
            "\"📁 Find Tesseract\" button and pick the "
            "Tesseract-OCR folder — or reinstall Tesseract "
            "(see README.txt).")
    try:
        langs = pytesseract.get_languages(config="")
    except Exception as exc:
        return False, "Could not check Tesseract languages: %s" % exc
    if "ara" not in langs:
        return False, (
            "Tesseract is installed, but the Arabic language is "
            "missing. Reinstall Tesseract and tick Arabic under "
            "'Additional language data' on the 'Choose Components' "
            "page, then reopen this app.")
    return True, "Tesseract ready (Arabic found) at %s" % cmd


def ocr_image(image, clear=True):
    """Read text from a screenshot with Tesseract (Arabic).

    Uses word-confidence filtering when available (drops words
    Tesseract is unsure about), and falls back to plain reading.
    Returns (text, error). Never raises.
    """
    ok, msg = ocr_status()
    if not ok:
        return None, msg
    try:
        import pytesseract
        prepared = preprocess_for_ocr(image, clear=clear)
        try:
            data = pytesseract.image_to_data(
                prepared, lang="ara",
                output_type=pytesseract.Output.DICT)
            cleaned = filter_low_confidence(data)
            if cleaned.strip():
                return cleaned, None
        except Exception:
            pass
        text = pytesseract.image_to_string(prepared, lang="ara")
        return text or "", None
    except Exception as exc:
        return None, "Reading the screen failed: %s" % exc


# ---------------------------------------------------------------------
# Screen Translate: read Arabic lines WITH their positions, so an
# English label can be drawn right on top of each line, in place —
# the whole watched window/screen, not just the chat box.
# ---------------------------------------------------------------------

SCREEN_TRANSLATE_INTERVAL_MS = 2500  # time between full-area scans
SCREEN_LABEL_BG = "#111111"
SCREEN_LABEL_FG = "#ffe95e"
# The cover overlay's background color. On Windows this exact color
# is made invisible (transparent color), so only the English labels
# show and the game shines through everywhere else.
SCREEN_OVERLAY_KEY = "#ff00ff"


def group_words_into_lines(data, scale=1.0, min_conf=40):
    """Group pytesseract image_to_data output into lines with boxes.

    Pure function — takes the raw data dict (as returned by
    pytesseract.image_to_data with Output.DICT). Words whose
    confidence is below min_conf are dropped (the same rule as
    filter_low_confidence). Words are grouped by
    (block_num, par_num, line_num), in reading order.

    Returns [{"text": str, "box": (x, y, w, h)}, ...] where each
    box is the union of the line's word boxes, DIVIDED by `scale`
    (the OCR preprocessing upscale) so the coordinates are in the
    ORIGINAL image's pixels. Never raises; bad data gives [].
    """
    try:
        words = data.get("text", []) or []
        n = len(words)

        def column(name, default):
            values = data.get(name) or []
            return [values[i] if i < len(values) else default
                    for i in range(n)]

        confs = column("conf", -1)
        lefts = column("left", 0)
        tops = column("top", 0)
        widths = column("width", 0)
        heights = column("height", 0)
        blocks = column("block_num", 0)
        pars = column("par_num", 0)
        line_nums = column("line_num", 0)

        try:
            factor = float(scale)
        except Exception:
            factor = 1.0
        if factor <= 0:
            factor = 1.0

        grouped = {}
        order = []
        for i, word in enumerate(words):
            if word is None or not str(word).strip():
                continue
            try:
                conf = float(confs[i])
            except Exception:
                conf = -1
            if conf < min_conf:
                continue
            try:
                x0 = float(lefts[i])
                y0 = float(tops[i])
                x1 = x0 + float(widths[i])
                y1 = y0 + float(heights[i])
            except Exception:
                continue
            key = (blocks[i], pars[i], line_nums[i])
            if key not in grouped:
                grouped[key] = {"words": [], "x0": None, "y0": None,
                                "x1": None, "y1": None}
                order.append(key)
            entry = grouped[key]
            entry["words"].append(str(word))
            entry["x0"] = x0 if entry["x0"] is None \
                else min(entry["x0"], x0)
            entry["y0"] = y0 if entry["y0"] is None \
                else min(entry["y0"], y0)
            entry["x1"] = x1 if entry["x1"] is None \
                else max(entry["x1"], x1)
            entry["y1"] = y1 if entry["y1"] is None \
                else max(entry["y1"], y1)

        lines = []
        for key in order:
            entry = grouped[key]
            if entry["x0"] is None:
                continue
            box = (int(round(entry["x0"] / factor)),
                   int(round(entry["y0"] / factor)),
                   int(round((entry["x1"] - entry["x0"]) / factor)),
                   int(round((entry["y1"] - entry["y0"]) / factor)))
            lines.append({"text": " ".join(entry["words"]),
                          "box": box})
        return lines
    except Exception:
        return []


def screen_translate_lines(lines):
    """Keep only the lines that should get an English cover label:
    the ones whose text contains Arabic."""
    return [ln for ln in (lines or [])
            if contains_arabic((ln or {}).get("text", ""))]


def ocr_lines_with_boxes(image, clear=True):
    """Read an image and return its Arabic lines WITH positions.

    Returns (lines, error). lines is a list of
    {"text": str, "box": (x, y, w, h)} — only lines containing
    Arabic — with boxes in the ORIGINAL image's pixel coordinates
    (the 2x OCR upscale is divided back out). When reading is not
    ready, returns ([], message). When image_to_data fails,
    returns ([], None): no made-up boxes, ever. Never raises.
    """
    ok, msg = ocr_status()
    if not ok:
        return [], msg
    try:
        import pytesseract
        prepared = preprocess_for_ocr(image, clear=clear)
        scale = 1.0
        try:
            if image is not None and image.size[0]:
                scale = prepared.size[0] / float(image.size[0])
        except Exception:
            scale = 1.0
        try:
            data = pytesseract.image_to_data(
                prepared, lang="ara",
                output_type=pytesseract.Output.DICT)
        except Exception:
            return [], None
        lines = group_words_into_lines(data, scale=scale)
        return screen_translate_lines(lines), None
    except Exception as exc:
        return [], "Reading the screen failed: %s" % exc


def translate_screen_lines(lines, cache=None, translate_fn=None):
    """Attach English to OCR lines for Screen Translate mode.

    Returns [{"text", "box", "english"}] for lines that contain
    Arabic AND translated fine. A line whose translation fails is
    skipped — its spot stays uncovered instead of being covered
    with wrong words. `cache` is a dict of line text -> English,
    reused across scans so the same line is not re-translated
    (translate_to_english has its own cache too).
    """
    if translate_fn is None:
        translate_fn = translate_to_english
    if cache is None:
        cache = {}
    out = []
    for ln in screen_translate_lines(lines):
        text = (ln.get("text") or "").strip()
        if not text:
            continue
        english = cache.get(text)
        if english is None:
            english, _error = translate_fn(text)
            if not english:
                continue
            cache[text] = english
        out.append({"text": text, "box": ln.get("box"),
                    "english": english})
    return out


def label_geometry(box, overlay_origin):
    """Where a cover label goes, relative to the overlay window.

    `box` is (x, y, w, h) in SCREEN coordinates; `overlay_origin`
    is the (left, top) screen position of the overlay window.
    Returns (x, y, w, h) relative to the overlay's top-left corner.
    """
    x, y, w, h = box
    ox, oy = overlay_origin
    return (int(x) - int(ox), int(y) - int(oy), int(w), int(h))


def label_font_size(box_height):
    """Pick a cover-label font size from the line's box height.

    A bit smaller than the box, clamped to 9..16 so labels stay
    readable on tiny chat text and calm on big headings.
    """
    try:
        size = int(round(float(box_height) * 0.62))
    except Exception:
        size = 11
    return max(9, min(16, size))


# ---------------------------------------------------------------------
# Transliteration: Arabic letters -> English letters (how it sounds)
# ---------------------------------------------------------------------

_LETTER_MAP = {
    "ا": "a", "ب": "b", "ت": "t", "ث": "th", "ج": "j", "ح": "h",
    "خ": "kh", "د": "d", "ذ": "dh", "ر": "r", "ز": "z", "س": "s",
    "ش": "sh", "ص": "s", "ض": "d", "ط": "t", "ظ": "z", "ع": "'",
    "غ": "gh", "ف": "f", "ق": "q", "ك": "k", "ل": "l", "م": "m",
    "ن": "n", "ه": "h", "و": "w", "ي": "y", "ة": "a", "ى": "a",
    "أ": "a", "إ": "a", "آ": "a", "ٱ": "a", "ؤ": "w", "ئ": "y",
    "ء": "'",
}

_DIACRITIC_MAP = {
    "ً": "an",   # tanwin fath
    "ٌ": "un",   # tanwin damm
    "ٍ": "in",   # tanwin kasr
    "َ": "a",    # fatha
    "ُ": "u",    # damma
    "ِ": "i",    # kasra
    "ْ": "",     # sukun
    "ٰ": "a",    # superscript alef
    "ٓ": "",     # maddah
    "ٔ": "",     # hamza above
    "ٕ": "",     # hamza below
}

_SHADDA = "ّ"
_TATWEEL = "ـ"

# Arabic punctuation -> its Latin twin, so the "sounds" line is
# fully readable in English letters.
_PUNCT_MAP = {"،": ",", "؟": "?", "؛": ";"}


def transliterate_arabic(text):
    """Write Arabic text in English letters, the way it sounds.

    Pure function. Tatweel is removed, shadda doubles the letter,
    short vowels come from the diacritics when they are written.
    Anything that is not an Arabic letter/diacritic passes through
    unchanged (spaces, punctuation, English words, numbers).
    """
    if not text:
        return ""
    out = []
    i = 0
    n = len(text)
    prev_tanwin_fath = False  # a tanwin fath swallows the alef after it
    while i < n:
        ch = text[i]
        if ch == _TATWEEL:
            i += 1
            continue
        if ch in _LETTER_MAP:
            if prev_tanwin_fath and ch == "ا":
                # The alef that carries a tanwin fath is not a
                # separate sound: "بًا" is "ban", not "bana".
                prev_tanwin_fath = False
                i += 1
                continue
            prev_tanwin_fath = False
            piece = _LETTER_MAP[ch]
            # Gather this letter's diacritic cluster. In real text
            # the vowel mark can come BEFORE the shadda, so collect
            # the whole cluster first, then emit in sound order:
            # letter, doubled letter (shadda), then the vowels.
            j = i + 1
            cluster = []
            while j < n and (text[j] in _DIACRITIC_MAP
                             or text[j] == _SHADDA):
                cluster.append(text[j])
                j += 1
            out.append(piece)
            if _SHADDA in cluster:
                out.append(piece)
            for mark in cluster:
                if mark == _SHADDA:
                    continue
                out.append(_DIACRITIC_MAP[mark])
                if mark == "ً":
                    prev_tanwin_fath = True
            i = j
            continue
        if ch in _DIACRITIC_MAP:
            # A stray mark with no letter before it.
            out.append(_DIACRITIC_MAP[ch])
            prev_tanwin_fath = (ch == "ً")
            i += 1
            continue
        prev_tanwin_fath = False
        out.append(_PUNCT_MAP.get(ch, ch))
        i += 1
    return "".join(out)


# ---------------------------------------------------------------------
# Translation: Arabic -> English (Google Translate via deep-translator)
# ---------------------------------------------------------------------

_TRANSLATION_CACHE = {}
_TRANSLATE_LIMIT = 450


def split_text_for_translation(text, limit=_TRANSLATE_LIMIT):
    """Split text into chunks of at most `limit` characters.

    Splits on line boundaries when possible. "".join(chunks) is
    exactly the original text — nothing is lost or added.
    """
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    chunks = []
    current = ""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        piece = line + ("\n" if i < len(lines) - 1 else "")
        while len(piece) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(piece[:limit])
            piece = piece[limit:]
        if len(current) + len(piece) > limit:
            chunks.append(current)
            current = piece
        else:
            current += piece
    if current:
        chunks.append(current)
    return chunks


def translate_to_english(text):
    """Translate Arabic text to English.

    Returns (english, error). english is None when translation
    failed (no internet, library missing, ...). Never raises.
    Results are cached in memory so repeated lines are free.
    """
    if not text or not text.strip():
        return "", None
    if text in _TRANSLATION_CACHE:
        return _TRANSLATION_CACHE[text], None
    try:
        from deep_translator import GoogleTranslator
    except Exception:
        return None, ("deep-translator is not installed yet — run "
                      "run_translator.bat once, it installs it.")
    try:
        translator = GoogleTranslator(source="ar", target="en")
        parts = []
        for chunk in split_text_for_translation(text):
            if not chunk.strip():
                parts.append(chunk)
                continue
            parts.append(translator.translate(chunk))
        english = "\n".join(p.strip("\n") for p in parts).strip()
        _TRANSLATION_CACHE[text] = english
        return english, None
    except Exception as exc:
        return None, ("Translation failed (internet needed): %s" % exc)


# ---------------------------------------------------------------------
# The live pipeline: OCR text -> rows of (arabic, sounds, english)
# ---------------------------------------------------------------------

def contains_arabic(text):
    """True when the text has at least one Arabic character."""
    if not text:
        return False
    return any("؀" <= ch <= "ۿ" or "ﭐ" <= ch <= "﷿"
               or "ﹰ" <= ch <= "﻿" for ch in text)


_has_arabic = contains_arabic


def parse_chat_line(line):
    """Split a chat line into (speaker, message).

    Roblox chat looks like "PlayerName: message text" or
    "[PlayerName]: message". When there is no speaker, returns
    (None, line).
    """
    import re
    if not line:
        return None, line
    text = line.strip()
    m = re.match(r"^\[([^\]]{1,32})\]:\s*(.*)$", text)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    m = re.match(r"^([A-Za-z0-9_.\-]{2,24}):\s+(.+)$", text)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return None, text


def process_ocr_text(text, seen, translate_fn=None):
    """Turn raw OCR text into NEW translation rows.

    `seen` is a set of chat lines already shown; it is updated in
    place so the same line is never translated twice. Only lines
    whose message contains Arabic are used (English chat lines are
    ignored). The speaker name is split off Roblox-style chat
    lines and only the message is translated. Returns rows:
    (speaker_or_None, arabic_message, transliteration,
     english_or_None, error_or_None, slang_list).
    """
    if translate_fn is None:
        translate_fn = translate_to_english
    rows = []
    if not text:
        return rows
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if line in seen:
            continue
        speaker, message = parse_chat_line(line)
        if not contains_arabic(message):
            continue
        seen.add(line)
        english, error = translate_fn(message)
        rows.append((speaker, message, transliterate_arabic(message),
                     english, error, word_help(message)))
    return rows


# ---------------------------------------------------------------------
# Egyptian word help: common chat slang, in plain English
# ---------------------------------------------------------------------

SLANG = {
    "ازيك": "how are you",
    "عامل ايه": "how are you doing (asking a guy)",
    "عامله ايه": "how are you doing (asking a girl)",
    "ماشي": "okay",
    "يلا": "come on / let's go",
    "اوي": "very / a lot",
    "قوي": "very (also: strong)",
    "كده": "like this / so",
    "كدة": "like this / so",
    "ليه": "why",
    "ايه": "what",
    "ده": "this (masculine)",
    "دي": "this (feminine)",
    "عايز": "I want (guy speaking)",
    "عاوزة": "I want (girl speaking)",
    "معلش": "sorry / never mind",
    "حاضر": "okay, will do",
    "تمام": "perfect / all good",
    "جدا": "very",
    "خالص": "totally / at all",
    "بكرة": "tomorrow",
    "امبارح": "yesterday",
    "دلوقتي": "right now",
    "شوية": "a little / a bit",
    "حاجة": "something / a thing",
}


def word_help(arabic_text):
    """Find known Egyptian slang words in a text.

    Returns [(word, meaning), ...] in the order they appear,
    with no repeats. Two-word phrases are matched first.
    """
    if not arabic_text:
        return []
    import re
    tokens = [t.strip("،؟.!؟:؛ ") for t in arabic_text.split()]
    found = []
    used = set()
    i = 0
    while i < len(tokens):
        if i + 1 < len(tokens):
            phrase = tokens[i] + " " + tokens[i + 1]
            if phrase in SLANG and phrase not in used:
                found.append((phrase, SLANG[phrase]))
                used.add(phrase)
                i += 2
                continue
        word = tokens[i]
        if word in SLANG and word not in used:
            found.append((word, SLANG[word]))
            used.add(word)
        i += 1
    return found


# ---------------------------------------------------------------------
# Say your own words: English -> Arabic
# ---------------------------------------------------------------------

_MY_WORDS_CACHE = {}


def translate_my_words(english_text):
    """Translate HER English words into Arabic.

    Returns (arabic, error). Never raises. Cached like the rest.
    """
    if not english_text or not english_text.strip():
        return "", None
    if english_text in _MY_WORDS_CACHE:
        return _MY_WORDS_CACHE[english_text], None
    try:
        from deep_translator import GoogleTranslator
    except Exception:
        return None, ("deep-translator is not installed yet — run "
                      "run_translator.bat once, it installs it.")
    try:
        translator = GoogleTranslator(source="en", target="ar")
        parts = []
        for chunk in split_text_for_translation(english_text):
            if not chunk.strip():
                parts.append(chunk)
                continue
            parts.append(translator.translate(chunk))
        arabic = "\n".join(p.strip("\n") for p in parts).strip()
        _MY_WORDS_CACHE[english_text] = arabic
        return arabic, None
    except Exception as exc:
        return None, ("Translation failed (internet needed): %s" % exc)


# ---------------------------------------------------------------------
# Small extras: beep, favorites, chat log
# ---------------------------------------------------------------------

def beep():
    """A short beep on Windows. A no-op everywhere else."""
    if sys.platform != "win32":
        return
    try:
        import winsound
        winsound.MessageBeep()
    except Exception:
        pass


FAVORITES_FILENAME = "favorites.json"


def default_favorites_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        FAVORITES_FILENAME)


def load_favorites(path=None):
    """Load saved favorite replies. Returns [] on any problem."""
    import json
    path = path or default_favorites_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        out = []
        for item in data:
            if isinstance(item, dict) and item.get("arabic"):
                out.append({"arabic": item["arabic"],
                            "english": item.get("english", "")})
        return out
    except Exception:
        return []


def save_favorites(path, items):
    """Save favorite replies to a JSON file. Returns the path."""
    import json
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(items, fh, ensure_ascii=False, indent=1)
    return path


def add_favorite(items, arabic, english):
    """Add a favorite if it is not already saved.

    Returns (items, added_bool). No duplicates (same Arabic text).
    """
    for item in items:
        if item.get("arabic") == arabic:
            return items, False
    return items + [{"arabic": arabic, "english": english}], True


def format_chat_log(entries):
    """Format history entries as a readable chat log string.

    Each entry is a dict with time, speaker, arabic, sounds,
    english keys.
    """
    lines = ["Mia's Arabic Screen Translator — chat log", ""]
    for e in entries:
        lines.append("[%s] %s" % (e.get("time", ""),
                                  e.get("speaker") or "Message"))
        lines.append("Arabic:  %s" % e.get("arabic", ""))
        lines.append("Sounds:  %s" % e.get("sounds", ""))
        lines.append("English: %s" % (e.get("english") or
                                      "(not translated)"))
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------
# Reply ideas: a built-in Egyptian phrase library (not AI).
# Ideas are picked by what the incoming message is about.
# ---------------------------------------------------------------------

REPLIES = [
    # --- greeting ---
    {"arabic": "أهلاً وسهلاً، نورت!",
     "english": "Hello and welcome — you lit up the place!",
     "category": "greeting", "keywords": ["hello", "salam", "اهلا"]},
    {"arabic": "أهلاً بيك، إزيك؟",
     "english": "Hello to you — how are you?",
     "category": "greeting", "keywords": ["hello", "hi", "اهلا"]},
    {"arabic": "وعليكم السلام ورحمة الله وبركاته",
     "english": "And peace be upon you too, and God's mercy and blessings.",
     "category": "greeting", "keywords": ["salam", "سلام"]},
    {"arabic": "صباح النور، عامل إيه النهاردة؟",
     "english": "Good morning (reply) — how are you doing today?",
     "category": "greeting", "keywords": ["good morning", "صباح"]},
    # --- how are you ---
    {"arabic": "الحمد لله أنا كويس، إنت عامل إيه؟",
     "english": "Thank God, I'm good. How are you doing? (asking a guy)",
     "category": "how_are_you", "keywords": ["how are you", "ازيك"]},
    {"arabic": "الحمد لله أنا كويسة، إنتي عاملة إيه؟",
     "english": "Thank God, I'm good. How are you doing? (asking a girl)",
     "category": "how_are_you", "keywords": ["how are you", "ازيك"]},
    {"arabic": "تمام الحمد لله، شكراً على سؤالك",
     "english": "All good, thank God. Thanks for asking.",
     "category": "how_are_you", "keywords": ["how are you", "كيف حالك"]},
    {"arabic": "أنا بخير، إنت أخبارك إيه؟",
     "english": "I'm fine. What's your news?",
     "category": "how_are_you", "keywords": ["how are you", "اخبارك"]},
    # --- thanks ---
    {"arabic": "العفو، على إيه!",
     "english": "You're welcome — don't mention it!",
     "category": "thanks", "keywords": ["thank", "شكرا"]},
    {"arabic": "تحت أمرك في أي وقت",
     "english": "At your service, any time.",
     "category": "thanks", "keywords": ["thank", "شكرا"]},
    {"arabic": "ده واجبي، متقولش كده",
     "english": "It's my duty — don't say that.",
     "category": "thanks", "keywords": ["thank", "تسلم"]},
    # --- question (helpful answer-style replies) ---
    {"arabic": "أيوه، بالظبط كده",
     "english": "Yes, exactly like that.",
     "category": "question", "keywords": ["?", "؟"]},
    {"arabic": "لأ، مش كده",
     "english": "No, it's not like that.",
     "category": "question", "keywords": ["?", "؟"]},
    {"arabic": "مش عارف، هسأل وأقولك",
     "english": "I don't know — I'll ask and tell you.",
     "category": "question", "keywords": ["what", "ايه", "مين"]},
    {"arabic": "ثانية واحدة، هشوف وأرجعلك",
     "english": "One moment — I'll check and come back to you.",
     "category": "question", "keywords": ["when", "امتى", "فين"]},
    # --- goodbye ---
    {"arabic": "مع السلامة، أشوفك قريب",
     "english": "Goodbye (go in peace) — see you soon.",
     "category": "goodbye", "keywords": ["bye", "مع السلامة"]},
    {"arabic": "خلي بالك من نفسك",
     "english": "Take care of yourself.",
     "category": "goodbye", "keywords": ["bye", "goodbye"]},
    {"arabic": "سلام، نتكلم بعدين",
     "english": "Bye — we'll talk later.",
     "category": "goodbye", "keywords": ["bye", "سلام"]},
    # --- general (always useful) ---
    {"arabic": "أيوه، تمام",
     "english": "Yes, okay.",
     "category": "general", "keywords": []},
    {"arabic": "فهمت عليك",
     "english": "I understand you / I got you.",
     "category": "general", "keywords": []},
    {"arabic": "ده جميل أوي!",
     "english": "That's really great!",
     "category": "general", "keywords": []},
    {"arabic": "ثانية واحدة من فضلك",
     "english": "One moment, please.",
     "category": "general", "keywords": []},
    {"arabic": "موافق، مفيش مشكلة",
     "english": "Agreed — no problem.",
     "category": "general", "keywords": []},
    {"arabic": "ربنا يخليك، تسلم",
     "english": "God bless you — thank you so much.",
     "category": "general", "keywords": []},
]

_CATEGORY_ORDER = ["greeting", "how_are_you", "thanks", "question",
                   "goodbye"]

_EN_TRIGGERS = {
    "greeting": ["hello", "hi", "hey", "salam", "salaam",
                 "good morning", "good evening", "welcome"],
    "how_are_you": ["how are you", "how's it going", "how is it going",
                    "how do you feel", "how are things"],
    "thanks": ["thank", "thanks", "thank you", "thx"],
    "goodbye": ["bye", "goodbye", "good night", "see you"],
}

_AR_TRIGGERS = {
    "greeting": ["اهلا", "أهلا", "سلام", "صباح الخير", "مساء الخير",
                 "هلا"],
    "how_are_you": ["ازيك", "إزيك", "عامل ايه", "عاملة ايه",
                    "كيف حالك", "اخبارك", "أخبارك"],
    "thanks": ["شكرا", "شكراً", "تسلم", "متشكر"],
    "goodbye": ["مع السلامة", "اشوفك", "أشوفك"],
}

_QUESTION_WORDS_EN = ["what", "where", "when", "why", "how", "who",
                      "which", "can", "do", "did", "is", "are"]
_QUESTION_WORDS_AR = ["مين", "ايه", "إيه", "امتى", "فين", "ليه",
                      "كام", "ازاي", "إزاي"]


def _normalize_for_match(text):
    """Strip Arabic vowel marks and unify alef shapes so matching
    works on fully-vocalized text too (school/Quran style writing
    puts marks between the letters)."""
    if not text:
        return ""
    out = []
    for ch in text:
        if "ً" <= ch <= "ْ" or ch == "ٰ" or ch == _TATWEEL:
            continue
        if ch in "أإآٱ":
            ch = "ا"
        out.append(ch)
    return "".join(out)


def _match_categories(incoming_english, incoming_arabic):
    """Which reply categories fit this incoming message?"""
    import re
    eng = (incoming_english or "").lower()
    ara = _normalize_for_match(incoming_arabic or "")
    matched = []
    for cat in _CATEGORY_ORDER:
        if cat == "question":
            continue
        hit = False
        for kw in _EN_TRIGGERS.get(cat, []):
            if re.search(r"\b" + re.escape(kw) + r"\b", eng):
                hit = True
                break
        if not hit:
            for kw in _AR_TRIGGERS.get(cat, []):
                if _normalize_for_match(kw) in ara:
                    hit = True
                    break
        if hit:
            matched.append(cat)
    # Question detection.
    is_question = False
    if eng.strip().endswith("?") or ara.strip().endswith("؟") \
            or ara.strip().endswith("?"):
        is_question = True
    if not is_question:
        first = eng.strip().split(" ")[0] if eng.strip() else ""
        if first in _QUESTION_WORDS_EN:
            is_question = True
    if not is_question:
        for kw in _QUESTION_WORDS_AR:
            if _normalize_for_match(kw) in ara:
                is_question = True
                break
    if is_question:
        # Questions slot in after how_are_you / before goodbye.
        if "goodbye" in matched:
            matched.insert(matched.index("goodbye"), "question")
        else:
            matched.append("question")
    return matched


def _reply_pool(incoming_english, incoming_arabic):
    """The full ordered pool of reply ideas for a message:
    matched categories first, then the always-useful general
    replies, with transliterations filled in. No repeats."""
    matched = _match_categories(incoming_english, incoming_arabic)
    pool = []
    seen_arabic = set()
    for cat in matched + ["general"]:
        for entry in REPLIES:
            if entry["category"] != cat:
                continue
            if entry["arabic"] in seen_arabic:
                continue
            seen_arabic.add(entry["arabic"])
            item = dict(entry)
            item["transliteration"] = transliterate_arabic(
                entry["arabic"])
            pool.append(item)
    return pool


def suggest_replies(incoming_english, incoming_arabic, limit=3):
    """Pick reply ideas for an incoming message.

    Matches on the message's English translation AND its Arabic
    text (greetings, how-are-you, thanks, questions, goodbyes),
    and falls back to always-useful general replies.
    """
    return _reply_pool(incoming_english, incoming_arabic)[:limit]


# ---------------------------------------------------------------------
# The app window
# ---------------------------------------------------------------------

if tk is not None:

    class TranslatorApp:
        def __init__(self, root):
            self.root = root
            root.title(APP_TITLE)
            root.geometry("900x880")
            root.minsize(780, 700)
            root.configure(bg=BG)

            self.window_choices = []  # [(label, hwnd_or_WHOLE_SCREEN)]
            self.seen_lines = set()
            self.watching = False
            self.latest_english = []
            self.latest_arabic = ""
            self.latest_english_text = ""
            self.history = []
            self.favorites = load_favorites()
            self._job = None
            self._queue = queue.Queue()
            self._busy = False
            self.chat_mode_on = False
            self.clear_mode_on = True
            self._reply_full = []
            self._reply_offset = 0
            self.current_ideas = []
            self.my_arabic = ""
            # Screen Translate (cover-in-place) state
            self.screen_translate_on = False
            self.screen_overlay = None
            self.screen_labels = []
            self.screen_translations = {}
            self._screen_signature = None
            self._screen_job = None
            self._screen_busy = False

            # ----- header -----
            header = tk.Frame(root, bg=BG)
            header.pack(fill="x", padx=12, pady=(10, 2))
            tk.Label(header, text="🌍 MIA'S ARABIC SCREEN TRANSLATOR",
                     bg=BG, fg=TEAL,
                     font=("Segoe UI", 15, "bold")).pack(side="left")
            tk.Label(header, text="  made to sit beside Roblox",
                     bg=BG, fg=MUTED,
                     font=("Segoe UI", 10)).pack(side="left", padx=8)

            tk.Label(root, bg=PANEL, fg=TEXT, wraplength=850,
                     justify="left",
                     text="Pick the Roblox window (or any app). I read "
                          "the Arabic in its chat, translate it to "
                          "English, and give you reply ideas in "
                          "Arabic that you can copy and paste back. "
                          "Works on any Arabic — school Arabic and "
                          "dialects like Egyptian (dialect "
                          "translations can be a little rough)."
                     ).pack(fill="x", padx=12, pady=(2, 6))

            # ----- window picker -----
            pick = tk.Frame(root, bg=PANEL)
            pick.pack(fill="x", padx=12, pady=(0, 6))
            tk.Label(pick, text="Watch this app:", bg=PANEL, fg=TEXT,
                     font=("Segoe UI", 10, "bold")).pack(
                         side="left", padx=8, pady=6)
            self.window_var = tk.StringVar()
            self.window_combo = ttk.Combobox(
                pick, textvariable=self.window_var, state="readonly",
                width=44)
            self.window_combo.pack(side="left", padx=4, pady=6)
            self.window_combo.bind("<<ComboboxSelected>>",
                                   lambda e: self._on_window_selected())
            tk.Button(pick, text="↻ Refresh", relief="flat", bg=CARD,
                      fg=TEXT, padx=10, pady=3,
                      command=self.refresh_windows).pack(
                          side="left", padx=4, pady=6)
            self.chat_var = tk.BooleanVar(value=False)
            tk.Checkbutton(
                pick, text="🎮 Roblox chat mode (read just the chat box)",
                variable=self.chat_var, bg=PANEL, fg=TEXT,
                selectcolor=CARD, activebackground=PANEL,
                activeforeground=TEXT,
                command=self._sync_chat_cache).pack(
                    side="left", padx=8, pady=6)

            # ----- controls -----
            controls = tk.Frame(root, bg=PANEL)
            controls.pack(fill="x", padx=12, pady=(0, 6))
            self.start_btn = tk.Button(
                controls, text="▶ Start watching", relief="flat",
                bg=TEAL, fg=TEAL_TEXT, padx=14, pady=5,
                font=("Segoe UI", 11, "bold"),
                command=self.toggle_watch)
            self.start_btn.pack(side="left", padx=8, pady=6)
            tk.Button(controls, text="🧪 Try a sample sentence",
                      relief="flat", bg=CARD, fg=TEXT, padx=10, pady=5,
                      command=self.try_sample).pack(
                          side="left", padx=4, pady=6)
            self.overlay_var = tk.BooleanVar(value=True)
            tk.Checkbutton(controls, text="Overlay on/off",
                           variable=self.overlay_var, bg=PANEL,
                           fg=TEXT, selectcolor=CARD,
                           activebackground=PANEL,
                           activeforeground=TEXT,
                           command=self._sync_overlay).pack(
                               side="left", padx=8)
            tk.Label(controls, text="Speed:", bg=PANEL, fg=MUTED).pack(
                side="left", padx=(10, 2))
            self.interval_var = tk.DoubleVar(value=1.5)
            ttk.Scale(controls, from_=1.0, to=5.0,
                      variable=self.interval_var, length=100).pack(
                          side="left")
            self.interval_label = tk.Label(
                controls, text="1.5s", bg=PANEL, fg=TEAL, width=4)
            self.interval_label.pack(side="left")
            self.interval_var.trace_add(
                "write",
                lambda *_: self.interval_label.configure(
                    text="%.1fs" % self.interval_var.get()))

            # ----- controls row 2 -----
            controls2 = tk.Frame(root, bg=PANEL)
            controls2.pack(fill="x", padx=12, pady=(0, 6))
            self.clear_var = tk.BooleanVar(value=True)
            tk.Checkbutton(controls2, text="🔍 Clear text mode",
                           variable=self.clear_var, bg=PANEL, fg=TEXT,
                           selectcolor=CARD, activebackground=PANEL,
                           activeforeground=TEXT,
                           command=self._sync_clear_cache).pack(
                               side="left", padx=8, pady=4)
            self.beep_var = tk.BooleanVar(value=True)
            tk.Checkbutton(controls2,
                           text="🔔 Beep on new Arabic message",
                           variable=self.beep_var, bg=PANEL, fg=TEXT,
                           selectcolor=CARD, activebackground=PANEL,
                           activeforeground=TEXT).pack(
                               side="left", padx=8, pady=4)
            tk.Button(controls2, text="💾 Save chat log", relief="flat",
                      bg=CARD, fg=TEXT, padx=10, pady=3,
                      command=self.save_chat_log).pack(
                          side="left", padx=4, pady=4)
            tk.Label(controls2, text="Overlay size:", bg=PANEL,
                     fg=MUTED).pack(side="left", padx=(12, 2))
            self.overlay_size_var = tk.IntVar(value=13)
            ttk.Scale(controls2, from_=10, to=20,
                      variable=self.overlay_size_var, length=90,
                      command=lambda *_: self._update_overlay()).pack(
                          side="left")

            # ----- screen translate -----
            controls3 = tk.Frame(root, bg=PANEL)
            controls3.pack(fill="x", padx=12, pady=(0, 6))
            self.screen_var = tk.BooleanVar(value=False)
            tk.Checkbutton(
                controls3,
                text="🖥️ Screen Translate: cover the whole screen "
                     "with English",
                variable=self.screen_var, bg=PANEL, fg=TEXT,
                selectcolor=CARD, activebackground=PANEL,
                activeforeground=TEXT,
                command=self._sync_screen_translate).pack(
                    side="left", padx=8, pady=4)
            self.screen_note_label = tk.Label(
                controls3, text="", bg=PANEL, fg=GOOD)
            self.screen_note_label.pack(side="left", padx=4)

            # ----- status lines -----
            self.ocr_label = tk.Label(root, text="", bg=BG, fg=WARN,
                                      wraplength=850, justify="left")
            self.ocr_label.pack(fill="x", padx=12)
            ocr_tools = tk.Frame(root, bg=BG)
            ocr_tools.pack(fill="x", padx=12)
            tk.Button(ocr_tools, text="📁 Find Tesseract", relief="flat",
                      bg=CARD, fg=TEXT, padx=10, pady=3,
                      command=self._find_tesseract).pack(
                          side="left", pady=2)
            self.status_label = tk.Label(root, text="Ready.",
                                         bg=BG, fg=MUTED, anchor="w")
            self.status_label.pack(fill="x", padx=12, pady=(2, 4))

            # ----- latest incoming message (top) -----
            latest = tk.Frame(root, bg=PANEL)
            latest.pack(fill="x", padx=12, pady=(0, 6))
            tk.Label(latest, text="📥 Latest message", bg=PANEL,
                     fg=TEAL, font=("Segoe UI", 10, "bold")).pack(
                         anchor="w", padx=8, pady=(4, 0))
            self.latest_ar_label = tk.Label(
                latest, text="—", bg=PANEL, fg=TEXT, wraplength=840,
                justify="left", font=("Segoe UI", 12))
            self.latest_ar_label.pack(anchor="w", padx=8)
            self.latest_sounds_label = tk.Label(
                latest, text="", bg=PANEL, fg=MUTED, wraplength=840,
                justify="left")
            self.latest_sounds_label.pack(anchor="w", padx=8)
            self.latest_en_label = tk.Label(
                latest, text="", bg=PANEL, fg=GOOD, wraplength=840,
                justify="left", font=("Segoe UI", 11, "bold"))
            self.latest_en_label.pack(anchor="w", padx=8)
            btn_row = tk.Frame(latest, bg=PANEL)
            btn_row.pack(anchor="w", padx=8, pady=(2, 6))
            self.copy_ar_btn = tk.Button(
                btn_row, text="📋 Copy Arabic", relief="flat", bg=CARD,
                fg=TEXT, padx=8, pady=2,
                command=lambda: self._copy_text(self.latest_arabic,
                                                self.copy_ar_btn))
            self.copy_ar_btn.pack(side="left", padx=(0, 6))
            self.copy_en_btn = tk.Button(
                btn_row, text="📋 Copy English", relief="flat", bg=CARD,
                fg=TEXT, padx=8, pady=2,
                command=lambda: self._copy_text(
                    self.latest_english_text, self.copy_en_btn))
            self.copy_en_btn.pack(side="left")

            # ----- reply ideas -----
            ideas_box = tk.Frame(root, bg=PANEL)
            ideas_box.pack(fill="x", padx=12, pady=(0, 6))
            title_row = tk.Frame(ideas_box, bg=PANEL)
            title_row.pack(fill="x", padx=8, pady=(4, 0))
            tk.Label(title_row, text="💡 Ideas for what to say",
                     bg=PANEL, fg=TEAL,
                     font=("Segoe UI", 10, "bold")).pack(side="left")
            tk.Button(title_row, text="🔄 More ideas", relief="flat",
                      bg=CARD, fg=TEXT, padx=8, pady=2,
                      command=self.more_ideas).pack(side="right")
            cards = tk.Frame(ideas_box, bg=PANEL)
            cards.pack(fill="x", padx=4, pady=4)
            self.idea_cards = []
            for i in range(3):
                card = tk.Frame(cards, bg=CARD)
                card.pack(side="left", fill="both", expand=True,
                          padx=4, pady=2)
                ar = tk.Label(card, text="—", bg=CARD, fg=TEXT,
                              wraplength=250, justify="left",
                              font=("Segoe UI", 12, "bold"))
                ar.pack(anchor="w", padx=8, pady=(6, 0))
                so = tk.Label(card, text="", bg=CARD, fg=MUTED,
                              wraplength=250, justify="left")
                so.pack(anchor="w", padx=8)
                en = tk.Label(card, text="", bg=CARD, fg=GOOD,
                              wraplength=250, justify="left")
                en.pack(anchor="w", padx=8)
                brow = tk.Frame(card, bg=CARD)
                brow.pack(anchor="w", padx=8, pady=(4, 6))
                copy_btn = tk.Button(brow, text="📋 Copy Arabic",
                                     relief="flat", bg=PANEL, fg=TEXT,
                                     padx=6, pady=2)
                copy_btn.pack(side="left", padx=(0, 4))
                star_btn = tk.Button(brow, text="⭐", relief="flat",
                                     bg=PANEL, fg=TEXT, padx=6, pady=2)
                star_btn.pack(side="left")
                self.idea_cards.append(
                    {"ar": ar, "sounds": so, "en": en,
                     "copy": copy_btn, "star": star_btn,
                     "arabic": "", "english": ""})
            tk.Label(ideas_box, bg=PANEL, fg=MUTED, wraplength=850,
                     justify="left",
                     text="Tip: click Copy, click back into Roblox "
                          "chat, press Ctrl+V, then Enter to send."
                     ).pack(anchor="w", padx=8, pady=(0, 6))

            # ----- favorites -----
            fav_box = tk.Frame(root, bg=PANEL)
            fav_box.pack(fill="x", padx=12, pady=(0, 6))
            tk.Label(fav_box, text="⭐ Favorites", bg=PANEL, fg=TEAL,
                     font=("Segoe UI", 10, "bold")).pack(
                         anchor="w", padx=8, pady=(4, 0))
            self.fav_container = tk.Frame(fav_box, bg=PANEL)
            self.fav_container.pack(fill="x", padx=4, pady=(0, 6))

            # ----- say your own words -----
            mine = tk.Frame(root, bg=PANEL)
            mine.pack(fill="x", padx=12, pady=(0, 6))
            tk.Label(mine, text="✍️ Say your own words — type English, "
                                "get Arabic:", bg=PANEL, fg=TEAL,
                     font=("Segoe UI", 10, "bold")).pack(
                         anchor="w", padx=8, pady=(4, 0))
            row = tk.Frame(mine, bg=PANEL)
            row.pack(fill="x", padx=8, pady=2)
            self.my_entry = tk.Entry(row, bg=CARD, fg=TEXT,
                                     insertbackground=TEXT,
                                     relief="flat", width=52)
            self.my_entry.pack(side="left", padx=(0, 6))
            tk.Button(row, text="Translate to Arabic", relief="flat",
                      bg=TEAL, fg=TEAL_TEXT, padx=10, pady=3,
                      font=("Segoe UI", 10, "bold"),
                      command=self.translate_my_words_clicked).pack(
                          side="left")
            out = tk.Frame(mine, bg=PANEL)
            out.pack(fill="x", padx=8, pady=(0, 6))
            self.my_ar_label = tk.Label(out, text="", bg=PANEL,
                                        fg=TEXT, wraplength=600,
                                        justify="left",
                                        font=("Segoe UI", 12, "bold"))
            self.my_ar_label.pack(side="left", padx=(0, 8))
            self.my_sounds_label = tk.Label(out, text="", bg=PANEL,
                                            fg=MUTED, justify="left")
            self.my_sounds_label.pack(side="left", padx=(0, 8))
            self.my_copy_btn = tk.Button(
                out, text="📋 Copy Arabic", relief="flat", bg=CARD,
                fg=TEXT, padx=8, pady=2,
                command=lambda: self._copy_text(self.my_arabic,
                                                self.my_copy_btn))
            self.my_copy_btn.pack(side="left")

            # ----- history log -----
            tk.Label(root, text="Chat history (this time)",
                     bg=BG, fg=MUTED).pack(anchor="w", padx=12)
            self.results = tk.Text(root, wrap="word", bg=CARD, fg=TEXT,
                                   relief="flat", height=9,
                                   font=("Segoe UI", 11))
            self.results.pack(fill="both", expand=True, padx=12,
                              pady=(0, 10))
            self.results.bind(
                "<Key>",
                lambda e: None if (e.state & 0x4
                                   and e.keysym.lower() == "c")
                else "break")

            # ----- overlay window -----
            self.overlay = tk.Toplevel(root)
            self.overlay.title("Translation overlay")
            self.overlay.overrideredirect(True)
            self.overlay.configure(bg="#101014")
            try:
                self.overlay.attributes("-topmost", True)
                self.overlay.attributes("-alpha", 0.88)
            except Exception:
                pass
            self.overlay_label = tk.Label(
                self.overlay, text="Translations will appear here.",
                bg="#101014", fg="#ffffff", justify="left",
                font=("Segoe UI", 13, "bold"), padx=12, pady=8)
            self.overlay_label.pack()
            self._place_overlay()

            self.refresh_windows()
            self._update_ocr_label()
            self._render_favorites()
            self.root.after(200, self._poll_queue)
            self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # ---------- small helpers ----------

        def _place_overlay(self):
            try:
                self.root.update_idletasks()
                sh = self.root.winfo_screenheight()
                self.overlay.geometry("+%d+%d" % (16, sh - 190))
            except Exception:
                pass

        def _sync_overlay(self):
            if self.overlay_var.get():
                self.overlay.deiconify()
            else:
                self.overlay.withdraw()

        def _sync_chat_cache(self):
            self.chat_mode_on = bool(self.chat_var.get())

        def _sync_clear_cache(self):
            self.clear_mode_on = bool(self.clear_var.get())

        def _on_close(self):
            self.watching = False
            self.screen_translate_on = False
            self.root.destroy()

        def _update_ocr_label(self):
            ok, msg = ocr_status()
            self.ocr_label.configure(
                text=("✅ " if ok else "⚠ ") + msg,
                fg=GOOD if ok else WARN)

        def _find_tesseract(self):
            if filedialog is None:
                return
            folder = filedialog.askdirectory(
                title="Pick the Tesseract-OCR folder")
            if not folder:
                return
            candidate = os.path.join(folder, "tesseract.exe")
            if os.path.isfile(candidate):
                try:
                    save_tesseract_setting(candidate)
                except Exception:
                    pass
                apply_tesseract_cmd()
                self._update_ocr_label()
                self.set_status(
                    "✅ Found it! Tesseract is set up: " + candidate)
            else:
                self.set_status(
                    "⚠ No tesseract.exe in that folder — pick the "
                    "Tesseract-OCR folder itself.")
                if messagebox is not None:
                    messagebox.showerror(
                        APP_TITLE,
                        "There is no tesseract.exe in that folder.\n"
                        "Pick the Tesseract-OCR folder itself "
                        "(usually in Program Files).")

        def _copy_text(self, text, btn=None):
            if not text:
                self.set_status("Nothing to copy yet.")
                return
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(text)
                self.root.update_idletasks()
            except Exception:
                pass
            if btn is not None:
                old = btn.cget("text")
                btn.configure(text="✅ Copied!")
                self.root.after(
                    1200, lambda: btn.winfo_exists()
                    and btn.configure(text=old))

        def set_status(self, text):
            self.status_label.configure(text=text)

        # ---------- window picker ----------

        def refresh_windows(self):
            self.window_choices = [("🌐 Whole Screen", WHOLE_SCREEN)]
            for hwnd, disp_title in sort_windows(list_open_windows()):
                label = disp_title if len(disp_title) <= 70 \
                    else disp_title[:67] + "..."
                self.window_choices.append((label, hwnd))
            self.window_combo["values"] = [
                label for label, _ in self.window_choices]
            if self.window_var.get() not in self.window_combo["values"]:
                self.window_var.set(self.window_choices[0][0])
            self._on_window_selected()
            self._update_ocr_label()

        def _on_window_selected(self):
            is_roblox = "roblox" in self.window_var.get().lower()
            self.chat_var.set(is_roblox)
            self._sync_chat_cache()

        def _selected_target(self):
            label = self.window_var.get()
            for lab, hwnd in self.window_choices:
                if lab == label:
                    return lab, hwnd
            return self.window_choices[0]

        # ---------- incoming rows + reply ideas ----------

        def add_row(self, speaker, arabic, sounds, english, error,
                    slang):
            who = (speaker + ": ") if speaker else ""
            self.results.insert("end", "📥 %s%s\n" % (who, arabic))
            self.results.insert("end", "Sounds:  %s\n" % sounds)
            if english is not None:
                self.results.insert(
                    "end", "English: %s%s\n" % (who, english))
                btn = tk.Button(self.results, text="📋 Copy English",
                                relief="flat", bg=PANEL, fg=TEXT,
                                padx=4, pady=0,
                                font=("Segoe UI", 8),
                                command=lambda en=english:
                                self._copy_text(en))
                self.results.window_create("end", window=btn)
                self.results.insert("end", "\n")
                self.latest_english.append(english)
                self.latest_english = self.latest_english[-4:]
            else:
                self.results.insert(
                    "end", "English: (could not translate — %s)\n"
                    % (error or "unknown error"))
            if slang:
                help_line = " · ".join("%s = %s" % (w, m)
                                       for w, m in slang)
                self.results.insert("end",
                                    "Word help: %s\n" % help_line)
            self.results.insert("end", "-" * 60 + "\n")
            self.results.see("end")
            self.history.append({
                "time": time.strftime("%H:%M:%S"),
                "speaker": speaker, "arabic": arabic,
                "sounds": sounds,
                "english": english if english is not None else ""})
            self._set_incoming(speaker, arabic, sounds, english,
                               error)

        def _set_incoming(self, speaker, arabic, sounds, english,
                          error):
            who = (speaker + ": ") if speaker else ""
            self.latest_arabic = arabic
            self.latest_english_text = english or ""
            self.latest_ar_label.configure(text=who + arabic)
            self.latest_sounds_label.configure(text="Sounds: " + sounds)
            if english is not None:
                self.latest_en_label.configure(
                    text="English: " + who + english)
            else:
                self.latest_en_label.configure(
                    text="English: (could not translate)")
            self._reply_full = _reply_pool(english or "", arabic)
            self._reply_offset = 0
            self._render_ideas()
            self._update_overlay()

        def _render_ideas(self):
            pool = self._reply_full
            if not pool:
                self.current_ideas = []
                for card in self.idea_cards:
                    card["ar"].configure(text="—")
                    card["sounds"].configure(text="")
                    card["en"].configure(text="")
                    card["arabic"] = ""
                    card["english"] = ""
                return
            picks = [pool[(self._reply_offset + i) % len(pool)]
                     for i in range(min(3, len(pool)))]
            self.current_ideas = picks
            for idx, card in enumerate(self.idea_cards):
                if idx < len(picks):
                    idea = picks[idx]
                    card["ar"].configure(text=idea["arabic"])
                    card["sounds"].configure(
                        text=idea["transliteration"])
                    card["en"].configure(text=idea["english"])
                    card["arabic"] = idea["arabic"]
                    card["english"] = idea["english"]
                    card["copy"].configure(
                        command=lambda c=card: self._copy_text(
                            c["arabic"], c["copy"]))
                    card["star"].configure(
                        command=lambda c=card:
                        self._star_idea(c))
                else:
                    card["ar"].configure(text="—")
                    card["sounds"].configure(text="")
                    card["en"].configure(text="")
                    card["arabic"] = ""
                    card["english"] = ""

        def more_ideas(self):
            if not self._reply_full:
                return
            self._reply_offset = (self._reply_offset + 3) \
                % len(self._reply_full)
            self._render_ideas()
            self._update_overlay()

        def _update_overlay(self):
            lines = list(self.latest_english[-3:])
            if self.current_ideas:
                lines.append("💡 " + self.current_ideas[0]["english"])
            if lines:
                self.overlay_label.configure(text="\n".join(lines))
            try:
                self.overlay_label.configure(
                    font=("Segoe UI",
                          int(self.overlay_size_var.get()), "bold"))
            except Exception:
                pass

        # ---------- screen translate (cover-in-place) ----------

        def _sync_screen_translate(self):
            self.screen_translate_on = bool(self.screen_var.get())
            if self.screen_translate_on:
                note = ("Screen Translate is ON — English labels "
                        "cover the Arabic where it sits.")
                self.screen_note_label.configure(text=note)
                self._ensure_screen_overlay()
                self.set_status(note)
                self._screen_tick()
            else:
                self.screen_note_label.configure(text="")
                if self._screen_job is not None:
                    try:
                        self.root.after_cancel(self._screen_job)
                    except Exception:
                        pass
                    self._screen_job = None
                self._hide_screen_overlay()
                self.set_status("Screen Translate is off.")

        def _ensure_screen_overlay(self):
            """Create the full-area cover overlay if needed.

            Its background color is made invisible on Windows
            (transparent color), so only the English labels show.
            Returns the overlay window.
            """
            if self.screen_overlay is not None:
                try:
                    if self.screen_overlay.winfo_exists():
                        return self.screen_overlay
                except Exception:
                    self.screen_overlay = None
            ov = tk.Toplevel(self.root)
            ov.title("Screen Translate overlay")
            ov.overrideredirect(True)
            ov.configure(bg=SCREEN_OVERLAY_KEY)
            try:
                ov.attributes("-topmost", True)
            except Exception:
                pass
            try:
                ov.attributes("-transparentcolor", SCREEN_OVERLAY_KEY)
            except Exception:
                # Not Windows (test machines): the overlay still
                # draws, just without the see-through background.
                pass
            self.screen_overlay = ov
            self.screen_labels = []
            self._screen_signature = None
            return ov

        def _hide_screen_overlay(self):
            if self.screen_overlay is not None:
                try:
                    self.screen_overlay.withdraw()
                except Exception:
                    pass

        def _make_screen_overlay_click_through(self):
            """Windows only: let mouse clicks pass through the
            cover overlay to the game underneath
            (WS_EX_TRANSPARENT), keep it see-through
            (WS_EX_LAYERED) and on top (WS_EX_TOPMOST).
            Guarded — if anything fails, the overlay just stays
            a normal (still working) window."""
            if sys.platform != "win32" \
                    or self.screen_overlay is None:
                return
            try:
                import ctypes
                hwnd = self.screen_overlay.winfo_id()
                user32 = ctypes.windll.user32
                GWL_EXSTYLE = -20
                WS_EX_LAYERED = 0x00080000
                WS_EX_TRANSPARENT = 0x00000020
                WS_EX_TOPMOST = 0x00000008
                get_long = getattr(user32, "GetWindowLongPtrW",
                                   user32.GetWindowLongW)
                set_long = getattr(user32, "SetWindowLongPtrW",
                                   user32.SetWindowLongW)
                style = get_long(hwnd, GWL_EXSTYLE)
                set_long(hwnd, GWL_EXSTYLE,
                         style | WS_EX_LAYERED | WS_EX_TRANSPARENT
                         | WS_EX_TOPMOST)
            except Exception:
                pass

        def capture_for_screen_translate(self, bbox=None):
            """Screenshot for a Screen Translate scan.

            The cover overlay is hidden for the instant of the
            capture (withdraw -> capture -> show again), so its
            own labels can never be read back. (They are English,
            so the Arabic filter would ignore them anyway — this
            is belt-and-suspenders.) Returns (image, error),
            like capture_screen.
            """
            ov = self.screen_overlay
            hidden = False
            if ov is not None:
                try:
                    ov.withdraw()
                    hidden = True
                    time.sleep(0.08)
                except Exception:
                    hidden = False
            try:
                return capture_screen(bbox)
            finally:
                if hidden and ov is not None:
                    try:
                        ov.deiconify()
                    except Exception:
                        pass

        def _screen_tick(self):
            if not self.screen_translate_on:
                return
            if not self._screen_busy:
                self._screen_busy = True
                _label, hwnd = self._selected_target()
                threading.Thread(
                    target=self._screen_scan_worker,
                    args=(hwnd, self.clear_mode_on),
                    daemon=True).start()
            self._screen_job = self.root.after(
                SCREEN_TRANSLATE_INTERVAL_MS, self._screen_tick)

        def _screen_scan_worker(self, hwnd, clear_mode):
            try:
                bbox = None
                rect = None
                if hwnd != WHOLE_SCREEN:
                    rect = get_window_rect(hwnd)
                    if rect is None:
                        self._queue.put(
                            ("status",
                             "Screen Translate: that window is "
                             "gone — pick it again or use Whole "
                             "Screen."))
                        return
                    bbox = rect
                img, err = self.capture_for_screen_translate(bbox)
                if img is None:
                    self._queue.put(("status", err))
                    return
                if rect is None:
                    rect = (0, 0, img.size[0], img.size[1])
                lines, err = ocr_lines_with_boxes(
                    img, clear=clear_mode)
                if err:
                    self._queue.put(
                        ("status", "Screen Translate: " + err))
                    return
                items = translate_screen_lines(
                    lines, cache=self.screen_translations)
                self._queue.put(("screen", (rect, items)))
            except Exception as exc:
                self._queue.put(
                    ("status", "Screen Translate went wrong: %s"
                     % exc))
            finally:
                self._screen_busy = False

        def _apply_screen_scan(self, rect, items):
            """Position the cover overlay over exactly the watched
            area and redraw its labels — but only when the set of
            (line, box, English) changed, so it does not flicker."""
            if not self.screen_translate_on:
                return
            ov = self._ensure_screen_overlay()
            left, top, right, bottom = rect
            width = max(1, right - left)
            height = max(1, bottom - top)
            try:
                ov.geometry("%dx%d%s%d%s%d" % (
                    width, height,
                    "+" if left >= 0 else "-", abs(left),
                    "+" if top >= 0 else "-", abs(top)))
                ov.deiconify()
                ov.lift()
            except Exception:
                pass
            self._make_screen_overlay_click_through()
            signature = tuple(
                (it["text"], it["box"], it["english"])
                for it in items)
            if signature == self._screen_signature:
                return  # nothing changed — no flicker
            self._screen_signature = signature
            for child in ov.winfo_children():
                child.destroy()
            self.screen_labels = []
            for it in items:
                bx, by, bw, bh = it["box"]
                screen_box = (left + bx, top + by, bw, bh)
                x, y, w, h = label_geometry(screen_box, (left, top))
                size = label_font_size(bh)
                # English is often longer than the Arabic — give
                # the label room, but never past the overlay edge.
                need = int(len(it["english"]) * size * 0.58) + 12
                w = max(w, need)
                if x + w > width:
                    w = max(10, width - x)
                lbl = tk.Label(ov, text=it["english"],
                               bg=SCREEN_LABEL_BG,
                               fg=SCREEN_LABEL_FG,
                               font=("Segoe UI", size, "bold"),
                               padx=4, pady=1, justify="left",
                               anchor="w")
                lbl.place(x=x, y=y, width=w,
                          height=max(h, size + 10))
                self.screen_labels.append(lbl)

        # ---------- favorites ----------

        def _star_idea(self, card):
            if not card["arabic"]:
                return
            self.favorites, added = add_favorite(
                self.favorites, card["arabic"], card["english"])
            if added:
                save_favorites(default_favorites_path(),
                               self.favorites)
                self._render_favorites()
                self.set_status("⭐ Saved to favorites.")
            else:
                self.set_status("That one is already in favorites.")

        def _render_favorites(self):
            for child in self.fav_container.winfo_children():
                child.destroy()
            if not self.favorites:
                tk.Label(self.fav_container,
                         text="No favorites yet — tap ⭐ on an idea "
                              "to save it here.",
                         bg=PANEL, fg=MUTED).pack(anchor="w", padx=8)
                return
            for fav in self.favorites:
                row = tk.Frame(self.fav_container, bg=PANEL)
                row.pack(fill="x", padx=4, pady=1)
                tk.Label(row, text=fav["arabic"], bg=PANEL, fg=TEXT,
                         font=("Segoe UI", 11, "bold")).pack(
                             side="left", padx=4)
                tk.Label(row, text=fav["english"], bg=PANEL,
                         fg=MUTED).pack(side="left", padx=4)
                tk.Button(
                    row, text="✖", relief="flat", bg=CARD, fg=TEXT,
                    padx=5, pady=1,
                    command=lambda f=fav: self._remove_favorite(f)
                ).pack(side="right", padx=2)
                tk.Button(
                    row, text="📋 Copy", relief="flat", bg=CARD,
                    fg=TEXT, padx=5, pady=1,
                    command=lambda f=fav: self._copy_text(f["arabic"])
                ).pack(side="right", padx=2)

        def _remove_favorite(self, fav):
            self.favorites = [f for f in self.favorites
                              if f["arabic"] != fav["arabic"]]
            save_favorites(default_favorites_path(), self.favorites)
            self._render_favorites()

        # ---------- say your own words ----------

        def translate_my_words_clicked(self):
            text = self.my_entry.get().strip()
            if not text:
                self.set_status("Type some English words first.")
                return
            self.set_status("Translating your words...")
            threading.Thread(target=self._my_words_worker,
                             args=(text,), daemon=True).start()

        def _my_words_worker(self, text):
            arabic, error = translate_my_words(text)
            self._queue.put(("mywords", (arabic, error)))

        # ---------- chat log ----------

        def save_chat_log(self):
            if not self.history:
                self.set_status("Nothing to save yet — no messages "
                                "translated so far.")
                return
            path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "chat_log.txt")
            try:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(format_chat_log(self.history))
                self.set_status("💾 Chat log saved to: %s" % path)
            except Exception as exc:
                self.set_status("Saving the chat log failed: %s"
                                % exc)

        # ---------- sample ----------

        def try_sample(self):
            english, error = translate_to_english(SAMPLE_SENTENCE)
            self.add_row(None, SAMPLE_SENTENCE,
                         transliterate_arabic(SAMPLE_SENTENCE),
                         english, error, word_help(SAMPLE_SENTENCE))
            if english is None:
                self.set_status(
                    "Sample: sounds are shown; translation needs "
                    "internet. (%s)" % (error or ""))
            else:
                self.set_status("Sample sentence done — it works.")

        # ---------- watching ----------

        def toggle_watch(self):
            if self.watching:
                self.watching = False
                self.start_btn.configure(text="▶ Start watching")
                self.set_status("Stopped.")
                return
            ok, msg = ocr_status()
            if not ok:
                self._update_ocr_label()
                if messagebox is not None:
                    messagebox.showwarning(
                        "Screen reading is not ready yet", msg)
                self.set_status("Screen reading is not ready — see "
                                "the yellow note above.")
                return
            self.watching = True
            self.start_btn.configure(text="⏹ Stop")
            label, _ = self._selected_target()
            self.set_status("Watching: %s" % label)
            self._tick()

        def _tick(self):
            if not self.watching:
                return
            if not self._busy:
                self._busy = True
                threading.Thread(
                    target=self._capture_and_read,
                    args=(self.chat_mode_on, self.clear_mode_on),
                    daemon=True).start()
            delay = int(self.interval_var.get() * 1000)
            self._job = self.root.after(delay, self._tick)

        def _capture_and_read(self, chat_mode, clear_mode):
            try:
                label, hwnd = self._selected_target()
                bbox = None
                if hwnd != WHOLE_SCREEN:
                    rect = get_window_rect(hwnd)
                    if rect is None:
                        self._queue.put(("status",
                                         "That window is gone — pick "
                                         "it again or use Whole "
                                         "Screen."))
                        return
                    bbox = capture_box_for(rect, chat_mode)
                img, err = capture_screen(bbox)
                if img is None:
                    self._queue.put(("status", err))
                    return
                text, err = ocr_image(img, clear=clear_mode)
                if text is None:
                    self._queue.put(("status", err))
                    return
                rows = process_ocr_text(text, self.seen_lines)
                self._queue.put(("rows", rows))
                note = " · chat box only" if (chat_mode
                                              and hwnd != WHOLE_SCREEN) \
                    else ""
                self._queue.put(("status",
                                 "Watching: %s%s · Last read: %s"
                                 % (label, note,
                                    time.strftime("%H:%M:%S"))))
            except Exception as exc:
                self._queue.put(("status", "Something went wrong: %s"
                                 % exc))
            finally:
                self._busy = False

        def _poll_queue(self):
            try:
                while True:
                    kind, payload = self._queue.get_nowait()
                    if kind == "rows":
                        for row in payload:
                            self.add_row(*row)
                        if payload and self.beep_var.get():
                            beep()
                    elif kind == "status":
                        self.set_status(payload)
                    elif kind == "screen":
                        rect, items = payload
                        self._apply_screen_scan(rect, items)
                    elif kind == "mywords":
                        arabic, error = payload
                        if arabic is not None:
                            self.my_arabic = arabic
                            self.my_ar_label.configure(text=arabic)
                            self.my_sounds_label.configure(
                                text=transliterate_arabic(arabic))
                            self.set_status("Your words in Arabic — "
                                            "copy and paste them.")
                        else:
                            self.my_ar_label.configure(text="")
                            self.my_sounds_label.configure(text="")
                            self.set_status(
                                "Could not translate your words: %s"
                                % (error or ""))
            except queue.Empty:
                pass
            self.root.after(200, self._poll_queue)


def main():
    if tk is None:
        print("tkinter is not available, so the app window cannot "
              "open. The helper functions still work.")
        return
    root = tk.Tk()
    TranslatorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
