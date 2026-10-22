from __future__ import annotations

import json
import math
import re
import os
import platform
import random
import subprocess
import sys
import threading
import time
import warnings
from pathlib import Path

import psutil

try:
    from core.avatar import HoloAvatar
    _HOLOAVATAR_IMPORT_ERROR = None
except Exception as _avatar_import_exc:  # HUD remains usable if the optional avatar module is unavailable
    HoloAvatar = None
    _HOLOAVATAR_IMPORT_ERROR = str(_avatar_import_exc)


if platform.system() == "Windows":
    _WIN_HIDE: dict = {"creationflags": subprocess.CREATE_NO_WINDOW}
else:
    _WIN_HIDE: dict = {}

from PyQt6.QtCore import (
    QEvent, QEasingCurve, QLineF, QMimeData, QObject, QParallelAnimationGroup, QPointF,
    QPropertyAnimation, QRect, QRectF, QSize, Qt, QTimer, QUrl, pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush, QColor, QConicalGradient, QDragEnterEvent, QDropEvent, QFont, QKeyEvent,
    QFontDatabase, QKeySequence, QLinearGradient, QPainter, QPainterPath,
    QPen, QPixmap, QRadialGradient, QShortcut,
)
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPushButton, QScrollArea, QSizePolicy, QSplitter, QGridLayout,
    QStackedWidget, QTextEdit, QVBoxLayout, QWidget, QProgressBar, QCheckBox,
    QSlider,
)

# ── Which Mark this is ───────────────────────────────────────────────────────
# One constant, read by the window title, the header badge and the PROTOCOL
# panel. It used to be typed separately in each of those places, and they drifted:
# Mark 52 and 53 shipped showing "PROTOCOL XLIX" — the number from Mark 49 — and
# Mark 55 shipped titled "MARK 54". Deriving the protocol from the name means a
# release bump is this one line.
APP_VERSION  = "MARK 54"
APP_PROTOCOL = APP_VERSION.split()[-1]
DEFAULT_ASSISTANT_NAME = "JANE"
DEFAULT_ASSISTANT_TAGLINE = "JUST ANOTHER NEURAL ENGINE"
DEFAULT_WAKE_WORD = "Hey JARVIS"
_WAKE_WORD_MIN = 2
_WAKE_WORD_MAX = 48

def _wake_word_store_path() -> Path:
    # Keep the phrase in its own tiny file as well as api_keys.json. Older setup
    # code can replace api_keys.json wholesale; this sidecar prevents that from
    # silently restoring the old default on the next launch.
    return CONFIG_DIR / "wake_word_phrase.txt"


def _load_wake_word_phrase() -> str:
    try:
        stored = _wake_word_store_path()
        if stored.exists():
            value = re.sub(r"\s+", " ", stored.read_text(encoding="utf-8")).strip()
            if len(value) >= _WAKE_WORD_MIN:
                return value[:_WAKE_WORD_MAX].strip()
    except Exception:
        pass
    cfg = _read_full_config()
    value = str(cfg.get("wake_word_phrase") or cfg.get("wake_word") or "").strip()
    return value[:_WAKE_WORD_MAX] if len(value) >= _WAKE_WORD_MIN else DEFAULT_WAKE_WORD


def _save_wake_word_phrase(value: str) -> str:
    phrase = re.sub(r"\s+", " ", str(value or "")).strip()[:_WAKE_WORD_MAX].strip()
    if len(phrase) < _WAKE_WORD_MIN:
        phrase = DEFAULT_WAKE_WORD
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _wake_word_store_path().write_text(phrase + "\n", encoding="utf-8")
    except Exception:
        pass
    try:
        cfg = _read_full_config()
        cfg["wake_word_phrase"] = phrase
        cfg["wake_word"] = phrase
        _write_full_config(cfg)
    except Exception:
        pass
    return phrase


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR   = _base_dir()
CONFIG_DIR = BASE_DIR / "config"
API_FILE   = CONFIG_DIR / "api_keys.json"


def _read_full_config() -> dict:
    """Read api_keys.json config dict. Returns {} on any error."""
    try:
        return json.loads(API_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_full_config(data: dict) -> bool:
    """Persist the shared config without silently losing unrelated keys."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = API_FILE.with_suffix(API_FILE.suffix + ".tmp")
        tmp.write_text(json.dumps(dict(data), indent=4), encoding="utf-8")
        tmp.replace(API_FILE)
        return True
    except Exception:
        return False


_UI_PREF_DEFAULTS = {
    "layout_preset": "balanced",
    "command_position": "bottom",
    "widget_position": "bottom_left",
    "left_width": 270,
    "right_width": 352,
    "center_scale": 1.00,
    "center_y": 0.44,
    "center_glow": 1.00,
    "center_motion": 1.00,
    "particle_density": 1.00,
    "particle_field": 1,
    "speech_reactivity": 1.00,
    "bar_reactivity": True,
    "low_motion": False,
    "show_diagnostics": True,
    "show_io": True,
    "show_system_info": True,
    "show_system_status": True,
    "show_status_tiles": True,
    "show_activity_log": True,
    "show_file_upload": True,
    "show_command_input": True,
    "show_content_panel": True,
    "show_quiz_panel": True,
    "show_footer": True,
    "show_weather": False,
    "show_clock": False,
    "show_battery": False,
    "show_network_widget": False,
    "show_quick_notes": False,
    "weather_location": "",
    "quick_notes": "",
}

def _normalise_ui_preferences(prefs: dict) -> dict:
    prefs = dict(_UI_PREF_DEFAULTS) | dict(prefs or {})
    for key, default, lo, hi in (
        ("center_scale", 1.0, 0.78, 1.18),
        ("center_y", 0.44, 0.34, 0.54),
        ("center_glow", 1.0, 0.40, 1.80),
        ("center_motion", 1.0, 0.35, 1.80),
        ("particle_density", 1.0, 0.40, 1.40),
        ("particle_field", 1.0, 1.0, 10.0),
        ("speech_reactivity", 1.0, 0.25, 2.20),
    ):
        try:
            prefs[key] = max(lo, min(hi, float(prefs.get(key, default))))
        except Exception:
            prefs[key] = default
    try:
        prefs["left_width"] = max(220, min(340, int(float(prefs.get("left_width", 270)))))
    except Exception:
        prefs["left_width"] = 270
    try:
        prefs["right_width"] = max(260, min(400, int(float(prefs.get("right_width", 352)))))
    except Exception:
        prefs["right_width"] = 352
    prefs["layout_preset"] = str(prefs.get("layout_preset") or "balanced").lower()
    if prefs["layout_preset"] not in {"balanced", "center", "telemetry", "minimal", "command", "utility", "clean", "immersive", "studio", "essentials"}:
        prefs["layout_preset"] = "balanced"
    prefs["command_position"] = str(prefs.get("command_position") or "bottom").lower()
    if prefs["command_position"] not in {"top", "bottom"}:
        prefs["command_position"] = "bottom"
    prefs["widget_position"] = str(prefs.get("widget_position") or "bottom_left").lower()
    if prefs["widget_position"] not in {"bottom_left", "right"}:
        prefs["widget_position"] = "bottom_left"
    prefs["weather_location"] = str(prefs.get("weather_location") or "").strip()[:80]
    prefs["quick_notes"] = str(prefs.get("quick_notes") or "")[:3000]
    for key in list(_UI_PREF_DEFAULTS):
        if key.startswith("show_") or key in {"bar_reactivity", "low_motion"}:
            prefs[key] = bool(prefs.get(key))
    # The command deck is an accessibility/fallback control: it is always present.
    prefs["show_command_input"] = True
    return prefs

def _load_ui_preferences() -> dict:
    cfg = _read_full_config()
    raw = cfg.get("ui_preferences")
    return _normalise_ui_preferences(raw if isinstance(raw, dict) else {})

def _save_ui_preferences(prefs: dict) -> dict:
    clean = _normalise_ui_preferences(prefs)
    cfg = _read_full_config()
    cfg["ui_preferences"] = clean
    _write_full_config(cfg)
    return clean


# Three visual centre modes. The third one is the current JANE orbital HUD.
_HUD_MODE_LABELS = {
    "face": "ANIMATED FACE",
    "reactor": "REACTOR CORE",
    "orbital": "ORBITAL HUD",
}


def _load_hud_ui_style() -> str:
    """Load the UI-owned three-way HUD mode without breaking legacy config."""
    cfg = _read_full_config()
    saved = str(cfg.get("hud_style_ui") or "").strip().lower()
    if saved in _HUD_MODE_LABELS:
        return saved
    # Older builds stored only the original two modes as face/core. Preserve those
    # semantics: legacy core -> REACTOR CORE. ORBITAL is remembered separately via
    # hud_style_ui once this build has been used.
    try:
        from memory.config_manager import get_hud_style
        return "face" if get_hud_style() == "face" else "reactor"
    except Exception:
        return "orbital"


def _save_hud_ui_style(mode: str) -> None:
    """Persist the UI mode and keep the legacy core/avatar setting compatible."""
    mode = mode if mode in _HUD_MODE_LABELS else "orbital"
    try:
        cfg = _read_full_config()
        cfg["hud_style_ui"] = mode
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        API_FILE.write_text(json.dumps(cfg, indent=4), encoding="utf-8")
    except Exception:
        pass
    try:
        from memory.config_manager import save_hud_style
        # The engine currently understands face/core. ORBITAL stays backed by the
        # core path for compatibility while ui.py selects its own renderer.
        save_hud_style("face" if mode == "face" else "core")
    except Exception:
        pass


_DEFAULT_W, _DEFAULT_H = 1360, 800
_MIN_W,     _MIN_H     = 1040, 640
_LEFT_W  = 270
_RIGHT_W = 352

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"


class C:
    BG        = "#020713"
    PANEL     = "#030d18"
    PANEL2    = "#07121e"
    BORDER    = "#17354b"
    BORDER_B  = "#2a6486"
    BORDER_A  = "#24506b"
    PRI       = "#55dbea"
    PRI_DIM   = "#2e9db1"
    PRI_GHO   = "#102b3b"
    ACC       = "#ff6b00"
    ACC2      = "#ffcc00"
    GREEN     = "#00ff88"
    GREEN_D   = "#00aa55"
    RED       = "#ff3355"
    MUTED_C   = "#ff3366"
    TEXT      = "#b2eef4"
    TEXT_DIM  = "#587e8e"
    TEXT_MED  = "#78a9b8"
    WHITE     = "#ecf7ff"
    DARK      = "#030b13"
    BAR_BG    = "#081b27"


# Keys tied to the accent colour — status colours (ACC, GREEN, RED…) stay fixed
_HUE_LINKED = (
    "BG", "PANEL", "PANEL2", "BORDER", "BORDER_B", "BORDER_A",
    "PRI", "PRI_DIM", "PRI_GHO", "TEXT", "TEXT_DIM", "TEXT_MED",
    "WHITE", "DARK", "BAR_BG",
)
_PALETTE_DEFAULTS: dict[str, str] = {k: getattr(C, k) for k in _HUE_LINKED}

DEFAULT_UI_COLOR = _PALETTE_DEFAULTS["PRI"]


def apply_ui_accent(accent_hex: str) -> bool:
    """
    Re-derives the whole teal-family palette from the chosen accent colour
    (hue shift — brightness/saturation ratios are preserved, design stays intact).
    Painted elements (HUD, waveform, metrics) pick up the new colour on the next
    frame; stylesheet-based panels pick it up when they are rebuilt.
    """
    import colorsys

    accent_hex = (accent_hex or "").strip().lower()
    if not (accent_hex.startswith("#") and len(accent_hex) == 7):
        return False
    try:
        int(accent_hex[1:], 16)
    except ValueError:
        return False

    def _hsv(h: str) -> tuple[float, float, float]:
        r = int(h[1:3], 16) / 255
        g = int(h[3:5], 16) / 255
        b = int(h[5:7], 16) / 255
        return colorsys.rgb_to_hsv(r, g, b)

    base_h            = _hsv(_PALETTE_DEFAULTS["PRI"])[0]
    acc_h, acc_s, _av = _hsv(accent_hex)
    dh   = acc_h - base_h
    grey = acc_s < 0.08   # near-grey accent → the whole theme is desaturated

    for key, hex0 in _PALETTE_DEFAULTS.items():
        h, s, v = _hsv(hex0)
        if grey:
            s *= 0.15
        r, g, b = colorsys.hsv_to_rgb((h + dh) % 1.0, s, v)
        setattr(C, key, "#{:02x}{:02x}{:02x}".format(
            int(r * 255 + 0.5), int(g * 255 + 0.5), int(b * 255 + 0.5)))
    return True


def current_palette() -> dict[str, str]:
    """A snapshot of the accent-linked colours currently on class C."""
    return {k: getattr(C, k) for k in _HUE_LINKED}


def retheme_all_widgets(old: dict[str, str], new: dict[str, str]) -> None:
    """
    LIVE full theme change. Replaces the old palette colours with the new ones
    in EVERY widget's stylesheet across the app and repaints them. This way the
    colour change applies INSTANTLY across the whole interface — panels, buttons,
    borders included — not just the painted elements. No restart needed.
    """
    mapping = {old[k].lower(): new[k].lower()
               for k in old if old[k].lower() != new.get(k, old[k]).lower()}
    if not mapping:
        return
    app = QApplication.instance()
    if app is None:
        return
    for w in app.allWidgets():
        try:
            ss = w.styleSheet()
            if ss:
                s2 = ss
                for o, n in mapping.items():
                    if o in s2:
                        s2 = s2.replace(o, n)
                if s2 != ss:
                    w.setStyleSheet(s2)
            w.update()
        except Exception:
            pass


def qcol(h: str, a: int = 255) -> QColor:
    c = QColor(h); c.setAlpha(a); return c


# ── Defensive button styling ───────────────────────────────────────────────
_BUTTON_QSS_PROPS = {
    "background", "background-color", "color", "border", "border-radius",
    "padding", "text-align", "font-weight", "min-width", "max-width",
    "min-height", "max-height", "margin",
}

def _normalise_button_qss(style: str) -> str:
    """Build a conservative Qt stylesheet from simple QPushButton rules only.

    Several earlier builds occasionally passed a stylesheet containing a token
    Qt's QSS parser did not accept. Reconstructing only selectors/properties we
    actually use prevents noisy "Could not parse stylesheet" warnings without
    changing button appearance.
    """
    import re as _re
    if not style:
        return ""
    allowed_props = {
        "background", "background-color", "color", "border", "border-radius",
        "padding", "text-align", "font-weight", "min-width", "max-width",
        "min-height", "max-height", "margin",
    }
    selector_re = _re.compile(
        r"^QPushButton(?:#[A-Za-z_][A-Za-z0-9_-]*)?(?::(?:hover|pressed|checked|disabled))?$"
    )
    rules = []
    for match in _re.finditer(r"([^{}]+)\{([^{}]*)\}", str(style), _re.S):
        selector = " ".join(match.group(1).split())
        if not selector_re.fullmatch(selector):
            continue
        decls = []
        for raw in match.group(2).split(";"):
            if ":" not in raw:
                continue
            prop, value = raw.split(":", 1)
            prop = prop.strip().lower()
            value = value.strip()
            if prop not in allowed_props or not value:
                continue
            # QSS accepts background, but background-color is the least
            # ambiguous form for flat buttons.
            if prop == "background":
                prop = "background-color"
            decls.append(f"{prop}: {value};")
        if decls:
            rules.append(f"{selector} {{ {' '.join(decls)} }}")
    return " ".join(rules)

class JANEButton(QPushButton):
    """QPushButton with defensive QSS normalisation and no keyboard activation.

    The HUD is intentionally mouse-driven. Keeping buttons out of the keyboard
    focus chain prevents Space/Ctrl+Space from activating the focused Settings
    control while still allowing text inputs to receive keyboard input.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def setStyleSheet(self, style: str) -> None:  # type: ignore[override]
        cleaned=_normalise_button_qss(str(style or ""))
        super().setStyleSheet(cleaned)


# ── Windows GPU via NVML DLL (no subprocess, no console window) ──────────────
_nvml_lib: object = None   # cached ctypes DLL
_nvml_ok:  object = None   # None=untested, True=works, False=unavailable


def _nvml_gpu_windows() -> float:
    """Return NVIDIA GPU utilisation % using nvml.dll directly — zero subprocess."""
    global _nvml_lib, _nvml_ok
    if _nvml_ok is False:
        return -1.0
    try:
        import ctypes

        class _Util(ctypes.Structure):
            _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

        if _nvml_lib is None:
            for dll_name in ("nvml", r"C:\Windows\System32\nvml.dll"):
                try:
                    lib = ctypes.WinDLL(dll_name)
                    lib.nvmlInit_v2()
                    _nvml_lib = lib
                    break
                except Exception:
                    continue

        if _nvml_lib is None:
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    message=r"The pynvml package is deprecated.*",
                    category=FutureWarning,
                )
                import pynvml  # type: ignore
            pynvml.nvmlInit()
            h = pynvml.nvmlDeviceGetHandleByIndex(0)
            _nvml_ok = True
            return float(pynvml.nvmlDeviceGetUtilizationRates(h).gpu)

        dev = ctypes.c_void_p()
        _nvml_lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
        util = _Util()
        _nvml_lib.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(util))
        _nvml_ok = True
        return float(util.gpu)
    except Exception:
        _nvml_ok = False
        return -1.0


class _SysMetrics:
    def __init__(self):
        self.cpu  = 0.0
        self.mem  = 0.0
        self.net  = 0.0   
        self.gpu  = -1.0  
        self.tmp  = -1.0  
        self._lock = threading.Lock()
        self._last_net = psutil.net_io_counters()
        self._last_net_t = time.time()
        # Prime psutil's CPU sampler so the first visible graph is a real sample.
        try:
            psutil.cpu_percent(interval=0.12)
        except Exception:
            pass
        self._running = True
        # Probe caches — GPU (NVML) and temperature (WMI) are the expensive
        # queries; initialise their handles once and reuse them instead of
        # rebuilding a connection on every poll.
        self._slow_tick = 0            # gpu/temp refreshed every 3rd cycle
        self._pynvml    = None         # cached pynvml module + device handle
        self._pynvml_h  = None
        self._pynvml_ok = None         # None=untested, False=unavailable here
        self._nv_unix   = None         # cached (lib, dev) for Linux/macOS NVML
        self._wmi_conn  = None         # cached WMI connection (creating one is slow)
        self._wmi_ok    = None         # None=untested, False=unavailable here
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def _loop(self):
        while self._running:
            try:
                self._update()
            except Exception:
                pass
            time.sleep(0.85)

    def _update(self):
        # A short blocking sample is more representative than interval=None for a
        # diagnostic graph, and this work stays entirely on the metrics thread.
        try:
            cpu = psutil.cpu_percent(interval=0.15)
        except Exception:
            cpu = 0.0
        mem = psutil.virtual_memory().percent

        nc  = psutil.net_io_counters()
        now = time.time()
        dt  = now - self._last_net_t
        if dt > 0:
            sent = (nc.bytes_sent - self._last_net.bytes_sent) / dt
            recv = (nc.bytes_recv - self._last_net.bytes_recv) / dt
            net  = (sent + recv) / (1024 * 1024)
        else:
            net = 0.0
        self._last_net   = nc
        self._last_net_t = now

        # GPU and temperature change slowly and are the most expensive probes
        # (NVML / WMI) — refresh them every 3rd cycle (~6 s) instead of every
        # cycle, reusing the previous reading in between.
        self._slow_tick = (self._slow_tick + 1) % 3
        if self._slow_tick == 1:
            gpu = self._get_gpu()
            tmp = self._get_temp()
        else:
            gpu = self.gpu
            tmp = self.tmp

        with self._lock:
            self.cpu = cpu
            self.mem = mem
            self.net = net
            self.gpu = gpu
            self.tmp = tmp

    def _get_gpu(self) -> float:
        # pynvml — subprocess-free; initialise once and reuse the handle.
        # Re-initialising NVML on every poll is slow, so cache it and stop
        # retrying pynvml entirely once it proves unavailable here.
        if self._pynvml_ok is not False:
            try:
                if self._pynvml_h is None:
                    with warnings.catch_warnings():
                        warnings.filterwarnings(
                            "ignore",
                            message=r"The pynvml package is deprecated.*",
                            category=FutureWarning,
                        )
                        import pynvml  # type: ignore
                    pynvml.nvmlInit()
                    self._pynvml    = pynvml
                    self._pynvml_h  = pynvml.nvmlDeviceGetHandleByIndex(0)
                    self._pynvml_ok = True
                return float(self._pynvml.nvmlDeviceGetUtilizationRates(self._pynvml_h).gpu)
            except Exception:
                self._pynvml_ok = False

        # Windows: nvml.dll via ctypes (already cached in _nvml_gpu_windows)
        if _OS == "Windows":
            return _nvml_gpu_windows()

        # Linux / macOS: libnvidia-ml shared lib via ctypes — init once, reuse
        try:
            import ctypes

            class _Util(ctypes.Structure):
                _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

            if self._nv_unix is None:
                _lib = "libnvidia-ml.so.1" if _OS == "Linux" else "libnvidia-ml.dylib"
                nv = ctypes.CDLL(_lib)
                nv.nvmlInit_v2()
                dev = ctypes.c_void_p()
                nv.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
                self._nv_unix = (nv, dev)

            nv, dev = self._nv_unix
            u = _Util()
            nv.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(u))
            return float(u.gpu)
        except Exception:
            pass

        return -1.0   # N/A — zero subprocess on all platforms

    def _get_temp(self) -> float:
        # psutil — works on Linux; occasionally Windows with driver support
        try:
            temps = psutil.sensors_temperatures()
            for name in ["coretemp", "k10temp", "cpu_thermal", "acpitz",
                         "cpu-thermal", "zenpower", "it8688"]:
                if name in temps and temps[name]:
                    return temps[name][0].current
            for entries in temps.values():
                if entries:
                    return entries[0].current
        except Exception:
            pass

        # Windows: wmi module (pure Python COM, zero subprocess). Reuse a single
        # connection — building a fresh wmi.WMI() on every poll spins up a COM
        # connection each time and is very slow. Give up after one failure.
        if _OS == "Windows" and self._wmi_ok is not False:
            try:
                if self._wmi_conn is None:
                    import wmi  # type: ignore
                    self._wmi_conn = wmi.WMI(namespace="root/wmi")
                tz = self._wmi_conn.MSAcpi_ThermalZoneTemperature()
                if tz:
                    return (tz[0].CurrentTemperature / 10.0) - 273.15
            except Exception:
                self._wmi_ok   = False
                self._wmi_conn = None

        return -1.0   # N/A — zero subprocess on all platforms

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "cpu": self.cpu,
                "mem": self.mem,
                "net": self.net,
                "gpu": self.gpu,
                "tmp": self.tmp,
            }


_metrics = _SysMetrics()

class HudCanvas(QWidget):
    """MARK 54 / JANE holographic core, designed around the reference HUD.

    Existing assistant integrations are intentionally preserved: the audio layer
    calls ``set_audio_level`` and MainWindow controls ``speaking``/``state`` and
    ``_assistant_name``.  Everything visible in the core is procedural, so there
    are no external image assets to break at runtime.
    """

    @property
    def _amp_disp(self) -> float:
        """Backward-compatible alias for the HUD's smoothed audio amplitude."""
        return self._amp_display

    @_amp_disp.setter
    def _amp_disp(self, value: float) -> None:
        self._amp_display = float(value)

    def __init__(self, face_path: str, assistant_name: str = DEFAULT_ASSISTANT_NAME, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumSize(320, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.muted = False
        self.speaking = False
        self.state = "INITIALISING"
        self._assistant_name = assistant_name
        self._assistant_tagline = DEFAULT_ASSISTANT_TAGLINE
        self._face_path = face_path
        self._ui_prefs = _load_ui_preferences()

        # New engine compatibility: the latest core can provide an animated
        # holographic avatar plus gaze/viseme data. The reactor-core visual
        # remains the default so the established JANE HUD does not change.
        self._avatar = None
        self._avatar_import_error = _HOLOAVATAR_IMPORT_ERROR
        self._face_fallback = None
        try:
            if HoloAvatar is not None:
                self._avatar = HoloAvatar()
        except Exception as exc:
            self._avatar_import_error = str(exc)
            self._avatar = None
        try:
            pm = QPixmap(self._face_path) if self._face_path else QPixmap()
            self._face_fallback = pm if not pm.isNull() else None
        except Exception:
            self._face_fallback = None
        self.hud_style = _load_hud_ui_style()
        self._core_phase = 0.0
        self._visemes = None
        self._vis_i = None

        self._tick = 0
        self._last_time = time.perf_counter()
        # Deliberately uneven orbital speeds so the reference's layered motion
        # feels organic rather than like one rigid spinning disk.
        self._orbit = [0.15, -0.10, 0.075, -0.052, 0.035, -0.024, 0.018]
        self._phase = [0.0, 0.9, 2.1, 3.4, 4.2, 5.0, 5.8]
        self._scan = 0.0
        self._scan2 = math.pi
        self._pulse = 0.0

        # Independent state for the original Animated Face profile.
        self._rings = [0.0, 120.0, 240.0]
        self._pulses = [0.0, 50.0, 100.0]
        self._legacy_scale = 1.0
        self._legacy_target_scale = 1.0
        self._legacy_halo = 55.0
        self._legacy_target_halo = 55.0
        self._blink = True
        self._blink_tick = 0
        self._face_px = None
        self._face_cache = None
        self._face_cache_sz = -1

        self._live_amp = 0.0
        self._amp_display = 0.0
        self._core_scale = 1.0
        self._core_energy = 0.25

        rng = random.Random(5313)
        self._particles: list[dict] = []
        for orbit, count, radius, flat, spread, palette in (
            (0, 135, 0.34, 0.13, 0.050, 0),
            (1, 115, 0.39, 0.16, 0.047, 1),
            (2, 100, 0.44, 0.205, 0.043, 0),
            (3, 88, 0.47, 0.245, 0.040, 1),
            (4, 76, 0.50, 0.29, 0.036, 0),
            (5, 65, 0.31, 0.095, 0.030, 1),
            (6, 50, 0.57, 0.095, 0.028, 0)
        ):
            for _ in range(count):
                self._particles.append({
                    "orbit": orbit,
                    "angle": rng.random() * math.tau,
                    "radius": radius + rng.uniform(-spread, spread),
                    "flat": max(0.055, flat + rng.uniform(-0.025, 0.025)),
                    "speed": rng.uniform(0.55, 1.35),
                    "size": rng.uniform(0.35, 1.7),
                    "alpha": rng.uniform(0.22, 0.9),
                    "phase": rng.random() * math.tau,
                    "palette": palette,
                })
        self._stars = [(rng.random(), rng.random(), rng.uniform(0.18, 0.8), rng.uniform(0.35, 1.15)) for _ in range(240)]

        # 48 smooth spectrum columns. One amplitude path drives both microphone
        # listening and Jane's own speech so the UI does not switch behaviour.
        self._bars = [0.0] * 48
        self._bar_env = [math.sin(math.pi * (i / 47.0)) ** 0.72 for i in range(48)]
        self._bar_phase = [i * 0.53 for i in range(48)]
        self._last_audio_t = 0.0
        self._speech_phase = 0.0
        self._paint_accum = 0.0
        self._paint_ema_ms = 8.0
        self._render_tier = 1
        self._quality_cooldown = 0
        self._paint_error_reported = False

        # Static background is cached so animation frames only redraw the parts
        # that actually move. This is one of the biggest performance wins for the
        # procedural HUD because stars and grid lines never change per frame.
        self._backdrop_cache = None
        self._backdrop_key = None
        self._grid_cache = None
        self._grid_key = None

        self._tmr = QTimer(self)
        try:
            self._tmr.setTimerType(Qt.TimerType.PreciseTimer)
        except Exception:
            pass
        self._tmr.timeout.connect(self._step)
        self._tmr.start(16)

    def resizeEvent(self, event):
        self._backdrop_cache = None
        self._backdrop_key = None
        super().resizeEvent(event)

    def set_audio_level(self, level: float) -> None:
        try:
            lv = float(level)
        except (TypeError, ValueError):
            return
        lv = min(1.0, max(0.0, lv))
        # Preserve the actual instantaneous level. Smoothing happens once in
        # _step(); a running maximum made the orb behave like a binary SPEAKING
        # animation instead of following individual syllables.
        self._live_amp = (self._live_amp * 0.35) + (lv * 0.65)
        self._last_audio_t = time.perf_counter()

    def set_speech_level(self, level: float) -> None:
        """Explicit output-speech amplitude hook; same signal path as set_audio_level."""
        self.set_audio_level(level)

    def glance(self, dx: float, dy: float, hold: float = 1.1) -> None:
        """Forward a gaze request to the optional avatar without breaking core mode."""
        try:
            if self._avatar is not None:
                self._avatar.glance(float(dx), float(dy), float(hold))
        except Exception:
            pass

    def push_visemes(self, frames, hop: float, at: float) -> None:
        """Queue speech visemes from the new audio engine on one continuous timeline."""
        try:
            if not frames:
                return
            hop = max(1e-3, float(hop))
            at = float(at)
            incoming = list(frames)
            cur = self._visemes
            if cur is not None:
                old_frames, t0, old_hop = cur
                if abs(old_hop - hop) < 1e-6:
                    idx = int(round((at - t0) / hop))
                    if 0 <= idx <= len(old_frames) + 1:
                        merged = old_frames[:idx] + incoming
                        played = max(0, int((time.time() - t0) / hop) - 2)
                        if played > 60:
                            merged = merged[played:]
                            t0 += played * hop
                            if self._vis_i is not None:
                                self._vis_i = max(0, self._vis_i - played)
                        self._visemes = (merged, t0, hop)
                        return
            self._visemes = (incoming, at, hop)
            self._vis_i = None
        except Exception:
            pass

    def _make_grid(self, W: int, H: int) -> QPixmap:
        """Cached dotted grid used by the original FACE/REACTOR HUDs."""
        pm=QPixmap(max(1,W),max(1,H))
        pm.fill(Qt.GlobalColor.transparent)
        gp=QPainter(pm); gp.setPen(QPen(qcol(C.PRI_GHO),1))
        for x in range(0,W,48):
            for y in range(0,H,48): gp.drawPoint(x,y)
        gp.end(); return pm

    def _load_face(self, path: str):
        """Load the original face image once and mask it to a clean circle."""
        self._face_px = None
        self._face_cache = None
        self._face_cache_sz = -1
        if not path:
            return
        try:
            from PIL import Image, ImageDraw
            import io
            img = Image.open(path).convert("RGBA")
            size = min(img.size)
            if size <= 0:
                return
            img = img.resize((size, size), Image.LANCZOS)
            mask = Image.new("L", (size, size), 0)
            ImageDraw.Draw(mask).ellipse((2, 2, size - 2, size - 2), fill=255)
            img.putalpha(mask)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            px = QPixmap()
            if px.loadFromData(buf.getvalue()):
                self._face_px = px
                return
        except Exception:
            pass
        try:
            px = QPixmap(path)
            if not px.isNull():
                self._face_px = px
        except Exception:
            pass

    def _set_hud_style_local(self, mode: str) -> None:
        mode = mode if mode in _HUD_MODE_LABELS else "orbital"
        self.hud_style = mode
        self._blink_tick = 0
        self._grid_cache = None
        self._backdrop_cache = None
        self.update()

    def _step(self):
        now = time.perf_counter()
        dt = min(0.05, max(0.001, now - self._last_time))
        self._last_time = now
        self._tick += 1
        self._core_phase += dt

        self._blink_tick += 1
        if self._blink_tick >= 38:
            self._blink = not self._blink
            self._blink_tick = 0

        # The newer audio engine can provide fine-grained speech frames. Use the
        # current/queued viseme level to enrich the same amplitude path used by
        # the reactor and spectrum without changing the existing public API.
        vis_level = None
        sched = self._visemes
        wall_now = time.time()
        if sched is not None:
            frames, t0, hop = sched
            # `at` is the wall-clock instant at which the audio batch begins to
            # sound. Use the same clock here; perf_counter has a different epoch.
            idx = int((wall_now - t0) / hop)
            if 0 <= idx < len(frames):
                start_i = self._vis_i if self._vis_i is not None else idx
                end_i = idx + 1
                recent = frames[max(0, start_i):end_i]
                self._vis_i = end_i
                try:
                    # Drive the orb from the current speech frame instead of the
                    # maximum since the previous paint. A running maximum makes one
                    # loud syllable hold the core at the same energy instead of letting
                    # each syllable rise and fall naturally.
                    f_now = frames[idx]
                    loud = max(0.0, min(1.0, float(f_now[0])))
                    openness = max(0.0, min(1.0, float(f_now[1]))) if len(f_now) > 1 else 0.0
                    if len(recent) >= 2:
                        f_prev = recent[-2]
                        loud = loud * 0.78 + max(0.0, min(1.0, float(f_prev[0]))) * 0.22
                        if len(f_prev) > 1:
                            openness = openness * 0.78 + max(0.0, min(1.0, float(f_prev[1]))) * 0.22
                    vis_level = max(0.0, min(1.0, loud * 0.78 + openness * 0.34))
                except Exception:
                    vis_level = None
            elif idx >= len(frames):
                self._visemes = None
                self._vis_i = None
        if vis_level is not None:
            self._live_amp = max(self._live_amp, min(1.0, vis_level))

        # Speech should drive the core, not the mere fact that ``speaking`` is
        # true. Viseme openness is used when available; an output-audio callback
        # remains authoritative whenever it is being delivered. There is no
        # artificial speaking pulse here, so quiet words stay quiet and louder
        # syllables actually push the orb harder.
        attack = 0.76
        release = 0.14
        if self._live_amp > self._amp_display:
            self._amp_display += (self._live_amp - self._amp_display) * attack
        else:
            self._amp_display += (self._live_amp - self._amp_display) * release
        self._live_amp *= 0.84
        amp = self._amp_display

        if self._avatar is not None and self.hud_style == "face":
            try:
                v_open = v_wide = v_level = None
                v_seq = None
                if sched is not None:
                    frames, _t0, _hop = sched
                    i = int((wall_now - _t0) / _hop)
                    if 0 <= i < len(frames):
                        v_level = float(frames[i][0])
                        v_open = float(frames[i][1]) if len(frames[i]) > 1 else 0.0
                        v_wide = float(frames[i][2]) if len(frames[i]) > 2 else 0.0
                        j = self._vis_i if self._vis_i is not None else i
                        v_seq = frames[max(0, j):i + 1]
                try:
                    self._avatar.step(dt, amp, speaking=(self.speaking and amp > 0.015),
                                      muted=self.muted, state=self.state,
                                      v_open=v_open, v_wide=v_wide or 0.0,
                                      v_level=v_level, v_seq=v_seq,
                                      v_hop=(sched[2] if sched is not None else 0.02))
                except TypeError:
                    # Older HoloAvatar builds do not expose viseme keyword args.
                    self._avatar.step(dt, amp, speaking=(self.speaking and amp > 0.015),
                                      muted=self.muted, state=self.state)
            except Exception:
                pass

        reactivity = float(self._ui_prefs.get("speech_reactivity", 1.0))
        reactivity = max(0.25, min(2.20, reactivity))
        motion_scale = 0.55 if self._ui_prefs.get("low_motion") else 1.0
        breathing = 0.004 * math.sin(self._tick * 0.052) * motion_scale
        speech_amp = max(0.0, min(1.0, amp * reactivity))
        # The centre reacts to the measured speech signal itself. SPEAKING only
        # selects the signal path; it never adds a fixed visual boost.
        signal = speech_amp
        target = 1.0 + breathing + signal * 0.68
        self._core_scale += (target - self._core_scale) * (0.38 if signal > 0.018 else 0.09)
        energy_target = 0.22 + signal * 2.85
        self._core_energy += (energy_target - self._core_energy) * 0.17

        boost = (1.0 + signal * 3.15) * motion_scale
        for i, base in enumerate(self._orbit):
            self._phase[i] = (self._phase[i] + base * dt * boost) % math.tau
        self._scan = (self._scan + dt * (0.48 + signal * 4.6) * motion_scale) % math.tau
        self._scan2 = (self._scan2 - dt * (0.34 + signal * 2.9) * motion_scale) % math.tau
        self._pulse += dt * (0.56 + signal * 6.8) * motion_scale
        # Keep pulse unbounded. Several central-layer phases multiply it by
        # non-integer factors; wrapping at tau would otherwise cause visible
        # discontinuities when the multiplier is applied.

        # Original FACE/REACTOR animation state: still reacts to the same measured signal.
        if self.muted:
            self._legacy_target_scale = 1.0 + signal * 0.02
            self._legacy_target_halo = 20.0 + signal * 22.0
        else:
            self._legacy_target_scale = 1.003 + signal * 0.16
            self._legacy_target_halo = 55.0 + signal * 110.0
        legacy_sp = 0.28 if signal > 0.02 else 0.10
        self._legacy_scale += (self._legacy_target_scale - self._legacy_scale) * legacy_sp
        self._legacy_halo += (self._legacy_target_halo - self._legacy_halo) * legacy_sp

        ring_boost = 1.0 + signal * 2.2
        ring_speeds = (0.55, -0.35, 0.9)
        for i, speed in enumerate(ring_speeds):
            self._rings[i] = (self._rings[i] + speed * ring_boost) % 360.0
        fw_legacy = min(self.width(), self.height())
        lim = fw_legacy * 0.74
        pulse_speed = 2.0 + signal * 8.0
        self._pulses = [r + pulse_speed for r in self._pulses if r + pulse_speed < lim]
        if len(self._pulses) < 3 and random.random() < (0.035 + signal * 0.10):
            self._pulses.append(0.0)

        n = len(self._bars)
        bars_on = bool(self._ui_prefs.get("bar_reactivity", True))
        bar_fast = signal > 0.015
        bar_rate = (0.56 if bar_fast else 0.14) * (0.68 if self._ui_prefs.get("low_motion") else 1.0)
        for i in range(n):
            shimmer = 0.72 + 0.28 * math.sin(self._tick * 0.19 + self._bar_phase[i])
            target_bar = (signal * (0.16 + 0.84 * self._bar_env[i]) * shimmer) if bars_on else 0.0
            self._bars[i] += (target_bar - self._bars[i]) * bar_rate

        # Animation state is advanced on the 60 Hz timer, but expensive QPainter
        # frames are deliberately paced around 45 FPS while active and ~28 FPS
        # while idle. This keeps Python paint work from starving input, signals,
        # response callbacks and the audio threads.
        self._paint_accum += dt
        active = amp > 0.008 or self.state in ("THINKING", "PROCESSING")
        paint_interval = 0.022 if active else 0.036
        if self._paint_accum >= paint_interval:
            self._paint_accum = min(0.04, self._paint_accum - paint_interval)
            if self.isVisible():
                self.update()

    @staticmethod
    def _blend(a, b, t):
        t = max(0.0, min(1.0, t))
        ca, cb = QColor(a), QColor(b)
        return QColor(
            int(ca.red() * (1 - t) + cb.red() * t),
            int(ca.green() * (1 - t) + cb.green() * t),
            int(ca.blue() * (1 - t) + cb.blue() * t),
        )

    def _glow(self, p: QPainter, cx, cy, radius, color, alpha):
        g = QRadialGradient(QPointF(cx, cy), radius)
        c = QColor(color); c.setAlpha(int(alpha))
        g.setColorAt(0.0, c)
        c2 = QColor(color); c2.setAlpha(int(alpha * 0.42))
        g.setColorAt(0.38, c2)
        c3 = QColor(color); c3.setAlpha(0)
        g.setColorAt(0.82, c3); g.setColorAt(1.0, c3)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(QRectF(cx-radius, cy-radius, radius*2, radius*2))

    def _arc(self, p, rect, start_deg, span_deg, color, width, alpha):
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(qcol(color, alpha), width))
        p.drawArc(rect, int(start_deg * 16), int(span_deg * 16))

    def _build_backdrop(self, W: int, H: int):
        pm = QPixmap(max(1, W), max(1, H))
        pm.fill(qcol(C.BG))
        bp = QPainter(pm)
        bg = QRadialGradient(QPointF(W * 0.50, H * 0.44), min(W, H) * 0.75)
        bg.setColorAt(0.0, QColor(5, 22, 35, 90))
        bg.setColorAt(0.52, QColor(3, 13, 24, 68))
        bg.setColorAt(1.0, QColor(0, 5, 10, 0))
        bp.setPen(Qt.PenStyle.NoPen)
        bp.setBrush(QBrush(bg))
        bp.drawRect(0, 0, W, H)
        bp.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        for sx, sy, sa, ss in self._stars:
            bp.setPen(Qt.PenStyle.NoPen)
            bp.setBrush(QBrush(qcol(C.WHITE, int(62 * sa))))
            bp.drawEllipse(QPointF(sx * W, sy * H), ss, ss)
        bp.setPen(QPen(qcol("#245173", 24), 1))
        for x in range(int((W * 0.50) % 54), W, 54):
            bp.drawLine(QPointF(x, 0), QPointF(x, H))
        for y in range(int((H * 0.44) % 54), H, 54):
            bp.drawLine(QPointF(0, y), QPointF(W, y))
        bp.end()
        return pm

    def _draw_orbit_fiber(self, p, cx, cy, rx, ry, phase, color_a, color_b, alpha=95, width=1.0, wobble=0.035):
        """Draw a softly irregular, hand-made-looking orbital fiber around the core."""
        path = QPainterPath()
        samples = 64
        for i in range(samples + 1):
            t = math.tau * i / samples
            wave = 1.0 + wobble * math.sin(3.0 * t + phase) + 0.018 * math.sin(7.0 * t - phase * 1.7)
            x = cx + math.cos(t + phase * 0.045) * rx * wave
            y = cy + math.sin(t + phase * 0.045) * ry * (1.0 + wobble * 0.4 * math.cos(2.0 * t + phase))
            if i == 0:
                path.moveTo(QPointF(x, y))
            else:
                path.lineTo(QPointF(x, y))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(qcol(color_a if math.sin(phase) < 0 else color_b, alpha), width))
        p.drawPath(path)

    def _core_colours(self):
        if self.muted:
            return qcol(C.MUTED_C), qcol(C.MUTED_C)
        if self.speaking:
            return qcol(C.PRI), qcol(C.ACC)
        if self.state in ("THINKING", "PROCESSING"):
            return qcol(C.PRI), qcol(C.ACC2)
        if self.state == "LISTENING":
            return qcol(C.PRI), qcol(C.GREEN)
        return qcol(C.PRI), qcol(C.PRI_DIM)

    def _paint_core(self, p: QPainter, cx: float, cy: float, r: float,
                        W: float = 0.0, H: float = 0.0):
            """Draw the reactor at (cx, cy) with outer radius r, using the whole
            canvas (W x H) for the marks that frame it."""
            main, acc = self._core_colours()
            bg = qcol(C.BG)
            amp = max(0.0, min(1.0, float(self._amp_display)))
            reactivity = max(0.25, min(2.20, float(self._ui_prefs.get("speech_reactivity", 1.0))))
            speech_signal = max(0.0, min(1.0, amp * reactivity))
            t = self._core_phase
            live = speech_signal > 0.035 and not self.muted

            def blend(col: QColor, a: float) -> QColor:
                """Pre-mix onto the background instead of asking Qt to composite.
                The raster engine's opaque path is several times faster than its
                translucent one, and everything here is a line or an arc."""
                k = max(0.0, min(1.0, a))
                return QColor(int(bg.red()   + (col.red()   - bg.red())   * k),
                              int(bg.green() + (col.green() - bg.green()) * k),
                              int(bg.blue()  + (col.blue()  - bg.blue())  * k))

            p.setBrush(Qt.BrushStyle.NoBrush)

            # 1. The atmosphere. One radial gradient doing what a stack of discs did
            #    badly: a wide, soft body of light that gives the thing presence
            #    before any detail is read. This single element decides whether the
            #    HUD looks vast or looks small, so it is drawn first and drawn big.
            # Concentrated rather than spread: a gradient reaching the outer rim
            # washes the whole disc a flat dim blue and reads as fog. Ending it at
            # two thirds leaves it a body of light with somewhere to fall off to,
            # which is what makes it look lit rather than tinted.
            lift = 1.0 + 0.72 * speech_signal
            p.setPen(Qt.PenStyle.NoPen)
            for gr, a0, a1 in ((r * 0.70, 0.30, 0.0), (r * 0.34, 0.34, 0.0)):
                g = QRadialGradient(cx, cy, gr)
                g.setColorAt(0.00, blend(main, min(0.95, a0 * lift)))
                g.setColorAt(0.45, blend(main, min(0.95, a0 * lift * 0.52)))
                g.setColorAt(0.78, blend(main, min(0.95, a0 * lift * 0.18)))
                g.setColorAt(1.00, blend(main, a1))
                p.setBrush(QBrush(g))
                p.drawEllipse(QRectF(cx - gr, cy - gr, gr * 2, gr * 2))
            p.setBrush(Qt.BrushStyle.NoBrush)

            # 2. Frame marks at the corners of the whole canvas, not of the circle.
            #    They are what set the scale: the eye reads the reactor as filling
            #    the room rather than sitting in the middle of it.
            if W > 40 and H > 40:
                m, arm = min(W, H) * 0.035, min(W, H) * 0.055
                p.setPen(QPen(blend(main, 0.45), 1.4))
                for sx, sy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
                    x = cx + sx * (W / 2 - m)
                    y = cy + sy * (H / 2 - m)
                    p.drawLine(QLineF(x, y, x - sx * arm, y))
                    p.drawLine(QLineF(x, y, x, y - sy * arm))

            # 3. Crosshair across the full canvas, broken around the core so it
            #    frames the reactor rather than crossing it.
            p.setPen(QPen(blend(main, 0.16), 1))
            gap = r * 0.62
            if W > 40:
                p.drawLine(QLineF(cx - W / 2, cy, cx - gap, cy))
                p.drawLine(QLineF(cx + gap, cy, cx + W / 2, cy))
            if H > 40:
                p.drawLine(QLineF(cx, cy - H / 2, cx, cy - gap))
                p.drawLine(QLineF(cx, cy + gap, cx, cy + H / 2))

            # 4. Two thin outer circles. Sparse on purpose — a dense ring reads as a
            #    grey band at this size, and restraint is what made the original
            #    look expensive.
            for rr, a in ((1.00, 0.34), (0.93, 0.16)):
                rad = r * rr
                p.setPen(QPen(blend(main, a), 1))
                p.drawEllipse(QRectF(cx - rad, cy - rad, rad * 2, rad * 2))

            # 5. Long, sparse graduations: 24 majors reaching well in from the rim,
            #    with shorter minors between them.
            major, minor = [], []
            for i in range(72):
                a = math.radians(i * 5.0)
                ca, sa = math.cos(a), math.sin(a)
                if i % 3 == 0:
                    major.append(QLineF(cx + ca * r * 0.885, cy + sa * r * 0.885,
                                        cx + ca * r * 0.985, cy + sa * r * 0.985))
                else:
                    minor.append(QLineF(cx + ca * r * 0.945, cy + sa * r * 0.945,
                                        cx + ca * r * 0.985, cy + sa * r * 0.985))
            p.setPen(QPen(blend(main, 0.42), 1.3))
            p.drawLines(major)
            p.setPen(QPen(blend(main, 0.18), 1))
            p.drawLines(minor)

            # 6. Sweeping arcs. Long spans, not dashes — the original's grandeur
            #    came from a few big strokes. Speed is the state: idle drifts,
            #    thinking hurries, speaking runs.
            rate = 1.0 + (1.9 if self.state in ("THINKING", "PROCESSING") else 0.0) + speech_signal * 2.4
            for k, (rr, span, count, dirn, col, a, wid) in enumerate((
                    (0.955, 118, 2, +1, acc,  0.75, 2.0),
                    (0.845, 82,  3, -1, main, 0.38, 1.3),
                    (0.760, 150, 1, +1, acc,  0.45, 1.6),
                    (0.660, 64,  4, -1, main, 0.26, 1.1),
                    (0.545, 128, 2, +1, main, 0.30, 1.2))):
                rad = r * rr
                p.setPen(QPen(blend(col, a), wid))
                box = QRectF(cx - rad, cy - rad, rad * 2, rad * 2)
                base = (t * rate * (9 + k * 6) * dirn) % 360.0
                for sgm in range(count):
                    p.drawArc(box, int((base + sgm * (360.0 / count)) * 16),
                              int(span * 16))

            # 7. The voice, as a ring of graduations that grow with it. Kept out at
            #    a wide radius so it never crowds the middle.
            n = 60
            ring = r * 0.415
            spikes = []
            for i in range(n):
                a = math.radians(i * (360.0 / n))
                ca, sa = math.cos(a), math.sin(a)
                wob = 0.5 + 0.5 * math.sin(t * 2.3 + i * 0.42)
                idle = 0.018 + 0.012 * math.sin(t * 1.2 + i * 0.7)
                h = r * (idle + (amp * 0.20 * wob if live else 0.0))
                spikes.append(QLineF(cx + ca * ring, cy + sa * ring,
                                     cx + ca * (ring + h), cy + sa * (ring + h)))
            p.setPen(QPen(blend(acc if live else main, 0.25 + 0.5 * amp), 1.6))
            p.drawLines(spikes)

            # 8. The inner ring the name sits in.
            inner = r * 0.355
            p.setPen(QPen(blend(acc, 0.30 + 0.45 * amp), 1.5))
            p.drawEllipse(QRectF(cx - inner, cy - inner, inner * 2, inner * 2))

            # 9. The name, sized from the string rather than from the radius alone:
            #    "J.A.R.V.I.S" and a name someone renamed to "MAX" are very
            #    different widths, and a fixed fraction of r spills one of them past
            #    the ring it is supposed to sit inside.
            name = self._assistant_name or ""
            if name:
                space = max(1.0, r * 0.018)
                fsz = max(8, int(min(r * 0.105,
                                     (inner * 1.75) / max(1, len(name)) * 1.6 - space)))
                f = QFont("Courier New", fsz, QFont.Weight.Bold)
                f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, space)
                p.setFont(f)
                p.setPen(QPen(blend(qcol(C.WHITE), 0.6 + 0.4 * min(1.0, amp * 2)), 1))
                p.drawText(QRectF(cx - r, cy - fsz, r * 2, fsz * 2),
                           Qt.AlignmentFlag.AlignCenter, name)


    def _paint_status_and_waveform(self, p: QPainter, W: int, H: int, cx: float, cy: float, fw: float) -> None:
        """Shared bottom HUD treatment for FACE and REACTOR modes."""
        sy = cy + fw * 0.40
        if self.muted:
            txt, col = "⊘  MUTED", qcol(C.MUTED_C)
        elif self.speaking:
            txt, col = "●  SPEAKING", qcol(C.ACC)
        elif self.state == "THINKING":
            sym = "◈" if self._blink else "◇"; txt, col = f"{sym}  THINKING", qcol(C.ACC2)
        elif self.state == "PROCESSING":
            sym = "▷" if self._blink else "▶"; txt, col = f"{sym}  PROCESSING", qcol(C.ACC2)
        elif self.state == "LISTENING":
            sym = "●" if self._blink else "○"; txt, col = f"{sym}  LISTENING", qcol(C.GREEN)
        else:
            sym = "●" if self._blink else "○"; txt, col = f"{sym}  {self.state}", qcol(C.PRI)

        p.setPen(QPen(col, 1))
        p.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        p.drawText(QRectF(0, sy, W, 26), Qt.AlignmentFlag.AlignCenter, txt)

        wy = sy + 30
        N, bw = 36, 8
        wx0 = (W - N * bw) / 2
        amp = max(0.0, min(1.0, float(self._amp_display)))
        speech_signal = amp * max(0.25, min(2.2, float(self._ui_prefs.get("speech_reactivity", 1.0))))
        speech_signal = max(0.0, min(1.0, speech_signal))
        mid = (N - 1) / 2.0
        for i in range(N):
            if self.muted:
                hgt, cl = 2, qcol(C.MUTED_C)
            else:
                env = (1.0 - abs(i - mid) / mid) ** 0.7
                shimmer = 0.55 + 0.45 * math.sin(self._tick * 0.18 + i * 0.7)
                idle = 3.0 + 2.0 * math.sin(self._tick * 0.09 + i * 0.6)
                hgt = int(max(2, min(24, idle + amp * 22.0 * env * shimmer)))
                cl = qcol(C.PRI) if amp > 0.05 and hgt > 12 else qcol(C.PRI_DIM if amp > 0.05 else C.BORDER_B)
            p.fillRect(QRectF(wx0 + i * bw, wy + 20 - hgt, bw - 1, hgt), cl)

        # Shared, perfectly centred technical footer.
        name = (self._assistant_name or DEFAULT_ASSISTANT_NAME).strip().upper()
        p.setFont(QFont("Courier New", 6))
        p.setPen(QPen(qcol("#497385", 150), 1))
        p.drawText(QRectF(0, H - 20, W, 12), Qt.AlignmentFlag.AlignCenter,
                   f"MARK 54  ·  {name}  ·  PERSONAL AI ASSISTANT")

    def _ensure_avatar(self):
        if self._avatar is not None:
            return self._avatar
        if HoloAvatar is None:
            return None
        try:
            self._avatar = HoloAvatar()
            self._avatar_import_error = None
        except Exception as exc:
            self._avatar_import_error = str(exc)
            self._avatar = None
        return self._avatar

    def _paint_face_fallback(self, p: QPainter, cx: float, cy: float, fw: float, sy: float):
        pm = self._face_fallback
        band_h = max(90.0, sy - 24.0)
        face_r = min(fw * 0.335, band_h * 0.42)
        p.setBrush(Qt.BrushStyle.NoBrush)
        if pm is not None:
            q = max(48, int(face_r * 2.0 / 4.0) * 4)
            scaled = pm.scaled(q, q, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            p.save()
            clip = QPainterPath()
            clip.addEllipse(QRectF(cx-face_r, cy-face_r, face_r*2, face_r*2))
            p.setClipPath(clip)
            p.drawPixmap(int(cx - scaled.width()/2), int(cy - scaled.height()/2), scaled)
            p.restore()
            glow = face_r * (1.04 + 0.07 * min(1.0, self._amp_display))
            p.setPen(QPen(qcol(C.PRI, 70 + int(45*self._amp_display)), 1.5))
            p.drawEllipse(QRectF(cx-glow, cy-glow, glow*2, glow*2))
            p.setPen(QPen(qcol(C.PRI, 95), 1.0))
            p.drawArc(QRectF(cx-glow*1.1, cy-glow*1.1, glow*2.2, glow*2.2), int((self._scan % 360)*16), 72*16)
            return
        p.setPen(QPen(qcol(C.PRI, 110), 1.2))
        p.drawEllipse(QRectF(cx-face_r, cy-face_r, face_r*2, face_r*2))
        p.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.TEXT_DIM, 190), 1))
        p.drawText(QRectF(cx-face_r, cy-face_r-20, face_r*2, 18), Qt.AlignmentFlag.AlignCenter, "ANIMATED FACE")

    def _paint_legacy_face(self, p: QPainter, cx: float, cy: float, fw: float) -> None:
        """Original Animated Face centrepiece, independent of Orbital HUD."""
        r_face = fw * 0.31
        amp = max(0.0, min(1.0, float(self._amp_display)))
        reactivity = max(0.25, min(2.20, float(self._ui_prefs.get("speech_reactivity", 1.0))))
        speech_signal = max(0.0, min(1.0, amp * reactivity))
        for i in range(10):
            r = r_face * (1.8 - i * 0.08)
            frc = 1.0 - i / 10.0
            alpha = max(0, min(255, int(self._legacy_halo * 0.085 * frc)))
            p.setPen(QPen(qcol(C.MUTED_C if self.muted else C.PRI, alpha), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(cx-r, cy-r, r*2, r*2))

        for pr in self._pulses:
            alpha = max(0, int(230 * (1.0 - pr / max(1.0, fw * 0.74))))
            p.setPen(QPen(qcol(C.MUTED_C if self.muted else C.PRI, alpha), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(cx-pr, cy-pr, pr*2, pr*2))

        for idx, (r_frac, width, arc_len, gap) in enumerate(
            ((0.48, 3, 115, 78), (0.40, 2, 78, 55), (0.32, 1, 56, 40))
        ):
            ring_r = fw * r_frac
            base = self._rings[idx]
            alpha = max(0, min(255, int(self._legacy_halo * (1.0 - idx * 0.18))))
            p.setPen(QPen(qcol(C.MUTED_C if self.muted else C.PRI, alpha), width))
            p.setBrush(Qt.BrushStyle.NoBrush)
            rect = QRectF(cx-ring_r, cy-ring_r, ring_r*2, ring_r*2)
            angle = base
            while angle < base + 360.0:
                p.drawArc(rect, int(angle*16), int(arc_len*16))
                angle += arc_len + gap

        sr = fw * 0.50
        srect = QRectF(cx-sr, cy-sr, sr*2, sr*2)
        p.setPen(QPen(qcol(C.MUTED_C if self.muted else C.PRI, min(255, int(self._legacy_halo*1.5))), 2.5))
        arc_span = 44 + int(72 * speech_signal)
        p.drawArc(srect, int(self._scan*16), int(arc_span*16))
        p.setPen(QPen(qcol(C.ACC, min(255, int(self._legacy_halo*0.75))), 1.5))
        p.drawArc(srect, int(self._scan2*16), int(arc_span*16))

        t_out, t_in = fw*0.497, fw*0.474
        p.setPen(QPen(qcol(C.PRI, 140), 1))
        for deg in range(0, 360, 10):
            rad = math.radians(deg)
            inn = t_in if deg % 30 == 0 else t_in + 6
            p.drawLine(QPointF(cx+t_out*math.cos(rad), cy-t_out*math.sin(rad)),
                       QPointF(cx+inn*math.cos(rad), cy-inn*math.sin(rad)))

        ch_r, gap_h = fw*0.51, fw*0.16
        p.setPen(QPen(qcol(C.PRI, int(self._legacy_halo*0.50)), 1))
        p.drawLine(QPointF(cx-ch_r, cy), QPointF(cx-gap_h, cy))
        p.drawLine(QPointF(cx+gap_h, cy), QPointF(cx+ch_r, cy))
        p.drawLine(QPointF(cx, cy-ch_r), QPointF(cx, cy-gap_h))
        p.drawLine(QPointF(cx, cy+gap_h), QPointF(cx, cy+ch_r))

        if self._face_px is not None:
            fsz = max(1, int(fw*0.62*self._legacy_scale))
            q_sz = max(1, (fsz//4)*4)
            if self._face_cache is None or self._face_cache_sz != q_sz:
                self._face_cache = self._face_px.scaled(
                    q_sz, q_sz, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
                self._face_cache_sz = q_sz
            scaled = self._face_cache
            p.drawPixmap(int(cx-scaled.width()/2), int(cy-scaled.height()/2), scaled)
        else:
            face_r = max(36.0, fw*0.24*self._legacy_scale)
            p.setPen(QPen(qcol(C.PRI, min(255, int(self._legacy_halo*1.7))), 1.2))
            p.setBrush(QBrush(qcol(C.PANEL2, 220)))
            p.drawEllipse(QRectF(cx-face_r, cy-face_r, face_r*2, face_r*2))
            p.setPen(QPen(qcol(C.PRI, 165), 1))
            p.drawEllipse(QRectF(cx-face_r*0.72, cy-face_r*0.72, face_r*1.44, face_r*1.44))
            p.setFont(QFont("Courier New", max(10, int(fw*0.025)), QFont.Weight.Bold))
            p.setPen(QPen(qcol(C.PRI, 190), 1))
            p.drawText(QRectF(cx-face_r, cy-10, face_r*2, 20), Qt.AlignmentFlag.AlignCenter, "ANIMATED FACE")

    def _paint_original_modes(self, _event):
        """Render the two original centrepieces as separate HUD profiles."""
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), qcol(C.BG))
        W, H = self.width(), self.height()
        cx, cy = W/2.0, H/2.0
        fw = min(W, H)

        gkey = (W, H, C.PRI_GHO)
        if self._grid_cache is None or self._grid_key != gkey:
            self._grid_cache = self._make_grid(W, H)
            self._grid_key = gkey
        p.drawPixmap(0, 0, self._grid_cache)

        sy = cy + fw*0.40
        band_top = 12.0
        band_h = max(60.0, sy - 12.0 - band_top)
        if self.hud_style == "face":
            avatar = self._avatar
            if avatar is not None:
                try:
                    head_r = min(fw * 0.355, band_h / (avatar.SPAN + 0.08))
                    head_cy = band_top + (band_h - avatar.SPAN * head_r) / 2.0 + head_r
                    if self.muted:
                        main_col = acc_col = qcol(C.MUTED_C)
                    else:
                        main_col = qcol(C.PRI)
                        acc_col = (qcol(C.ACC) if self.speaking else
                                   qcol(C.ACC2 if self.state in ("THINKING", "PROCESSING") else
                                        C.GREEN if self.state == "LISTENING" else C.PRI))
                    avatar.paint(p, cx, head_cy, head_r, main_col, acc_col, qcol(C.BG))
                except Exception as exc:
                    self._avatar_import_error = str(exc)
                    self._paint_legacy_face(p, cx, band_top + band_h/2.0, fw)
            else:
                self._paint_legacy_face(p, cx, band_top + band_h/2.0, fw)
        else:
            r = min(W*0.46, band_h/2.0)
            self._paint_core(p, cx, band_top + band_h/2.0, r, W, band_h)

        if self.muted:
            txt, col = "⊘  MUTED", qcol(C.MUTED_C)
        elif self.speaking:
            txt, col = "●  SPEAKING", qcol(C.ACC)
        elif self.state == "THINKING":
            sym = "◈" if self._blink else "◇"; txt, col = f"{sym}  THINKING", qcol(C.ACC2)
        elif self.state == "PROCESSING":
            sym = "▷" if self._blink else "▶"; txt, col = f"{sym}  PROCESSING", qcol(C.ACC2)
        elif self.state == "LISTENING":
            sym = "●" if self._blink else "○"; txt, col = f"{sym}  LISTENING", qcol(C.GREEN)
        else:
            sym = "●" if self._blink else "○"; txt, col = f"{sym}  {self.state}", qcol(C.PRI)
        p.setPen(QPen(col, 1)); p.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        p.drawText(QRectF(0, sy, W, 26), Qt.AlignmentFlag.AlignCenter, txt)

        wy = sy + 30
        N, bw = 36, 8
        wx0 = (W-N*bw)/2.0
        amp = max(0.0, min(1.0, self._amp_display))
        mid = (N-1)/2.0
        for i in range(N):
            if self.muted:
                hgt, cl = 2, qcol(C.MUTED_C)
            else:
                env = (1.0 - abs(i-mid)/mid) ** 0.7
                shimmer = 0.55 + 0.45*math.sin(self._tick*0.18+i*0.7)
                idle = 3.0 + 2.0*math.sin(self._tick*0.09+i*0.6)
                hgt = int(max(2, min(24, idle+amp*22.0*env*shimmer)))
                cl = qcol(C.PRI) if amp > 0.05 and hgt > 12 else qcol(C.PRI_DIM if amp > 0.05 else C.BORDER_B)
            p.fillRect(QRectF(wx0+i*bw, wy+20-hgt, bw-1, hgt), cl)
        p.end()

    def _paint_legacy_error(self, mode: str) -> None:
        """Neutral fault view; never substitutes another HUD design."""
        W,H=max(1,self.width()),max(1,self.height())
        p=QPainter(self)
        if not p.isActive(): return
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.fillRect(self.rect(),qcol(C.BG))
            gkey=(W,H,C.PRI_GHO)
            if self._grid_cache is None or self._grid_key != gkey:
                self._grid_cache=self._make_grid(W,H); self._grid_key=gkey
            p.drawPixmap(0,0,self._grid_cache)
            p.setFont(QFont("Courier New",8,QFont.Weight.Bold)); p.setPen(qcol(C.ACC2,180))
            p.drawText(QRectF(0,H*0.46,W,22),Qt.AlignmentFlag.AlignCenter,f"{mode.upper()} RENDER ERROR")
            p.setFont(QFont("Courier New",7)); p.setPen(qcol(C.TEXT_DIM,170))
            p.drawText(QRectF(0,H*0.46+24,W,18),Qt.AlignmentFlag.AlignCenter,f"{self._assistant_name or DEFAULT_ASSISTANT_NAME} HAS KEPT THE INTERFACE RUNNING")
            p.setPen(qcol(C.TEXT_DIM,160))
            p.drawText(QRectF(0,H-22,W,12),Qt.AlignmentFlag.AlignCenter,f"MARK 54  ·  {self._assistant_name or DEFAULT_ASSISTANT_NAME}  ·  PERSONAL AI ASSISTANT")
        finally:
            p.end()

    def paintEvent(self, _event):
        mode=getattr(self,"hud_style","orbital")
        if mode not in ("face","reactor","orbital"):
            mode="orbital"; self.hud_style=mode
        if mode in ("face","reactor"):
            try:
                self._paint_original_modes(_event)
            except Exception as exc:
                if not getattr(self, "_paint_error_reported", False):
                    self._paint_error_reported = True
                self._paint_legacy_error(mode)
            return
        try:
            self._paint_orbital_event(_event)
        except Exception:
            # Never spam the console from a cosmetic HUD failure. Keep the UI alive.
            self._paint_legacy_error("ORBITAL")

    def _paint_orbital_event(self, _event):
        paint_start = time.perf_counter()
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        W, H = self.width(), self.height()
        cx, cy = W * 0.50, H * max(0.34, min(0.54, float(self._ui_prefs.get("center_y", 0.44))))
        fw = float(min(W, H))
        # Smoothed live audio amplitude used by the reactive core, particles,
        # and spectrum. Keep this local to paintEvent so every visual reads the
        # same stable 0..1 value for the current frame.
        amp = max(0.0, min(1.0, float(self._amp_display)))
        reactivity = max(0.25, min(2.20, float(self._ui_prefs.get("speech_reactivity", 1.0))))
        speech_signal = max(0.0, min(1.0, amp * reactivity))
        # Define the central radius before any orbital/near-field layer can use it.
        # Keeping this near the top of paintEvent makes the local unambiguously
        # initialized on every paint path and prevents stale/partial edits from
        # producing an UnboundLocalError.
        center_scale = max(0.78, min(1.18, float(self._ui_prefs.get("center_scale", 1.0))))
        core_r = fw * 0.218 * self._core_scale * center_scale
        glow_mult = max(0.40, min(1.80, float(self._ui_prefs.get("center_glow", 1.0))))
        density = max(0.40, min(1.40, float(self._ui_prefs.get("particle_density", 1.0))))
        particle_field = max(1.0, min(10.0, float(self._ui_prefs.get("particle_field", 1.0))))
        particle_mix = (particle_field - 1.0) / 9.0
        core_r = max(76.0, min(core_r, fw * 0.255))
        backdrop_key = (W, H, C.BG, C.WHITE)
        if self._backdrop_cache is None or self._backdrop_key != backdrop_key:
            self._backdrop_cache = self._build_backdrop(W, H)
            self._backdrop_key = backdrop_key
        p.drawPixmap(0, 0, self._backdrop_cache)

        # ── Giant transparent shell ────────────────────────────────────────────
        shell = fw * 0.475
        self._glow(p, cx, cy, shell * 0.80, "#36cfff", 26)
        self._glow(p, cx, cy, shell * 0.67, "#8f61ff", 22)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(qcol("#4cb6d6", 58), 1.0))
        p.drawEllipse(QRectF(cx-shell, cy-shell, shell*2, shell*2))
        p.setPen(QPen(qcol("#7d6dcb", 26), 0.9))
        p.drawEllipse(QRectF(cx-shell*0.965, cy-shell*0.965, shell*1.93, shell*1.93))

        # Meridian / latitude lines make the outer shell read as a sphere rather
        # than a flat circle.
        for i in range(-4, 5):
            f = i / 4.0
            rx = shell * max(0.05, math.sqrt(max(0.0, 1.0 - f*f)))
            rect = QRectF(cx-rx, cy-shell, rx*2, shell*2)
            col = "#6a78bc" if i % 2 else "#54bed6"
            p.setPen(QPen(qcol(col, 13 if i else 22), 0.8))
            p.drawArc(rect, int((self._scan * 22 + i*19) * 16), 360*16)
        for i in range(-3, 4):
            f = i / 3.0
            ry = shell * (0.16 + 0.66 * (1 - abs(f)))
            rect = QRectF(cx-shell, cy-ry, shell*2, ry*2)
            p.setPen(QPen(qcol("#7184be", 16), 0.8))
            p.drawEllipse(rect)

        # ── The defining layered orbital telemetry ─────────────────────────────
        # Several thin elliptical ribbons are stacked across the sphere. Their
        # colour is intentionally a cyan↔violet blend rather than one flat hue.
        rings = [
            (0.30, 0.075, 1.0, 70),
            (0.34, 0.102, 1.0, 96),
            (0.375, 0.128, 1.2, 112),
            (0.410, 0.155, 0.95, 86),
            (0.448, 0.186, 0.85, 76),
            (0.480, 0.215, 0.75, 54),
        ]
        for idx, (rf, flat, width, alpha) in enumerate(rings):
            rx, ry = fw * rf, fw * flat
            base = math.degrees(self._phase[idx]) + idx * 17
            rect = QRectF(cx-rx, cy-ry, rx*2, ry*2)
            # Full faint loop.
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(qcol("#587798", alpha//3), max(0.6, width*0.5)))
            p.drawEllipse(rect)
            # Long segmented main arc + opposite short telemetry arc.
            main = self._blend("#9d6cff", "#42e3f2", idx / (len(rings)-1))
            main_hex = main.name()
            self._arc(p, rect, base, 235, main_hex, width, alpha)
            self._arc(p, rect, base + 247, 46, "#d9f7ff", max(0.55, width*0.56), alpha//2)
            self._arc(p, rect, base + 112, 18 + idx*2, "#78dff0", 0.7, alpha//2)

        # Dense micro-data ticks around the orbital plane.
        orbit_rect = QRectF(cx-fw*0.50, cy-fw*0.22, fw, fw*0.44)
        for i in range(72):
            a = (i * 3.91 + self._scan * 38) % 360
            rx = orbit_rect.width() * (0.29 + 0.21 * math.sin(math.radians(a))**2)
            px = cx + math.cos(math.radians(a)) * fw * 0.42
            py = cy + math.sin(math.radians(a)) * fw * 0.17
            length = 2.2 if i % 5 else 5.0
            p.setPen(QPen(qcol("#b7e9f5" if i % 5 else "#9d82ff", 66 if i % 5 else 110), 0.7))
            p.drawLine(QPointF(px, py-length/2), QPointF(px, py+length/2))

        # ── Orbiting particles ─────────────────────────────────────────────────
        density_step = max(1, int(round(1.0 / density)))
        particle_step = density_step if self._render_tier >= 2 else max(density_step, (2 if self._render_tier == 1 else 3))
        for pi in range(0, len(self._particles), particle_step):
            pt = self._particles[pi]
            orbit = pt["orbit"]
            a = pt["angle"] + self._phase[orbit] * pt["speed"]
            rr = fw * pt["radius"]
            x = cx + math.cos(a) * rr
            y = cy + math.sin(a) * rr * pt["flat"]
            front = 0.23 + 0.77 * (0.5 + 0.5 * math.sin(a))
            tw = 0.82 + 0.18 * math.sin(self._tick * 0.055 + pt["phase"])
            size = pt["size"] * (0.62 + front * 0.90) * (1.0 + amp * 0.24)
            alpha = int((22 + 172*front) * pt["alpha"] * tw)
            color = "#a47aff" if pt["palette"] else "#57dce9"
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(qcol(color, max(16, min(210, alpha)))))
            p.drawEllipse(QPointF(x, y), size, size)
            if size > 1.4:
                p.setBrush(QBrush(qcol("#e5fbff", min(165, alpha))))
                p.drawEllipse(QPointF(x, y), 0.55, 0.55)

        # ── Near-field energy layers ───────────────────────────────────────────
        # The reference's center is not a single orb: it is a stack of close,
        # translucent, irregular particle/mesh shells. Four independent layers
        # overlap in violet, cyan, and blue to create the mixed holographic depth.
        layer_specs = [
            (0.98, 0.82, 0.070, -17.0, "#aa73ff", 62),
            (1.10, 0.68, 0.055,  23.0, "#4ee4ef", 58),
            (1.23, 0.54, 0.039, -31.0, "#7794ff", 50),
            (1.36, 0.40, 0.028,  39.0, "#c17aff", 42),
        ]
        for li, (mul, flat, wobble, rot, color, a0) in enumerate(layer_specs):
            p.save(); p.translate(cx, cy)
            p.rotate(rot + math.degrees(self._pulse) * (0.20 if li % 2 else -0.15))
            rr = core_r * mul
            pts=[]
            sample_n = 48 if self._render_tier >= 2 else (32 if self._render_tier == 1 else 24)
            for k in range(sample_n):
                t = math.tau * k / float(sample_n)
                wv = 1.0 + wobble * (0.55*math.sin(3*t + li) + 0.30*math.sin(7*t - self._pulse*1.7) + 0.15*math.cos(11*t + li*0.7))
                pts.append(QPointF(math.cos(t)*rr*wv, math.sin(t)*rr*flat*(0.98+0.05*math.sin(2*t+li))*wv))
            path=QPainterPath(); path.moveTo(pts[0])
            for pt in pts[1:]: path.lineTo(pt)
            path.closeSubpath()
            cc=QColor(color); cc.setAlpha(min(115, a0+int(self._core_energy*4)))
            p.setBrush(QBrush(cc)); p.setPen(QPen(qcol(color, max(18,a0-20)), 0.7)); p.drawPath(path)
            p.setBrush(Qt.BrushStyle.NoBrush); p.setPen(QPen(qcol(color, max(10,a0//2)), 0.55))
            p.drawEllipse(QRectF(-rr*0.98, -rr*flat*0.98, rr*1.96, rr*flat*1.96))
            p.restore()

        # Small hot particles remain close to the core and shimmer with the voice.
        hot_n = 52 if self._render_tier >= 2 else (40 if self._render_tier == 1 else 30)
        for k in range(hot_n):
            t = k*(math.tau/float(hot_n)) + self._pulse*(0.68+(k%3)*0.13)
            rr = core_r*(1.04+0.30*((k%7)/6.0))
            wob=1.0+0.06*math.sin(5*t+k)
            px=cx+math.cos(t)*rr*wob; py=cy+math.sin(t)*rr*0.72*wob
            col=self._blend("#b274ff","#48e3ee",0.5+0.5*math.sin(t)).name()
            aa=int(52+100*(0.5+0.5*math.sin(t*2+self._tick*0.03)))
            p.setBrush(QBrush(qcol(col,aa))); p.setPen(Qt.PenStyle.NoPen)
            s=0.65+amp*0.9; p.drawEllipse(QPointF(px,py),s,s)

        # ── Central holographic JANE nucleus ───────────────────────────────────
        # The reference has a smaller glassy nucleus surrounded by several very
        # close, translucent particle/energy layers. Build those layers separately
        # so the purple↔cyan mixing is visible at a glance.
        # core_r was initialized at the top of paintEvent so every central layer
        # uses the same radius and no paint path can read it before assignment.

        # Broad mixed glow: cyan + violet overlap rather than two clean halves.
        self._glow(p, cx - core_r*0.34, cy, core_r*2.45, '#b477ff', (58 + self._core_energy*13) * glow_mult)
        self._glow(p, cx + core_r*0.34, cy, core_r*2.45, '#45e5ed', (62 + self._core_energy*13) * glow_mult)
        self._glow(p, cx, cy, core_r*2.00, '#8a76df', (28 + self._core_energy*7) * glow_mult)

        # Close irregular fibers — the visual signature of the reference nucleus.
        fiber_specs = [
            (1.00, 0.38, -0.05, '#a86fff', '#53e2ea', 118, 1.15),
            (0.96, 0.46,  0.45, '#57e2ea', '#b070ff', 102, 1.00),
            (1.10, 0.56,  1.05, '#b070ff', '#5ce7ef', 86, 0.90),
            (0.88, 0.64,  1.55, '#5de4ed', '#a66cff', 78, 0.85),
            (1.18, 0.30,  2.15, '#a66cff', '#52dce8', 68, 0.82),
        ]
        for rx_f, ry_f, ph, ca, cb, al, wd in fiber_specs:
            self._draw_orbit_fiber(p, cx, cy, core_r*rx_f, core_r*ry_f, self._pulse*0.65 + ph, ca, cb, al + int(self._core_energy*8), wd, 0.030 + self._amp_display*0.018)

        # Dense near-field particles in three very close, differently-shaped layers.
        # This is intentionally irregular: the target has particles that feel like
        # energy trapped around the glass core rather than a perfect geometric ring.
        for layer, (rx_f, ry_f, count, phase_shift) in enumerate((
            (0.92, 0.42, 72, 0.0),
            (1.05, 0.58, 88, 1.7),
            (1.17, 0.72, 104, 3.1),
        )):
            for j in range(count):
                t = (j / count) * math.tau + self._pulse * (0.25 + layer*0.05) + phase_shift
                wob = 1.0 + 0.045*math.sin(4*t + j*0.31) + 0.025*math.sin(9*t - j*0.14)
                ex = core_r * rx_f * wob * math.cos(t)
                ey = core_r * ry_f * (1.0 + 0.08*math.sin(3*t + layer)) * math.sin(t)
                px = cx + ex
                py = cy + ey
                mix = 0.5 + 0.5*math.sin(t*1.15 + j*0.19 + layer)
                col = self._blend('#b06fff', '#55e3eb', mix).name()
                aa = int(32 + 135*(0.5 + 0.5*math.sin(t*1.7 + j*0.47)))
                r = 0.38 + 0.85*(0.5 + 0.5*math.sin(j*1.93 + self._tick*0.035 + layer))
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(qcol(col, aa)))
                p.drawEllipse(QPointF(px, py), r, r)
                if j % 13 == 0:
                    p.setPen(QPen(qcol('#d8f4ff', min(180, aa+35)), 0.55))
                    p.drawLine(QPointF(px-r*1.6, py), QPointF(px+r*1.6, py))

        # Glass sphere / dark core.
        core_grad = QRadialGradient(QPointF(cx-core_r*0.18, cy-core_r*0.20), core_r*1.22)
        solid_mix = 1.0 - 0.86 * particle_mix
        core_grad.setColorAt(0.0, QColor(29, 46, 68, int((120 + self._core_energy*8) * solid_mix)))
        core_grad.setColorAt(0.34, QColor(12, 28, 46, int(175 * solid_mix)))
        core_grad.setColorAt(0.72, QColor(3, 10, 20, int(226 * solid_mix)))
        core_grad.setColorAt(1.0, QColor(0, 3, 8, int(248 * solid_mix)))
        p.setBrush(QBrush(core_grad))
        p.setPen(QPen(qcol('#a8a9df', int(150 * solid_mix)), 1.05))
        p.drawEllipse(QRectF(cx-core_r, cy-core_r, core_r*2, core_r*2))

        # Optional continuum from the existing glass planet (1/10) to a dense
        # suspended particle core (10/10). The distribution is deterministic so
        # changing the slider never causes particle popping or random reshuffles.
        if particle_mix > 0.001:
            base_n = int(60 + 320 * particle_mix * density)
            max_n = 105 if self._render_tier == 0 else (170 if self._render_tier == 1 else 250)
            field_n = min(base_n, max_n)
            for j in range(field_n):
                q = (j * 0.61803398875) % 1.0
                radial = 0.10 + 0.90 * ((j * 37) % field_n) / max(1, field_n - 1)
                ang = math.tau * q + self._pulse * (0.025 + 0.018 * (j % 5))
                wob = 1.0 + 0.05 * math.sin(j * 1.17 + self._pulse * 0.22)
                ex = math.cos(ang) * core_r * radial * 0.98 * wob
                ey = math.sin(ang) * core_r * radial * (0.56 + 0.20 * math.sin(j * 0.37)) * wob
                px, py = cx + ex, cy + ey
                mix = 0.5 + 0.5 * math.sin(j * 0.41 + self._pulse * 0.18)
                col = self._blend('#b66fff', '#56e9ef', mix).name()
                aa = int((24 + 145 * particle_mix) * (0.55 + 0.45 * (0.5 + 0.5 * math.sin(j * 0.93 + self._tick * 0.05))))
                size = (0.28 + 0.85 * ((j * 17) % 23) / 22.0) * (1.0 + 0.42 * speech_signal)
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(qcol(col, min(220, aa))))
                p.drawEllipse(QPointF(px, py), size, size)
            
        # ── Expressive inner particle/membrane field ───────────────────────────
        # The reference centre has visible energy trapped *inside* the glass orb,
        # not only particles on the outer orbit. These translucent membranes and
        # bright motes sit on top of the dark core so the cyan/violet mixture reads
        # immediately instead of disappearing behind the nucleus.
        inner_layers = (
            (0.70, 0.34, 0.0, "#b06fff", 110),
            (0.82, 0.48, 1.9, "#55e4ee", 105),
            (0.94, 0.62, 3.4, "#8b78e8", 92),
        )
        for li, (rf, flat, phase0, col, al0) in enumerate(inner_layers):
            path = QPainterPath()
            for j in range(64):
                t = math.tau * j / 64.0
                wv = 1.0 + 0.065*math.sin(3*t + self._pulse*0.42 + phase0) + 0.032*math.sin(8*t - self._pulse*0.27 + li)
                xx = cx + math.cos(t + phase0*0.11) * core_r * rf * wv
                yy = cy + math.sin(t + phase0*0.11) * core_r * flat * wv
                if j == 0: path.moveTo(QPointF(xx, yy))
                else: path.lineTo(QPointF(xx, yy))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(qcol(col, al0), 0.9 if li < 2 else 0.7))
            p.drawPath(path)

        inner_n = 84 if self._render_tier >= 2 else (64 if self._render_tier == 1 else 48)
        for j in range(inner_n):
            t = (j / float(inner_n)) * math.tau + self._pulse * (0.18 + (j % 5) * 0.012)
            layer = j % 3
            rf = (0.30, 0.55, 0.79)[layer] + 0.035*math.sin(j*1.73)
            flat = (0.34, 0.52, 0.68)[layer]
            wob = 1.0 + 0.07*math.sin(j*0.71 + self._pulse*0.31)
            px = cx + math.cos(t) * core_r * rf * wob
            py = cy + math.sin(t) * core_r * flat * wob
            mix = 0.5 + 0.5*math.sin(t*1.4 + j*0.17)
            col = self._blend("#c080ff", "#4fe5ee", mix).name()
            aa = int(72 + 125*(0.5 + 0.5*math.sin(j*0.93 + self._pulse*0.55)))
            r = 0.32 + 0.90*(0.5 + 0.5*math.sin(j*1.37 + self._tick*0.04)) * (1.0 + amp*0.28)
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(qcol(col, min(225, aa))))
            p.drawEllipse(QPointF(px, py), r, r)
            if j % 19 == 0:
                p.setPen(QPen(qcol("#e6fbff", min(190, aa+35)), 0.65))
                p.drawLine(QPointF(px-r*2.2, py), QPointF(px+r*2.2, py))

        # Fine suspended motes INSIDE the core: these are deliberately clustered
        # in the central volume (not around the outer orbit) so the reference-like
        # particle texture stays visible even when the outer rings are moving.
        mote_n = 112 if self._render_tier >= 2 else (84 if self._render_tier == 1 else 64)
        for j in range(mote_n):
            u = ((j * 37) % 211) / 210.0
            v = ((j * 91) % 223) / 222.0
            ang = u * math.tau + self._pulse * (0.14 + (j % 7) * 0.011)
            radial = (0.08 + 0.82 * (v ** 0.72)) * (0.92 + 0.08 * math.sin(j * 1.71 + self._pulse * 0.20))
            ex = math.cos(ang) * core_r * radial * 0.88
            ey = math.sin(ang) * core_r * radial * (0.50 + 0.18 * math.sin(j * 0.63))
            px = cx + ex
            py = cy + ey
            mix = 0.5 + 0.5 * math.sin(j * 0.37 + self._pulse * 0.33)
            col = self._blend("#d18aff", "#56e9ef", mix).name()
            aa = int(42 + 150 * (0.5 + 0.5 * math.sin(j * 0.91 + self._pulse * 0.47)))
            rad = (0.28 + 0.82 * ((j * 13) % 17) / 16.0) * (1.0 + amp * 0.32)
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(qcol(col, min(225, aa))))
            p.drawEllipse(QPointF(px, py), rad, rad)
            if j % 23 == 0:
                p.setPen(QPen(qcol("#ecfbff", min(185, aa + 30)), 0.65))
                p.drawLine(QPointF(px - rad*2.5, py), QPointF(px + rad*2.5, py))

        # Soft mixed light inside the core makes the purple/cyan overlap unmistakable.
        mix_glow = QRadialGradient(QPointF(cx, cy), core_r * 0.92)
        mix_glow.setColorAt(0.0, QColor(126, 214, 238, 34 + int(self._core_energy * 5)))
        mix_glow.setColorAt(0.34, QColor(150, 105, 232, 24))
        mix_glow.setColorAt(0.65, QColor(82, 214, 225, 16))
        mix_glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(mix_glow))
        p.drawEllipse(QRectF(cx-core_r*0.95, cy-core_r*0.95, core_r*1.90, core_r*1.90))

        # Off-axis glass highlight gives the centre depth instead of reading as a flat disk.
        hi = QRadialGradient(QPointF(cx-core_r*0.42, cy-core_r*0.42), core_r*0.75)
        hi.setColorAt(0.0, QColor(184, 132, 255, 58))
        hi.setColorAt(0.36, QColor(90, 224, 237, 28))
        hi.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(hi))
        p.drawEllipse(QRectF(cx-core_r*1.02, cy-core_r*1.02, core_r*2.04, core_r*2.04))

        # Internal latitude/longitude mesh.
        for k in range(-5, 6):
            f = k / 5.0
            ry = core_r * (0.16 + 0.82 * (1.0 - abs(f)))
            rect = QRectF(cx-core_r*0.98, cy + f*core_r*0.20 - ry*0.5, core_r*1.96, ry)
            mix = 0.5 + 0.5 * math.sin(k*0.7 + self._pulse)
            col = self._blend('#b274ff', '#59e3ec', mix).name()
            p.setPen(QPen(qcol(col, 33 if k else 54), 0.72)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(rect)
        for n in range(7):
            f = -0.80 + n*0.265
            rx = core_r * math.sqrt(max(0.08, 1-f*f))
            col = '#8b7cda' if n % 2 else '#5ccfdb'
            p.setPen(QPen(qcol(col, 40), 0.68))
            p.drawEllipse(QRectF(cx-rx, cy-core_r*0.86, rx*2, core_r*1.72))

        # Two luminous wrapped belts and four tiny tracking arcs.
        core_ring = QRectF(cx-core_r*1.26, cy-core_r*0.50, core_r*2.52, core_r*1.00)
        belt_span = 112 + int(55 * speech_signal)
        self._arc(p, core_ring, 18 + math.degrees(self._pulse), belt_span, '#ca8cff', 1.05 + 0.45*speech_signal, 175 + int(45*speech_signal))
        self._arc(p, core_ring, 198 + math.degrees(self._pulse)*0.72, max(90, belt_span-10), '#5de8ef', 1.05 + 0.45*speech_signal, 175 + int(45*speech_signal))
        for off, col, span in ((-18, '#b57aff', 58), (42, '#5ce6ee', 44), (132, '#8a8de0', 42), (228, '#61dce6', 36)):
            self._arc(p, QRectF(cx-core_r*1.08, cy-core_r*1.08, core_r*2.16, core_r*2.16), off + math.degrees(self._pulse)*0.55, span, col, 0.72, 105)

        # JANE identity — keep it simple, but give the name a clear violet→cyan energy treatment.
        name = (self._assistant_name or 'JANE').strip().upper()
        name_size = max(20, min(36, int(fw * 0.057)))
        name_font = QFont('Segoe UI', name_size, QFont.Weight.Normal)
        try:
            name_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2.2)
        except Exception:
            pass
        p.setFont(name_font)
        name_rect = QRectF(cx-core_r*1.22, cy-name_size*0.48, core_r*2.44, name_size*1.16)
        # Saturated split glow gives the lettering colour without adding new HUD text.
        p.setPen(QPen(qcol('#9d6cff', 52 + int(self._core_energy*10)), 3.2))
        p.drawText(name_rect.translated(-1.0, 0), Qt.AlignmentFlag.AlignCenter, name)
        p.setPen(QPen(qcol('#55e1eb', 46 + int(self._core_energy*10)), 3.0))
        p.drawText(name_rect.translated(1.0, 0), Qt.AlignmentFlag.AlignCenter, name)
        name_grad = QLinearGradient(name_rect.left(), 0, name_rect.right(), 0)
        name_grad.setColorAt(0.0, QColor('#a66cff'))
        name_grad.setColorAt(0.45, QColor('#f4fbff'))
        name_grad.setColorAt(0.70, QColor('#75e8ef'))
        name_grad.setColorAt(1.0, QColor('#43cfe0'))
        p.setPen(QPen(QBrush(name_grad), 1))
        p.drawText(name_rect, Qt.AlignmentFlag.AlignCenter, name)

        # ── Bottom spectrum + fixed status ─────────────────────────────────────
        bar_y = cy + fw * 0.365
        total_w = min(W*0.56, 470.0)
        bw = total_w / len(self._bars)
        x0 = cx - total_w/2
        base_y = bar_y + 24
        p.setPen(QPen(qcol("#18394a", 170), 1))
        p.drawLine(QPointF(x0, base_y+1), QPointF(x0+total_w, base_y+1))
        for i, value in enumerate(self._bars):
            t = i / max(1, len(self._bars)-1)
            col = self._blend("#39e1ee", "#9d6cff", t).name()
            h = 2.3 + value * 38.0
            p.setBrush(QBrush(qcol(col, 240 if value > 0.045 else 118)))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRect(QRectF(x0 + i*bw, base_y-h, max(1.1, bw-1.0), h))

        if self.muted:
            status, scol = "MUTED", C.MUTED_C
        elif self.speaking:
            status, scol = "SPEAKING", "#57dce8"
        elif self.state == "THINKING":
            status, scol = "THINKING", "#ae83ff"
        elif self.state == "PROCESSING":
            status, scol = "PROCESSING", "#ae83ff"
        elif self.state == "LISTENING":
            status, scol = "LISTENING", "#57dce8"
        else:
            status, scol = self.state, C.PRI
        status_y = base_y + 16
        # Restore the original clean reference status treatment: a simple centered
        # pulsing dot followed by the fixed state text.
        status_font = max(10, int(fw*0.023))
        p.setPen(QPen(qcol(scol, 248), 1))
        p.setFont(QFont("Courier New", status_font, QFont.Weight.Bold))
        blink_dot = "●" if (self._tick // 24) % 2 else "○"
        p.drawText(QRectF(0, status_y, W, status_font + 6),
                   Qt.AlignmentFlag.AlignCenter, f"{blink_dot}  {status}")

        # Clean, perfectly centred technical footer.
        p.setFont(QFont("Courier New", 6))
        p.setPen(QPen(qcol("#497385", 150), 1))
        p.drawText(QRectF(0, H-20, W, 12), Qt.AlignmentFlag.AlignCenter,
                   f"MARK 54  ·  {name}  ·  PERSONAL AI ASSISTANT")

        # Registration marks in the center panel corners.
        m, L = 8, 14
        p.setPen(QPen(qcol("#55dced", 75), 1))
        for x, y, dx, dy in ((m,m,1,1),(W-m,m,-1,1),(m,H-m,1,-1),(W-m,H-m,-1,-1)):
            p.drawLine(QPointF(x,y), QPointF(x+dx*L,y))
            p.drawLine(QPointF(x,y), QPointF(x,y+dy*L))

        p.end()
        # Gentle adaptive quality: when procedural painting gets expensive,
        # reduce particle sampling before input/audio threads feel it. Recover
        # one tier when the canvas is comfortably fast again. This never changes
        # the assistant engine or response path.
        elapsed_ms = (time.perf_counter() - paint_start) * 1000.0
        self._paint_ema_ms = self._paint_ema_ms * 0.88 + elapsed_ms * 0.12
        if self._paint_ema_ms > 15.0 and self._render_tier > 0:
            self._render_tier -= 1
        elif self._paint_ema_ms < 7.5 and self._render_tier < 2:
            self._render_tier += 1


class MetricBar(QWidget):
    """Compact live diagnostic graph styled after the MARK 54 reference HUD."""

    def __init__(self, label: str, color: str = C.PRI, parent=None):
        super().__init__(parent)
        self._label = label
        self._color = color
        self._value = 0.0
        self._text = "--"
        self._history: list[float] = []
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(59)

    def set_value(self, pct: float, text: str):
        try:
            v = max(0.0, min(100.0, float(pct)))
        except (TypeError, ValueError):
            return
        first = not self._history
        self._value = v
        self._text = text
        if first:
            self._history = [v] * 32
        else:
            self._history.append(v)
            self._history = self._history[-32:]
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        p.setBrush(QBrush(qcol("#06111b", 235)))
        p.setPen(QPen(qcol("#1a3b50", 185), 1))
        p.drawRoundedRect(QRectF(0.5, 0.5, max(1, W-1), max(1, H-1)), 4, 4)

        p.setFont(QFont("Courier New", 6, QFont.Weight.Bold))
        p.setPen(QPen(qcol("#799caf", 245), 1))
        p.drawText(QRectF(7, 4, W*0.56, 11), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._label)

        value_col = C.RED if self._value > 85 else self._color
        p.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        p.setPen(QPen(qcol(value_col, 248), 1))
        p.drawText(QRectF(W*0.45, 4, max(1, W-8-W*0.45), 11), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._text)

        gx, gy, gw, gh = 6, 18, max(10, W-12), max(8, H-24)
        p.setBrush(QBrush(qcol("#04101a", 245)))
        p.setPen(QPen(qcol("#123142", 120), 1))
        p.drawRoundedRect(QRectF(gx, gy, gw, gh), 2, 2)
        p.setPen(QPen(qcol("#2b5364", 48), 1))
        for frac in (0.33, 0.66):
            yy = gy + gh * (1-frac)
            p.drawLine(QPointF(gx+1, yy), QPointF(gx+gw-1, yy))

        hist = self._history or [self._value]
        denom = max(1, len(hist)-1)
        scale = max(100.0, max(hist) * 1.10)
        pts=[]
        for i, val in enumerate(hist):
            xx = gx + (i/denom)*gw
            yy = gy + gh - (val/scale)*gh
            pts.append(QPointF(xx, yy))
        area = QPainterPath(); area.moveTo(QPointF(gx, gy+gh)); area.lineTo(pts[0])
        for pt in pts[1:]: area.lineTo(pt)
        area.lineTo(QPointF(gx+gw, gy+gh)); area.closeSubpath()
        fc=QColor(self._color); fc.setAlpha(26)
        p.setBrush(QBrush(fc)); p.setPen(Qt.PenStyle.NoPen); p.drawPath(area)
        line=QPainterPath(pts[0])
        for pt in pts[1:]: line.lineTo(pt)
        p.setBrush(Qt.BrushStyle.NoBrush); p.setPen(QPen(qcol(self._color, 225), 1.15)); p.drawPath(line)
        p.setBrush(QBrush(qcol(self._color, 210))); p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(pts[-1], 1.25, 1.25)
        p.end()


class LogWidget(QTextEdit):
    _sig = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        # Cap scrollback so an hours-long session can't grow the document
        # without bound — keeps memory flat and every insert cheap. Oldest
        # lines drop off the top automatically.
        self.document().setMaximumBlockCount(600)
        self.setFont(QFont("Courier New", 8))
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.setStyleSheet(f"""
            QTextEdit {{
                background: {C.PANEL};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 4px;
                padding: 6px;
                selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{
                background: {C.BG};
                width: 8px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B};
                border-radius: 4px;
                min-height: 20px;
            }}
        """)
        self._queue: list[str] = []
        self._typing  = False
        self._text    = ""
        self._pos     = 0
        self._tag     = "sys"
        self._ai_name_lc = "jarvis"   # updated when assistant name changes
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._sig.connect(self._enqueue)

    def append_log(self, text: str):
        self._sig.emit(text)

    def _enqueue(self, text: str):
        self._queue.append(text)
        if not self._typing:
            self._next()

    def _next(self):
        if not self._queue:
            self._typing = False
            return
        self._typing = True
        self._text   = self._queue.pop(0)
        self._pos    = 0
        tl = self._text.lower()
        _ai_pfx = f"{self._ai_name_lc}:"
        if   tl.startswith("you:"):                              self._tag = "you"
        elif tl.startswith(_ai_pfx) or tl.startswith("jarvis:"): self._tag = "ai"
        elif tl.startswith("file:"):                             self._tag = "file"
        elif "err" in tl:                                        self._tag = "err"
        else:                                                    self._tag = "sys"
        self._tmr.start(3)

    def _step(self):
        if self._pos < len(self._text):
            # Insert a small chunk per tick instead of one character. The old
            # 6 ms/character loop made long AI replies feel visibly delayed.
            remaining = len(self._text) - self._pos
            chunk = min(10 if remaining > 80 else 6, remaining)
            chunk_text = self._text[self._pos:self._pos + chunk]
            cur = self.textCursor()
            fmt = cur.charFormat()
            col = {
                "you":  qcol(C.WHITE),
                "ai":   qcol(C.PRI),
                "err":  qcol(C.RED),
                "file": qcol(C.GREEN),
                "sys":  qcol(C.ACC2),
            }.get(self._tag, qcol(C.TEXT))
            fmt.setForeground(QBrush(col))
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText(chunk_text, fmt)
            self.setTextCursor(cur)
            self._pos += chunk
            if self._pos % 28 < chunk or self._pos >= len(self._text):
                self.ensureCursorVisible()
        else:
            self._tmr.stop()
            cur = self.textCursor()
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText("\n")
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            QTimer.singleShot(5, self._next)

_FILE_ICONS = {
    "image":   ("🖼", "#00d4ff"), "video":   ("🎬", "#ff6b00"),
    "audio":   ("🎵", "#cc44ff"), "pdf":     ("📄", "#ff4444"),
    "word":    ("📝", "#4488ff"), "excel":   ("📊", "#44bb44"),
    "code":    ("💻", "#ffcc00"), "archive": ("📦", "#ff8844"),
    "pptx":    ("📊", "#ff6622"), "text":    ("📃", "#aaaaaa"),
    "data":    ("🔧", "#88ddff"), "unknown": ("📎", "#888888"),
}
_EXT_TO_CAT = {
    **dict.fromkeys(["jpg","jpeg","png","gif","webp","bmp","tiff","svg","ico"], "image"),
    **dict.fromkeys(["mp4","avi","mov","mkv","wmv","flv","webm","m4v"],         "video"),
    **dict.fromkeys(["mp3","wav","ogg","m4a","aac","flac","wma","opus"],        "audio"),
    **dict.fromkeys(["pdf"],                                                     "pdf"),
    **dict.fromkeys(["doc","docx"],                                              "word"),
    **dict.fromkeys(["xls","xlsx","ods"],                                        "excel"),
    **dict.fromkeys(["ppt","pptx"],                                              "pptx"),
    **dict.fromkeys(["py","js","ts","jsx","tsx","html","css","java","c","cpp",
                     "cs","go","rs","rb","php","swift","kt","sh","sql","lua"],   "code"),
    **dict.fromkeys(["zip","rar","tar","gz","7z","bz2","xz"],                   "archive"),
    **dict.fromkeys(["txt","md","rst","log"],                                    "text"),
    **dict.fromkeys(["csv","tsv","json","xml"],                                  "data"),
}

def _file_category(path: Path) -> str:
    return _EXT_TO_CAT.get(path.suffix.lower().lstrip("."), "unknown")

def _fmt_size(size: int) -> str:
    if   size < 1024:    return f"{size} B"
    elif size < 1024**2: return f"{size/1024:.1f} KB"
    elif size < 1024**3: return f"{size/1024**2:.1f} MB"
    else:                return f"{size/1024**3:.1f} GB"


class FileDropZone(QWidget):
    file_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(100)
        self._current_file: str | None = None
        self._hovering  = False
        self._drag_over = False
        self._dash_offset = 0.0
        self._anim_tmr = QTimer(self)
        self._anim_tmr.timeout.connect(self._animate)
        self._anim_tmr.start(40)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._canvas = _DropCanvas(self)
        layout.addWidget(self._canvas)

    def _animate(self):
        # The marching-ants dashed border is only meaningful while the user is
        # hovering or dragging a file over the zone. When idle, skip the repaint
        # entirely instead of redrawing the whole zone 25×/s forever — that idle
        # repaint held the GIL and stole time from the audio/response threads.
        if not (self._hovering or self._drag_over):
            return
        self._dash_offset = (self._dash_offset + 0.8) % 20
        self._canvas.update()

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._drag_over = True; self._canvas.update()

    def dragLeaveEvent(self, e):
        self._drag_over = False; self._canvas.update()

    def dropEvent(self, e: QDropEvent):
        self._drag_over = False
        urls = e.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if Path(path).is_file():
                self._set_file(path)
        self._canvas.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._browse()

    def enterEvent(self, e):
        self._hovering = True; self._canvas.update()

    def leaveEvent(self, e):
        self._hovering = False; self._canvas.update()

    def current_file(self) -> str | None:
        return self._current_file

    def clear_file(self):
        self._current_file = None; self._canvas.update()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select a file", str(Path.home()),
            "All Files (*.*);;"
            "Images (*.jpg *.jpeg *.png *.gif *.webp *.bmp *.svg);;"
            "Documents (*.pdf *.docx *.txt *.md *.pptx);;"
            "Data (*.csv *.xlsx *.json *.xml);;"
            "Code (*.py *.js *.ts *.html *.css *.java *.cpp *.go);;"
            "Audio (*.mp3 *.wav *.ogg *.m4a *.aac *.flac);;"
            "Video (*.mp4 *.avi *.mov *.mkv *.wmv *.webm);;"
            "Archives (*.zip *.rar *.tar *.gz *.7z)",
        )
        if path:
            self._set_file(path)

    def _set_file(self, path: str):
        self._current_file = path
        self._canvas.update()
        self.file_selected.emit(path)


class _DropCanvas(QWidget):
    def __init__(self, zone: FileDropZone):
        super().__init__(zone)
        self._z = zone

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        z    = self._z
        W, H = self.width(), self.height()
        pad  = 6
        rect = QRectF(pad, pad, W - pad * 2, H - pad * 2)

        bg_col = qcol("#001a24" if z._drag_over else ("#001218" if z._hovering else C.PANEL))
        p.setBrush(QBrush(bg_col)); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:   border_col = qcol(C.GREEN, 200)
        elif z._drag_over:    border_col = qcol(C.PRI, 230)
        elif z._hovering:     border_col = qcol(C.BORDER_B, 200)
        else:                 border_col = qcol(C.BORDER, 160)

        pen = QPen(border_col, 1.5, Qt.PenStyle.DashLine)
        pen.setDashOffset(z._dash_offset)
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:   self._paint_file(p, W, H)
        elif z._drag_over:    self._paint_drag_over(p, W, H)
        else:                 self._paint_idle(p, W, H, z._hovering)

        p.end()

    def _paint_idle(self, p, W, H, hover):
        cx, cy = W / 2, H / 2
        col = qcol(C.PRI_DIM if not hover else C.PRI)
        p.setPen(QPen(col, 2)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(cx, cy - 14), QPointF(cx, cy + 4))
        p.drawLine(QPointF(cx - 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx + 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx - 14, cy + 4), QPointF(cx + 14, cy + 4))
        p.setFont(QFont("Courier New", 8))
        p.setPen(QPen(qcol(C.PRI_DIM if not hover else C.TEXT), 1))
        p.drawText(QRectF(0, cy + 8, W, 16), Qt.AlignmentFlag.AlignCenter,
                   "Drop file here  or  Click to Browse")
        p.setFont(QFont("Courier New", 7))
        p.setPen(QPen(qcol("#1a4a5a"), 1))
        p.drawText(QRectF(0, cy + 24, W, 14), Qt.AlignmentFlag.AlignCenter,
                   "Images · Video · Audio · PDF · Docs · Code · Data")

    def _paint_drag_over(self, p, W, H):
        cx, cy = W / 2, H / 2
        p.setFont(QFont("Courier New", 20))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy - 24, W, 32), Qt.AlignmentFlag.AlignCenter, "⬇")
        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy + 12, W, 16), Qt.AlignmentFlag.AlignCenter, "Release to load")

    def _paint_file(self, p, W, H):
        path = Path(self._z._current_file)
        cat  = _file_category(path)
        icon, icon_col = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size_str = _fmt_size(path.stat().st_size)
        ext_str  = path.suffix.upper().lstrip(".") or "FILE"

        block_x, block_w = 10, 60
        p.setFont(QFont("Segoe UI Emoji", 22) if _OS == "Windows" else QFont("Arial", 22))
        p.setPen(QPen(qcol(icon_col), 1))
        p.drawText(QRectF(block_x, 0, block_w, H), Qt.AlignmentFlag.AlignCenter, icon)

        tx = block_x + block_w + 6
        tw = W - tx - 38

        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.WHITE), 1))
        name = path.name if len(path.name) <= 34 else path.name[:31] + "..."
        p.drawText(QRectF(tx, H * 0.18, tw, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)

        p.setFont(QFont("Courier New", 7))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(tx, H * 0.18 + 18, tw, 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"{ext_str}  ·  {size_str}")

        p.setFont(QFont("Courier New", 6))
        p.setPen(QPen(qcol("#1e5c6a"), 1))
        par = str(path.parent)
        if len(par) > 42: par = "…" + par[-41:]
        p.drawText(QRectF(tx, H * 0.18 + 34, tw, 12),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, par)

        p.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.RED, 180), 1))
        p.drawText(QRectF(W - 34, 0, 28, H), Qt.AlignmentFlag.AlignCenter, "✕")

    def mousePressEvent(self, e):
        z = self._z
        if z._current_file and e.pos().x() > self.width() - 34:
            z.clear_file()
        else:
            z.mousePressEvent(e)


class _CameraPreview(QWidget):
    """Floating overlay that briefly shows what the camera captured."""

    _W, _H = 244, 188

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            _CameraPreview {{
                background: rgba(0, 6, 10, 242);
                border: 1px solid {C.PRI};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._W)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 5, 6, 6)
        lay.setSpacing(4)

        hdr = QHBoxLayout()
        title = QLabel("◈  VISUAL INPUT")
        title.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(title)
        hdr.addStretch()
        close_btn = JANEButton("✕")
        close_btn.setFixedSize(16, 16)
        close_btn.setFont(QFont("Courier New", 8))
        close_btn.setStyleSheet(
            f"color: {C.TEXT_DIM}; background: transparent; border: none;"
        )
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.hide)
        hdr.addWidget(close_btn)
        lay.addLayout(hdr)

        self._img_lbl = QLabel()
        self._img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_lbl.setStyleSheet("background: transparent;")
        lay.addWidget(self._img_lbl)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

        self.hide()

    def show_frame(self, img_bytes: bytes) -> None:
        px = QPixmap()
        px.loadFromData(img_bytes)
        if not px.isNull():
            max_w = self._W - 12
            scaled = px.scaled(
                max_w, 160,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._img_lbl.setPixmap(scaled)
            self._img_lbl.setFixedSize(scaled.width(), scaled.height())
            self.adjustSize()
        self.show()
        self.raise_()
        self._timer.start(6_000)   # auto-dismiss after 6 s


class SetupOverlay(QWidget):
    done = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            SetupOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)

        detected = {"darwin": "mac", "windows": "windows"}.get(
            _OS.lower(), "linux"
        )
        self._sel_os = detected

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 22, 30, 22)
        layout.setSpacing(8)

        def _lbl(txt, font_size=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont("Courier New", font_size,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        layout.addWidget(_lbl("◈  INITIALISATION REQUIRED", 13, True))
        layout.addWidget(_lbl("Configure the assistant before first boot.", 9, color=C.PRI_DIM))
        layout.addSpacing(6)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep)
        layout.addSpacing(4)

        layout.addWidget(_lbl("GEMINI API KEY", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        self._key_input = QLineEdit()
        self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_input.setPlaceholderText("AIza…")
        self._key_input.setFont(QFont("Courier New", 10))
        self._key_input.setFixedHeight(32)
        self._key_input.setStyleSheet(f"""
            QLineEdit {{
                background: #000d12; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        layout.addWidget(self._key_input)
        layout.addSpacing(12)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep2)
        layout.addSpacing(4)

        layout.addWidget(_lbl("OPERATING SYSTEM", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        det_name = {"windows": "Windows", "mac": "macOS", "linux": "Linux"}[detected]
        layout.addWidget(_lbl(f"Auto-detected: {det_name}", 8, color=C.ACC2,
                               align=Qt.AlignmentFlag.AlignLeft))

        os_row = QHBoxLayout(); os_row.setSpacing(6)
        self._os_btns: dict[str, QPushButton] = {}
        for key, label in [("windows","⊞  Windows"),("mac","  macOS"),("linux","🐧  Linux")]:
            btn = JANEButton(label)
            btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
            btn.setFixedHeight(32)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, k=key: self._sel(k))
            os_row.addWidget(btn)
            self._os_btns[key] = btn
        layout.addLayout(os_row)
        self._sel(detected)
        layout.addSpacing(12)

        init_btn = JANEButton("▸  INITIALISE SYSTEMS")
        init_btn.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
        init_btn.setFixedHeight(36)
        init_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        init_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
            }}
            QPushButton:hover {{
                background: {C.PRI_GHO}; border: 1px solid {C.PRI};
            }}
        """)
        init_btn.clicked.connect(self._submit)
        layout.addWidget(init_btn)

    def _sel(self, key: str):
        self._sel_os = key
        pal = {"windows":(C.PRI,"#001a22"),"mac":(C.ACC2,"#1a1400"),"linux":(C.GREEN,"#001a0d")}
        for k, btn in self._os_btns.items():
            if k == key:
                fg, bg = pal[k]
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {fg}; color: {bg};
                        border: none; border-radius: 3px; font-weight: bold;
                    }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: #000d12; color: {C.TEXT_DIM};
                        border: 1px solid {C.BORDER}; border-radius: 3px;
                    }}
                    QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
                """)

    def _submit(self):
        key = self._key_input.text().strip()
        if not key:
            self._key_input.setStyleSheet(
                self._key_input.styleSheet() +
                f" QLineEdit {{ border: 1px solid {C.RED}; }}"
            )
            return
        self.done.emit(key, self._sel_os)


class HueWheel(QWidget):
    """
    Circular colour picker. The user drags the handle (small white circle)
    around the wheel to choose from ALL hues. The filled circle in the centre
    is a live preview of the selected colour.
    """

    hue_picked    = pyqtSignal(str)   # while dragging (live)
    hue_committed = pyqtSignal(str)   # when the handle is released

    _RING = 16   # ring thickness (px)

    def __init__(self, initial_hex: str = DEFAULT_UI_COLOR, parent=None):
        super().__init__(parent)
        self.setFixedSize(148, 148)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hue  = 0.53
        self._drag = False
        self.set_color(initial_hex)

    # ── API ──────────────────────────────────────────────────────────────────
    def color(self) -> str:
        return QColor.fromHsvF(self._hue, 1.0, 1.0).name()

    def set_color(self, hex_str: str):
        c = QColor((hex_str or "").strip())
        if c.isValid() and c.hsvHueF() >= 0:
            self._hue = c.hsvHueF()
            self.update()

    # ── geometry helpers ─────────────────────────────────────────────────────
    def _ring_rect(self) -> QRectF:
        m = self._RING / 2 + 3
        return QRectF(self.rect()).adjusted(m, m, -m, -m)

    def _hue_from_pos(self, pos: QPointF) -> float:
        c  = QRectF(self.rect()).center()
        dx = pos.x() - c.x()
        dy = c.y() - pos.y()          # screen y goes down — flip to math axis
        ang = math.atan2(dy, dx)      # [-π, π], counter-clockwise
        return (ang / (2 * math.pi)) % 1.0

    # ── drawing ──────────────────────────────────────────────────────────────
    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect   = self._ring_rect()
        center = rect.center()

        grad = QConicalGradient(center, 0)
        for i in range(0, 361, 20):
            grad.setColorAt(i / 360.0, QColor.fromHsvF((i % 360) / 360.0, 1.0, 1.0))
        p.setPen(QPen(QBrush(grad), self._RING))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(rect)

        # centre preview circle
        preview = QColor.fromHsvF(self._hue, 1.0, 1.0)
        inner   = rect.adjusted(30, 30, -30, -30)
        p.setPen(QPen(qcol(C.BORDER_B), 1))
        p.setBrush(QBrush(preview))
        p.drawEllipse(inner)

        # draggable handle
        r   = rect.width() / 2
        ang = self._hue * 2 * math.pi
        hx  = center.x() + r * math.cos(ang)
        hy  = center.y() - r * math.sin(ang)
        p.setPen(QPen(QColor("#00060a"), 2))
        p.setBrush(QBrush(QColor("#ffffff")))
        p.drawEllipse(QPointF(hx, hy), 7.5, 7.5)
        p.end()

    # ── fare ─────────────────────────────────────────────────────────────────
    def mousePressEvent(self, e):
        self._drag = True
        self._hue  = self._hue_from_pos(e.position())
        self.update()
        self.hue_picked.emit(self.color())

    def mouseMoveEvent(self, e):
        if self._drag:
            self._hue = self._hue_from_pos(e.position())
            self.update()
            self.hue_picked.emit(self.color())

    def mouseReleaseEvent(self, e):
        if self._drag:
            self._drag = False
            self.hue_committed.emit(self.color())


class CustomizeOverlay(QWidget):
    """Floating identity/appearance editor."""

    saved = pyqtSignal(str, str, str, str, str)   # name, user, ui_color, voice, tagline
    _OW, _OH = 400, 588

    def __init__(self, assistant_name=DEFAULT_ASSISTANT_NAME, user_name="",
                 ui_color=DEFAULT_UI_COLOR, voice="", tagline=DEFAULT_ASSISTANT_TAGLINE, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            CustomizeOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 18, 24, 18)
        lay.setSpacing(8)

        def _lbl(txt, fs=9, bold=False, color=C.PRI, align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt); w.setAlignment(align)
            w.setFont(QFont("Courier New", fs,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        _fs = (f"QLineEdit {{ background: #000d12; color: {C.TEXT}; "
               f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px; }}"
               f"QLineEdit:focus {{ border: 1px solid {C.PRI}; }}")

        lay.addWidget(_lbl("⚙  CUSTOMISE ASSISTANT", 12, True))
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        lay.addWidget(_lbl("ASSISTANT NAME", 8, color=C.TEXT_DIM,
                            align=Qt.AlignmentFlag.AlignLeft))
        self._name_input = QLineEdit(assistant_name)
        self._name_input.setFont(QFont("Courier New", 10))
        self._name_input.setFixedHeight(32)
        self._name_input.setStyleSheet(_fs)
        lay.addWidget(self._name_input)

        lay.addSpacing(4)
        lay.addWidget(_lbl("ASSISTANT TAGLINE", 8, color=C.TEXT_DIM,
                            align=Qt.AlignmentFlag.AlignLeft))
        self._tagline_input = QLineEdit(tagline or DEFAULT_ASSISTANT_TAGLINE)
        self._tagline_input.setMaxLength(72)
        self._tagline_input.setPlaceholderText(DEFAULT_ASSISTANT_TAGLINE)
        self._tagline_input.setFont(QFont("Courier New", 9))
        self._tagline_input.setFixedHeight(32)
        self._tagline_input.setStyleSheet(_fs)
        lay.addWidget(self._tagline_input)

        lay.addSpacing(4)
        lay.addWidget(_lbl("YOUR NAME  (optional)", 8,
                            color=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft))
        self._user_input = QLineEdit(user_name)
        self._user_input.setPlaceholderText("e.g.  Tony   (leave blank for auto)")
        self._user_input.setFont(QFont("Courier New", 10))
        self._user_input.setFixedHeight(32)
        self._user_input.setStyleSheet(_fs)
        lay.addWidget(self._user_input)

        # ── Assistant voice — Gemini prebuilt voices ─────────────────────────
        # Names are language-neutral proper nouns, so the row reads the same in
        # every locale. Selecting one and applying rebuilds the Live session.
        from memory.config_manager import AVAILABLE_VOICES, DEFAULT_VOICE
        lay.addSpacing(4)
        lay.addWidget(_lbl("ASSISTANT VOICE", 8, color=C.TEXT_DIM,
                            align=Qt.AlignmentFlag.AlignLeft))
        self._sel_voice   = (voice or DEFAULT_VOICE)
        if self._sel_voice not in AVAILABLE_VOICES:
            self._sel_voice = DEFAULT_VOICE
        self._voice_btns: dict[str, QPushButton] = {}
        voice_row = QHBoxLayout(); voice_row.setSpacing(4)
        for _v in AVAILABLE_VOICES:
            b = JANEButton(_v)
            b.setCheckable(True)
            b.setFixedHeight(28)
            b.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, name=_v: self._on_voice_pick(name))
            self._voice_btns[_v] = b
            voice_row.addWidget(b)
        lay.addLayout(voice_row)
        self._refresh_voice_btns()

        # ── UI colour — colour wheel ─────────────────────────────────────────
        lay.addSpacing(4)
        clr_hdr = QHBoxLayout()
        clr_hdr.addWidget(_lbl("UI COLOUR  —  drag the handle", 8,
                               color=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft))
        clr_hdr.addStretch()
        df_btn = JANEButton("DEFAULT")
        df_btn.setFixedSize(64, 20)
        df_btn.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        df_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        df_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
        """)
        df_btn.clicked.connect(lambda: self._set_color(DEFAULT_UI_COLOR))
        clr_hdr.addWidget(df_btn)
        lay.addLayout(clr_hdr)

        self._initial_color = (ui_color or DEFAULT_UI_COLOR).strip().lower()
        self._sel_color     = self._initial_color
        self.on_preview     = None   # callable(hex) — live preview; MainWindow wires it

        self._wheel = HueWheel(self._sel_color)
        wheel_row = QHBoxLayout()
        wheel_row.addStretch(); wheel_row.addWidget(self._wheel); wheel_row.addStretch()
        lay.addLayout(wheel_row)
        self._wheel.hue_picked.connect(self._on_wheel_pick)
        self._wheel.hue_committed.connect(self._on_wheel_commit)

        self._hex_input = QLineEdit(self._sel_color)
        self._hex_input.setPlaceholderText("#00d4ff   (custom hex colour)")
        self._hex_input.setFont(QFont("Courier New", 10))
        self._hex_input.setFixedHeight(28)
        self._hex_input.setStyleSheet(_fs)
        self._hex_input.textEdited.connect(self._on_hex_edited)
        lay.addWidget(self._hex_input)

        lay.addSpacing(6)
        btn_row = QHBoxLayout(); btn_row.setSpacing(8)

        reset_btn = JANEButton("RESET DEFAULTS")
        reset_btn.setFixedHeight(34)
        reset_btn.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        reset_btn.setStyleSheet(f"""
            QPushButton {{ background:transparent; color:{C.TEXT_DIM}; border:1px solid {C.BORDER}; border-radius:3px; }}
            QPushButton:hover {{ color:{C.ACC2}; border:1px solid {C.BORDER_B}; background:{C.PANEL2}; }}
        """)
        reset_btn.clicked.connect(self._reset_defaults)
        btn_row.addWidget(reset_btn)

        save_btn = JANEButton("▸  APPLY CHANGES")
        save_btn.setFixedHeight(34)
        save_btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)

        cancel_btn = JANEButton("CANCEL")
        cancel_btn.setFixedHeight(34)
        cancel_btn.setFont(QFont("Courier New", 9))
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
        """)
        cancel_btn.clicked.connect(self._cancel)
        btn_row.addWidget(cancel_btn)
        lay.addLayout(btn_row)

    def _reset_defaults(self):
        """Restore the built-in identity/appearance without closing the editor."""
        self._name_input.setText(DEFAULT_ASSISTANT_NAME)
        self._tagline_input.setText(DEFAULT_ASSISTANT_TAGLINE)
        self._user_input.clear()
        self._sel_color = DEFAULT_UI_COLOR
        self._set_color(DEFAULT_UI_COLOR)
        try:
            from memory.config_manager import DEFAULT_VOICE
            self._sel_voice = DEFAULT_VOICE
            self._refresh_voice_btns()
        except Exception:
            pass

    # ── voice selection ──────────────────────────────────────────────────────
    def _on_voice_pick(self, name: str):
        self._sel_voice = name
        self._refresh_voice_btns()

    def _refresh_voice_btns(self):
        """Highlight the selected voice pill; dim the rest."""
        for name, b in self._voice_btns.items():
            on = (name == self._sel_voice)
            b.setChecked(on)
            if on:
                b.setStyleSheet(f"""
                    QPushButton {{ background: {C.PRI_GHO}; color: {C.PRI};
                        border: 1px solid {C.PRI}; border-radius: 3px; }}
                """)
            else:
                b.setStyleSheet(f"""
                    QPushButton {{ background: transparent; color: {C.TEXT_MED};
                        border: 1px solid {C.BORDER}; border-radius: 3px; }}
                    QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
                """)

    # ── colour flow ──────────────────────────────────────────────────────────
    def _set_color(self, hx: str, update_wheel: bool = True, preview: bool = True):
        """Updates the selected colour; hex box + wheel stay in sync, theme is live-previewed."""
        self._sel_color = hx.strip().lower()
        self._hex_input.blockSignals(True)
        self._hex_input.setText(self._sel_color)
        self._hex_input.blockSignals(False)
        if update_wheel:
            self._wheel.set_color(self._sel_color)
        if preview and self.on_preview:
            self.on_preview(self._sel_color)

    def _on_wheel_pick(self, hx: str):
        # While dragging: update the hex box, don't apply the theme yet
        self._sel_color = hx
        self._hex_input.blockSignals(True)
        self._hex_input.setText(hx)
        self._hex_input.blockSignals(False)

    def _on_wheel_commit(self, hx: str):
        # Handle released → live-preview the whole interface
        self._set_color(hx, update_wheel=False)

    def _on_hex_edited(self, text: str):
        t = text.strip().lower()
        if t.startswith("#") and len(t) == 7:
            try:
                int(t[1:], 16)
            except ValueError:
                return
            self._set_color(t, update_wheel=True, preview=True)

    def _cancel(self):
        # If a preview was applied, revert to the colour from launch
        if self.on_preview and self._sel_color != self._initial_color:
            self.on_preview(self._initial_color)
        self.hide()

    def _save(self):
        name = self._name_input.text().strip() or DEFAULT_ASSISTANT_NAME
        tagline = self._tagline_input.text().strip() or DEFAULT_ASSISTANT_TAGLINE
        user = self._user_input.text().strip()
        self.saved.emit(name, user, self._sel_color or DEFAULT_UI_COLOR, self._sel_voice, tagline)
        self.hide()


class PluginManagerOverlay(QWidget):
    """Floating overlay — lists discovered plugins with per-plugin ON/OFF toggles."""

    _OW = 420

    def __init__(self, plugins: list[dict], parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            PluginManagerOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(6)

        hdr = QLabel("🧩  PLUGIN MANAGER")
        hdr.setFont(QFont("Courier New", 12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        lay.addWidget(hdr)
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        if not plugins:
            empty = QLabel("No plugins found in /plugins.")
            empty.setFont(QFont("Courier New", 8))
            empty.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            lay.addWidget(empty)

        for p in plugins:
            lay.addLayout(self._build_row(p))

        lay.addSpacing(4)
        close_btn = JANEButton("CLOSE")
        close_btn.setFixedHeight(30)
        close_btn.setFont(QFont("Courier New", 9))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self.hide)
        lay.addWidget(close_btn)
        self.adjustSize()

    def _build_row(self, p: dict) -> QHBoxLayout:
        row = QHBoxLayout(); row.setSpacing(6)

        label_text = p["name"] if p["valid"] else f"{p['name']}  (⚠ {p['file']})"
        lbl = QLabel(label_text)
        lbl.setFont(QFont("Courier New", 8))
        lbl.setStyleSheet(f"color: {C.TEXT if p['valid'] else C.TEXT_DIM}; background: transparent;")
        lbl.setToolTip(p["description"] if p["valid"] else p["error"])
        lbl.setWordWrap(False)
        row.addWidget(lbl, stretch=1)

        btn = JANEButton()
        btn.setFixedSize(72, 24)
        btn.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        if not p["valid"]:
            btn.setText("BROKEN")
            btn.setEnabled(False)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                }}
            """)
        else:
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._style_toggle(btn, p["enabled"])
            btn.clicked.connect(lambda _, name=p["name"], b=btn: self._toggle(name, b))
        row.addWidget(btn)
        return row

    def _style_toggle(self, btn: QPushButton, enabled: bool):
        if enabled:
            btn.setText("ON")
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: #001a08; color: {C.GREEN};
                    border: 1px solid {C.GREEN_D}; border-radius: 3px;
                }}
                QPushButton:hover {{ background: #002010; }}
            """)
        else:
            btn.setText("OFF")
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
            """)

    def _toggle(self, name: str, btn: QPushButton):
        from memory.config_manager import get_plugin_enabled, save_plugin_enabled
        new_val = not get_plugin_enabled(name)
        save_plugin_enabled(name, new_val)
        self._style_toggle(btn, new_val)


class _HudOverlay(QWidget):
    """Base for the floating panels placed by hand over the HUD.

    They are children of the central widget but sit in no layout, so Qt never
    invalidates the region they occupy when they hide or shrink: the HUD keeps
    painting around them and their last frame stays on screen as a ghost. Any
    overlay positioned with _centre_overlay needs this."""

    def hideEvent(self, e):
        p = self.parentWidget()
        if p is not None:
            # Repaint exactly what we were covering, before we stop covering it.
            p.update(self.geometry())
        super().hideEvent(e)

    def closeEvent(self, e):
        p = self.parentWidget()
        if p is not None:
            p.update(self.geometry())
        super().closeEvent(e)


class ConfirmBanner(_HudOverlay):
    """The gate in front of an action that cannot be taken back.

    The old confirmation was a tool parameter the model filled in itself, which
    means it confirmed its own shutdown requests. This is the interface asking,
    and the answer travels from a human finger to core/confirm.py without the
    model in the loop. Nothing blocks while it is up: the assistant keeps
    talking, so this costs no latency — unlike the old gate, which spent two
    tool round trips on every power command."""

    answered = pyqtSignal(bool)
    _OW = 430

    def __init__(self, title: str, detail: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            ConfirmBanner {{
                background: rgba(14, 3, 0, 250);
                border: 1px solid {C.ACC};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(8)

        hdr = QLabel("⚠  CONFIRM")
        hdr.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.ACC}; background: transparent;")
        lay.addWidget(hdr)

        ttl = QLabel(title)
        ttl.setWordWrap(True)
        ttl.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
        ttl.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        lay.addWidget(ttl)

        if detail:
            dtl = QLabel(detail)
            dtl.setWordWrap(True)
            dtl.setFont(QFont("Courier New", 8))
            dtl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            lay.addWidget(dtl)

        row = QHBoxLayout(); row.setSpacing(8)

        yes = JANEButton("▸  CONFIRM")
        yes.setFixedHeight(32)
        yes.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        yes.setCursor(Qt.CursorShape.PointingHandCursor)
        yes.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.ACC};
                border: 1px solid {C.ACC}; border-radius: 3px; }}
            QPushButton:hover {{ background: rgba(255,107,0,40); }}
        """)
        yes.clicked.connect(lambda: self.answered.emit(True))
        row.addWidget(yes)

        no = JANEButton("CANCEL")
        no.setFixedHeight(32)
        no.setFont(QFont("Courier New", 9))
        no.setCursor(Qt.CursorShape.PointingHandCursor)
        no.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
        """)
        no.clicked.connect(lambda: self.answered.emit(False))
        row.addWidget(no)
        lay.addLayout(row)

        # Default focus on CANCEL: if someone hits Enter without reading, the
        # safe answer wins.
        no.setDefault(True)
        no.setFocus()


class AudioDeviceOverlay(_HudOverlay):
    """Choose which microphone JANE listens to and which speakers it uses.

    Both audio streams used to open with no `device=` at all, so they always
    took the OS default — which on Windows moves by itself the moment a headset
    is plugged in. 'JANE can't hear me' is usually 'JANE is listening to the
    webcam'."""

    picked = pyqtSignal()      # emitted after Apply, when something changed
    _OW = 460

    def __init__(self, parent=None):
        super().__init__(parent)
        from core.audio_devices import list_devices, DEFAULT_LABEL
        from memory.config_manager import get_input_device, get_output_device

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            AudioDeviceOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(6)

        hdr = QLabel("🎧  AUDIO DEVICES")
        hdr.setFont(QFont("Courier New", 12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        lay.addWidget(hdr)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        _combo_css = (
            f"QComboBox {{ background: #000d12; color: {C.TEXT}; "
            f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px; }}"
            f"QComboBox:hover {{ border: 1px solid {C.BORDER_B}; }}"
            f"QComboBox QAbstractItemView {{ background: #000d12; color: {C.TEXT}; "
            f"selection-background-color: {C.PRI_GHO}; border: 1px solid {C.BORDER}; }}"
        )

        def _row(label: str, kind: str, current: str) -> QComboBox:
            cap = QLabel(label)
            cap.setFont(QFont("Courier New", 8))
            cap.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            lay.addWidget(cap)

            box = QComboBox()
            box.setFont(QFont("Courier New", 9))
            box.setFixedHeight(30)
            box.setStyleSheet(_combo_css)
            # The list is served from a cache warmed on a background thread at
            # startup, so opening this panel never blocks the Qt thread on the
            # host audio API.
            box.addItem(DEFAULT_LABEL, "")
            for name in list_devices(kind):
                box.addItem(name, name)
            idx = box.findData(current) if current else 0
            box.setCurrentIndex(idx if idx >= 0 else 0)
            if current and idx < 0:
                # Saved device is not plugged in right now. Show it rather than
                # silently resetting the user's choice to default.
                box.addItem(f"{current}  (not connected)", current)
                box.setCurrentIndex(box.count() - 1)
            lay.addWidget(box)
            return box

        self._in_box  = _row("MICROPHONE — what the assistant hears",
                             "input", get_input_device())
        lay.addSpacing(4)
        self._out_box = _row("SPEAKERS — what the assistant talks through",
                             "output", get_output_device())

        note = QLabel("Applying reconnects the session. Your conversation is kept.")
        note.setWordWrap(True)
        note.setFont(QFont("Courier New", 7))
        note.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        lay.addSpacing(6)
        lay.addWidget(note)

        row = QHBoxLayout(); row.setSpacing(8)
        ok = JANEButton("▸  APPLY")
        ok.setFixedHeight(32)
        ok.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        ok.setCursor(Qt.CursorShape.PointingHandCursor)
        ok.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px; }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        ok.clicked.connect(self._apply)
        row.addWidget(ok)

        cancel = JANEButton("CLOSE")
        cancel.setFixedHeight(32)
        cancel.setFont(QFont("Courier New", 9))
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
        """)
        cancel.clicked.connect(self.hide)
        row.addWidget(cancel)
        lay.addLayout(row)

    def _apply(self):
        from memory.config_manager import (
            get_input_device, get_output_device,
            save_input_device, save_output_device,
        )
        new_in  = self._in_box.currentData()  or ""
        new_out = self._out_box.currentData() or ""
        changed = (new_in != get_input_device()) or (new_out != get_output_device())
        save_input_device(new_in)
        save_output_device(new_out)
        self.hide()
        # Only rebuild the session if something actually moved — a no-op Apply
        # should not cost a reconnect.
        if changed:
            self.picked.emit()


class MemoryOverlay(_HudOverlay):
    """Everything JANE has stored about you, and when it learned it.

    Memory used to be a 2200-character store that deleted its oldest entries
    when full and mentioned it only on stdout. The cap is gone; this panel is
    the other half of that change — a memory you cannot inspect is a memory you
    cannot trust, and 'delete' has to be something the person can do."""

    _OW = 520

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            MemoryOverlay {{
                background: rgba(0, 6, 10, 246);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)

        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(20, 16, 20, 16)
        self._lay.setSpacing(5)
        self._rebuild()

    def _clear_layout(self):
        """Take every item out of the layout and detach it from the widget tree
        in this call.

        deleteLater() on its own is not enough: it queues destruction for the
        next event-loop pass, and until then the old rows are still children of
        this widget and still paint — which is what drew half of the previous
        panel over the new one. setParent(None) removes them from the tree now;
        deleteLater() then frees them safely."""
        while self._lay.count():
            item = self._lay.takeAt(0)
            w = item.widget()
            if w is not None:
                # hide() stops it painting in this frame; deleteLater() frees it
                # safely afterwards. setParent(None) would also stop the paint,
                # but it turns the widget into a top-level window for the moment
                # between the two calls, which is not something to leave lying
                # around inside a click handler.
                w.hide()
                w.deleteLater()
                continue
            sub = item.layout()
            if sub is not None:
                while sub.count():
                    si = sub.takeAt(0)
                    sw = si.widget()
                    if sw is not None:
                        sw.hide()
                        sw.deleteLater()
                sub.deleteLater()

    def _settle(self, before):
        """Size the panel to its content, re-centre it, and repaint what the old
        size covered.

        The re-size has to happen here rather than at the end of _rebuild
        because Qt has not polished the freshly-created children at that point,
        so the size hint it would read is the empty-layout one. Measured: a
        first adjustSize() returned 32 px for a panel whose content needed 155,
        and a second call — after the same widgets had been through the event
        loop — returned 155. So this runs twice: once now, once on the next
        turn, from _rebuild.

        The re-centre and the repaint are needed because the overlay is placed
        by hand and is in no layout: shrinking it leaves it off-centre and
        leaves its former pixels on screen, since nothing tells the parent that
        region changed. The repaint has to cover the union of the old and new
        rectangles."""
        self._lay.invalidate()
        self._lay.activate()
        self.updateGeometry()
        self.adjustSize()

        p = self.parentWidget()
        if p is None:
            self.update()
            return
        self.move(max(0, (p.width()  - self.width())  // 2),
                  max(0, (p.height() - self.height()) // 2))
        p.update(before.united(self.geometry()))
        self.update()

    def _rebuild(self):
        before = self.geometry()
        self._clear_layout()

        from memory.memory_manager import all_entries_for_ui

        hdr = QLabel("🧠  WHAT THE ASSISTANT REMEMBERS")
        hdr.setFont(QFont("Courier New", 12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        self._lay.addWidget(hdr)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        self._lay.addWidget(sep)

        rows = all_entries_for_ui()

        cap = QLabel(f"{len(rows)} stored facts — newest first. "
                     f"Nothing here is sent anywhere; it lives in "
                     f"memory/long_term.json on this machine.")
        cap.setWordWrap(True)
        cap.setFont(QFont("Courier New", 7))
        cap.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        self._lay.addWidget(cap)

        if not rows:
            empty = QLabel("Nothing stored yet.")
            empty.setFont(QFont("Courier New", 9))
            empty.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            self._lay.addWidget(empty)
        else:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFixedHeight(min(420, 34 * len(rows) + 10))
            scroll.setStyleSheet(
                f"QScrollArea {{ border: 1px solid {C.BORDER}; border-radius: 3px; "
                f"background: transparent; }}"
            )
            inner = QWidget()
            ilay  = QVBoxLayout(inner)
            ilay.setContentsMargins(6, 6, 6, 6)
            ilay.setSpacing(3)

            for r in rows:
                line = QHBoxLayout(); line.setSpacing(6)
                txt = QLabel(f"<b>{r['key'].replace('_', ' ')}</b> "
                             f"<span style='color:{C.TEXT_MED}'>— {r['value']}</span>")
                txt.setWordWrap(True)
                txt.setFont(QFont("Courier New", 8))
                txt.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
                line.addWidget(txt, 1)

                meta = QLabel(f"{r['category'][:4]} · {r['updated'] or '—'}")
                meta.setFont(QFont("Courier New", 7))
                meta.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
                line.addWidget(meta)

                rm = JANEButton("✕")
                rm.setFixedSize(20, 20)
                rm.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
                rm.setCursor(Qt.CursorShape.PointingHandCursor)
                rm.setToolTip("Forget this")
                rm.setStyleSheet(f"""
                    QPushButton {{ background: transparent; color: {C.TEXT_DIM};
                        border: 1px solid {C.BORDER}; border-radius: 3px; }}
                    QPushButton:hover {{ color: {C.RED}; border: 1px solid {C.RED}; }}
                """)
                rm.clicked.connect(
                    lambda _=False, c=r["category"], k=r["key"]: self._forget(c, k))
                line.addWidget(rm)

                holder = QWidget()
                holder.setLayout(line)
                ilay.addWidget(holder)

            ilay.addStretch()
            scroll.setWidget(inner)
            self._lay.addWidget(scroll)

        close = JANEButton("CLOSE")
        close.setFixedHeight(30)
        close.setFont(QFont("Courier New", 9))
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
        """)
        close.clicked.connect(self.hide)
        self._lay.addWidget(close)

        self._settle(before)
        # …and again once Qt has polished the new children, because the size
        # hint is not final until then. Harmless when the first pass already
        # got it right: _settle is idempotent.
        QTimer.singleShot(0, lambda g=before: self._settle(g))

    def _forget(self, category: str, key: str):
        from memory.memory_manager import forget
        forget(key, category)
        # Rebuild on the NEXT event-loop turn, not inside this click handler.
        # The rebuild destroys the very ✕ button that emitted this signal, and
        # Qt is entitled to touch the sender after a slot returns; tearing it
        # down mid-emission is how a widget ends up half-alive on screen.
        QTimer.singleShot(0, self._rebuild)


class ClipboardPanel(QWidget):
    """Floating panel shown when text is copied — offers quick Jarvis actions."""

    action_requested = pyqtSignal(str)
    _W, _H = 326, 112

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            ClipboardPanel {{
                background: rgba(0, 8, 14, 248);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._W)
        self._clip_text = ""

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 7)
        lay.setSpacing(4)

        hdr = QHBoxLayout(); hdr.setSpacing(4)
        icon_lbl = QLabel("◈  CLIPBOARD DETECTED")
        icon_lbl.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        icon_lbl.setStyleSheet(f"color: {C.ACC2}; background: transparent;")
        hdr.addWidget(icon_lbl); hdr.addStretch()
        x_btn = JANEButton("✕")
        x_btn.setFixedSize(16, 16)
        x_btn.setFont(QFont("Courier New", 8))
        x_btn.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")
        x_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        x_btn.clicked.connect(self.hide)
        hdr.addWidget(x_btn)
        lay.addLayout(hdr)

        self._preview = QLabel()
        self._preview.setFont(QFont("Courier New", 8))
        self._preview.setStyleSheet(f"""
            color: {C.TEXT}; background: {C.PANEL2};
            border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 6px;
        """)
        self._preview.setWordWrap(False)
        self._preview.setFixedHeight(28)
        lay.addWidget(self._preview)

        btn_row = QHBoxLayout(); btn_row.setSpacing(4)
        _bs = (f"QPushButton {{ background: {C.PANEL2}; color: {C.TEXT_MED}; "
               f"border: 1px solid {C.BORDER}; border-radius: 2px; }}"
               f"QPushButton:hover {{ color: {C.PRI}; border: 1px solid {C.BORDER_B}; }}")
        for label, cmd_fmt in [
            ("TRANSLATE", "Translate this text to English: {text}"),
            ("SUMMARISE", "Summarise this: {text}"),
            ("EXPLAIN",   "Explain this: {text}"),
            ("FIX",       "Fix grammar and spelling: {text}"),
        ]:
            b = JANEButton(label)
            b.setFixedHeight(22)
            b.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(_bs)
            b.clicked.connect(lambda _, c=cmd_fmt: self._trigger(c))
            btn_row.addWidget(b)
        lay.addLayout(btn_row)

        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.timeout.connect(self.hide)
        self.hide()

    def _trigger(self, cmd_fmt: str):
        if self._clip_text:
            self.action_requested.emit(cmd_fmt.format(text=self._clip_text[:800]))
        self.hide()

    def show_clipboard(self, text: str):
        self._clip_text = text
        preview = text[:58].replace('\n', ' ')
        if len(text) > 58:
            preview += "…"
        self._preview.setText(f'"{preview}"')
        self.show(); self.raise_()
        self._dismiss_timer.start(8000)


class PluginSettingsOverlay(QWidget):
    """Floating overlay — renders per-plugin settings forms.

    Fully generic: it iterates the settings schemas a plugin declared via its
    PLUGIN_SETTINGS constant (delivered by PluginRegistry.settings_schemas) and
    builds a form for each. It knows NOTHING about any specific plugin, so the
    core stays clean and plugins remain pure drop-in — install a plugin that
    declares fields (e.g. the 3D-printer suite) and its section appears here;
    install none and this panel simply says there's nothing to configure.
    """

    _test_done = pyqtSignal(str, bool, str)   # namespace, ok, message
    _OW = 460

    def __init__(self, sections: list[dict], parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            PluginSettingsOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self._sections = sections or []
        self._widgets: dict[tuple, object] = {}    # (namespace, key) -> input widget
        self._types:   dict[tuple, str]    = {}     # (namespace, key) -> field type
        self._status_labels: dict[str, QLabel] = {} # namespace -> status QLabel
        self._test_done.connect(self._on_test_done)

        self._fs = (f"QLineEdit {{ background: #000d12; color: {C.TEXT}; "
                    f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px; }}"
                    f"QLineEdit:focus {{ border: 1px solid {C.PRI}; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 16, 22, 16)
        root.setSpacing(8)

        root.addWidget(self._lbl("⚙  PLUGIN SETTINGS", 12, True))
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        root.addWidget(sep)

        if not self._sections:
            root.addWidget(self._lbl(
                "No configurable plugins are installed.\nDrop a plugin that needs "
                "settings (like the 3D-printer suite) into the plugins folder and "
                "it will show up here.", 9, color=C.TEXT_DIM))
        else:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setStyleSheet("QScrollArea { background: transparent; }")
            inner = QWidget()
            inner.setStyleSheet("background: transparent;")
            form = QVBoxLayout(inner)
            form.setContentsMargins(0, 0, 6, 0)
            form.setSpacing(6)
            for sec in self._sections:
                self._build_section(form, sec)
            form.addStretch(1)
            scroll.setWidget(inner)
            root.addWidget(scroll, 1)

        # ── bottom buttons ───────────────────────────────────────────────────
        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        if self._sections:
            save_btn = JANEButton("▸  SAVE")
            save_btn.setFixedHeight(34)
            save_btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
            save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            save_btn.setStyleSheet(f"""
                QPushButton {{ background: transparent; color: {C.PRI};
                    border: 1px solid {C.PRI_DIM}; border-radius: 3px; }}
                QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
            """)
            save_btn.clicked.connect(self._save_all)
            btn_row.addWidget(save_btn)

        close_btn = JANEButton("CLOSE")
        close_btn.setFixedHeight(34)
        close_btn.setFont(QFont("Courier New", 9))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self.hide)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    # ── helpers ───────────────────────────────────────────────────────────────
    def _lbl(self, txt, fs=9, bold=False, color=C.PRI,
             align=Qt.AlignmentFlag.AlignLeft):
        w = QLabel(txt); w.setAlignment(align); w.setWordWrap(True)
        w.setFont(QFont("Courier New", fs,
                        QFont.Weight.Bold if bold else QFont.Weight.Normal))
        w.setStyleSheet(f"color: {color}; background: transparent;")
        return w

    def _build_section(self, form: QVBoxLayout, sec: dict):
        ns     = sec.get("namespace") or sec.get("plugin") or "plugin"
        title  = sec.get("title") or ns
        fields = sec.get("fields") or []
        values = sec.get("values") or {}

        form.addSpacing(4)
        form.addWidget(self._lbl(title, 10, True, C.PRI))

        for field in fields:
            if not isinstance(field, dict) or not field.get("key"):
                continue
            key   = field["key"]
            ftype = (field.get("type") or "text").lower()
            label = field.get("label") or key
            default = field.get("default")
            stored  = values.get(key, default)

            form.addWidget(self._lbl(label.upper(), 8, color=C.TEXT_DIM))

            if ftype == "choice":
                w = QComboBox()
                w.addItems([str(o) for o in field.get("options", [])])
                w.setFont(QFont("Courier New", 9))
                w.setFixedHeight(30)
                w.setStyleSheet(
                    f"QComboBox {{ background: #000d12; color: {C.TEXT}; "
                    f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 2px 8px; }}"
                    f"QComboBox QAbstractItemView {{ background: #000d12; color: {C.TEXT}; "
                    f"selection-background-color: {C.PRI_GHO}; }}")
                if stored is not None:
                    w.setCurrentText(str(stored))
            elif ftype == "toggle":
                w = JANEButton()
                w.setCheckable(True)
                w.setChecked(bool(stored))
                w.setFixedHeight(28)
                w.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
                w.setCursor(Qt.CursorShape.PointingHandCursor)
                self._style_toggle(w)
                w.toggled.connect(lambda _=False, b=w: self._style_toggle(b))
            else:  # text / password
                w = QLineEdit("" if stored is None else str(stored))
                w.setFont(QFont("Courier New", 10))
                w.setFixedHeight(30)
                w.setStyleSheet(self._fs)
                if field.get("placeholder"):
                    w.setPlaceholderText(str(field["placeholder"]))
                if ftype == "password":
                    w.setEchoMode(QLineEdit.EchoMode.Password)

            self._widgets[(ns, key)] = w
            self._types[(ns, key)]   = ftype
            form.addWidget(w)

        # optional test/connect action button + status line
        action = sec.get("action")
        if isinstance(action, dict) and callable(action.get("run")):
            form.addSpacing(2)
            ab = JANEButton(str(action.get("label") or "TEST"))
            ab.setFixedHeight(30)
            ab.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            ab.setCursor(Qt.CursorShape.PointingHandCursor)
            ab.setStyleSheet(f"""
                QPushButton {{ background: #00091a; color: {C.PRI};
                    border: 1px solid {C.PRI_DIM}; border-radius: 3px; }}
                QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
            """)
            ab.clicked.connect(lambda _=False, n=ns: self._run_action(n))
            form.addWidget(ab)

        status = self._lbl("", 8, color=C.TEXT_DIM)
        self._status_labels[ns] = status
        form.addWidget(status)

        line = QFrame(); line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet(f"color: {C.BORDER}; margin: 4px 0;")
        form.addWidget(line)

    def _style_toggle(self, btn: QPushButton):
        on = btn.isChecked()
        btn.setText("ON" if on else "OFF")
        if on:
            btn.setStyleSheet(f"QPushButton {{ background: {C.PRI_GHO}; color: {C.PRI}; "
                              f"border: 1px solid {C.PRI}; border-radius: 3px; }}")
        else:
            btn.setStyleSheet(f"QPushButton {{ background: transparent; color: {C.TEXT_MED}; "
                              f"border: 1px solid {C.BORDER}; border-radius: 3px; }}")

    # ── data ──────────────────────────────────────────────────────────────────
    def _gather(self, ns: str) -> dict:
        out = {}
        for (n, key), w in self._widgets.items():
            if n != ns:
                continue
            t = self._types.get((n, key), "text")
            if t == "choice":
                out[key] = w.currentText()
            elif t == "toggle":
                out[key] = w.isChecked()
            else:
                out[key] = w.text().strip()
        return out

    def _save_ns(self, ns: str):
        from memory.config_manager import save_plugin_config
        save_plugin_config(ns, self._gather(ns))

    def _save_all(self):
        for sec in self._sections:
            ns = sec.get("namespace") or sec.get("plugin")
            if ns:
                self._save_ns(ns)
                lbl = self._status_labels.get(ns)
                if lbl:
                    lbl.setText("Saved ✓")
                    lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")

    def _run_action(self, ns: str):
        sec = next((s for s in self._sections
                    if (s.get("namespace") or s.get("plugin")) == ns), None)
        if not sec:
            return
        run_fn = (sec.get("action") or {}).get("run")
        if not callable(run_fn):
            return
        self._save_ns(ns)                 # persist what the user typed before testing
        values = self._gather(ns)
        lbl = self._status_labels.get(ns)
        if lbl:
            lbl.setText("Testing…")
            lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")

        def worker():
            try:
                res = run_fn(values)
                if isinstance(res, tuple) and len(res) == 2:
                    ok, msg = bool(res[0]), str(res[1])
                else:
                    ok, msg = bool(res), str(res)
            except Exception as e:
                ok, msg = False, str(e)
            self._test_done.emit(ns, ok, msg)

        threading.Thread(target=worker, daemon=True).start()

    def _on_test_done(self, ns: str, ok: bool, msg: str):
        lbl = self._status_labels.get(ns)
        if not lbl:
            return
        lbl.setText(msg)
        color = C.PRI if ok else "#ff6b6b"
        lbl.setStyleSheet(f"color: {color}; background: transparent;")


class RemoteKeyOverlay(QWidget):
    """Floating overlay — QR code for instant phone pairing + manual key fallback."""

    closed = pyqtSignal()

    _OW, _OH = 400, 465

    def __init__(self, url: str, key: str, auto_login_url: str = "",
                 manual_url: str = "", expiry_secs: int = 600, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            RemoteKeyOverlay {{
                background: rgba(0, 4, 12, 0.95);
                border: 1px solid {C.BORDER_B};
                border-radius: 14px;
            }}
        """)
        self._expiry          = time.time() + expiry_secs
        self._on_new_key      = None
        self._auto_login_url  = auto_login_url
        self._manual_url      = manual_url or url

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 16, 24, 16)
        lay.setSpacing(5)

        def _lbl(txt, fs=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont("Courier New", fs,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            w.setWordWrap(True)
            return w

        lay.addWidget(_lbl("◈  REMOTE ACCESS", 12, True))
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 1px 0;")
        lay.addWidget(sep)

        # ── QR code ───────────────────────────────────────────────────────────
        self._qr_label = QLabel()
        self._qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_label.setFixedSize(176, 176)
        self._qr_label.setStyleSheet(
            "background: white; border-radius: 10px; padding: 4px;"
        )
        qr_row = QHBoxLayout()
        qr_row.addStretch()
        qr_row.addWidget(self._qr_label)
        qr_row.addStretch()
        lay.addLayout(qr_row)

        self._update_qr(auto_login_url)

        lay.addWidget(_lbl("Scan with phone camera to connect instantly", 8, color=C.TEXT_DIM))

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER}; margin: 1px 0;")
        lay.addWidget(sep2)

        lay.addWidget(_lbl("Or enter manually:", 7, color=C.TEXT_DIM,
                           align=Qt.AlignmentFlag.AlignLeft))

        self._url_lbl = QLabel(self._manual_url)
        self._url_lbl.setFont(QFont("Courier New", 8))
        self._url_lbl.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent;")
        self._url_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._url_lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self._url_lbl)

        self._key_lbl = QLabel(key)
        self._key_lbl.setFont(QFont("Courier New", 28, QFont.Weight.Bold))
        self._key_lbl.setStyleSheet(f"""
            color: {C.ACC};
            background: {C.PANEL2};
            border: 1px solid {C.BORDER_B};
            border-radius: 8px;
            padding: 6px 4px;
            letter-spacing: 10px;
        """)
        self._key_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._key_lbl)

        self._timer_lbl = QLabel()
        self._timer_lbl.setFont(QFont("Courier New", 8))
        self._timer_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._timer_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._timer_lbl)

        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        new_btn = QPushButton("NEW KEY")
        new_btn.setFixedHeight(32)
        new_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 5px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        new_btn.clicked.connect(self._refresh_key)
        btn_row.addWidget(new_btn)

        close_btn = QPushButton("DISMISS")
        close_btn.setFixedHeight(32)
        close_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 5px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self._do_close)
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)

        self._ctimer = QTimer(self)
        self._ctimer.timeout.connect(self._tick)
        self._ctimer.start(1000)
        self._tick()

    def set_new_key_callback(self, fn) -> None:
        self._on_new_key = fn

    def _update_qr(self, url: str) -> None:
        if not url:
            self._qr_label.setText("—")
            return
        try:
            import qrcode as _qrmod
            from io import BytesIO
            qr = _qrmod.QRCode(
                box_size=5, border=2,
                error_correction=_qrmod.constants.ERROR_CORRECT_M,
            )
            qr.add_data(url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = BytesIO()
            img.save(buf, format="PNG")
            px = QPixmap()
            px.loadFromData(buf.getvalue())
            self._qr_label.setPixmap(
                px.scaled(170, 170,
                          Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
            )
        except ImportError:
            self._qr_label.setText("pip install\nqrcode[pil]")
            self._qr_label.setFont(QFont("Courier New", 8))
            self._qr_label.setStyleSheet(
                "color: #888; background: white; border-radius: 10px; padding: 4px;"
            )
        except Exception:
            self._qr_label.setText(url[:28])
            self._qr_label.setFont(QFont("Courier New", 7))
            self._qr_label.setStyleSheet(
                f"color: {C.PRI}; background: white; border-radius: 10px; padding: 4px;"
            )

    def _tick(self):
        remaining = max(0, int(self._expiry - time.time()))
        m, s = divmod(remaining, 60)
        self._timer_lbl.setText(f"Key expires in  {m:02d}:{s:02d}")
        if remaining == 0:
            self._do_close()

    def mark_connected(self) -> None:
        """Call from any thread when a phone successfully connects."""
        self._ctimer.stop()
        self._key_lbl.setText("CONNECTED")
        self._key_lbl.setStyleSheet(f"""
            color: {C.GREEN};
            background: rgba(34,197,94,0.08);
            border: 2px solid rgba(34,197,94,0.4);
            border-radius: 8px;
            padding: 6px 4px;
            letter-spacing: 4px;
        """)
        self._qr_label.setText("✓")
        self._qr_label.setFont(QFont("Courier New", 54, QFont.Weight.Bold))
        self._qr_label.setStyleSheet(
            "color: #00ff88; background: #001a0d; border-radius: 10px;"
        )
        self._timer_lbl.setText("Phone connected — JARVIS ready")
        self._timer_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent;")

    def _refresh_key(self):
        if self._on_new_key:
            result = self._on_new_key()
            if result:
                url    = result[0]
                key    = result[1]
                auto   = result[2] if len(result) >= 3 else ""
                manual = result[3] if len(result) >= 4 else url
                self._manual_url     = manual or url
                self._url_lbl.setText(self._manual_url)
                self._key_lbl.setText(key)
                self._auto_login_url = auto
                self._update_qr(auto or url)
                self._expiry = time.time() + 600
                self._key_lbl.setStyleSheet(f"""
                    color: {C.ACC};
                    background: {C.PANEL2};
                    border: 1px solid {C.BORDER_B};
                    border-radius: 8px;
                    padding: 6px 4px;
                    letter-spacing: 10px;
                """)
                self._timer_lbl.setStyleSheet(
                    f"color: {C.TEXT_MED}; background: transparent;"
                )
                self._ctimer.start(1000)
                self._tick()

    def _do_close(self):
        self._ctimer.stop()
        self.hide()
        self.closed.emit()


class WeatherWidget(QWidget):
    """Optional network weather tile. Disabled by default and fully off when hidden."""
    data_ready = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("WeatherWidget")
        self.setFixedHeight(84)
        self.setStyleSheet(f"""
            QWidget#WeatherWidget {{ background:#03111b; border:1px solid {C.BORDER}; border-radius:4px; }}
        """)
        lay = QVBoxLayout(self); lay.setContentsMargins(8,6,8,6); lay.setSpacing(2)
        hdr = QHBoxLayout(); hdr.setSpacing(4)
        self._title = QLabel("◌  WEATHER")
        self._title.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        self._title.setStyleSheet(f"color:{C.PRI}; background:transparent;")
        hdr.addWidget(self._title); hdr.addStretch()
        self._temp = QLabel("--°")
        self._temp.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        self._temp.setStyleSheet(f"color:{C.WHITE}; background:transparent;")
        hdr.addWidget(self._temp)
        lay.addLayout(hdr)
        self._detail = QLabel("Set a location in UI WORKBENCH")
        self._detail.setWordWrap(True)
        self._detail.setFont(QFont("Courier New", 6))
        self._detail.setStyleSheet(f"color:{C.TEXT_DIM}; background:transparent;")
        lay.addWidget(self._detail)

        self.data_ready.connect(self._apply_data)
        self._location = ""
        self._debounce_tmr = QTimer(self); self._debounce_tmr.setSingleShot(True); self._debounce_tmr.setInterval(650); self._debounce_tmr.timeout.connect(self.refresh)
        self._refresh_tmr = QTimer(self)
        self._refresh_tmr.setInterval(15 * 60 * 1000)
        self._refresh_tmr.timeout.connect(self.refresh)

    def set_location(self, location: str, refresh: bool = True):
        self._location = str(location or "").strip()[:80]
        if refresh:
            self.refresh()
        elif self.isVisible() and self._location:
            self._debounce_tmr.start()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()
        self._refresh_tmr.start()

    def hideEvent(self, event):
        self._refresh_tmr.stop(); self._debounce_tmr.stop()
        super().hideEvent(event)

    def refresh(self):
        if not self.isVisible() or not self._location:
            if not self._location:
                self._temp.setText("--°")
                self._detail.setText("Set a location in UI WORKBENCH")
            return
        loc = self._location
        self._detail.setText(f"Updating {loc}…")
        def _work():
            try:
                from urllib.parse import quote
                from urllib.request import Request, urlopen
                geocode_url = (
                    "https://geocoding-api.open-meteo.com/v1/search?name="
                    + quote(loc) + "&count=1&language=en&format=json"
                )
                req = Request(geocode_url, headers={"User-Agent":"JaneAI/54"})
                with urlopen(req, timeout=5) as r:
                    geo = json.loads(r.read().decode("utf-8"))
                rows = geo.get("results") or []
                if not rows:
                    raise RuntimeError("location not found")
                g = rows[0]
                lat, lon = float(g["latitude"]), float(g["longitude"])
                weather_url = (
                    "https://api.open-meteo.com/v1/forecast?latitude="
                    f"{lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m&timezone=auto"
                )
                req2 = Request(weather_url, headers={"User-Agent":"JaneAI/54"})
                with urlopen(req2, timeout=5) as r:
                    cur = json.loads(r.read().decode("utf-8")).get("current", {})
                code = int(cur.get("weather_code", -1))
                names = {
                    0:"Clear sky", 1:"Mainly clear", 2:"Partly cloudy", 3:"Overcast",
                    45:"Fog",48:"Rime fog",51:"Light drizzle",53:"Drizzle",55:"Heavy drizzle",
                    61:"Light rain",63:"Rain",65:"Heavy rain",71:"Light snow",73:"Snow",75:"Heavy snow",
                    80:"Rain showers",81:"Rain showers",82:"Heavy showers",95:"Thunderstorm",96:"Storm + hail",99:"Storm + hail"
                }
                city = g.get("name") or loc
                text = (f"{city}  ·  {names.get(code, 'Current conditions')}  ·  "
                        f"Feels {float(cur.get('apparent_temperature', cur.get('temperature_2m', 0))):.0f}°  ·  "
                        f"Humidity {float(cur.get('relative_humidity_2m', 0)):.0f}%  ·  Wind {float(cur.get('wind_speed_10m', 0)):.0f} km/h")
                self.data_ready.emit(f"{float(cur.get('temperature_2m', 0)):.0f}°", text)
            except Exception as exc:
                self.data_ready.emit("--°", f"Weather unavailable  ·  {loc}  ·  {exc}")
        threading.Thread(target=_work, daemon=True, name="jane-weather").start()

    def _apply_data(self, temp: str, detail: str):
        self._temp.setText(temp)
        self._detail.setText(detail)


class ClockWidget(QWidget):
    """Local time/date card; no network and negligible CPU usage."""
    def __init__(self, parent=None):
        super().__init__(parent); self.setFixedHeight(84)
        self.setStyleSheet(f"QWidget {{ background:#03111b; border:1px solid {C.BORDER}; border-radius:4px; }}")
        lay=QVBoxLayout(self); lay.setContentsMargins(8,6,8,6); lay.setSpacing(1)
        self._time=QLabel(); self._time.setFont(QFont("Courier New",15,QFont.Weight.Bold)); self._time.setStyleSheet(f"color:{C.WHITE};background:transparent;"); lay.addWidget(self._time)
        self._date=QLabel(); self._date.setFont(QFont("Courier New",7,QFont.Weight.Bold)); self._date.setStyleSheet(f"color:{C.PRI};background:transparent;"); lay.addWidget(self._date)
        self._timer=QTimer(self); self._timer.timeout.connect(self._tick); self._timer.start(1000); self._tick()
    def _tick(self): self._time.setText(time.strftime("%H:%M:%S")); self._date.setText(time.strftime("%A  %d %B %Y").upper())


class BatteryWidget(QWidget):
    """Battery and charging state; safely shows N/A on desktops."""
    def __init__(self, parent=None):
        super().__init__(parent); self.setFixedHeight(84)
        self.setStyleSheet(f"QWidget {{ background:#03111b; border:1px solid {C.BORDER}; border-radius:4px; }}")
        lay=QVBoxLayout(self); lay.setContentsMargins(8,6,8,6); lay.setSpacing(2)
        h=QHBoxLayout(); t=QLabel("◫  POWER"); t.setFont(QFont("Courier New",7,QFont.Weight.Bold)); t.setStyleSheet(f"color:{C.PRI};background:transparent;"); h.addWidget(t); h.addStretch(); self._state=QLabel("DESKTOP"); self._state.setFont(QFont("Courier New",6,QFont.Weight.Bold)); self._state.setStyleSheet(f"color:{C.TEXT_DIM};background:transparent;"); h.addWidget(self._state); lay.addLayout(h)
        self._pct=QLabel("N/A"); self._pct.setFont(QFont("Courier New",12,QFont.Weight.Bold)); self._pct.setStyleSheet(f"color:{C.WHITE};background:transparent;"); lay.addWidget(self._pct)
        self._bar=QProgressBar(); self._bar.setRange(0,100); self._bar.setValue(0); self._bar.setTextVisible(False); self._bar.setFixedHeight(4); self._bar.setStyleSheet(f"QProgressBar {{ background:#081b27;border:0;border-radius:2px; }} QProgressBar::chunk {{ background:{C.PRI};border-radius:2px; }}"); lay.addWidget(self._bar)
        self._timer=QTimer(self); self._timer.timeout.connect(self._tick); self._timer.start(5000); self._tick()
    def _tick(self):
        try:
            b=psutil.sensors_battery()
            if b is None: self._pct.setText("N/A"); self._state.setText("DESKTOP"); self._bar.setValue(0); return
            pct=max(0,min(100,int(round(b.percent)))); self._pct.setText(f"{pct}%"); self._state.setText("CHARGING" if b.power_plugged else "BATTERY"); self._bar.setValue(pct)
        except Exception: self._pct.setText("N/A"); self._state.setText("UNAVAILABLE"); self._bar.setValue(0)


class NetworkWidget(QWidget):
    """Live network throughput using the existing metrics sampler."""
    def __init__(self, parent=None):
        super().__init__(parent); self.setFixedHeight(84)
        self.setStyleSheet(f"QWidget {{ background:#03111b; border:1px solid {C.BORDER}; border-radius:4px; }}")
        lay=QVBoxLayout(self); lay.setContentsMargins(8,6,8,6); lay.setSpacing(2)
        t=QLabel("⌁  NETWORK"); t.setFont(QFont("Courier New",7,QFont.Weight.Bold)); t.setStyleSheet(f"color:{C.PRI};background:transparent;"); lay.addWidget(t)
        self._rate=QLabel("0.0 MB/s"); self._rate.setFont(QFont("Courier New",11,QFont.Weight.Bold)); self._rate.setStyleSheet(f"color:{C.WHITE};background:transparent;"); lay.addWidget(self._rate)
        d=QLabel("LIVE LINK"); d.setFont(QFont("Courier New",6,QFont.Weight.Bold)); d.setStyleSheet(f"color:{C.TEXT_DIM};background:transparent;"); lay.addWidget(d)
        self._timer=QTimer(self); self._timer.timeout.connect(self._tick); self._timer.start(1200); self._tick()
    def _tick(self):
        try: self._rate.setText(f"{float(_metrics.snapshot().get('net',0.0)):.1f} MB/s")
        except Exception: self._rate.setText("--")


class QuickNotesWidget(QWidget):
    """Local scratchpad stored with UI preferences."""
    changed = pyqtSignal(str)
    def __init__(self, text_value="", parent=None):
        super().__init__(parent); self.setFixedHeight(150)
        self.setStyleSheet(f"QWidget {{ background:#03111b; border:1px solid {C.BORDER}; border-radius:4px; }}")
        lay=QVBoxLayout(self); lay.setContentsMargins(8,6,8,6); lay.setSpacing(4)
        t=QLabel("✎  QUICK NOTES"); t.setFont(QFont("Courier New",7,QFont.Weight.Bold)); t.setStyleSheet(f"color:{C.PRI};background:transparent;"); lay.addWidget(t)
        self._edit=QTextEdit(); self._edit.setPlainText(str(text_value or "")[:3000]); self._edit.setPlaceholderText("Type a note…"); self._edit.setFont(QFont("Courier New",7)); self._edit.setStyleSheet(f"QTextEdit {{ background:#020b12;color:{C.TEXT};border:1px solid {C.BORDER};border-radius:3px;padding:4px; }}"); self._edit.textChanged.connect(lambda: self.changed.emit(self._edit.toPlainText()[:3000])); lay.addWidget(self._edit,1)


class UIWorkbenchOverlay(QWidget):
    """Live UI workbench with instant preview and autosave."""
    saved = pyqtSignal(dict)
    changed = pyqtSignal(dict)
    _OW, _OH = 600, 760

    def __init__(self, prefs: dict, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            UIWorkbenchOverlay {{ background:rgba(0,6,10,248); border:1px solid {C.BORDER_B}; border-radius:8px; }}
            QCheckBox {{ color:{C.TEXT_MED}; background:transparent; spacing:7px; }}
            QCheckBox::indicator {{ width:15px; height:15px; border:1px solid {C.BORDER_B}; border-radius:3px; background:#06131f; }}
            QCheckBox::indicator:checked {{ background:{C.PRI}; border:1px solid {C.PRI}; }}
            QComboBox {{ background:#06131f; color:{C.TEXT}; border:1px solid {C.BORDER}; border-radius:4px; padding:5px 8px; }}
            QSlider::groove:horizontal {{ height:4px; background:#102432; border-radius:2px; }}
            QSlider::handle:horizontal {{ width:14px; margin:-5px 0; border-radius:7px; background:{C.PRI}; }}
        """)
        self._prefs = _normalise_ui_preferences(prefs)
        root=QVBoxLayout(self); root.setContentsMargins(16,14,16,14); root.setSpacing(7)
        hdr=QHBoxLayout(); title=QLabel("◈  UI WORKBENCH"); title.setFont(QFont("Courier New",13,QFont.Weight.Bold)); title.setStyleSheet(f"color:{C.WHITE};background:transparent;"); hdr.addWidget(title); hdr.addStretch(); note=QLabel("LIVE PREVIEW  ·  AUTO-SAVE"); note.setFont(QFont("Courier New",6,QFont.Weight.Bold)); note.setStyleSheet(f"color:{C.PRI};background:transparent;"); hdr.addWidget(note); root.addLayout(hdr)
        line=QFrame(); line.setFixedHeight(2); line.setStyleSheet(f"background:{C.PRI};border:none;"); root.addWidget(line)
        scroll=QScrollArea(); scroll.setFrameShape(QFrame.Shape.NoFrame); scroll.setWidgetResizable(True); scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner=QWidget(); inner.setStyleSheet("background:transparent;"); body=QVBoxLayout(inner); body.setContentsMargins(2,2,2,4); body.setSpacing(7); scroll.setWidget(inner); root.addWidget(scroll,1)
        self._checks={}
        def section(txt,hint=""):
            row=QHBoxLayout(); l=QLabel(txt); l.setFont(QFont("Courier New",7,QFont.Weight.Bold)); l.setStyleSheet(f"color:{C.TEXT_MED};background:transparent;"); row.addWidget(l)
            if hint: h=QLabel(hint); h.setFont(QFont("Courier New",6)); h.setStyleSheet(f"color:{C.TEXT_DIM};background:transparent;"); row.addWidget(h)
            row.addStretch(); body.addLayout(row)
        def rule(): x=QFrame(); x.setFixedHeight(1); x.setStyleSheet(f"background:{C.BORDER};border:none;"); body.addWidget(x)
        def chk(key,label,protected=False):
            c=QCheckBox(label); c.setChecked(bool(self._prefs.get(key,True))); c.setFont(QFont("Courier New",7,QFont.Weight.Bold))
            if protected: c.setChecked(True); c.setEnabled(False); c.setToolTip("Always available so you can manually type to JANE and interrupt speech.")
            self._checks[key]=c; body.addWidget(c); return c
        def slider(label_text,low,high,value,key,fmt):
            row=QHBoxLayout(); cap=QLabel(label_text); cap.setFont(QFont("Courier New",7,QFont.Weight.Bold)); cap.setStyleSheet(f"color:{C.TEXT_MED};background:transparent;"); out=QLabel(); out.setFont(QFont("Courier New",7,QFont.Weight.Bold)); out.setStyleSheet(f"color:{C.PRI};background:transparent;"); row.addWidget(cap); row.addStretch(); row.addWidget(out); body.addLayout(row)
            sl=QSlider(Qt.Orientation.Horizontal); sl.setRange(low,high); sl.setValue(int(value)); out.setText(fmt(sl.value())); sl.valueChanged.connect(lambda v: out.setText(fmt(v))); body.addWidget(sl); setattr(self,key,sl); return sl

        section("PRESETS","useful starting layouts")
        self._layout_combo=QComboBox()
        for label,data in (("BALANCED","balanced"),("CENTER FOCUS","center"),("TELEMETRY","telemetry"),("MINIMAL","minimal"),("COMMAND DESK","command"),("UTILITY","utility"),("CLEAN HUD","clean"),("IMMERSIVE","immersive"),("STUDIO","studio"),("ESSENTIALS","essentials")): self._layout_combo.addItem(label,data)
        self._layout_combo.setCurrentIndex(max(0,self._layout_combo.findData(str(self._prefs.get("layout_preset","balanced"))))); body.addWidget(self._layout_combo)
        n=QLabel("Pick a preset, then fine-tune widgets, placement and motion. Changes preview immediately and auto-save."); n.setWordWrap(True); n.setFont(QFont("Courier New",6)); n.setStyleSheet(f"color:{C.TEXT_DIM};background:transparent;"); body.addWidget(n)

        section("LAYOUT","panel sizes + command/widget placement")
        self._command_pos_combo=QComboBox()
        self._command_pos_combo.addItem("COMMAND DECK · BOTTOM", "bottom")
        self._command_pos_combo.addItem("COMMAND DECK · TOP", "top")
        self._command_pos_combo.setCurrentIndex(max(0, self._command_pos_combo.findData(self._prefs.get("command_position","bottom"))))
        body.addWidget(self._command_pos_combo)
        self._widget_pos_combo=QComboBox()
        self._widget_pos_combo.addItem("UTILITY WIDGETS · BOTTOM LEFT", "bottom_left")
        self._widget_pos_combo.addItem("UTILITY WIDGETS · RIGHT PANEL", "right")
        self._widget_pos_combo.setCurrentIndex(max(0, self._widget_pos_combo.findData(self._prefs.get("widget_position","bottom_left"))))
        body.addWidget(self._widget_pos_combo)
        widget_note=QLabel("Bottom-left keeps the right chat/command panel clear; right mode shares that panel with utilities.")
        widget_note.setWordWrap(True); widget_note.setFont(QFont("Courier New",6)); widget_note.setStyleSheet(f"color:{C.TEXT_DIM};background:transparent;"); body.addWidget(widget_note)
        slider("LEFT PANEL WIDTH",220,340,self._prefs.get("left_width",270),"_left_width_slider",lambda v:f"{v} px")
        slider("RIGHT PANEL WIDTH",260,400,self._prefs.get("right_width",352),"_right_width_slider",lambda v:f"{v} px")
        slider("CENTRE VERTICAL",34,54,float(self._prefs.get("center_y",.44))*100,"_center_y_slider",lambda v:f"{v}%")

        section("CENTREPIECE","live Orbital controls")
        slider("CENTRE SCALE",78,118,float(self._prefs.get("center_scale",1))*100,"_scale_slider",lambda v:f"{v}%")
        slider("CENTRE GLOW",40,180,float(self._prefs.get("center_glow",1))*100,"_glow_slider",lambda v:f"{v}%")
        slider("CENTRE MOTION",35,180,float(self._prefs.get("center_motion",1))*100,"_motion_slider",lambda v:f"{v}%")
        slider("PARTICLE DENSITY",40,140,float(self._prefs.get("particle_density",1))*100,"_density_slider",lambda v:f"{v}%")
        slider("PARTICLE FIELD",1,10,float(self._prefs.get("particle_field",1)),"_particle_field_slider",lambda v:f"{int(v)}/10")
        slider("SPEECH REACTIVITY",25,220,float(self._prefs.get("speech_reactivity",1))*100,"_react_slider",lambda v:f"{v}%")
        chk("bar_reactivity","REACTIVE LISTEN / SPEAK BAR  ·  follows live voice")
        chk("low_motion","LOW-MOTION MODE  ·  reduce movement / redraw work")

        rule(); section("CORE WIDGETS","command controls stay available")
        chk("show_command_input","COMMAND DECK  ·  TYPE + SEND + INTERRUPT + MIC",protected=True)
        for key,label in (("show_activity_log","ACTIVITY LOG"),("show_file_upload","FILE UPLOAD / DROP ZONE"),("show_content_panel","CONTENT / BRIEFINGS / SEARCH"),("show_quiz_panel","QUIZ PANEL"),("show_footer","MARK 54 FOOTER")): chk(key,label)
        section("SYSTEM WIDGETS","optional telemetry")
        for key,label in (("show_diagnostics","SYSTEM DIAGNOSTICS / CPU GPU NETWORK TEMP RAM"),("show_io","I/O STATS / UPTIME / PROCESS COUNT"),("show_system_info","SYSTEM INFO / LIVE TELEMETRY"),("show_system_status","SYSTEM STATUS / AUDIO + AI + SESSION"),("show_status_tiles","STATUS TILES / CORE + SECURITY + PROTOCOL")): chk(key,label)
        section("UTILITY WIDGETS","modular useful cards")
        for key,label in (("show_weather","WEATHER / CURRENT CONDITIONS"),("show_clock","CLOCK + DATE"),("show_battery","BATTERY / CHARGING"),("show_network_widget","NETWORK RATE"),("show_quick_notes","QUICK NOTES / SCRATCHPAD")): chk(key,label)
        section("WEATHER","location required")
        self._weather_input=QLineEdit(str(self._prefs.get("weather_location") or "")); self._weather_input.setPlaceholderText("City or town, e.g. Maasbommel"); self._weather_input.setFixedHeight(30); self._weather_input.setFont(QFont("Courier New",8)); self._weather_input.setStyleSheet(f"QLineEdit {{ background:#06131f;color:{C.TEXT};border:1px solid {C.BORDER};border-radius:4px;padding:0 8px; }} QLineEdit:focus {{ border:1px solid {C.PRI}; }}"); body.addWidget(self._weather_input)

        foot=QHBoxLayout(); reset=JANEButton("RESET UI DEFAULTS"); save=JANEButton("SAVE & CLOSE"); close=JANEButton("CLOSE")
        for b in (reset,save,close): b.setFixedHeight(34); b.setFont(QFont("Courier New",7,QFont.Weight.Bold)); b.setCursor(Qt.CursorShape.PointingHandCursor)
        reset.clicked.connect(self._reset); save.clicked.connect(self._save); close.clicked.connect(self.hide); foot.addWidget(reset); foot.addWidget(save,1); foot.addWidget(close); root.addLayout(foot)

        self._live_timer=QTimer(self); self._live_timer.setSingleShot(True); self._live_timer.setInterval(120); self._live_timer.timeout.connect(self._emit_live)
        self._layout_combo.currentIndexChanged.connect(lambda _=0:self._apply_preset_to_checks(self._layout_combo.currentData()))
        controls=[self._layout_combo,self._command_pos_combo,self._widget_pos_combo,self._left_width_slider,self._right_width_slider,self._center_y_slider,self._scale_slider,self._glow_slider,self._motion_slider,self._density_slider,self._particle_field_slider,self._react_slider]
        for w in controls:
            (w.currentIndexChanged if isinstance(w,QComboBox) else w.valueChanged).connect(lambda *_: self._queue_live())
        self._weather_input.textChanged.connect(lambda *_: self._queue_live())
        for key,c in self._checks.items():
            if c.isEnabled(): c.stateChanged.connect(lambda *_: self._queue_live())

    def _queue_live(self):
        if self._live_timer.isActive(): self._live_timer.stop()
        self._live_timer.start()

    def _collect(self):
        p=dict(self._prefs); p["layout_preset"]=str(self._layout_combo.currentData() or "balanced"); p["command_position"]=str(self._command_pos_combo.currentData() or "bottom"); p["widget_position"]=str(self._widget_pos_combo.currentData() or "bottom_left"); p["left_width"]=self._left_width_slider.value(); p["right_width"]=self._right_width_slider.value(); p["center_y"]=self._center_y_slider.value()/100.0; p["center_scale"]=self._scale_slider.value()/100.0; p["center_glow"]=self._glow_slider.value()/100.0; p["center_motion"]=self._motion_slider.value()/100.0; p["particle_density"]=self._density_slider.value()/100.0; p["particle_field"]=self._particle_field_slider.value(); p["speech_reactivity"]=self._react_slider.value()/100.0; p["weather_location"]=self._weather_input.text().strip(); p["quick_notes"]=p.get("quick_notes","")
        for key,c in self._checks.items(): p[key]=c.isChecked()
        p["show_command_input"]=True; return _normalise_ui_preferences(p)

    def _emit_live(self):
        p=self._collect(); self._prefs=dict(p); self.changed.emit(dict(p))

    def _apply_preset_to_checks(self,preset):
        hidden=set(); preset=str(preset or "balanced")
        if preset=="center": hidden.update(("show_diagnostics","show_io","show_system_info","show_system_status","show_status_tiles","show_file_upload"))
        elif preset=="telemetry": hidden.update(("show_activity_log","show_file_upload","show_quiz_panel","show_weather","show_clock","show_battery","show_quick_notes"))
        elif preset=="minimal": hidden.update(("show_diagnostics","show_io","show_system_info","show_system_status","show_status_tiles","show_activity_log","show_file_upload","show_content_panel","show_quiz_panel","show_weather","show_clock","show_battery","show_network_widget","show_quick_notes"))
        elif preset=="command": hidden.update(("show_diagnostics","show_io","show_system_info","show_system_status","show_status_tiles","show_file_upload","show_weather"))
        elif preset=="utility": hidden.update(("show_diagnostics","show_io","show_system_info","show_system_status","show_status_tiles","show_file_upload"))
        elif preset=="clean": hidden.update(("show_diagnostics","show_io","show_system_status","show_status_tiles","show_activity_log","show_file_upload","show_quiz_panel"))
        elif preset=="immersive": hidden.update(("show_diagnostics","show_io","show_status_tiles","show_file_upload","show_activity_log"))
        elif preset=="studio": hidden.update(("show_diagnostics","show_io","show_status_tiles","show_quiz_panel"))
        elif preset=="essentials": hidden.update(("show_diagnostics","show_io","show_file_upload","show_activity_log","show_quiz_panel","show_content_panel","show_weather","show_battery","show_network_widget","show_quick_notes"))
        profiles={
            "balanced": ("bottom","bottom_left",270,352,44,100,100,100,100,1),
            "center": ("bottom","bottom_left",240,310,46,114,115,85,100,2),
            "telemetry": ("bottom","right",320,370,43,94,85,110,120,1),
            "minimal": ("bottom","bottom_left",225,300,44,100,70,55,100,1),
            "command": ("top","bottom_left",240,390,44,98,85,95,115,2),
            "utility": ("bottom","right",250,380,44,102,90,105,105,2),
            "clean": ("bottom","bottom_left",235,330,44,106,80,70,90,1),
            "immersive": ("bottom","bottom_left",230,320,47,118,125,120,130,8),
            "studio": ("top","right",250,360,43,102,90,95,115,4),
            "essentials": ("bottom","bottom_left",235,320,44,105,80,70,95,1),
        }
        prof=profiles.get(preset, profiles["balanced"])
        self._command_pos_combo.blockSignals(True); self._command_pos_combo.setCurrentIndex(max(0,self._command_pos_combo.findData(prof[0]))); self._command_pos_combo.blockSignals(False)
        self._widget_pos_combo.blockSignals(True); self._widget_pos_combo.setCurrentIndex(max(0,self._widget_pos_combo.findData(prof[1]))); self._widget_pos_combo.blockSignals(False)
        for sl,val in ((self._left_width_slider,prof[2]),(self._right_width_slider,prof[3]),(self._center_y_slider,prof[4]),(self._scale_slider,prof[5]),(self._glow_slider,prof[6]),(self._motion_slider,prof[7]),(self._density_slider,prof[8]),(self._particle_field_slider,prof[9])):
            sl.blockSignals(True); sl.setValue(int(val)); sl.blockSignals(False)
        self._queue_live()
        for key,c in self._checks.items():
            if key.startswith("show_") and c.isEnabled(): c.setChecked(key not in hidden)
        self._queue_live()

    def _reset(self):
        self._prefs=dict(_UI_PREF_DEFAULTS); self._layout_combo.setCurrentIndex(0); self._command_pos_combo.setCurrentIndex(0); self._widget_pos_combo.setCurrentIndex(0); self._left_width_slider.setValue(270); self._right_width_slider.setValue(352); self._center_y_slider.setValue(44); self._scale_slider.setValue(100); self._glow_slider.setValue(100); self._motion_slider.setValue(100); self._density_slider.setValue(100); self._particle_field_slider.setValue(1); self._react_slider.setValue(100); self._weather_input.clear()
        for key,c in self._checks.items():
            c.setChecked(bool(self._prefs.get(key,True)))
        self._checks["show_command_input"].setChecked(True); self._queue_live()

    def _save(self):
        p=self._collect(); self._prefs=_save_ui_preferences(p); self.saved.emit(dict(self._prefs)); self.hide()


class SettingsOverlay(QWidget):
    """Integrated MARK 54 command-deck settings panel.

    The panel owns only presentation/state refresh. MainWindow still owns the actual
    actions, so existing functionality and callbacks stay untouched.
    """
    _OW, _MAX_H = 440, 660

    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self._owner = owner
        self.setObjectName("SettingsOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(self._OW)
        self.setMaximumHeight(self._MAX_H)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.setStyleSheet(
            f"QWidget#SettingsOverlay {{ background:transparent; border:1px solid {C.BORDER_B}; border-radius:8px; }}"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)

        # Header -------------------------------------------------------------
        hdr = QHBoxLayout(); hdr.setSpacing(8)
        title_box = QVBoxLayout(); title_box.setSpacing(1)
        title = QLabel("SETTINGS")
        title.setFont(QFont("Courier New", 13, QFont.Weight.Bold))
        title.setStyleSheet(f"color:{C.WHITE}; background:transparent;")
        title_box.addWidget(title)
        self._settings_sub_lbl = QLabel(f"SYSTEM CONTROLS  /  {owner._assistant_name.upper()} CONFIGURATION")
        self._settings_sub_lbl.setFont(QFont("Courier New", 6, QFont.Weight.Bold))
        self._settings_sub_lbl.setStyleSheet(f"color:{C.TEXT_DIM}; background:transparent;")
        title_box.addWidget(self._settings_sub_lbl)
        hdr.addLayout(title_box, 1)

        self._live_label = QLabel("● ONLINE")
        self._live_label.setMinimumWidth(72)
        self._live_label.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        self._live_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hdr.addWidget(self._live_label, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(hdr)

        # Thin HUD-style scan line.
        scan = QFrame(); scan.setFixedHeight(2)
        scan.setStyleSheet(f"background:{C.PRI}; border:none;")
        root.addWidget(scan)

        # Status card -------------------------------------------------------
        self._status_card = QFrame(); self._status_card.setObjectName("SettingsStatusCard")
        self._status_card.setStyleSheet(
            f"QFrame#SettingsStatusCard {{ background:#06131f; border:1px solid {C.BORDER}; border-radius:5px; }}"
        )
        stat = QHBoxLayout(self._status_card)
        stat.setContentsMargins(10, 7, 10, 7); stat.setSpacing(8)

        profile = QVBoxLayout(); profile.setSpacing(1)
        cap = QLabel("ACTIVE HUD PROFILE")
        cap.setFont(QFont("Courier New", 6, QFont.Weight.Bold))
        cap.setStyleSheet(f"color:{C.TEXT_DIM}; background:transparent;")
        profile.addWidget(cap)
        self._mode_value = QLabel("ORBITAL HUD")
        self._mode_value.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
        self._mode_value.setStyleSheet(f"color:{C.PRI}; background:transparent;")
        profile.addWidget(self._mode_value)
        stat.addLayout(profile, 1)

        state_box = QVBoxLayout(); state_box.setSpacing(1)
        state_cap = QLabel("SYSTEM STATE")
        state_cap.setFont(QFont("Courier New", 6, QFont.Weight.Bold))
        state_cap.setAlignment(Qt.AlignmentFlag.AlignRight)
        state_cap.setStyleSheet(f"color:{C.TEXT_DIM}; background:transparent;")
        state_box.addWidget(state_cap)
        self._state_value = QLabel("READY")
        self._state_value.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._state_value.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._state_value.setStyleSheet(f"color:{C.GREEN}; background:transparent;")
        state_box.addWidget(self._state_value)
        stat.addLayout(state_box)
        root.addWidget(self._status_card)

        # Scrollable command body ------------------------------------------
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setStyleSheet(
            "QScrollArea { background:transparent; border:none; } "
            f"QScrollBar:vertical {{ background:transparent; width:6px; border:none; }} "
            f"QScrollBar::handle:vertical {{ background:{C.BORDER_B}; border-radius:3px; min-height:28px; }} "
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; border:none; }"
        )
        inner = QWidget(); inner.setStyleSheet("background:transparent;")
        body = QVBoxLayout(inner)
        body.setContentsMargins(2, 2, 2, 4)
        body.setSpacing(7)
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

        def section(label: str, hint: str = ""):
            row = QHBoxLayout(); row.setSpacing(6)
            lab = QLabel(label)
            lab.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            lab.setStyleSheet(f"color:{C.TEXT_MED}; background:transparent;")
            row.addWidget(lab)
            if hint:
                h = QLabel(hint)
                h.setFont(QFont("Courier New", 6))
                h.setStyleSheet(f"color:{C.TEXT_DIM}; background:transparent;")
                row.addWidget(h)
            row.addStretch(1)
            body.addLayout(row)

        def rule():
            line = QFrame(); line.setFixedHeight(1)
            line.setStyleSheet(f"background:{C.BORDER}; border:none;")
            body.addWidget(line)

        def action_button(text, cb, tooltip=""):
            b = JANEButton(text)
            b.setMinimumHeight(34)
            b.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(
                f"QPushButton {{ background:#061521; color:{C.TEXT_MED}; border:1px solid {C.BORDER}; "
                "border-radius:4px; text-align:left; padding:0 10px; }} "
                f"QPushButton:hover {{ background:#0a2030; color:{C.WHITE}; border:1px solid {C.BORDER_B}; }} "
                f"QPushButton:pressed {{ background:{C.PRI_GHO}; border:1px solid {C.PRI}; }}"
            )
            if tooltip:
                b.setToolTip(tooltip)
            b.clicked.connect(cb)
            body.addWidget(b)
            return b

        section("CUSTOMISATION", "identity / voice / appearance")
        self._identity_name = QLabel(owner._assistant_name.upper())
        self._identity_name.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        self._identity_name.setStyleSheet(f"color:{C.PRI}; background:{C.PANEL2}; border:1px solid {C.BORDER}; border-radius:4px; padding:7px 9px;")
        body.addWidget(self._identity_name)
        self._identity_tagline = QLabel(getattr(owner, "_assistant_tagline", DEFAULT_ASSISTANT_TAGLINE))
        self._identity_tagline.setWordWrap(True)
        self._identity_tagline.setFont(QFont("Courier New", 7))
        self._identity_tagline.setStyleSheet(f"color:{C.TEXT_DIM}; background:{C.PANEL}; border-left:2px solid {C.PRI_DIM}; padding:3px 7px;")
        body.addWidget(self._identity_tagline)
        action_button("⚙  CUSTOMISE ASSISTANT", owner._open_customize, "Change the assistant name, tagline, voice and UI colour.")
        action_button("◈  UI WORKBENCH", owner._open_ui_workbench, "Customise layout, widgets, centre scale, speech reactivity, motion and the optional weather tile.")
        action_button("↺  RESET ASSISTANT DEFAULTS", owner._reset_assistant_defaults, "Restore the default name, tagline and UI colour without touching API keys, audio devices or behaviour settings.")

        rule()
        section("SYSTEM & TOOLS", "launch / manage")
        action_button("◈  REMOTE CONTROL", owner._open_remote, "Pair a phone to the assistant over the local network.")
        remote_note = QLabel("PHONE PAIRING  ·  LOCAL NETWORK")
        remote_note.setFont(QFont("Courier New", 5, QFont.Weight.Bold))
        remote_note.setStyleSheet(f"color:{C.TEXT_DIM}; background:transparent; padding-left:10px;")
        body.addWidget(remote_note)
        action_button("⛶  FULLSCREEN   [F11]", owner._toggle_fullscreen, "Toggle fullscreen mode.")
        action_button("⊞  CREATE DESKTOP SHORTCUT", owner._create_desktop_shortcut)
        action_button("◌  AUDIO DEVICES", owner._open_audio_devices, "Choose microphone and speaker devices.")
        action_button("▣  MEMORY", owner._open_memory_panel, "Review the assistant's memory controls.")
        action_button("◇  PLUGINS", owner._open_plugin_manager, "Manage installed plugins.")
        action_button("⚙  PLUGIN SETTINGS", owner._open_plugin_settings)

        rule()
        section("BEHAVIOUR", "live state")
        self._auto_btn = action_button("AUTO-START: OFF", owner._toggle_autostart)
        self._brief_btn = action_button("MORNING BRIEF: OFF", owner._toggle_brief)
        self._wake_btn = action_button("WAKE WORD: OFF", owner._toggle_wake_word)

        wake_row = QHBoxLayout(); wake_row.setSpacing(6)
        self._wake_phrase_input = QLineEdit(owner._wake_word_phrase)
        self._wake_phrase_input.setPlaceholderText(DEFAULT_WAKE_WORD)
        self._wake_phrase_input.setMaxLength(_WAKE_WORD_MAX)
        self._wake_phrase_input.setFixedHeight(32)
        self._wake_phrase_input.setFont(QFont("Courier New", 8))
        self._wake_phrase_input.setStyleSheet(
            f"QLineEdit {{ background:#06131f; color:{C.TEXT}; border:1px solid {C.BORDER}; border-radius:4px; padding:0 9px; }} "
            f"QLineEdit:focus {{ border:1px solid {C.PRI}; }}"
        )
        wake_row.addWidget(self._wake_phrase_input, 1)
        self._wake_phrase_apply = JANEButton("APPLY")
        self._wake_phrase_apply.setFixedHeight(32)
        self._wake_phrase_apply.setFont(QFont("Courier New", 6, QFont.Weight.Bold))
        self._wake_phrase_apply.setCursor(Qt.CursorShape.PointingHandCursor)
        self._wake_phrase_apply.setStyleSheet(
            f"QPushButton {{ background:#071a24; color:{C.PRI}; border:1px solid {C.BORDER_B}; border-radius:4px; padding:0 11px; }} "
            f"QPushButton:hover {{ background:#0a2330; color:{C.WHITE}; }}"
        )
        self._wake_phrase_apply.clicked.connect(owner._apply_wake_word_phrase)
        self._wake_phrase_input.editingFinished.connect(owner._apply_wake_word_phrase)
        wake_row_widget = QWidget(); wake_row_widget.setStyleSheet("background:transparent;")
        wake_row_widget.setLayout(wake_row)
        body.addWidget(wake_row_widget)
        self._wake_phrase_note = QLabel("CUSTOM WAKE PHRASE  ·  type the exact English phrase you want JANE to listen for")
        self._wake_phrase_note.setWordWrap(True)
        self._wake_phrase_note.setFont(QFont("Courier New", 5, QFont.Weight.Bold))
        self._wake_phrase_note.setStyleSheet(f"color:{C.TEXT_DIM}; background:transparent;")
        body.addWidget(self._wake_phrase_note)

        self._ptt_btn = action_button("PUSH-TO-TALK: OFF", owner._toggle_ptt)
        legend = QLabel("GREEN = ENABLED    DIM = OFF    ◉ = ACTIVE")
        legend.setFont(QFont("Courier New", 5, QFont.Weight.Bold))
        legend.setStyleSheet(f"color:{C.TEXT_DIM}; background:transparent;")
        legend.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body.addWidget(legend)

        rule()
        section("HUD VISUAL", "select active centrepiece")

        # Three real choices — not an on/off switch.
        self._hud_buttons = {}
        hud_meta = (
            ("face", "01  ·  ANIMATED FACE", "Holographic head with gaze and speech animation."),
            ("reactor", "02  ·  REACTOR CORE", "Original mechanical energy core driven by state and audio."),
            ("orbital", "03  ·  ORBITAL HUD", "Layered violet/cyan orbital telemetry — the signature assistant HUD."),
        )
        for mode, label, tip in hud_meta:
            b = JANEButton(label)
            b.setMinimumHeight(39)
            b.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, m=mode: owner._set_hud_style(m))
            self._hud_buttons[mode] = b
            body.addWidget(b)

        # Full-width footer inside the scroller so its centring is real, not just
        # text alignment inside a label that kept its natural content width.
        self._brand_note = QLabel(f"MARK 54   ·   {getattr(owner, '_assistant_tagline', DEFAULT_ASSISTANT_TAGLINE)}")
        self._brand_note.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._brand_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._brand_note.setFont(QFont("Courier New", 6, QFont.Weight.Bold))
        self._brand_note.setStyleSheet(f"color:{C.TEXT_DIM}; background:transparent;")
        body.addSpacing(2)
        body.addWidget(self._brand_note)

        close_row = QHBoxLayout(); close_row.setContentsMargins(0, 2, 0, 0)
        close = JANEButton("CLOSE SETTINGS")
        close.setFixedHeight(34)
        close.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        close.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(
            f"QPushButton {{ background:#06131f; color:{C.TEXT_MED}; border:1px solid {C.BORDER}; border-radius:4px; }} "
            f"QPushButton:hover {{ background:#0a2030; color:{C.PRI}; border:1px solid {C.BORDER_B}; }}"
        )
        close.clicked.connect(self.hide)
        close_row.addWidget(close)
        root.addLayout(close_row)

        # State is refreshed lazily after the panel exists, keeping construction cheap.

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), qcol("#020912", 248))

        # Subtle orbital wash: cyan/ice-blue throughout.
        grad = QLinearGradient(QPointF(0, 0), QPointF(w, h))
        grad.setColorAt(0.0, qcol(C.PRI, 22))
        grad.setColorAt(0.48, qcol("#14203b", 10))
        grad.setColorAt(1.0, qcol("#3d76b4", 14))
        p.fillRect(self.rect(), QBrush(grad))

        # Fine telemetry lines and orbit dots keep Settings visually related to Orbital HUD.
        p.setPen(QPen(qcol(C.PRI, 22), 1))
        p.drawLine(QPointF(12, 58), QPointF(w - 12, 58))
        p.drawLine(QPointF(12, h - 42), QPointF(w - 12, h - 42))
        p.setPen(QPen(qcol("#3d76b4", 18), 1))
        p.drawArc(QRectF(w - 116, -54, 150, 150), 205 * 16, 120 * 16)
        p.drawArc(QRectF(-54, h - 104, 130, 130), 20 * 16, 118 * 16)
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(11):
            x = 18 + i * ((w - 36) / 10.0)
            a = 40 + int(38 * (0.5 + 0.5 * math.sin(i * 1.4)))
            p.setBrush(QBrush(qcol(C.PRI if i % 2 == 0 else "#3d76b4", a)))
            p.drawEllipse(QPointF(x, 9), 1.1, 1.1)
        p.end()

    @staticmethod
    def _state_style(on: bool):
        if on:
            return (
                f"QPushButton {{ background:#001a0d; color:{C.GREEN}; border:1px solid {C.GREEN_D}; "
                "border-radius:4px; text-align:left; padding:0 10px; }} "
                f"QPushButton:hover {{ background:#002417; color:{C.WHITE}; border:1px solid {C.GREEN}; }}"
            )
        return (
            f"QPushButton {{ background:#06111a; color:{C.TEXT_DIM}; border:1px solid {C.BORDER}; "
            "border-radius:4px; text-align:left; padding:0 10px; }} "
            f"QPushButton:hover {{ background:#0a1d2a; color:{C.TEXT}; border:1px solid {C.BORDER_B}; }}"
        )

    @staticmethod
    def _hud_button_style(selected: bool):
        return (
            f"QPushButton {{ background:{C.PRI_GHO if selected else '#06111a'}; "
            f"color:{C.PRI if selected else C.TEXT_DIM}; "
            f"border:{2 if selected else 1}px solid {C.PRI if selected else C.BORDER}; "
            "border-radius:4px; text-align:left; padding:0 10px; }} "
            f"QPushButton:hover {{ background:#0a2130; color:{C.WHITE}; border:{2 if selected else 1}px solid {C.BORDER_B}; }}"
        )

    def _refresh_state(self):
        owner = self._owner
        if hasattr(self, "_identity_name"):
            self._identity_name.setText(owner._assistant_name.upper())
            self._identity_tagline.setText(getattr(owner, "_assistant_tagline", DEFAULT_ASSISTANT_TAGLINE))
            if hasattr(self, "_settings_sub_lbl"):
                self._settings_sub_lbl.setText(f"SYSTEM CONTROLS  /  {owner._assistant_name.upper()} CONFIGURATION")
            if hasattr(self, "_brand_note"):
                self._brand_note.setText(f"MARK 54   ·   {getattr(owner, '_assistant_tagline', DEFAULT_ASSISTANT_TAGLINE)}")
        try:
            auto = bool(owner._check_autostart())
        except Exception:
            auto = False
        self._auto_btn.setText(f"AUTO-START: {'ON' if auto else 'OFF'}")
        self._auto_btn.setStyleSheet(self._state_style(auto))

        try:
            from memory.config_manager import get_brief_enabled
            brief = bool(get_brief_enabled())
        except Exception:
            brief = False
        self._brief_btn.setText(f"MORNING BRIEF: {'ON' if brief else 'OFF'}")
        self._brief_btn.setStyleSheet(self._state_style(brief))

        try:
            if hasattr(self, "_wake_phrase_input") and not self._wake_phrase_input.hasFocus():
                self._wake_phrase_input.setText(owner._wake_word_phrase)
        except Exception:
            pass
        try:
            st = owner._wake_state()
            wake, ready = bool(st.get("enabled")), bool(st.get("ready"))
            if not ready:
                self._wake_btn.setText("WAKE WORD: DOWNLOAD")
                self._wake_btn.setStyleSheet(self._state_style(False))
            else:
                self._wake_btn.setText(f"WAKE WORD: {'ON' if wake else 'OFF'}")
                self._wake_btn.setStyleSheet(self._state_style(wake))
        except Exception:
            self._wake_btn.setText("WAKE WORD: OFF")
            self._wake_btn.setStyleSheet(self._state_style(False))

        try:
            from core.hotkey import chord_label
            from memory.config_manager import get_push_to_talk_enabled
            ptt = bool(get_push_to_talk_enabled())
            ptt_text = chord_label() if ptt else "OFF"
        except Exception:
            ptt = False
            ptt_text = "OFF"
        self._ptt_btn.setText(f"PUSH-TO-TALK: {ptt_text}")
        self._ptt_btn.setStyleSheet(self._state_style(ptt))

        mode = getattr(owner.hud, "hud_style", _load_hud_ui_style())
        if mode not in _HUD_MODE_LABELS:
            mode = "orbital"
        self._mode_value.setText(_HUD_MODE_LABELS[mode])

        state = str(getattr(owner.hud, "state", "READY") or "READY").upper()
        self._state_value.setText(state)
        self._state_value.setStyleSheet(
            f"color:{C.RED if state in ('ERROR', 'OFFLINE') else C.GREEN}; background:transparent;"
        )

        ready = bool(getattr(owner, "_ready", True))
        self._live_label.setText("●  ONLINE" if ready else "●  SETUP")
        self._live_label.setStyleSheet(
            f"color:{C.GREEN if ready else C.ACC2}; "
            f"background:{'#00150b' if ready else '#171203'}; "
            f"border:1px solid {C.GREEN_D if ready else C.ACC2}; border-radius:3px; padding:4px 7px;"
        )

        labels = {
            "face": "01  ·  ANIMATED FACE",
            "reactor": "02  ·  REACTOR CORE",
            "orbital": "03  ·  ORBITAL HUD",
        }
        for key, b in self._hud_buttons.items():
            selected = key == mode
            b.setText(labels[key] + ("    ◉ ACTIVE" if selected else ""))
            b.setStyleSheet(self._hud_button_style(selected))


class MainWindow(QMainWindow):
    _log_sig        = pyqtSignal(str)
    _state_sig      = pyqtSignal(str)
    _content_sig    = pyqtSignal(str, str)   # (title, text) — thread-safe content display
    _reconfig_sig   = pyqtSignal()           # trigger setup overlay from any thread
    _camera_sig     = pyqtSignal(bytes)      # show camera frame preview (small overlay)
    _cam_stream_sig = pyqtSignal(bool)       # True=start live stream, False=stop
    _cam_frame_sig  = pyqtSignal(bytes)      # live camera frame → HUD area
    _clipboard_sig  = pyqtSignal(str)        # clipboard text changed (thread-safe)
    _confirm_sig    = pyqtSignal(str, str)   # (title, detail) — irreversible-action gate
    _confirm_hide_sig = pyqtSignal()
    _wake_dl_sig    = pyqtSignal(bool, str)  # wake-word install finished (ok, message)
    _quiz_sig       = pyqtSignal(str, object, object)  # (topic, questions, grader)
    _quiz_hide_sig  = pyqtSignal()
    _review_sig     = pyqtSignal(str, str, object, object)  # document review payload

    def __init__(self, face_path: str):
        super().__init__()
        self._face_path = face_path

        # Load customization from config
        _cfg = _read_full_config()
        self._ui_prefs = _load_ui_preferences()
        self._ui_save_timer = QTimer(self); self._ui_save_timer.setSingleShot(True); self._ui_save_timer.timeout.connect(self._persist_ui_preferences)
        self._assistant_name: str = (_cfg.get("assistant_name") or DEFAULT_ASSISTANT_NAME).strip()
        self._assistant_tagline: str = (_cfg.get("assistant_tagline") or DEFAULT_ASSISTANT_TAGLINE).strip()
        self._user_name: str = str(_cfg.get("user_name") or "").strip()
        self._wake_word_phrase: str = _load_wake_word_phrase()
        # Ensure the persisted phrase survives any older setup/config writer.
        try: _save_wake_word_phrase(self._wake_word_phrase)
        except Exception: pass
        _display = self._assistant_name.upper()

        # Apply the saved UI colour BEFORE panels/stylesheets are built
        _ui_color = (_cfg.get("ui_color") or "").strip()
        if _ui_color and _ui_color.lower() != DEFAULT_UI_COLOR:
            apply_ui_accent(_ui_color)

        self.setWindowTitle(f"{_display} — {APP_VERSION}")
        self.setMinimumSize(_MIN_W, _MIN_H)
        self.resize(_DEFAULT_W, _DEFAULT_H)

        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            (screen.width()  - _DEFAULT_W) // 2,
            (screen.height() - _DEFAULT_H) // 2,
        )

        self.on_text_command   = None
        self.on_remote_clicked = None   # callable: () -> (url, key) | None
        self.on_interrupt      = None   # callable: () -> None — stop JANE mid-speech
        self.on_voice_change   = None   # callable: () -> None — rebuild session with new voice
        self.on_identity_change = None  # callable: () -> None — identity/user-name config changed
        self.on_audio_device_change = None  # callable: () -> None — reopen audio streams
        self._confirm_overlay  = None   # live ConfirmBanner, if one is on screen
        self.get_plugins       = None   # callable: () -> list[dict], set by JarvisLive
        self.get_plugin_settings = None # callable: () -> list[dict] settings schemas, set by JarvisLive
        self.on_wake_toggle    = None   # callable: (enable: bool) -> str, set by JarvisLive
        self.on_wake_manual    = None   # callable: () -> None — manual sleep/wake
        self.on_wake_word_change = None  # callable: (phrase: str) -> None, set by the engine
        self.on_push_to_talk   = None   # callable: (enable: bool) -> str scope
        self.ptt_hold          = None   # callable: (held: bool) -> None
        self.wake_get_state    = None   # callable: () -> dict {enabled, awake, ready}
        self._muted            = False
        self._current_file: str | None = None
        self._remote_overlay: RemoteKeyOverlay | None = None
        self._customize_overlay: CustomizeOverlay | None = None
        self._settings_overlay: SettingsOverlay | None = None
        self._ptt_event_filter_installed = False
        self._ptt_local_enabled = False
        self._ptt_pressed = False
        self._install_shortcut_guard()

        central = QWidget()
        central.setStyleSheet(f"background: {C.BG};")
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self._left_panel = self._build_left_panel()
        body.addWidget(self._left_panel, stretch=0)

        # Center column: HUD + resizable content panel via QSplitter
        self.hud = HudCanvas(face_path, _display)
        self.hud._assistant_tagline = self._assistant_tagline
        self.hud.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._content_panel = self._build_content_panel()
        self._quiz_panel = self._build_quiz_panel()

        # Live camera container — replaces HUD when camera stream is active
        _cam_cont = QWidget()
        _cam_cont.setStyleSheet("background: #000308;")
        _cam_v = QVBoxLayout(_cam_cont)
        _cam_v.setContentsMargins(0, 0, 0, 0)
        _cam_v.setSpacing(0)
        _cam_hdr = QHBoxLayout()
        _cam_hdr.setContentsMargins(8, 5, 8, 5)
        _cam_title = QLabel("◈  CAMERA FEED")
        _cam_title.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        _cam_title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        _cam_hdr.addWidget(_cam_title)
        _cam_hdr.addStretch()
        _cam_x = JANEButton("✕  CLOSE")
        _cam_x.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        _cam_x.setCursor(Qt.CursorShape.PointingHandCursor)
        _cam_x.setStyleSheet(f"""
            QPushButton {{
                color: {C.TEXT_DIM}; background: transparent;
                border: none; padding: 2px 6px;
            }}
            QPushButton:hover {{ color: {C.PRI}; }}
        """)
        _cam_x.clicked.connect(self.stop_camera_stream)
        _cam_hdr.addWidget(_cam_x)
        _cam_v.addLayout(_cam_hdr)
        self._cam_live_lbl = QLabel()
        self._cam_live_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cam_live_lbl.setStyleSheet("background: transparent;")
        self._cam_live_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        _cam_v.addWidget(self._cam_live_lbl, stretch=1)

        # Stack: 0 = animated HUD, 1 = live camera
        self._hud_cam_stack = QStackedWidget()
        self._hud_cam_stack.addWidget(self.hud)
        self._hud_cam_stack.addWidget(_cam_cont)

        self._center_split = QSplitter(Qt.Orientation.Vertical)
        self._center_split.setStyleSheet(f"""
            QSplitter::handle {{
                background: {C.BORDER};
                height: 4px;
            }}
            QSplitter::handle:hover {{
                background: {C.PRI_DIM};
            }}
        """)
        self._center_split.addWidget(self._hud_cam_stack)
        self._center_split.addWidget(self._content_panel)
        self._center_split.addWidget(self._quiz_panel)
        self._center_split.setStretchFactor(0, 3)
        self._center_split.setStretchFactor(1, 1)
        self._center_split.setCollapsible(0, False)
        body.addWidget(self._center_split, stretch=5)

        self._right_panel = self._build_right_panel()
        self._log._ai_name_lc = self._assistant_name.lower()
        body.addWidget(self._right_panel, stretch=0)

        root.addLayout(body, stretch=1)
        self._footer = self._build_footer()
        root.addWidget(self._footer)

        # Apply saved UI visibility/customisation after every widget exists.
        self._apply_ui_preferences(self._ui_prefs, log=False, refresh_weather=False)

        # Quick-access drawer (floating overlay, built after central widget layout is done)
        self._quick_drawer = self._build_quick_drawer()
        self._update_autostart_btn(self._check_autostart())
        from memory.config_manager import get_brief_enabled as _gbe
        self._update_brief_btn(_gbe())

        self._clock_tmr = QTimer(self)
        self._clock_tmr.timeout.connect(self._tick_clock)
        self._clock_tmr.start(1000)
        self._tick_clock()

        # Metric update timer
        self._metric_tmr = QTimer(self)
        self._metric_tmr.timeout.connect(self._update_metrics)
        self._metric_tmr.start(1500)
        self._update_metrics()

        self._log_sig.connect(self._log.append_log)
        self._state_sig.connect(self._apply_state)
        self._content_sig.connect(self._show_content)
        self._reconfig_sig.connect(self._show_setup)
        self._camera_sig.connect(self._show_camera_frame)
        self._confirm_sig.connect(self._show_confirm_banner)
        self._confirm_hide_sig.connect(self._hide_confirm_banner)
        self._cam_stream_sig.connect(self._on_cam_stream)
        self._cam_frame_sig.connect(self._on_cam_frame)
        self._clipboard_sig.connect(self._show_clipboard_panel)
        self._wake_dl_sig.connect(self._on_wake_install_done)
        self._quiz_sig.connect(self._show_quiz)
        self._quiz_hide_sig.connect(self._hide_quiz)
        self._review_sig.connect(self._show_review)
        self._cam_stop = threading.Event()

        # Camera preview overlay (child of central widget, positioned in resizeEvent)
        self._cam_preview = _CameraPreview(self.centralWidget())

        # Clipboard panel (child of central widget, bottom-center)
        self._clipboard_panel = ClipboardPanel(self.centralWidget())
        self._clipboard_panel.action_requested.connect(self._on_clipboard_action)
        QApplication.clipboard().dataChanged.connect(self._on_clipboard_changed)

        self._overlay: SetupOverlay | None = None
        self._ready = self._check_config()
        if not self._ready:
            self._show_setup()

        sc_mute = QShortcut(QKeySequence("F4"), self)
        sc_mute.activated.connect(self._toggle_mute)
        sc_full = QShortcut(QKeySequence("F11"), self)
        sc_full.activated.connect(self._toggle_fullscreen)
        sc_intr = QShortcut(QKeySequence("Escape"), self)
        sc_intr.activated.connect(self._do_interrupt)

        # Pre-build the settings drawer after first paint so its first open is instant.
        # Do not let a previous button click leave keyboard focus behind. This
        # is especially important for Ctrl+Space because Qt otherwise treats the
        # Space part as a button activation. Text fields remain keyboard-focusable.
        for _btn in self.findChildren(QPushButton):
            try:
                _btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            except Exception:
                pass

        QTimer.singleShot(180, self._prime_settings_overlay)

    def _prime_settings_overlay(self):
        if getattr(self, "_settings_overlay", None) is not None:
            return
        try:
            cw = self.centralWidget()
            ov = SettingsOverlay(self, parent=cw)
            ov.hide()
            self._settings_overlay = ov
            QTimer.singleShot(0, ov._refresh_state)
        except Exception as exc:
            self._log.append_log(f"ERR: Settings preload failed — {exc}")

    def _show_camera_frame(self, img_bytes: bytes):
        """Slot — display camera preview overlay (main thread)."""
        self._cam_preview.show_frame(img_bytes)
        cw = self.centralWidget()
        pw = _CameraPreview._W
        ph = self._cam_preview.height()
        self._cam_preview.setGeometry(
            cw.width() - (self._right_panel.width() if getattr(self, "_right_panel", None) is not None else _RIGHT_W) - pw - 12,
            cw.height() - ph - 28,
            pw, ph,
        )

    # --- Live camera stream in HUD area ------------------------------------
    def _on_cam_stream(self, start: bool) -> None:
        if start:
            self._hud_cam_stack.setCurrentIndex(1)
        else:
            self._hud_cam_stack.setCurrentIndex(0)
            self._cam_live_lbl.clear()

    def _on_cam_frame(self, data: bytes) -> None:
        px = QPixmap()
        px.loadFromData(data)
        if not px.isNull():
            w, h = self._cam_live_lbl.width(), self._cam_live_lbl.height()
            if w > 1 and h > 1:
                self._cam_live_lbl.setPixmap(
                    px.scaled(w, h,
                              Qt.AspectRatioMode.KeepAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
                )

    def start_camera_stream(self) -> None:
        self._cam_stop.clear()
        self._cam_stream_sig.emit(True)
        t = threading.Thread(target=self._cam_loop, daemon=True, name="cam-stream")
        t.start()

    def _cam_loop(self) -> None:
        try:
            import cv2
            # Reuse camera index detected by screen_processor (cached in api_keys.json)
            cam_idx = 0
            try:
                import json as _j
                cfg = _j.loads((CONFIG_DIR / "api_keys.json").read_text())
                cam_idx = int(cfg.get("camera_index", 0))
            except Exception:
                pass
            try:
                backend = cv2.CAP_DSHOW if _OS == "Windows" else cv2.CAP_ANY
            except AttributeError:
                backend = 0
            cap = cv2.VideoCapture(cam_idx, backend)
            if not cap.isOpened():
                cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                return
            # warm-up frames
            for _ in range(5):
                cap.read()
            while not self._cam_stop.wait(0.033) and cap.isOpened():
                ret, frame = cap.read()
                if ret and frame is not None:
                    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 65])
                    self._cam_frame_sig.emit(buf.tobytes())
            cap.release()
        except Exception as e:
            print(f"[Camera] Stream error: {e}")
        finally:
            self._cam_stream_sig.emit(False)

    def stop_camera_stream(self) -> None:
        self._cam_stop.set()

    # ------------------------------------------------------------------
    # Icon generation — arc-reactor style, rendered with Pillow
    # ------------------------------------------------------------------
    @staticmethod
    def _build_jarvis_icon(out_path: Path) -> bool:
        """
        Render a JANE arc-reactor icon at 4× resolution and downsample
        for crisp results at all sizes. Saves a multi-res .ico to out_path.
        Returns True on success.
        """
        try:
            import math
            import PIL.Image
            import PIL.ImageDraw
            import PIL.ImageFilter
        except ImportError:
            return False

        CYAN   = (0, 212, 255)
        DIM    = (0, 100, 140)
        DARK   = (0, 6, 10)
        GLOW   = (0, 160, 200)
        WHITE  = (220, 240, 255)

        def _render(sz: int) -> PIL.Image.Image:
            S  = sz * 4                     # draw at 4× then downscale
            img = PIL.Image.new("RGBA", (S, S), (0, 0, 0, 0))
            d   = PIL.ImageDraw.Draw(img)
            cx = cy = S // 2

            # ── filled background circle ──────────────────────────────────
            R = S // 2 - 2
            d.ellipse([cx-R, cy-R, cx+R, cy+R], fill=(*DARK, 255))

            # ── outer border ring ─────────────────────────────────────────
            lw = max(2, S // 40)
            d.ellipse([cx-R, cy-R, cx+R, cy+R],
                      outline=(*CYAN, 220), width=lw)

            # ── mid decorative ring ───────────────────────────────────────
            R2 = int(R * 0.72)
            d.ellipse([cx-R2, cy-R2, cx+R2, cy+R2],
                      outline=(*DIM, 180), width=max(1, lw // 2))

            # ── 6 radial spokes (hex bolt) ────────────────────────────────
            R_inner = int(R * 0.30)
            R_outer = int(R * 0.62)
            spoke_w = max(1, S // 80)
            for i in range(6):
                angle = math.radians(i * 60 - 30)
                x1 = cx + int(R_inner * math.cos(angle))
                y1 = cy + int(R_inner * math.sin(angle))
                x2 = cx + int(R_outer * math.cos(angle))
                y2 = cy + int(R_outer * math.sin(angle))
                d.line([x1, y1, x2, y2], fill=(*GLOW, 200), width=spoke_w)

            # ── 6 tick marks on outer ring ────────────────────────────────
            for i in range(6):
                angle = math.radians(i * 60)
                for dr in range(lw * 2):
                    rx = (R - lw - dr)
                    d.point(
                        [cx + int(rx * math.cos(angle)),
                         cy + int(rx * math.sin(angle))],
                        fill=(*WHITE, 220),
                    )

            # ── inner glowing ring ────────────────────────────────────────
            Ri = int(R * 0.26)
            d.ellipse([cx-Ri, cy-Ri, cx+Ri, cy+Ri],
                      outline=(*CYAN, 255), width=max(2, lw))

            # ── bright glow soft blur applied before core ─────────────────
            # (draw a slightly larger cyan circle on a separate layer)
            glow_layer = PIL.Image.new("RGBA", (S, S), (0, 0, 0, 0))
            gd = PIL.ImageDraw.Draw(glow_layer)
            Rc = int(R * 0.13)
            gd.ellipse([cx-Rc*2, cy-Rc*2, cx+Rc*2, cy+Rc*2],
                       fill=(*CYAN, 110))
            glow_layer = glow_layer.filter(PIL.ImageFilter.GaussianBlur(S // 14))
            img = PIL.Image.alpha_composite(img, glow_layer)
            d   = PIL.ImageDraw.Draw(img)

            # ── core dot ──────────────────────────────────────────────────
            d.ellipse([cx-Rc, cy-Rc, cx+Rc, cy+Rc], fill=(*WHITE, 255))

            # ── downscale to target size ──────────────────────────────────
            return img.resize((sz, sz), PIL.Image.LANCZOS)

        try:
            sizes  = [256, 128, 64, 48, 32, 16]
            frames = [_render(s) for s in sizes]
            frames[0].save(
                out_path,
                format="ICO",
                append_images=frames[1:],
                sizes=[(s, s) for s in sizes],
            )
            return True
        except Exception as e:
            print(f"[Shortcut] ⚠️  Icon generation failed: {e}")
            return False

    @staticmethod
    def _create_lnk_windows(lnk: str, target: str, args: str,
                             work_dir: str, icon_loc: str, description: str = "Personal AI Assistant") -> None:
        """
        Create a Windows .lnk shortcut WITHOUT launching PowerShell or cmd.
        Tries win32com (pywin32) first; falls back to wscript.exe + VBScript.
        wscript.exe is a GUI-mode host — it never opens a console window.
        """
        # ── Option 1: pywin32 (pure Python COM, zero subprocess) ──────────
        try:
            from win32com.client import Dispatch   # type: ignore
            sh = Dispatch("WScript.Shell")
            sc = sh.CreateShortCut(lnk)
            sc.TargetPath       = target
            sc.Arguments        = f'"{args}"'
            sc.WorkingDirectory = work_dir
            sc.Description      = description
            sc.IconLocation     = icon_loc
            sc.save()
            return
        except ImportError:
            pass

        # ── Option 2: wscript.exe + VBScript (always available on Windows,
        #    GUI-mode executable — never opens a console window) ────────────
        vbs = "\n".join([
            'Set ws = CreateObject("WScript.Shell")',
            f'Set sc = ws.CreateShortcut("{lnk}")',
            f'sc.TargetPath = "{target}"',
            f'sc.Arguments = Chr(34) & "{args}" & Chr(34)',
            f'sc.WorkingDirectory = "{work_dir}"',
            f'sc.Description = "{description.replace(chr(34), chr(39))}"',
            f'sc.IconLocation = "{icon_loc}"',
            'sc.Save',
        ])
        import tempfile
        fd, tmp = tempfile.mkstemp(suffix=".vbs")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(vbs)
            proc = subprocess.Popen(
                ["wscript.exe", "/nologo", tmp],
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
            )
            proc.wait(timeout=10)
        finally:
            try:
                os.unlink(tmp)
            except Exception:
                pass

    @staticmethod
    def _get_desktop_dir() -> Path:
        """
        Resolve the user's REAL desktop directory instead of assuming
        ~/Desktop, which breaks when:
          • OneDrive "Known Folder Move" relocates the desktop
            (C:/Users/x/OneDrive/Desktop) — very common on Win 10/11;
          • the XDG desktop is localized on Linux (~/Masaüstü,
            ~/Schreibtisch, ~/Bureau, …).
        Falls back to ~/Desktop only as a last resort.
        """
        home = Path.home()
        _os = platform.system()

        if _os == "Windows":
            # ── 1) SHGetKnownFolderPath(FOLDERID_Desktop) — the canonical
            #       answer; follows OneDrive redirection. No dependencies. ──
            try:
                import ctypes
                from ctypes import wintypes

                class _GUID(ctypes.Structure):
                    _fields_ = [("Data1", wintypes.DWORD),
                                ("Data2", wintypes.WORD),
                                ("Data3", wintypes.WORD),
                                ("Data4", ctypes.c_ubyte * 8)]

                # FOLDERID_Desktop {B4BFCC3A-DB2C-424C-B029-7FE99A87C641}
                fid = _GUID(0xB4BFCC3A, 0xDB2C, 0x424C,
                            (ctypes.c_ubyte * 8)(0xB0, 0x29, 0x7F, 0xE9,
                                                 0x9A, 0x87, 0xC6, 0x41))
                buf = ctypes.c_wchar_p()
                if ctypes.windll.shell32.SHGetKnownFolderPath(
                        ctypes.byref(fid), 0, None, ctypes.byref(buf)) == 0:
                    p = Path(buf.value)
                    ctypes.windll.ole32.CoTaskMemFree(buf)
                    if p.is_dir():
                        return p
            except Exception:
                pass

            # ── 2) Registry: User Shell Folders (may contain %VARS%) ──────
            try:
                import winreg
                with winreg.OpenKey(
                        winreg.HKEY_CURRENT_USER,
                        r"Software\Microsoft\Windows\CurrentVersion"
                        r"\Explorer\User Shell Folders") as key:
                    val, _t = winreg.QueryValueEx(key, "Desktop")
                p = Path(os.path.expandvars(val))
                if p.is_dir():
                    return p
            except Exception:
                pass

        elif _os == "Linux":
            # ── xdg-user-dir honours localized names (~/Masaüstü, …) ──────
            try:
                out = subprocess.run(["xdg-user-dir", "DESKTOP"],
                                     capture_output=True, text=True, timeout=5)
                p = Path(out.stdout.strip())
                if out.stdout.strip() and p != home and p.is_dir():
                    return p
            except Exception:
                pass
            try:
                cfg = home / ".config" / "user-dirs.dirs"
                for line in cfg.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("XDG_DESKTOP_DIR"):
                        val = line.split("=", 1)[1].strip().strip('"')
                        p = Path(val.replace("$HOME", str(home)))
                        if p != home and p.is_dir():
                            return p
            except Exception:
                pass

        # macOS: ~/Desktop is always the real path (localization is
        # display-only). Everything else lands here as a last resort.
        return home / "Desktop"

    def _create_desktop_shortcut(self):
        """
        Create a desktop shortcut on Windows / macOS / Linux.
        Never opens a terminal, console, or PowerShell window on any platform.
        """
        import stat as _stat
        script  = Path(__file__).resolve().parent / "main.py"
        python  = Path(sys.executable)
        desktop = self._get_desktop_dir()

        # Arc-reactor icon (.ico — also exported as .png for Linux/macOS)
        ico_path = Path(__file__).resolve().parent / "config" / "jarvis.ico"
        if not ico_path.exists():
            self._build_jarvis_icon(ico_path)

        try:
            _os = platform.system()

            # ── Windows ───────────────────────────────────────────────────────
            if _os == "Windows":
                pythonw  = python.parent / "pythonw.exe"
                target   = str(pythonw if pythonw.exists() else python)
                lnk      = str(desktop / "JANE.lnk")
                icon_loc = str(ico_path) if ico_path.exists() else f"{target},0"
                self._create_lnk_windows(lnk, target, str(script),
                                         str(script.parent), icon_loc,
                                         f"{self._assistant_name} Personal AI Assistant")

            # ── macOS — proper .app bundle (no Terminal window) ───────────────
            elif _os == "Darwin":
                app     = desktop / "JANE.app"
                mac_dir = app / "Contents" / "MacOS"
                res_dir = app / "Contents" / "Resources"
                mac_dir.mkdir(parents=True, exist_ok=True)
                res_dir.mkdir(exist_ok=True)

                # Launcher executable (bash — runs as background process,
                # macOS does NOT open Terminal for executables inside .app bundles)
                launcher = mac_dir / "JANE"
                launcher.write_text(
                    "#!/usr/bin/env bash\n"
                    f'cd "{script.parent}"\n'
                    f'exec "{python}" "{script}"\n'
                )
                launcher.chmod(launcher.stat().st_mode
                               | _stat.S_IEXEC | _stat.S_IXGRP | _stat.S_IXOTH)

                # Minimal Info.plist (required for .app recognition)
                (app / "Contents" / "Info.plist").write_text(
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                    '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                    '<plist version="1.0"><dict>\n'
                    '  <key>CFBundleExecutable</key><string>JANE</string>\n'
                    '  <key>CFBundleIdentifier</key>'
                    '<string>com.jarvis.assistant</string>\n'
                    f'  <key>CFBundleName</key><string>{self._assistant_name}</string>\n'
                    '  <key>CFBundlePackageType</key><string>APPL</string>\n'
                    '  <key>CFBundleVersion</key><string>1.0</string>\n'
                    '</dict></plist>\n'
                )

                # Optional: copy icon as .icns (skip silently if Pillow is missing)
                try:
                    import PIL.Image
                    icns = res_dir / "AppIcon.icns"
                    PIL.Image.open(ico_path).save(icns, format="ICNS")
                    # Inject icon reference into plist
                    plist = app / "Contents" / "Info.plist"
                    txt = plist.read_text()
                    plist.write_text(
                        txt.replace(
                            '</dict></plist>',
                            '  <key>CFBundleIconFile</key>'
                            '<string>AppIcon</string>\n</dict></plist>\n',
                        )
                    )
                except Exception:
                    pass  # icon is optional

            # ── Linux — .desktop file (Terminal=false, no console) ────────────
            else:
                # Export .ico → .png for better desktop integration
                png_path = ico_path.with_suffix(".png")
                if not png_path.exists() and ico_path.exists():
                    try:
                        import PIL.Image
                        PIL.Image.open(ico_path).resize(
                            (256, 256), PIL.Image.LANCZOS
                        ).save(png_path, format="PNG")
                    except Exception:
                        png_path = ico_path  # fallback to .ico

                icon_line = f"Icon={png_path}\n" if png_path.exists() else ""
                desk = desktop / "JANE.desktop"
                desk.write_text(
                    "[Desktop Entry]\n"
                    f"Name={self._assistant_name}\n"
                    f"Exec={python} {script}\n"
                    f"Path={script.parent}\n"
                    "Type=Application\n"
                    "Terminal=false\n"
                    "Categories=Utility;\n"
                    + icon_line
                )
                desk.chmod(desk.stat().st_mode | 0o755)

            self._log.append_log("SYS: Desktop shortcut created.")
        except Exception as e:
            self._log.append_log(f"ERR: Shortcut failed — {e}")

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cw = self.centralWidget()
        if self._overlay and self._overlay.isVisible():
            ow, oh = 460, 390
            self._overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        if self._remote_overlay and self._remote_overlay.isVisible():
            ow, oh = RemoteKeyOverlay._OW, RemoteKeyOverlay._OH
            self._remote_overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        if self._customize_overlay and self._customize_overlay.isVisible():
            ow, oh = CustomizeOverlay._OW, CustomizeOverlay._OH
            self._customize_overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        if hasattr(self, "_settings_overlay") and self._settings_overlay and self._settings_overlay.isVisible():
            self._position_settings_overlay()
        if hasattr(self, "_ui_workbench") and self._ui_workbench and self._ui_workbench.isVisible():
            self._ui_workbench.adjustSize()
            self._ui_workbench.move(
                max(0, (cw.width() - self._ui_workbench.width()) // 2),
                max(8, (cw.height() - self._ui_workbench.height()) // 2),
            )
            self._ui_workbench.raise_()
        # Camera preview — bottom-right corner of the center/HUD area
        pw = _CameraPreview._W
        ph = self._cam_preview.height() or _CameraPreview._H
        self._cam_preview.setGeometry(
            cw.width() - _RIGHT_W - pw - 12,
            cw.height() - ph - 28,
            pw, ph,
        )
        # Clipboard panel — bottom-center
        if hasattr(self, '_clipboard_panel') and self._clipboard_panel.isVisible():
            self._position_clipboard_panel()
        # Quick drawer — reposition if open
        if hasattr(self, '_quick_drawer') and self._quick_drawer.isVisible():
            self._position_quick_drawer()

    def _update_metrics(self):
        snap = _metrics.snapshot()

        # CPU
        cpu = snap["cpu"]
        self._bar_cpu.set_value(cpu, f"{cpu:.0f}%")

        # MEM
        mem = snap["mem"]
        self._bar_mem.set_value(mem, f"{mem:.0f}%")

        # NET
        net = snap["net"]
        if net < 1.0:
            net_str = f"{net*1024:.0f}KB/s"
        else:
            net_str = f"{net:.1f}MB/s"
        net_pct = min(100, net * 2.5)  # 40 MB/s = 100% reference range
        self._bar_net.set_value(net_pct, net_str)

        # GPU
        gpu = snap["gpu"]
        if gpu >= 0:
            self._bar_gpu.set_value(gpu, f"{gpu:.0f}%")
        else:
            self._bar_gpu.set_value(0, "N/A")

        # TMP
        tmp = snap["tmp"]
        if tmp >= 0:
            tmp_pct = min(100, (tmp / 100) * 100)
            self._bar_tmp.set_value(tmp_pct, f"{tmp:.0f}°C")
            if hasattr(self, "_io_temp_lbl"):
                self._io_temp_lbl.setText(f"TEMP  {tmp:.0f}°C")
        else:
            self._bar_tmp.set_value(0, "N/A")
            if hasattr(self, "_io_temp_lbl"):
                self._io_temp_lbl.setText("TEMP  --")

        try:
            boot_t  = psutil.boot_time()
            elapsed = time.time() - boot_t
            h = int(elapsed // 3600)
            m = int((elapsed % 3600) // 60)
            self._uptime_lbl.setText(f"UP  {h:02d}:{m:02d}")
        except Exception:
            self._uptime_lbl.setText("UP  --:--")

        # Process enumeration is one of the slower system probes; only do it
        # when a visible widget actually needs the number.
        if getattr(self, "_ui_prefs", {}).get("show_io", True):
            try:
                proc_count = len(psutil.pids())
                self._proc_lbl.setText(f"PROC  {proc_count}")
            except Exception:
                self._proc_lbl.setText("PROC  --")

        if hasattr(self, "_mini_labels"):
            self._mini_labels["cpu"].setText(f"{snap['cpu']:.0f}%")
            self._mini_labels["mem"].setText(f"{snap['mem']:.0f}%")
            self._mini_labels["gpu"].setText("N/A" if snap["gpu"] < 0 else f"{snap['gpu']:.0f}%")
            self._mini_labels["net"].setText(f"{snap['net']:.0f}")
            self._mini_labels["tmp"].setText("N/A" if snap["tmp"] < 0 else f"{snap['tmp']:.0f}°")
            if hasattr(self, "_health_bar"):
                health = 100 - min(100, max(0, snap["cpu"] * 0.72 + snap["mem"] * 0.18))
                self._health_bar.setValue(int(health))
            if hasattr(self, "_aux_labels"):
                self._aux_labels["audio"].setText("MUTED" if self._muted else ("SPEAK" if self.hud.speaking else "READY"))
                self._aux_labels["audio"].setStyleSheet(f"color:{C.MUTED_C if self._muted else ('#67b6df' if self.hud.speaking else '#72ddea')};background:transparent;")
                self._aux_labels["ai"].setText("BUSY" if self.hud.state in ("THINKING", "PROCESSING") else "ACTIVE")
                self._aux_labels["session"].setText("ONLINE" if getattr(self, "_connected", True) else "OFFLINE")
                load = min(100, snap["cpu"] * 0.72 + snap["mem"] * 0.28)
                self._load_bar.setValue(int(load))
            if hasattr(self, "_status_tiles"):
                busy = self.hud.state in ("THINKING", "PROCESSING")
                self._status_tiles["core"].setText("BUSY" if busy else "ACTIVE")
                self._status_tiles["core"].setStyleSheet(f"color:{'#62abd8' if busy else '#62e1ed'};background:transparent;border:none;")
                self._status_tiles["sec"].setText("MUTED" if self._muted else "CLEARED")
                self._status_tiles["sec"].setStyleSheet(f"color:{C.MUTED_C if self._muted else '#62abd8'};background:transparent;border:none;")


    def _build_header(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(42)
        w.setStyleSheet(f"background: #020b12; border-bottom: 1px solid {C.BORDER};")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(12, 0, 14, 0)
        lay.setSpacing(10)

        left = QVBoxLayout(); left.setSpacing(0)
        title_row = QHBoxLayout(); title_row.setSpacing(4)
        self._header_title_left = QLabel(f"◩  {self._assistant_name.upper()} - MARK 54")
        title_left = self._header_title_left
        title_left.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        title_left.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        title_row.addWidget(title_left)
        settings_quick = JANEButton("⚙")
        settings_quick.setObjectName("HeaderSettings")
        settings_quick.setFixedSize(28, 22)
        settings_quick.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        settings_quick.setFlat(True)
        settings_quick.setFont(QFont("Segoe UI Symbol", 10))
        settings_quick.setToolTip("SETTINGS")
        settings_quick.setCursor(Qt.CursorShape.PointingHandCursor)
        settings_quick.setStyleSheet(f"""
            QPushButton#HeaderSettings {{
                color: #6f8290; background: transparent; border: 1px solid transparent;
                border-radius: 3px; padding: 0;
            }}
            QPushButton#HeaderSettings:hover {{
                color: {C.PRI}; border: 1px solid {C.BORDER_B}; background: #04131d;
            }}
        """)
        settings_quick.setToolTip("Open / close settings")
        settings_quick.clicked.connect(self._open_settings)
        title_row.addWidget(settings_quick)
        left.addLayout(title_row)
        self._header_state = QLabel("● SYSTEM ONLINE")
        self._header_state.setFont(QFont("Courier New", 6, QFont.Weight.Bold))
        self._header_state.setStyleSheet("color: #59e6ee; background: transparent;")
        left.addWidget(self._header_state)
        lay.addLayout(left)

        lay.addStretch(1)
        center = QVBoxLayout(); center.setSpacing(0)
        self._title_lbl = QLabel(self._assistant_name.upper())
        self._title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title_lbl.setFont(QFont("Segoe UI", 15, QFont.Weight.Normal))
        self._title_lbl.setStyleSheet("color: #d4eef7; background: transparent;")
        center.addWidget(self._title_lbl)
        self._sub_lbl = QLabel(self._assistant_tagline)
        self._sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sub_lbl.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        self._sub_lbl.setStyleSheet("color: #63b8d4; background: transparent; letter-spacing: 1.35px;")
        center.addWidget(self._sub_lbl)
        self._sub_brand_lbl = QLabel(f"MARK 54  ·  {self._assistant_name.upper()}  ·  PERSONAL AI ASSISTANT")
        self._sub_brand_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sub_brand_lbl.setFont(QFont("Courier New", 5))
        self._sub_brand_lbl.setStyleSheet("color: #557386; background: transparent;")
        center.addWidget(self._sub_brand_lbl)
        lay.addLayout(center, stretch=2)
        lay.addStretch(1)

        right = QVBoxLayout(); right.setSpacing(0)
        self._clock_lbl = QLabel("00:00:00")
        self._clock_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._clock_lbl.setFont(QFont("Courier New", 12, QFont.Weight.Normal))
        self._clock_lbl.setStyleSheet("color: #9cb1c0; background: transparent;")
        right.addWidget(self._clock_lbl)
        self._date_lbl = QLabel("")
        self._date_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._date_lbl.setFont(QFont("Courier New", 6))
        self._date_lbl.setStyleSheet("color: #4d6978; background: transparent;")
        right.addWidget(self._date_lbl)
        lay.addLayout(right)
        return w

    def _tick_clock(self):
        self._clock_lbl.setText(time.strftime("%H:%M:%S"))
        self._date_lbl.setText(time.strftime("%b %d, %Y"))

    def _build_left_panel(self) -> QWidget:
        w = QWidget()
        w.setFixedWidth(_LEFT_W)
        w.setStyleSheet(f"background: #020a12; border-right: 1px solid {C.BORDER};")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(9, 8, 9, 8)
        lay.setSpacing(6)
        self._left_layout = lay

        brand_row = QHBoxLayout(); brand_row.setSpacing(5)
        brand = QLabel("MARK 54")
        brand.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        brand.setStyleSheet("color: #8095a3; background: transparent;")
        brand_row.addWidget(brand); brand_row.addStretch()
        badge = QLabel("◉")
        badge.setFont(QFont("Segoe UI Symbol", 8))
        badge.setStyleSheet("color: #3d91b8; background: transparent;")
        brand_row.addWidget(badge); lay.addLayout(brand_row)
        line = QFrame(); line.setFrameShape(QFrame.Shape.HLine); line.setStyleSheet("color: #173342;")
        lay.addWidget(line)

        self._left_diag_group = QWidget()
        diag_lay = QVBoxLayout(self._left_diag_group); diag_lay.setContentsMargins(0,0,0,0); diag_lay.setSpacing(6)
        diag = QLabel("▸  SYSTEM DIAGNOSTICS")
        diag.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        diag.setStyleSheet("color: #6d95aa; background: transparent;")
        diag_lay.addWidget(diag)

        # Two columns of small, readable graph cards; RAM spans the bottom row.
        self._bar_cpu = MetricBar("CPU", "#5a9fce")
        self._bar_gpu = MetricBar("GPU", "#56dde8")
        self._bar_net = MetricBar("NETWORK", "#58bde0")
        self._bar_tmp = MetricBar("TEMP", "#5a9ac9")
        self._bar_mem = MetricBar("RAM", "#4fcedb")
        grid = QGridLayout(); grid.setContentsMargins(0,0,0,0); grid.setHorizontalSpacing(5); grid.setVerticalSpacing(5)
        grid.addWidget(self._bar_cpu, 0, 0); grid.addWidget(self._bar_gpu, 0, 1)
        grid.addWidget(self._bar_net, 1, 0); grid.addWidget(self._bar_tmp, 1, 1)
        grid.addWidget(self._bar_mem, 2, 0, 1, 2)
        diag_lay.addLayout(grid)
        lay.addWidget(self._left_diag_group)

        io_panel = QWidget()
        self._io_panel = io_panel
        io_panel.setStyleSheet("background: #030f18; border: 1px solid #17384b; border-radius: 4px;")
        io = QVBoxLayout(io_panel); io.setContentsMargins(8, 6, 8, 6); io.setSpacing(3)
        cap = QLabel("I/O STATS")
        cap.setFont(QFont("Courier New", 7, QFont.Weight.Bold)); cap.setStyleSheet("color: #6e98aa; background: transparent;")
        io.addWidget(cap)
        self._uptime_lbl = QLabel("UP  --:--")
        self._uptime_lbl.setFont(QFont("Courier New", 7, QFont.Weight.Bold)); self._uptime_lbl.setStyleSheet("color: #56e5d8; background: transparent;")
        io.addWidget(self._uptime_lbl)
        self._proc_lbl = QLabel("PROC  --")
        self._proc_lbl.setFont(QFont("Courier New", 7)); self._proc_lbl.setStyleSheet("color: #7895a5; background: transparent;")
        io.addWidget(self._proc_lbl)
        self._io_temp_lbl = QLabel("TEMP  --")
        self._io_temp_lbl.setFont(QFont("Courier New", 7)); self._io_temp_lbl.setStyleSheet("color: #65a8d2; background: transparent;")
        io.addWidget(self._io_temp_lbl)
        lay.addWidget(io_panel)

        # Small telemetry card fills the lower-left dead space without adding
        # another wall of buttons. It is intentionally compact and factual.
        info = QWidget()
        self._info_panel = info
        info.setStyleSheet("background: #030f18; border: 1px solid #17384b; border-radius: 4px;")
        il = QVBoxLayout(info); il.setContentsMargins(8, 6, 8, 6); il.setSpacing(4)
        icap = QLabel("SYSTEM INFO")
        icap.setFont(QFont("Courier New", 7, QFont.Weight.Bold)); icap.setStyleSheet("color: #6e98aa; background: transparent;")
        il.addWidget(icap)
        info_grid = QGridLayout(); info_grid.setContentsMargins(0,0,0,0); info_grid.setHorizontalSpacing(10); info_grid.setVerticalSpacing(3)
        self._mini_labels = {}
        for r, (key, label) in enumerate((("cpu","CPU"),("mem","RAM"),("gpu","GPU"),("net","NET"),("tmp","TEMP"))):
            l = QLabel(label); l.setFont(QFont("Courier New", 6, QFont.Weight.Bold)); l.setStyleSheet("color:#527d8e;background:transparent;")
            v = QLabel("--"); v.setAlignment(Qt.AlignmentFlag.AlignRight); v.setFont(QFont("Courier New", 7, QFont.Weight.Bold)); v.setStyleSheet("color:#9fc9d4;background:transparent;")
            info_grid.addWidget(l, r, 0); info_grid.addWidget(v, r, 1); self._mini_labels[key] = v
        il.addLayout(info_grid)
        health = QProgressBar(); health.setRange(0,100); health.setValue(100); health.setTextVisible(False); health.setFixedHeight(4)
        health.setStyleSheet(f"QProgressBar {{ background:#081b27; border:0; border-radius:2px; }} QProgressBar::chunk {{ background:{C.PRI}; border-radius:2px; }}")
        self._health_bar = health; il.addSpacing(2); il.addWidget(health)
        lay.addWidget(info)

        # Compact lower-left telemetry block: fills the otherwise empty area
        # with useful system state without turning the reference-style panel into
        # a wall of controls. Values are updated from _update_metrics().
        aux = QWidget()
        self._aux_panel = aux
        aux.setStyleSheet("background: #030f18; border: 1px solid #17384b; border-radius: 4px;")
        al = QVBoxLayout(aux); al.setContentsMargins(8, 6, 8, 7); al.setSpacing(4)
        acap = QLabel("SYSTEM STATUS")
        acap.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        acap.setStyleSheet("color: #6e98aa; background: transparent;")
        al.addWidget(acap)
        self._aux_labels = {}
        for key, label, val in (("audio","AUDIO LINK","READY"), ("ai","AI CORE","ACTIVE"), ("session","SESSION","ONLINE")):
            row = QHBoxLayout(); row.setSpacing(5)
            a = QLabel(label); a.setFont(QFont("Courier New", 6, QFont.Weight.Bold)); a.setStyleSheet("color:#527d8e;background:transparent;")
            b = QLabel(val); b.setAlignment(Qt.AlignmentFlag.AlignRight); b.setFont(QFont("Courier New", 6, QFont.Weight.Bold)); b.setStyleSheet("color:#72ddea;background:transparent;")
            row.addWidget(a); row.addStretch(); row.addWidget(b)
            al.addLayout(row); self._aux_labels[key] = b
        self._load_bar = QProgressBar(); self._load_bar.setRange(0,100); self._load_bar.setValue(0); self._load_bar.setTextVisible(False); self._load_bar.setFixedHeight(3)
        self._load_bar.setStyleSheet(f"QProgressBar {{ background:#081b27; border:0; border-radius:1px; }} QProgressBar::chunk {{ background:#6f7cff; border-radius:1px; }}")
        al.addSpacing(1); al.addWidget(self._load_bar)
        lay.addWidget(aux)

        # Three restrained system-status tiles echo the reference's bottom-left
        # diagnostics without bringing back the old non-functional buttons.
        self._status_tiles_panel = QWidget()
        st_lay = QVBoxLayout(self._status_tiles_panel); st_lay.setContentsMargins(0,0,0,0); st_lay.setSpacing(0)
        stat_grid = QGridLayout(); stat_grid.setContentsMargins(0,0,0,0); stat_grid.setHorizontalSpacing(5); stat_grid.setVerticalSpacing(5)
        self._status_tiles = {}
        for col_i, (key, title, value, colour) in enumerate((
            ("core", "AI CORE", "ACTIVE", "#62e1ed"),
            ("sec", "SEC", "CLEARED", "#62abd8"),
            ("proto", "PROTO", "LIII", "#7895a5"),
        )):
            tile = QWidget()
            tile.setStyleSheet("background:#030f18; border:1px solid #17384b; border-radius:3px;")
            tl = QVBoxLayout(tile); tl.setContentsMargins(4,4,4,4); tl.setSpacing(1)
            t = QLabel(title); t.setAlignment(Qt.AlignmentFlag.AlignCenter); t.setFont(QFont("Courier New", 5, QFont.Weight.Bold)); t.setStyleSheet("color:#527d8e;background:transparent;border:none;")
            v = QLabel(value); v.setAlignment(Qt.AlignmentFlag.AlignCenter); v.setFont(QFont("Courier New", 6, QFont.Weight.Bold)); v.setStyleSheet(f"color:{colour};background:transparent;border:none;")
            tl.addWidget(t); tl.addWidget(v)
            stat_grid.addWidget(tile, 0, col_i); self._status_tiles[key] = v
        st_lay.addLayout(stat_grid)
        lay.addWidget(self._status_tiles_panel)

        lay.addStretch(1)
        return w

    def _build_right_panel(self) -> QWidget:
        w = QWidget(); w.setFixedWidth(_RIGHT_W); w.setStyleSheet("background:#010a11;border-left:1px solid #143244;")
        lay=QVBoxLayout(w); lay.setContentsMargins(9,9,9,9); lay.setSpacing(6)
        self._right_layout = lay
        def sec(txt):
            l=QLabel("▸ "+txt); l.setFont(QFont("Courier New",7,QFont.Weight.Bold)); l.setStyleSheet("color:#6f96aa;background:transparent;padding-bottom:2px;"); return l

        # Command deck deliberately lives at the top: manual input and interrupt are fallback controls.
        self._command_group=QWidget(); cg=QVBoxLayout(self._command_group); cg.setContentsMargins(7,7,7,7); cg.setSpacing(5); self._command_group.setStyleSheet(f"background:#030f18;border:1px solid {C.BORDER_B};border-radius:5px;")
        cg.addWidget(sec("COMMAND DECK")); cg.addLayout(self._build_input_row())
        self._interrupt_btn=JANEButton("▣  INTERRUPT SPEECH  [ESC]"); self._interrupt_btn.setFixedHeight(34); self._interrupt_btn.setFont(QFont("Courier New",7,QFont.Weight.Bold)); self._interrupt_btn.setCursor(Qt.CursorShape.PointingHandCursor); self._interrupt_btn.setStyleSheet(f"QPushButton {{ background:#072034;color:{C.PRI};border:1px solid {C.PRI_DIM};border-radius:3px; }} QPushButton:hover {{ background:#0b2b42;color:{C.WHITE}; }} QPushButton:pressed {{ background:#103a55; }}"); self._interrupt_btn.clicked.connect(self._do_interrupt); cg.addWidget(self._interrupt_btn)
        self._mute_btn=JANEButton("MICROPHONE ACTIVE"); self._mute_btn.setFixedHeight(31); self._mute_btn.setFont(QFont("Courier New",7,QFont.Weight.Bold)); self._mute_btn.setCursor(Qt.CursorShape.PointingHandCursor); self._mute_btn.clicked.connect(self._toggle_mute); self._style_mute_btn(); cg.addWidget(self._mute_btn)
        # Default layout keeps the command deck at the bottom; the Workbench can move it live.
        if self._ui_prefs.get("command_position", "bottom") == "top":
            lay.addWidget(self._command_group)

        self._activity_group=QWidget(); ag=QVBoxLayout(self._activity_group); ag.setContentsMargins(0,0,0,0); ag.setSpacing(5); ag.addWidget(sec("ACTIVITY LOG")); self._log=LogWidget(); ag.addWidget(self._log,1); lay.addWidget(self._activity_group,1)
        self._activity_sep=QFrame(); self._activity_sep.setFrameShape(QFrame.Shape.HLine); self._activity_sep.setStyleSheet("color:#163546;margin:1px 0;"); lay.addWidget(self._activity_sep)
        self._upload_group=QWidget(); ug=QVBoxLayout(self._upload_group); ug.setContentsMargins(0,0,0,0); ug.setSpacing(5); ug.addWidget(sec("FILE UPLOAD")); self._drop_zone=FileDropZone(); self._drop_zone.setFixedHeight(84); self._drop_zone.file_selected.connect(self._on_file_selected); ug.addWidget(self._drop_zone); self._file_hint=QLabel("No file loaded - drag & drop or click to browse"); self._file_hint.setFont(QFont("Courier New",6)); self._file_hint.setWordWrap(True); self._file_hint.setStyleSheet("color:#527181;background:transparent;"); ug.addWidget(self._file_hint); lay.addWidget(self._upload_group)

        # Utility cards live in the right column so they stay visible when enabled.
        self._utility_deck = QWidget()
        deck_lay = QGridLayout(self._utility_deck); deck_lay.setContentsMargins(0,0,0,0); deck_lay.setHorizontalSpacing(5); deck_lay.setVerticalSpacing(5)
        self._weather_widget = WeatherWidget()
        self._clock_widget = ClockWidget()
        self._battery_widget = BatteryWidget()
        self._network_widget = NetworkWidget()
        self._quick_notes_widget = QuickNotesWidget(self._ui_prefs.get("quick_notes",""))
        self._quick_notes_widget.changed.connect(self._on_quick_notes_changed)
        deck_lay.addWidget(self._weather_widget,0,0,1,2)
        deck_lay.addWidget(self._clock_widget,1,0)
        deck_lay.addWidget(self._battery_widget,1,1)
        deck_lay.addWidget(self._network_widget,2,0,1,2)
        deck_lay.addWidget(self._quick_notes_widget,3,0,1,2)
        lay.addWidget(self._utility_deck)

        if self._ui_prefs.get("command_position", "bottom") != "top":
            lay.addWidget(self._command_group)
        return w

    def _build_quick_drawer(self) -> QWidget:
        """Floating overlay panel shown when the ⚙ header button is toggled."""
        _BTN_STYLE_PRI = f"""
            QPushButton {{
                background: #00091a; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
                text-align: left; padding: 0 8px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """
        _BTN_STYLE_DIM = f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
                text-align: left; padding: 0 8px;
            }}
            QPushButton:hover {{ color: {C.PRI}; border: 1px solid {C.BORDER_B}; }}
        """

        w = QWidget(self.centralWidget())
        w.setObjectName("QuickDrawer")
        w.setStyleSheet(f"""
            QWidget#QuickDrawer {{
                background: {C.DARK};
                border: 1px solid {C.BORDER_B};
                border-top: none;
                border-radius: 0 0 6px 6px;
            }}
        """)
        w.hide()

        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(5)

        hdr = QLabel("◈ CONTROLS")
        hdr.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent; "
                          f"border-bottom: 1px solid {C.BORDER}; padding-bottom: 4px;")
        lay.addWidget(hdr)

        remote_btn = JANEButton("◉  REMOTE CONTROL")
        remote_btn.setFixedHeight(30)
        remote_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        remote_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        remote_btn.setStyleSheet(_BTN_STYLE_PRI)
        remote_btn.clicked.connect(self._open_remote)
        lay.addWidget(remote_btn)

        fs_btn = JANEButton("⛶  FULLSCREEN  [F11]")
        fs_btn.setFixedHeight(26)
        fs_btn.setFont(QFont("Courier New", 7))
        fs_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        fs_btn.setStyleSheet(_BTN_STYLE_DIM)
        fs_btn.clicked.connect(self._toggle_fullscreen)
        lay.addWidget(fs_btn)

        sc_btn = JANEButton("⊞  CREATE DESKTOP SHORTCUT")
        sc_btn.setFixedHeight(26)
        sc_btn.setFont(QFont("Courier New", 7))
        sc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        sc_btn.setStyleSheet(_BTN_STYLE_DIM)
        sc_btn.clicked.connect(self._create_desktop_shortcut)
        lay.addWidget(sc_btn)

        self._autostart_btn = JANEButton("◉  AUTO-START: OFF")
        self._autostart_btn.setFixedHeight(26)
        self._autostart_btn.setFont(QFont("Courier New", 7))
        self._autostart_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._autostart_btn.clicked.connect(self._toggle_autostart)
        lay.addWidget(self._autostart_btn)

        cust_btn = JANEButton("⚙  CUSTOMISE ASSISTANT")
        cust_btn.setFixedHeight(26)
        cust_btn.setFont(QFont("Courier New", 7))
        cust_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cust_btn.setStyleSheet(_BTN_STYLE_DIM)
        cust_btn.clicked.connect(self._open_customize)
        lay.addWidget(cust_btn)

        self._brief_btn = JANEButton()
        self._brief_btn.setFixedHeight(26)
        self._brief_btn.setFont(QFont("Courier New", 7))
        self._brief_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._brief_btn.clicked.connect(self._toggle_brief)
        lay.addWidget(self._brief_btn)

        # ── Wake word ──────────────────────────────────────────────────────────
        self._wake_btn = JANEButton()
        self._wake_btn.setFixedHeight(26)
        self._wake_btn.setFont(QFont("Courier New", 7))
        self._wake_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._wake_btn.clicked.connect(self._toggle_wake_word)
        lay.addWidget(self._wake_btn)

        self._wake_sleep_btn = JANEButton()
        self._wake_sleep_btn.setFixedHeight(26)
        self._wake_sleep_btn.setFont(QFont("Courier New", 7))
        self._wake_sleep_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._wake_sleep_btn.clicked.connect(self._tap_wake_manual)
        lay.addWidget(self._wake_sleep_btn)
        # Neutral placeholder now; the real state (which may load the model to
        # check readiness) is resolved lazily the first time the drawer opens.
        self._wake_btn.setText("🎙  WAKE WORD")
        self._wake_btn.setStyleSheet(_BTN_STYLE_DIM)
        self._wake_sleep_btn.hide()

        self._ptt_btn = JANEButton("🎚  PUSH-TO-TALK")
        self._ptt_btn.setFixedHeight(26)
        self._ptt_btn.setFont(QFont("Courier New", 7))
        self._ptt_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ptt_btn.setStyleSheet(_BTN_STYLE_DIM)
        self._ptt_btn.clicked.connect(self._toggle_ptt)
        lay.addWidget(self._ptt_btn)
        self._refresh_talk_btns()

        self._hud_btn = JANEButton("◉  HUD STYLE")
        self._hud_btn.setFixedHeight(26)
        self._hud_btn.setFont(QFont("Courier New", 7))
        self._hud_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hud_btn.setStyleSheet(_BTN_STYLE_DIM)
        self._hud_btn.clicked.connect(self._toggle_hud_style)
        lay.addWidget(self._hud_btn)
        self._refresh_hud_btn()

        audio_btn = JANEButton("🎧  AUDIO DEVICES")
        audio_btn.setFixedHeight(26)
        audio_btn.setFont(QFont("Courier New", 7))
        audio_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        audio_btn.setStyleSheet(_BTN_STYLE_DIM)
        audio_btn.clicked.connect(self._open_audio_devices)
        lay.addWidget(audio_btn)

        mem_btn = JANEButton("🧠  MEMORY")
        mem_btn.setFixedHeight(26)
        mem_btn.setFont(QFont("Courier New", 7))
        mem_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        mem_btn.setStyleSheet(_BTN_STYLE_DIM)
        mem_btn.clicked.connect(self._open_memory_panel)
        lay.addWidget(mem_btn)

        plugin_btn = JANEButton("🧩  PLUGINS")
        plugin_btn.setFixedHeight(26)
        plugin_btn.setFont(QFont("Courier New", 7))
        plugin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        plugin_btn.setStyleSheet(_BTN_STYLE_DIM)
        plugin_btn.clicked.connect(self._open_plugin_manager)
        lay.addWidget(plugin_btn)

        settings_btn = JANEButton("⚙  PLUGIN SETTINGS")
        settings_btn.setFixedHeight(26)
        settings_btn.setFont(QFont("Courier New", 7))
        settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        settings_btn.setStyleSheet(_BTN_STYLE_DIM)
        settings_btn.clicked.connect(self._open_plugin_settings)
        lay.addWidget(settings_btn)

        w.adjustSize()
        return w

    def _toggle_drawer(self, checked: bool):
        if checked:
            self._refresh_wake_btns()   # resolve wake state on open (lazy)
            self._position_quick_drawer()
            self._quick_drawer.show()
            self._quick_drawer.raise_()
        else:
            self._quick_drawer.hide()

    def _position_quick_drawer(self):
        if not hasattr(self, '_quick_drawer'):
            return
        _W = 220
        self._quick_drawer.setFixedWidth(_W)
        self._quick_drawer.adjustSize()
        self._quick_drawer.setGeometry(12, 54, _W, self._quick_drawer.sizeHint().height())

    def _set_widget_position(self, position: str):
        """Move the utility widget deck between bottom-left and right panel live."""
        position = "right" if str(position).lower() == "right" else "bottom_left"
        deck = getattr(self, "_utility_deck", None)
        if deck is None:
            return
        try:
            # Remove from whichever layout currently owns it.
            for lay in (getattr(self, "_left_layout", None), getattr(self, "_right_layout", None)):
                if lay is not None:
                    lay.removeWidget(deck)
            deck.setParent(self._right_panel if position == "right" else self._left_panel)
            if position == "right":
                lay = self._right_layout
            else:
                lay = self._left_layout
            # Keep it before the bottom stretch / command deck.
            insert_at = max(0, lay.count() - 1)
            lay.insertWidget(insert_at, deck)
            deck.show()
            deck.raise_()
            self._ui_prefs["widget_position"] = position
        except Exception as exc:
            try:
                self._log.append_log(f"SYS: Widget placement fallback — {exc}")
            except Exception:
                pass

    def _set_command_position(self, position: str):
        position = "top" if str(position).lower() == "top" else "bottom"
        lay = getattr(self, "_right_layout", None)
        group = getattr(self, "_command_group", None)
        if lay is None or group is None:
            return
        try:
            lay.removeWidget(group)
            if position == "top":
                lay.insertWidget(0, group)
            else:
                lay.addWidget(group)
            group.show()
        except Exception:
            pass

    def _build_input_row(self) -> QHBoxLayout:
        row = QHBoxLayout(); row.setSpacing(5)
        self._input = QLineEdit()
        self._input.setPlaceholderText("Type a command or question…")
        self._input.setFont(QFont("Courier New", 8))
        self._input.setFixedHeight(30)
        self._input.setStyleSheet(f"""
            QLineEdit {{
                background: #000d14; color: {C.WHITE};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 3px 7px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        self._input.returnPressed.connect(self._send)
        row.addWidget(self._input)

        send = JANEButton("▸")
        send.setFixedSize(30, 30)
        send.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        send.setCursor(Qt.CursorShape.PointingHandCursor)
        send.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        send.clicked.connect(self._send)
        row.addWidget(send)
        return row

    def _build_content_panel(self) -> QWidget:
        """
        Collapsible panel below the HUD — shows search results, news, briefings.
        Hidden by default; appears when show_content() is called.
        """
        w = QWidget()
        w.setObjectName("ContentPanel")
        w.setStyleSheet(f"""
            QWidget#ContentPanel {{
                background: {C.PANEL};
                border-top: 1px solid {C.BORDER_B};
            }}
        """)
        w.hide()

        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 7, 12, 8)
        lay.setSpacing(5)

        # ── header row ───────────────────────────────────────────────────────
        hdr = QHBoxLayout(); hdr.setSpacing(6)

        dot = QLabel("◈")
        dot.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        dot.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(dot)

        self._content_title_lbl = QLabel("BRIEFING")
        self._content_title_lbl.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._content_title_lbl.setStyleSheet(
            f"color: {C.PRI}; background: transparent; letter-spacing: 1px;"
        )
        hdr.addWidget(self._content_title_lbl)
        hdr.addStretch()

        self._content_ts_lbl = QLabel("")
        self._content_ts_lbl.setFont(QFont("Courier New", 7))
        self._content_ts_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        hdr.addWidget(self._content_ts_lbl)

        dismiss = JANEButton("DISMISS  ✕")
        dismiss.setFont(QFont("Courier New", 7))
        dismiss.setFixedHeight(18)
        dismiss.setCursor(Qt.CursorShape.PointingHandCursor)
        dismiss.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 2px; padding: 0 5px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
        """)
        dismiss.clicked.connect(w.hide)
        hdr.addWidget(dismiss)
        lay.addLayout(hdr)

        # ── separator ─────────────────────────────────────────────────────────
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};"); lay.addWidget(sep)

        # ── text display ──────────────────────────────────────────────────────
        self._content_display = QTextEdit()
        self._content_display.setReadOnly(True)
        self._content_display.setFont(QFont("Courier New", 8))
        self._content_display.setMinimumHeight(60)
        self._content_display.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._content_display.setStyleSheet(f"""
            QTextEdit {{
                background: {C.DARK};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 3px;
                padding: 6px 8px;
                selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{
                background: {C.BG}; width: 6px; border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B}; border-radius: 3px; min-height: 16px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0; border: none;
            }}
        """)
        lay.addWidget(self._content_display)

        return w

    def _show_content(self, title: str, text: str):
        """Slot — runs on Qt main thread. Updates and shows the content panel."""
        if not self._ui_prefs.get("show_content_panel", True):
            return
        import time as _time
        self._content_title_lbl.setText(title.upper()[:48])
        self._content_ts_lbl.setText(_time.strftime("%H:%M:%S"))
        self._content_display.setPlainText(text)
        self._content_display.moveCursor(
            self._content_display.textCursor().MoveOperation.Start
        )
        first_show = not self._content_panel.isVisible()
        self._content_panel.show()
        if first_show:
            total = self._center_split.height()
            self._center_split.setSizes([max(total - 220, 120), 220])

    @staticmethod
    def _esc(s) -> str:
        return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace("\n", "<br>"))

    def _build_quiz_panel(self) -> QWidget:
        w = QWidget()
        w.setObjectName("QuizPanel")
        w.setStyleSheet(f"QWidget#QuizPanel {{ background: {C.PANEL}; border-top: 1px solid {C.BORDER_B}; }}")
        w.hide()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 7, 12, 8)
        lay.setSpacing(6)

        hdr = QHBoxLayout(); hdr.setSpacing(6)
        dot = QLabel("◈"); dot.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        dot.setStyleSheet(f"color: {C.PRI}; background: transparent;"); hdr.addWidget(dot)
        self._quiz_title_lbl = QLabel("QUIZ")
        self._quiz_title_lbl.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._quiz_title_lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(self._quiz_title_lbl); hdr.addStretch()
        self._quiz_count_lbl = QLabel("")
        self._quiz_count_lbl.setFont(QFont("Courier New", 7)); self._quiz_count_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        hdr.addWidget(self._quiz_count_lbl)
        quit_btn = JANEButton("DISMISS  ✕")
        quit_btn.setFont(QFont("Courier New", 7)); quit_btn.setFixedHeight(18)
        quit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        quit_btn.setStyleSheet(f"QPushButton {{ background: transparent; color: {C.TEXT_DIM}; border: 1px solid {C.BORDER}; border-radius: 2px; padding: 0 5px; }} QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}")
        quit_btn.clicked.connect(self._hide_quiz); hdr.addWidget(quit_btn); lay.addLayout(hdr)
        rule=QFrame(); rule.setFixedHeight(1); rule.setStyleSheet(f"background:{C.BORDER};"); lay.addWidget(rule)
        self._quiz_q_lbl=QLabel(""); self._quiz_q_lbl.setWordWrap(True); self._quiz_q_lbl.setFont(QFont("Courier New", 9)); self._quiz_q_lbl.setStyleSheet(f"color:{C.WHITE}; background:transparent;"); lay.addWidget(self._quiz_q_lbl)
        self._quiz_answers=QWidget(); self._quiz_answers.setStyleSheet("background:transparent;")
        self._quiz_answers_lay=QVBoxLayout(self._quiz_answers); self._quiz_answers_lay.setContentsMargins(0,2,0,0); self._quiz_answers_lay.setSpacing(4); lay.addWidget(self._quiz_answers)
        self._quiz_note_lbl=QLabel(""); self._quiz_note_lbl.setWordWrap(True); self._quiz_note_lbl.setFont(QFont("Courier New",8)); self._quiz_note_lbl.hide(); lay.addWidget(self._quiz_note_lbl)
        foot=QHBoxLayout(); foot.addStretch()
        self._quiz_next_btn=self._quiz_btn("NEXT  →", primary=True); self._quiz_next_btn.setFixedWidth(110); self._quiz_next_btn.clicked.connect(self._quiz_next); self._quiz_next_btn.hide(); foot.addWidget(self._quiz_next_btn); lay.addLayout(foot)
        self._quiz=None
        return w

    def _quiz_btn(self, text: str, primary: bool = False) -> QPushButton:
        b=JANEButton(text); b.setFont(QFont("Courier New",8)); b.setCursor(Qt.CursorShape.PointingHandCursor); b.setMinimumHeight(24)
        edge=C.BORDER_B if primary else C.BORDER; col=C.PRI if primary else C.TEXT_MED
        b.setStyleSheet(f"QPushButton {{ background:{C.PANEL2}; color:{col}; border:1px solid {edge}; border-radius:2px; padding:3px 9px; text-align:left; }} QPushButton:hover {{ color:{C.WHITE}; border:1px solid {C.PRI_DIM}; }} QPushButton:disabled {{ color:{C.TEXT_DIM}; border:1px solid {C.BORDER}; }}")
        return b

    def _show_review(self, title: str, summary: str, findings, unclear):
        e=self._esc; parts=[f'<div style="color:{C.TEXT}; font-family:Courier New;">']
        if summary:
            parts.append(f'<div style="color:{C.WHITE}; border-left:2px solid {C.PRI}; padding-left:8px; margin-bottom:10px;">{e(summary)}</div>')
        for finding in (findings or []):
            sev=finding.get("severity"); mapping={"serious":("RED","▲"),"caution":("ACC2","●"),"note":("PRI_DIM","·")}; key,mark=mapping.get(sev,("PRI_DIM","·")); colour=getattr(C,key)
            parts.append(f'<div style="margin-bottom:11px;"><span style="color:{colour}; font-weight:bold;">{mark}</span> <span style="color:{C.WHITE}; font-weight:bold;">{e(finding.get("heading"))}</span>')
            if finding.get("detail"): parts.append(f'<div style="margin-left:12px;">{e(finding["detail"])}</div>')
            if finding.get("quote"): parts.append(f'<div style="margin-left:12px; color:{C.TEXT_DIM}; border-left:1px solid {C.BORDER}; padding-left:7px;">&ldquo;{e(finding["quote"])}&rdquo;</div>')
            if finding.get("suggestion"): parts.append(f'<div style="margin-left:12px; color:{C.PRI};">&rarr; {e(finding["suggestion"])}</div>')
            parts.append('</div>')
        if unclear:
            parts.append(f'<div style="margin-top:6px; border-top:1px solid {C.BORDER}; padding-top:7px; color:{C.TEXT_MED};">The document does not settle:</div>')
            for item in unclear: parts.append(f'<div style="margin-left:12px; color:{C.TEXT_MED};">&middot; {e(item)}</div>')
        parts.append('</div>')
        import time as _time
        self.hud.glance(0.0,-0.85,hold=1.3)
        self._content_title_lbl.setText((title or "Document")[:48]); self._content_ts_lbl.setText(_time.strftime("%H:%M:%S")); self._content_display.setHtml("".join(parts)); self._content_display.moveCursor(self._content_display.textCursor().MoveOperation.Start)
        first_show=not self._content_panel.isVisible(); self._content_panel.show()
        if first_show:
            total=self._center_split.height(); self._center_split.setSizes([max(total-260,120),260,0])

    def _show_quiz(self, topic: str, questions, grader=None):
        if not questions or not self._ui_prefs.get("show_quiz_panel", True): return
        self._quiz={"topic":topic or "","questions":list(questions),"grader":grader,"i":0,"results":[],"answered":False}
        self._quiz_title_lbl.setText((topic or "quiz").upper()[:48]); self.hud.glance(0.0,-0.85,hold=1.3)
        first_show=not self._quiz_panel.isVisible(); self._quiz_panel.show()
        if first_show:
            total=self._center_split.height(); self._center_split.setSizes([max(total-250,120),0,250])
        self._quiz_render()

    def _hide_quiz(self):
        self._quiz_panel.hide(); self._quiz=None

    def _quiz_clear_answers(self):
        while self._quiz_answers_lay.count():
            item=self._quiz_answers_lay.takeAt(0); w=item.widget()
            if w is not None: w.deleteLater()

    def _quiz_render(self):
        q=self._quiz["questions"][self._quiz["i"]]; n,total=self._quiz["i"]+1,len(self._quiz["questions"]); self._quiz_count_lbl.setText(f"{n} / {total}"); self._quiz_q_lbl.setText(q.get("question", "")); self._quiz_note_lbl.hide(); self._quiz_next_btn.hide(); self._quiz["answered"]=False; self._quiz_clear_answers()
        opts=q.get("options") or []
        if opts:
            for text in opts:
                b=self._quiz_btn("   "+text); b.clicked.connect(lambda _=False,t=text:self._quiz_submit(t)); self._quiz_answers_lay.addWidget(b)
        else:
            row=QWidget(); row.setStyleSheet("background:transparent;"); h=QHBoxLayout(row); h.setContentsMargins(0,0,0,0); h.setSpacing(6)
            field=QLineEdit(); field.setFont(QFont("Courier New",9)); field.setPlaceholderText("your answer"); field.setStyleSheet(f"QLineEdit {{ background:{C.PANEL2}; color:{C.WHITE}; border:1px solid {C.BORDER}; border-radius:2px; padding:4px 7px; }} QLineEdit:focus {{ border:1px solid {C.PRI_DIM}; }}")
            send=self._quiz_btn("ANSWER",primary=True); send.setFixedWidth(90); field.returnPressed.connect(lambda:self._quiz_submit(field.text())); send.clicked.connect(lambda:self._quiz_submit(field.text())); h.addWidget(field,stretch=1); h.addWidget(send); self._quiz_answers_lay.addWidget(row); field.setFocus()

    def _quiz_submit(self, given: str):
        if self._quiz is None or self._quiz["answered"]: return
        self._quiz["answered"]=True; q=self._quiz["questions"][self._quiz["i"]]; grader=self._quiz.get("grader"); verdict=None
        if callable(grader):
            try: verdict=grader(q,given)
            except Exception: verdict=None
        self._quiz["results"].append({"question":q.get("question", ""),"type":q.get("type", ""),"given":str(given or "").strip(),"answer":q.get("answer", ""),"correct":verdict})
        for i in range(self._quiz_answers_lay.count()):
            w=self._quiz_answers_lay.itemAt(i).widget()
            if w is not None: w.setEnabled(False)
        if verdict is True: mark,colour="✓  correct",C.GREEN
        elif verdict is False: mark,colour="✕  "+str(q.get("answer", "")),C.RED
        else: mark,colour="…  noted — I'll go over this one with you",C.ACC2
        note=q.get("note") or ""; self._quiz_note_lbl.setText(mark+("\n"+note if note else "")); self._quiz_note_lbl.setStyleSheet(f"color:{colour}; background:transparent;"); self._quiz_note_lbl.show()
        last=self._quiz["i"]>=len(self._quiz["questions"])-1; self._quiz_next_btn.setText("FINISH  →" if last else "NEXT  →"); self._quiz_next_btn.show(); self._quiz_next_btn.setFocus()

    def _quiz_next(self):
        if self._quiz is None: return
        if self._quiz["i"]>=len(self._quiz["questions"])-1: self._quiz_finish()
        else: self._quiz["i"]+=1; self._quiz_render()

    def _quiz_finish(self):
        if self._quiz is None: return
        topic=self._quiz["topic"]; results=self._quiz["results"]; right=sum(1 for r in results if r["correct"] is True); unsure=sum(1 for r in results if r["correct"] is None); total=len(results); self._quiz_panel.hide(); self._quiz=None
        self._log.append_log(f"QUIZ: {topic or 'quiz'} — {right}/{total} correct")
        lines=[f"[QUIZ_DONE] topic={topic or 'general'} | auto-marked {right}/{total} correct"+(f", {unsure} still need your marking" if unsure else "")]
        for i,r in enumerate(results,1):
            state="correct" if r["correct"] is True else "wrong" if r["correct"] is False else "NEEDS MARKING"; lines.append(f"{i}. [{r['type']}] {r['question']} | they answered: {r['given'] or '(blank)'} | expected: {r['answer']} | {state}")
        lines.append("Mark every question flagged NEEDS MARKING yourself — accept an answer that means the same thing. Then tell them how they did.")
        if self.on_text_command:
            threading.Thread(target=self.on_text_command,args=("\n".join(lines),),daemon=True).start()

    def _refresh_talk_btns(self):
        if not hasattr(self,"_ptt_btn"): return
        try:
            from core.hotkey import chord_label
            from memory.config_manager import get_push_to_talk_enabled
            ptt=get_push_to_talk_enabled(); label=f"🎚  PUSH-TO-TALK: {chord_label()}" if ptt else "🎚  PUSH-TO-TALK: OFF"
        except Exception:
            ptt=False; label="🎚  PUSH-TO-TALK"
        self._ptt_btn.setText(label)
        self._ptt_btn.setStyleSheet(f"QPushButton {{ background:{'#001a08' if ptt else 'transparent'}; color:{C.GREEN if ptt else C.TEXT_MED}; border:1px solid {C.GREEN_D if ptt else C.BORDER}; border-radius:3px; text-align:left; padding:0 8px; }} QPushButton:hover {{ color:{C.PRI}; border:1px solid {C.BORDER_B}; }}")
        self._refresh_settings_overlay()

    def _refresh_hud_btn(self):
        if not hasattr(self, "_hud_btn"):
            return
        labels = {"face": "🧑  HUD: ANIMATED FACE",
                  "reactor": "◉  HUD: REACTOR CORE",
                  "orbital": "◉  HUD: ORBITAL HUD"}
        mode = getattr(self.hud, "hud_style", _load_hud_ui_style())
        self._hud_btn.setText(labels.get(mode, labels["orbital"]))
        self._hud_btn.setStyleSheet(
            f"QPushButton {{ background:{C.PANEL2}; color:{C.PRI}; border:1px solid {C.BORDER_A}; "
            f"border-radius:3px; text-align:left; padding:0 8px; }} "
            f"QPushButton:hover {{ color:{C.WHITE}; border:1px solid {C.BORDER_B}; }}"
        )

    def _set_hud_style(self, mode: str):
        if mode not in _HUD_MODE_LABELS:
            mode = "orbital"
        try:
            _save_hud_ui_style(mode)
        except Exception:
            pass
        try:
            self.hud._set_hud_style_local(mode)
        except Exception:
            self.hud.hud_style = mode
            self.hud.update()
        self._refresh_hud_btn()
        ov = getattr(self, "_settings_overlay", None)
        if ov is not None:
            try:
                ov._refresh_state()
            except Exception:
                pass
        self._log.append_log(f"SYS: HUD switched to { _HUD_MODE_LABELS[mode].lower() }.")

    def _toggle_hud_style(self):
        current = getattr(self.hud, "hud_style", _load_hud_ui_style())
        order = ("face", "reactor", "orbital")
        try:
            nxt = order[(order.index(current) + 1) % len(order)]
        except ValueError:
            nxt = "orbital"
        self._set_hud_style(nxt)

    def _toggle_ptt(self):
        try:
            from memory.config_manager import get_push_to_talk_enabled, save_push_to_talk_enabled
            want=not get_push_to_talk_enabled(); save_push_to_talk_enabled(want); scope=None
            if self.on_push_to_talk:
                try: scope=self.on_push_to_talk(want)
                except Exception as exc: self._log.append_log(f"ERR: Push-to-talk failed — {exc}"); save_push_to_talk_enabled(False); want=False
            self._apply_ptt_shortcut(want and scope!="global"); self._refresh_talk_btns()
        except Exception as exc:
            self._log.append_log(f"ERR: Push-to-talk unavailable — {exc}")

    def _install_shortcut_guard(self):
        """Install one app-level Ctrl+Space guard for the lifetime of this window."""
        app = QApplication.instance()
        if app is None or self._ptt_event_filter_installed:
            return
        try:
            app.installEventFilter(self)
            self._ptt_event_filter_installed = True
        except Exception:
            pass

    def _apply_ptt_shortcut(self, needed: bool):
        """Enable/disable local PTT while the Ctrl+Space guard remains installed."""
        self._install_shortcut_guard()
        self._ptt_local_enabled = bool(needed)
        if not needed:
            self._ptt_pressed = False
            self._ptt_hold(False)

    @staticmethod
    def _is_ctrl_space(event) -> bool:
        try:
            return (isinstance(event, QKeyEvent)
                    and event.key() == Qt.Key.Key_Space
                    and bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
                    and not bool(event.modifiers() & (Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier)))
        except Exception:
            return False

    def eventFilter(self, obj, event):
        try:
            # Space can be released after Ctrl, so treat a pending local PTT
            # release as part of the same chord even when modifiers are gone.
            if (event.type() == QEvent.Type.KeyRelease
                    and isinstance(event, QKeyEvent)
                    and event.key() == Qt.Key.Key_Space
                    and getattr(self, "_ptt_pressed", False)
                    and self.isVisible()):
                self._ptt_pressed = False
                if self._ptt_local_enabled:
                    self._ptt_hold(False)
                return True

            if not (self.isVisible() and self._is_ctrl_space(event)):
                return super().eventFilter(obj, event)

            window = None
            try:
                window = obj.window() if hasattr(obj, "window") else None
            except Exception:
                window = None
            inside_jane = (window is self or obj is self)
            if not inside_jane:
                return super().eventFilter(obj, event)

            # Consume the chord at the application filter before Qt reaches
            # QAbstractButton/shortcut dispatch. It is swallowed even when PTT
            # is disabled so Ctrl+Space can never become a Settings activation.
            if event.type() == QEvent.Type.ShortcutOverride:
                event.accept()
                return True
            if event.type() == QEvent.Type.KeyPress:
                if self._ptt_local_enabled and not self._ptt_pressed:
                    self._ptt_pressed = True
                    self._ptt_hold(True)
                return True
            if event.type() == QEvent.Type.KeyRelease:
                self._ptt_pressed = False
                if self._ptt_local_enabled:
                    self._ptt_hold(False)
                return True
        except Exception:
            pass
        return super().eventFilter(obj, event)

    def _ptt_hold(self, held: bool):
        cb=getattr(self,"ptt_hold",None)
        if cb:
            try: cb(bool(held))
            except Exception: pass

    def closeEvent(self, event):
        """Stop background UI services cleanly when JANE closes."""
        try: self._cam_stop.set()
        except Exception: pass
        try:
            app = QApplication.instance()
            if app is not None and self._ptt_event_filter_installed:
                app.removeEventFilter(self)
                self._ptt_event_filter_installed = False
            self._ptt_local_enabled = False
            self._ptt_pressed = False
        except Exception: pass
        super().closeEvent(event)

    def _build_footer(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(18)
        w.setStyleSheet("background:#01080d; border-top:1px solid #0e2634;")
        grid = QGridLayout(w)
        grid.setContentsMargins(8, 0, 8, 0)
        grid.setHorizontalSpacing(0)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)

        left = QLabel("[F4] MUTE   [F11] FULLSCREEN   [ESC] INTERRUPT")
        left.setFont(QFont("Courier New", 5))
        left.setStyleSheet("color:#355665; background:transparent;")
        left.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(left, 0, 0)

        mid = QLabel(f"MARK 54  /  {self._assistant_name.upper()}  /  PERSONAL AI ASSISTANT")
        mid.setFont(QFont("Courier New", 5, QFont.Weight.Bold))
        mid.setStyleSheet("color:#446b7c; background:transparent;")
        mid.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mid.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        grid.addWidget(mid, 0, 1)

        right = QLabel("◀━━━━━━━━▶")
        right.setFont(QFont("Courier New", 5))
        right.setStyleSheet("color:#3c9da8; background:transparent;")
        right.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(right, 0, 2)
        return w
    def _on_file_selected(self, path: str):
        self._current_file = path
        p    = Path(path)
        cat  = _file_category(p)
        icon, _ = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size = _fmt_size(p.stat().st_size)
        self._file_hint.setText(f"{icon}  {p.name}  ·  {size}  ·  Tell {self._assistant_name} what to do with it")
        self._log.append_log(f"FILE: {p.name} ({size}) loaded")
        if self.on_text_command:
            msg = (
                f"[FILE_UPLOADED] path={path} | name={p.name} | "
                f"type={p.suffix.lstrip('.')} | size={size} | "
                f"Briefly tell the user you can see the file '{p.name}' "
                f"({size}) has been uploaded and ask what they'd like to do with it."
            )
            threading.Thread(target=self.on_text_command, args=(msg,), daemon=True).start()

    def notify_phone_connected(self) -> None:
        if self._remote_overlay and self._remote_overlay.isVisible():
            self._remote_overlay.mark_connected()


    def _open_remote(self):
        if not self.on_remote_clicked:
            self._log.append_log("SYS: Dashboard not running — remote unavailable.")
            return
        result = self.on_remote_clicked()
        if not result:
            self._log.append_log("SYS: Could not generate remote key.")
            return
        url    = result[0]
        key    = result[1]
        auto   = result[2] if len(result) >= 3 else ""
        manual = result[3] if len(result) >= 4 else url
        if self._remote_overlay:
            self._remote_overlay._do_close()
        cw  = self.centralWidget()
        ow, oh = RemoteKeyOverlay._OW, RemoteKeyOverlay._OH
        ov  = RemoteKeyOverlay(url, key, auto_login_url=auto, manual_url=manual,
                               expiry_secs=600, parent=cw)
        ov.set_new_key_callback(self.on_remote_clicked)
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.closed.connect(lambda: setattr(self, '_remote_overlay', None))
        ov.show()
        self._remote_overlay = ov
        self._log.append_log(f"SYS: Remote key generated — manual: {manual or url}")


    # ── Auto-start ──────────────────────────────────────────────────────────────

    def _check_autostart(self) -> bool:
        """Returns True if auto-start is currently registered on this OS."""
        try:
            if _OS == "Windows":
                import winreg
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
                try:
                    winreg.QueryValueEx(key, "JANE_AI")
                    return True
                except FileNotFoundError:
                    return False
                finally:
                    winreg.CloseKey(key)
            elif _OS == "Darwin":
                return (Path.home() / "Library" / "LaunchAgents"
                        / "com.jarvis.assistant.plist").exists()
            else:
                return (Path.home() / ".config" / "autostart" / "jarvis.desktop").exists()
        except Exception:
            return False

    def _toggle_autostart(self):
        currently_on = self._check_autostart()
        try:
            script = str(Path(__file__).resolve().parent / "main.py")
            if _OS == "Windows":
                import winreg
                reg = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_ALL_ACCESS)
                if currently_on:
                    winreg.DeleteValue(reg, "JANE_AI")
                else:
                    pythonw = Path(sys.executable).parent / "pythonw.exe"
                    exe = str(pythonw if pythonw.exists() else sys.executable)
                    winreg.SetValueEx(reg, "JANE_AI", 0, winreg.REG_SZ,
                                      f'"{exe}" "{script}"')
                winreg.CloseKey(reg)
            elif _OS == "Darwin":
                plist_dir = Path.home() / "Library" / "LaunchAgents"
                plist_dir.mkdir(parents=True, exist_ok=True)
                plist = plist_dir / "com.jarvis.assistant.plist"
                if currently_on:
                    plist.unlink(missing_ok=True)
                else:
                    plist.write_text(
                        '<?xml version="1.0" encoding="UTF-8"?>\n'
                        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                        '<plist version="1.0"><dict>\n'
                        '  <key>Label</key><string>com.jarvis.assistant</string>\n'
                        '  <key>ProgramArguments</key><array>\n'
                        f'    <string>{sys.executable}</string>\n'
                        f'    <string>{script}</string>\n'
                        '  </array>\n'
                        '  <key>RunAtLoad</key><true/>\n'
                        '</dict></plist>\n'
                    )
            else:
                desk_dir = Path.home() / ".config" / "autostart"
                desk_dir.mkdir(parents=True, exist_ok=True)
                desk = desk_dir / "jarvis.desktop"
                if currently_on:
                    desk.unlink(missing_ok=True)
                else:
                    desk.write_text(
                        "[Desktop Entry]\n"
                        f"Name={self._assistant_name}\n"
                        f"Exec={sys.executable} {script}\n"
                        "Type=Application\nTerminal=false\n"
                        "X-GNOME-Autostart-enabled=true\n"
                    )
            enabled = not currently_on
            self._update_autostart_btn(enabled)
            self._log.append_log(
                f"SYS: Auto-start {'enabled' if enabled else 'disabled'}.")
        except Exception as e:
            self._log.append_log(f"ERR: Auto-start failed — {e}")
        self._refresh_settings_overlay()

    def _update_autostart_btn(self, enabled: bool):
        if not hasattr(self, '_autostart_btn'):
            return
        if enabled:
            self._autostart_btn.setText("◉  AUTO-START: ON")
            self._autostart_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #001a08; color: {C.GREEN};
                    border: 1px solid {C.GREEN_D}; border-radius: 3px;
                }}
                QPushButton:hover {{ background: #002010; }}
            """)
        else:
            self._autostart_btn.setText("◉  AUTO-START: OFF")
            self._autostart_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
            """)

    def _toggle_brief(self):
        from memory.config_manager import get_brief_enabled, save_brief_enabled
        new_val = not get_brief_enabled()
        save_brief_enabled(new_val)
        self._update_brief_btn(new_val)
        self._refresh_settings_overlay()

    # ── Wake word settings ───────────────────────────────────────────────────

    def set_wake_word_phrase(self, value: str) -> str:
        """Set and persist the phrase, then notify the attached wake engine."""
        phrase = _save_wake_word_phrase(value)
        self._wake_word_phrase = phrase
        self._notify_wake_word_engine(phrase)
        self._refresh_settings_overlay()
        return phrase

    def process_wake_transcript(self, transcript: str) -> bool:
        """Return True when the current phrase occurs in a live transcript."""
        return self.wake_word_matches(transcript)

    def wake_word_matches(self, transcript: str) -> bool:
        """Match a speech-to-text result against the configured wake phrase."""
        try:
            norm=lambda v: re.sub(r"[^a-z0-9']+"," ",str(v or "").lower()).strip()
            phrase=norm(self._wake_word_phrase)
            text=norm(transcript)
            return bool(phrase) and phrase in text
        except Exception:
            return False

    def _notify_wake_word_engine(self, phrase: str) -> bool:
        """Push the saved phrase into the real detector using several non-invasive APIs."""
        phrase = _save_wake_word_phrase(phrase)
        notified = False

        cb = getattr(self, "on_wake_word_change", None)
        if callable(cb):
            try:
                cb(phrase)
                notified = True
            except Exception as exc:
                self._log.append_log(f"SYS: Wake callback rejected phrase — {exc}")

        # Prefer methods on the object owning an existing UI callback.
        for source in (getattr(self, "on_wake_toggle", None), getattr(self, "wake_get_state", None)):
            target = getattr(source, "__self__", None)
            if target is None:
                continue
            for name in (
                "set_wake_word", "set_wake_phrase", "set_wake_word_phrase",
                "configure_wake_word", "set_wake_phrase_text", "update_wake_word",
                "set_keyword", "set_keyword_phrase",
            ):
                fn = getattr(target, name, None)
                if callable(fn):
                    try:
                        fn(phrase)
                        notified = True
                        break
                    except Exception:
                        pass

        # Support module-level configurators and simple public state variables.
        try:
            import importlib
            mod = importlib.import_module("core.wake_word")
            for name in (
                "set_wake_word", "set_wake_phrase", "set_wake_word_phrase",
                "configure_wake_word", "set_wake_phrase_text", "update_wake_word",
                "set_keyword", "set_keyword_phrase",
            ):
                fn = getattr(mod, name, None)
                if callable(fn):
                    try:
                        fn(phrase)
                        notified = True
                        break
                    except Exception:
                        pass

            for attr in (
                "WAKE_WORD", "WAKE_PHRASE", "wake_word", "wake_phrase",
                "KEYWORD", "KEYWORD_PHRASE", "keyword",
            ):
                if hasattr(mod, attr) and isinstance(getattr(mod, attr), str):
                    setattr(mod, attr, phrase)
                    notified = True

            # Common detector instances. We only call explicit setter-style methods
            # and never alter model/path fields, so this remains safe across backends.
            for holder_name in ("detector", "wake_detector", "engine", "_detector", "_engine", "_wake"):
                holder = getattr(mod, holder_name, None)
                if holder is None:
                    continue
                for name in (
                    "set_wake_word", "set_wake_phrase", "set_keyword",
                    "configure_wake_word", "update_wake_word", "set_phrase",
                ):
                    fn = getattr(holder, name, None)
                    if callable(fn):
                        try:
                            fn(phrase)
                            notified = True
                            break
                        except Exception:
                            pass
        except Exception:
            pass
        return notified

    def _apply_wake_word_phrase(self):
        field = getattr(getattr(self, "_settings_overlay", None), "_wake_phrase_input", None)
        value = field.text() if field is not None else self._wake_word_phrase
        value = _save_wake_word_phrase(value)
        self._wake_word_phrase = value
        self._log.append_log(f"SYS: Wake phrase set — {value}")
        try:
            notified = self._notify_wake_word_engine(value)
        except Exception as exc:
            notified = False
            self._log.append_log(f"ERR: Wake detector update failed — {exc}")
        if notified:
            self._log.append_log(f"SYS: Wake detector updated — listening for {value}.")
        else:
            self._log.append_log("SYS: Phrase saved. No live wake-engine configurator is attached yet.")
        self._refresh_settings_overlay()

    def _wake_state(self) -> dict:
        """Combined state for the two wake-word buttons. Readiness is a cheap,
        deterministic on-disk check now (see core.wake_word.is_ready), so there
        is nothing to cache — the button never flickers to a stale value."""
        if self.wake_get_state:
            try:
                s = self.wake_get_state()
                return {"ready": bool(s.get("ready")),
                        "enabled": bool(s.get("enabled")),
                        "awake": bool(s.get("awake"))}
            except Exception:
                pass
        # Before JarvisLive has wired its callback (drawer built at startup).
        ready, enabled = False, False
        try:
            from core.wake_word import is_ready
            from memory.config_manager import get_wake_word_enabled
            ready, enabled = is_ready(), get_wake_word_enabled()
        except Exception:
            pass
        return {"ready": ready, "enabled": enabled, "awake": True}

    def _refresh_wake_btns(self):
        if not hasattr(self, '_wake_btn'):
            return
        st = self._wake_state()
        _on = f"""
            QPushButton {{ background: #001a08; color: {C.GREEN};
                border: 1px solid {C.GREEN_D}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ background: #002010; }}"""
        _off = f"""
            QPushButton {{ background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}"""
        self._wake_btn.setEnabled(True)
        try: self._wake_btn.setToolTip(f"Wake phrase: {self._wake_word_phrase}")
        except Exception: pass
        if not st["ready"]:
            self._wake_btn.setText("⬇  WAKE WORD: DOWNLOAD")
            self._wake_btn.setStyleSheet(_off)
            self._wake_sleep_btn.hide()
        elif st["enabled"]:
            self._wake_btn.setText("🎙  WAKE WORD: ON")
            self._wake_btn.setStyleSheet(_on)
            self._wake_sleep_btn.show()
            self._wake_sleep_btn.setText("😴  SLEEP NOW" if st["awake"] else "👂  WAKE NOW")
            self._wake_sleep_btn.setStyleSheet(_off)
        else:
            self._wake_btn.setText("🎙  WAKE WORD: OFF")
            self._wake_btn.setStyleSheet(_off)
            self._wake_sleep_btn.hide()
        self._refresh_settings_overlay()

    def _toggle_wake_word(self):
        st = self._wake_state()
        if not st["ready"]:
            # First time: download openwakeword + model in a worker thread.
            self._wake_btn.setText("⬇  DOWNLOADING… (one-time)")
            self._wake_btn.setEnabled(False)
            def _work():
                try:
                    from core.wake_word import install_and_download
                    ok, msg = install_and_download(
                        logger=lambda m: self._log_sig.emit(f"SYS: {m}"))
                except Exception as e:
                    ok, msg = False, str(e)
                if ok and self.on_wake_toggle:
                    try:
                        self.on_wake_toggle(True)   # auto-enable after a successful download
                    except Exception:
                        pass
                self._wake_dl_sig.emit(ok, msg)
            threading.Thread(target=_work, daemon=True).start()
            return
        # Already downloaded → just flip enabled/disabled through JarvisLive.
        if self.on_wake_toggle:
            try:
                self.on_wake_toggle(not st["enabled"])
            except Exception:
                pass
        self._refresh_wake_btns()

    def _on_wake_install_done(self, ok: bool, msg: str):
        self._log_sig.emit(f"SYS: {'Wake word ready.' if ok else 'Wake word setup failed: ' + msg}")
        self._refresh_wake_btns()

    def _tap_wake_manual(self):
        if self.on_wake_manual:
            try:
                self.on_wake_manual()
            except Exception:
                pass
        self._refresh_wake_btns()

    def _update_brief_btn(self, enabled: bool):
        if not hasattr(self, '_brief_btn'):
            return
        if enabled:
            self._brief_btn.setText("☀  MORNING BRIEF: ON")
            self._brief_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #001a08; color: {C.GREEN};
                    border: 1px solid {C.GREEN_D}; border-radius: 3px;
                    text-align: left; padding: 0 8px;
                }}
                QPushButton:hover {{ background: #002010; }}
            """)
        else:
            self._brief_btn.setText("☀  MORNING BRIEF: OFF")
            self._brief_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                    text-align: left; padding: 0 8px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
            """)


    def _refresh_settings_overlay(self):
        ov = getattr(self, "_settings_overlay", None)
        if ov is not None:
            try:
                ov._refresh_state()
            except Exception:
                pass

    # ── Master settings ─────────────────────────────────────────────────────────

    def _position_settings_overlay(self):
        ov = getattr(self, "_settings_overlay", None)
        if ov is None or not ov.isVisible():
            return
        cw = self.centralWidget()
        available_h = max(420, cw.height() - 20)
        ov.setFixedHeight(min(SettingsOverlay._MAX_H, available_h))
        margin = 12
        # The overlay is parented to centralWidget(), so these are its exact
        # local coordinates. Keep the entire panel on-screen and inside the HUD.
        x = max(0, (cw.width() - ov.width()) // 2)
        y = max(8, (cw.height() - ov.height()) // 2)
        ov.move(x, y)
        ov.raise_()

    def _open_settings(self):
        """Header gear toggles the centered settings panel; the panel CLOSE button remains available."""
        cw = self.centralWidget()
        ov = getattr(self, "_settings_overlay", None)
        if ov is None:
            ov = SettingsOverlay(self, parent=cw)
            self._settings_overlay = ov
        if ov.isVisible():
            ov.hide()
            return
        ov.show()
        self._position_settings_overlay()
        ov.raise_()
        QTimer.singleShot(0, self._refresh_settings_overlay)

    def _on_quick_notes_changed(self, text: str):
        self._ui_prefs["quick_notes"] = str(text or "")[:3000]
        try: self._ui_save_timer.start(650)
        except Exception: pass

    # ── UI Workbench ───────────────────────────────────────────────────────────

    def _open_ui_workbench(self):
        ov = getattr(self, "_ui_workbench", None)
        if ov is not None:
            ov.hide()
            ov.deleteLater()
        ov = UIWorkbenchOverlay(self._ui_prefs, parent=self.centralWidget())
        ov.changed.connect(self._apply_ui_preferences_live)
        ov.saved.connect(self._apply_ui_preferences)
        self._ui_workbench = ov
        self._centre_overlay(ov)

    def _apply_ui_preferences_live(self, prefs: dict):
        self._apply_ui_preferences(prefs, log=False, refresh_weather=False, persist=False)
        try: self._ui_save_timer.start(450)
        except Exception: pass

    def _persist_ui_preferences(self):
        try: self._ui_prefs = _save_ui_preferences(self._ui_prefs)
        except Exception: pass

    def _apply_ui_preferences(self, prefs: dict, log: bool = True, refresh_weather: bool = True, persist: bool = True):
        """Apply widget visibility/layout without rebuilding the assistant engine."""
        self._ui_prefs = _save_ui_preferences(prefs) if persist else _normalise_ui_preferences(prefs)
        self._ui_prefs["show_command_input"] = True
        self._set_widget_position(self._ui_prefs.get("widget_position", "bottom_left"))
        self._set_command_position(self._ui_prefs.get("command_position", "bottom"))

        # Side-panel widths are purely presentational. They are applied live without
        # touching the assistant engine, microphone loop, model or response path.
        try:
            if hasattr(self, "_left_panel"):
                self._left_panel.setFixedWidth(int(self._ui_prefs.get("left_width", _LEFT_W)))
            if hasattr(self, "_right_panel"):
                self._right_panel.setFixedWidth(int(self._ui_prefs.get("right_width", _RIGHT_W)))
        except Exception:
            pass

        # Grouped panel widgets. Hidden groups collapse their own layout space.
        for attr,key in (("_left_diag_group","show_diagnostics"),("_io_panel","show_io"),("_info_panel","show_system_info"),("_aux_panel","show_system_status"),("_status_tiles_panel","show_status_tiles"),("_activity_group","show_activity_log"),("_upload_group","show_file_upload"),("_command_group","show_command_input")):
            w=getattr(self,attr,None)
            if w is not None: w.setVisible(True if key=="show_command_input" else bool(self._ui_prefs.get(key,True)))
        for attr,key in (("_content_panel","show_content_panel"),("_quiz_panel","show_quiz_panel"),("_footer","show_footer")): 
            w=getattr(self,attr,None)
            if w is not None and not w.isVisible():
                # content/quiz can be hidden by the user; preserving their own
                # dynamic visibility still works when enabled.
                w.setVisible(False)
        # Don't force a hidden content/quiz back on. For enabled options, their
        # current visibility is left unchanged because these panels are event-driven.
        weather=getattr(self,"_weather_widget",None)
        if weather is not None:
            enabled_weather = bool(self._ui_prefs.get("show_weather",False))
            weather.setVisible(enabled_weather)
            location = self._ui_prefs.get("weather_location", "") or ""
            weather.set_location(location, refresh=(refresh_weather and enabled_weather and bool(location)))
        for attr,key in (("_clock_widget","show_clock"),("_battery_widget","show_battery"),("_network_widget","show_network_widget"),("_quick_notes_widget","show_quick_notes")):
            ww=getattr(self,attr,None)
            if ww is not None: ww.setVisible(bool(self._ui_prefs.get(key,False)))
        deck=getattr(self,"_utility_deck",None)
        if deck is not None:
            any_utility = any(bool(self._ui_prefs.get(k,False)) for k in ("show_weather","show_clock","show_battery","show_network_widget","show_quick_notes"))
            deck.setVisible(any_utility)
            if any_utility:
                deck.adjustSize()
        try:
            self.hud._ui_prefs = dict(self._ui_prefs)
            self.hud.update()
        except Exception:
            pass
        if log:
            self._log.append_log("SYS: UI workbench settings applied.")
        if getattr(self,"_settings_overlay",None) is not None:
            self._settings_overlay._refresh_state()

    # ── Customization ────────────────────────────────────────────────────────────

    def _reset_assistant_defaults(self):
        """Reset presentation identity without touching API/audio/behaviour settings."""
        self._apply_name_update(
            DEFAULT_ASSISTANT_NAME, "", DEFAULT_UI_COLOR, "", DEFAULT_ASSISTANT_TAGLINE
        )
        self._log.append_log("SYS: Assistant identity reset to defaults.")

    def _open_customize(self):
        cfg = _read_full_config()
        if self._customize_overlay:
            self._customize_overlay.hide()
        cw = self.centralWidget()
        ov = CustomizeOverlay(
            cfg.get("assistant_name", DEFAULT_ASSISTANT_NAME) or DEFAULT_ASSISTANT_NAME,
            cfg.get("user_name", ""),
            cfg.get("ui_color", "") or DEFAULT_UI_COLOR,
            cfg.get("voice_name", ""),
            cfg.get("assistant_tagline", DEFAULT_ASSISTANT_TAGLINE) or DEFAULT_ASSISTANT_TAGLINE,
            parent=cw,
        )
        ow, oh = CustomizeOverlay._OW, CustomizeOverlay._OH
        oh = min(oh, cw.height() - 16)
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.on_preview = self._preview_ui_color
        ov.saved.connect(self._apply_name_update)
        ov.show()
        self._customize_overlay = ov

    def _preview_ui_color(self, hex_color: str):
        """Live preview — paints the whole interface the new colour (does NOT write to config)."""
        old = current_palette()
        if apply_ui_accent(hex_color):
            retheme_all_widgets(old, current_palette())

    def _apply_name_update(self, name: str, user_name: str, ui_color: str = "",
                           voice: str = "", tagline: str = ""):
        """Update all name/theme-dependent UI elements and persist to config."""
        self._assistant_name = name.strip() or DEFAULT_ASSISTANT_NAME
        self._assistant_tagline = tagline.strip() or DEFAULT_ASSISTANT_TAGLINE
        self._user_name = str(user_name or "").strip()
        display = self._assistant_name.upper()
        self.setWindowTitle(f"{display} — {APP_VERSION}")
        self._title_lbl.setText(display)
        self._sub_lbl.setText(self._assistant_tagline)
        self._sub_brand_lbl.setText(f"MARK 54  ·  {display}  ·  PERSONAL AI ASSISTANT")
        if hasattr(self, "_header_title_left"):
            self._header_title_left.setText(f"◩  {display} - MARK 54")
        self._log._ai_name_lc = self._assistant_name.lower()
        self.hud._assistant_name = display
        self.hud._assistant_tagline = self._assistant_tagline

        color_changed = False
        if ui_color:
            old = current_palette()
            if apply_ui_accent(ui_color):
                # Live-paint the whole interface (panels, buttons, borders, HUD)
                retheme_all_widgets(old, current_palette())
                color_changed = old["PRI"] != C.PRI

        # Voice change → persist and, if it actually changed, rebuild the Live
        # session so the new voice takes effect (it's fixed at connect time).
        voice_changed = False
        if voice:
            from memory.config_manager import get_voice, save_voice
            if voice != get_voice():
                save_voice(voice)
                voice_changed = True

        try:
            data = _read_full_config()
            data["assistant_name"] = self._assistant_name
            data["assistant_tagline"] = self._assistant_tagline
            data["user_name"] = user_name.strip()
            if ui_color:
                data["ui_color"] = ui_color.strip().lower()
            API_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")
            self._log.append_log(f"SYS: Identity updated — {display}")
            self._refresh_settings_overlay()
            if color_changed:
                self._log.append_log(f"SYS: UI colour applied — {ui_color}")
            if voice_changed:
                self._log.append_log(f"SYS: Voice set — {voice}")
        except Exception as e:
            self._log.append_log(f"ERR: Config save failed — {e}")

        if voice_changed and self.on_voice_change:
            self.on_voice_change()
        if self.on_identity_change:
            try:
                self.on_identity_change()
            except Exception:
                pass

    def _centre_overlay(self, ov) -> None:
        """Place a floating overlay in the middle of the HUD and show it."""
        cw = self.centralWidget()
        ov.adjustSize()
        ov.setGeometry(
            max(0, (cw.width()  - ov.width())  // 2),
            max(0, (cw.height() - ov.height()) // 2),
            ov.width(), ov.height(),
        )
        ov.show()
        ov.raise_()

    # ── Audio devices ────────────────────────────────────────────────────────

    def _open_audio_devices(self):
        ov = AudioDeviceOverlay(parent=self.centralWidget())
        ov.picked.connect(self._on_audio_devices_applied)
        self._centre_overlay(ov)
        self._audio_overlay = ov            # keep a reference so it isn't GC'd

    def _on_audio_devices_applied(self):
        self._log.append_log("SYS: Audio devices updated.")
        if self.on_audio_device_change:
            self.on_audio_device_change()

    # ── Memory panel ─────────────────────────────────────────────────────────

    def _open_memory_panel(self):
        ov = MemoryOverlay(parent=self.centralWidget())
        self._centre_overlay(ov)
        self._memory_overlay = ov

    # ── Irreversible-action confirmation ─────────────────────────────────────

    def _show_confirm_banner(self, title: str, detail: str):
        self._hide_confirm_banner()
        ov = ConfirmBanner(title, detail, parent=self.centralWidget())
        ov.answered.connect(self._on_confirm_answered)
        self._centre_overlay(ov)
        self._confirm_overlay = ov

    def _hide_confirm_banner(self):
        ov = getattr(self, "_confirm_overlay", None)
        if ov is not None:
            ov.hide()
            ov.deleteLater()
            self._confirm_overlay = None

    def _on_confirm_answered(self, accepted: bool):
        # Tear the banner down first: core.confirm.resolve() may be about to
        # shut the machine down, and a live widget mid-callback is not where you
        # want to be when that happens.
        self._hide_confirm_banner()
        try:
            from core.confirm import resolve
            resolve(bool(accepted))
        except Exception as e:
            self._log.append_log(f"ERR: Confirmation failed — {e}")

    def _open_plugin_manager(self):
        plugins = self.get_plugins() if self.get_plugins else []
        cw = self.centralWidget()
        ov = PluginManagerOverlay(plugins, parent=cw)
        ov.adjustSize()
        ov.setGeometry(
            (cw.width()  - ov.width())  // 2,
            (cw.height() - ov.height()) // 2,
            ov.width(), ov.height(),
        )
        ov.show()
        ov.raise_()
        self._plugin_manager_overlay = ov   # keep a reference so it isn't GC'd

    def _open_plugin_settings(self):
        sections = self.get_plugin_settings() if self.get_plugin_settings else []
        cw = self.centralWidget()
        ov = PluginSettingsOverlay(sections, parent=cw)
        ow = PluginSettingsOverlay._OW
        oh = min(560, cw.height() - 16)
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.show()
        ov.raise_()
        self._plugin_settings_overlay = ov   # keep a reference so it isn't GC'd

    # ── Clipboard intelligence ───────────────────────────────────────────────────

    def _on_clipboard_changed(self):
        try:
            text = QApplication.clipboard().text().strip()
            if len(text) >= 10:
                self._clipboard_sig.emit(text)
        except Exception:
            pass

    def _show_clipboard_panel(self, text: str):
        self._clipboard_panel.show_clipboard(text)
        self._position_clipboard_panel()

    def _position_clipboard_panel(self):
        cw = self.centralWidget()
        pw = ClipboardPanel._W
        ph = self._clipboard_panel.sizeHint().height() or ClipboardPanel._H
        x = (cw.width() - pw) // 2
        y = cw.height() - ph - 6
        self._clipboard_panel.setGeometry(x, y, pw, ph)
        self._clipboard_panel.raise_()

    def _on_clipboard_action(self, cmd: str):
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(cmd,), daemon=True).start()

    # ────────────────────────────────────────────────────────────────────────────

    def _do_interrupt(self):
        if self.on_interrupt:
            self.on_interrupt()

    def _toggle_mute(self):
        self._muted = not self._muted
        self.hud.muted = self._muted
        self._style_mute_btn()
        if self._muted:
            self._apply_state("MUTED")
            self._log.append_log("SYS: Microphone muted.")
        else:
            self._apply_state("LISTENING")
            self._log.append_log("SYS: Microphone active.")

    def _style_mute_btn(self):
        if self._muted:
            self._mute_btn.setText("MICROPHONE MUTED")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #180b18; color: #ff709a;
                    border: 1px solid #a84d72; border-radius: 3px;
                }}
                QPushButton:hover {{ background: #241025; }}
            """)
        else:
            self._mute_btn.setText("MICROPHONE ACTIVE")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #08221f; color: #63e4db;
                    border: 1px solid #2d8f8a; border-radius: 3px;
                }}
                QPushButton:hover {{ background: #0b302c; }}
            """)

    def _send(self):
        txt = self._input.text().strip()
        if not txt: return
        self._input.clear()
        self._log.append_log(f"You: {txt}")
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(txt,), daemon=True).start()

    def _apply_state(self, state: str):
        self.hud.state    = state
        self.hud.speaking = (state == "SPEAKING")

    def _check_config(self) -> bool:
        if not API_FILE.exists(): return False
        try:
            d = json.loads(API_FILE.read_text(encoding="utf-8"))
            return bool(d.get("gemini_api_key")) and bool(d.get("os_system"))
        except Exception:
            return False

    def _show_setup(self):
        ov = SetupOverlay(self.centralWidget())
        cw = self.centralWidget()
        ow, oh = 460, 390
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.done.connect(self._on_setup_done)
        ov.show()
        self._overlay = ov

    def _on_setup_done(self, key: str, os_name: str):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        API_FILE.write_text(
            json.dumps({"gemini_api_key": key, "os_system": os_name}, indent=4),
            encoding="utf-8",
        )
        self._ready = True
        if self._overlay:
            self._overlay.hide()
            self._overlay = None
        self._apply_state("LISTENING")
        cfg = _read_full_config()
        self._assistant_name = cfg.get("assistant_name", DEFAULT_ASSISTANT_NAME) or DEFAULT_ASSISTANT_NAME
        self._assistant_tagline = cfg.get("assistant_tagline", DEFAULT_ASSISTANT_TAGLINE) or DEFAULT_ASSISTANT_TAGLINE
        self._user_name = str(cfg.get("user_name") or "").strip()
        if hasattr(self, "_title_lbl"):
            self._title_lbl.setText(self._assistant_name.upper())
        if hasattr(self, "_sub_lbl"):
            self._sub_lbl.setText(self._assistant_tagline)
        if hasattr(self, "_sub_brand_lbl"):
            self._sub_brand_lbl.setText(f"MARK 54  ·  {self._assistant_name.upper()}  ·  PERSONAL AI ASSISTANT")
        self.hud._assistant_name = self._assistant_name.upper()
        self.hud._assistant_tagline = self._assistant_tagline
        self._log.append_log(f"SYS: Initialised. OS={os_name.upper()}. {self._assistant_name} online.")


class _RootShim:
    def __init__(self, app: QApplication):
        self._app = app
    def mainloop(self):
        self._app.exec()
    def protocol(self, *_):
        pass


class JarvisUI:
    def __init__(self, face_path: str, size=None):
        self._app = QApplication.instance() or QApplication(sys.argv)
        self._app.setStyle("Fusion")
        self._win = MainWindow(face_path)
        self.root = _RootShim(self._app)
        self._win.show()

    @property
    def muted(self) -> bool:
        return self._win._muted

    @muted.setter
    def muted(self, v: bool):
        if v != self._win._muted:
            self._win._toggle_mute()

    @property
    def current_file(self) -> str | None:
        return self._win._drop_zone.current_file()

    @property
    def on_text_command(self):
        return self._win.on_text_command

    @on_text_command.setter
    def on_text_command(self, cb):
        self._win.on_text_command = cb

    @property
    def on_remote_clicked(self):
        return self._win.on_remote_clicked

    @on_remote_clicked.setter
    def on_remote_clicked(self, cb):
        self._win.on_remote_clicked = cb

    @property
    def on_interrupt(self):
        return self._win.on_interrupt

    @on_interrupt.setter
    def on_interrupt(self, cb):
        self._win.on_interrupt = cb

    @property
    def on_voice_change(self):
        return self._win.on_voice_change

    @on_voice_change.setter
    def on_voice_change(self, cb):
        self._win.on_voice_change = cb

    @property
    def on_identity_change(self):
        return self._win.on_identity_change

    @on_identity_change.setter
    def on_identity_change(self, cb):
        self._win.on_identity_change = cb

    @property
    def user_name(self) -> str:
        return self._win._user_name

    @property
    def on_audio_device_change(self):
        return self._win.on_audio_device_change

    @on_audio_device_change.setter
    def on_audio_device_change(self, cb):
        self._win.on_audio_device_change = cb

    def show_confirm(self, title: str, detail: str) -> None:
        """Thread-safe: raise the irreversible-action gate. Called from action
        handlers running in executor threads, so it goes through a signal."""
        self._win._confirm_sig.emit(str(title)[:120], str(detail)[:300])

    def hide_confirm(self) -> None:
        """Thread-safe: take the gate down."""
        self._win._confirm_hide_sig.emit()

    @property
    def get_plugins(self):
        return self._win.get_plugins

    @get_plugins.setter
    def get_plugins(self, cb):
        self._win.get_plugins = cb

    @property
    def get_plugin_settings(self):
        return self._win.get_plugin_settings

    @get_plugin_settings.setter
    def get_plugin_settings(self, cb):
        self._win.get_plugin_settings = cb

    @property
    def on_wake_toggle(self):
        return self._win.on_wake_toggle

    @on_wake_toggle.setter
    def on_wake_toggle(self, cb):
        self._win.on_wake_toggle = cb
        if callable(cb):
            try:
                self._win._notify_wake_word_engine(self._win._wake_word_phrase)
            except Exception:
                pass

    @property
    def on_wake_manual(self):
        return self._win.on_wake_manual

    @on_wake_manual.setter
    def on_wake_manual(self, cb):
        self._win.on_wake_manual = cb

    @property
    def wake_get_state(self):
        return self._win.wake_get_state

    @wake_get_state.setter
    def wake_get_state(self, cb):
        self._win.wake_get_state = cb
        if callable(cb):
            try:
                self._win._notify_wake_word_engine(self._win._wake_word_phrase)
            except Exception:
                pass

    @property
    def wake_word_phrase(self) -> str:
        return self._win._wake_word_phrase

    @wake_word_phrase.setter
    def wake_word_phrase(self, value: str):
        phrase = _save_wake_word_phrase(value)
        self._win._wake_word_phrase = phrase
        try:
            self._win._notify_wake_word_engine(phrase)
        except Exception:
            pass
        self._win._refresh_settings_overlay()

    def wake_word_matches(self, transcript: str) -> bool:
        return self._win.wake_word_matches(transcript)

    def process_wake_transcript(self, transcript: str) -> bool:
        """Return True when a transcript contains the currently configured phrase.

        The voice engine can call this before dispatching a normal user command,
        allowing custom phrases without changing the UI's public surface.
        """
        return self._win.wake_word_matches(transcript)

    def set_wake_word_phrase(self, value: str) -> str:
        return self._win.set_wake_word_phrase(value)

    @property
    def on_wake_word_change(self):
        return self._win.on_wake_word_change

    @on_wake_word_change.setter
    def on_wake_word_change(self, cb):
        self._win.on_wake_word_change = cb
        if callable(cb):
            try:
                cb(self._win._wake_word_phrase)
            except Exception:
                pass

    def set_audio_level(self, level: float) -> None:
        """Thread-safe: feed a 0.0–1.0 live audio level to the HUD waveform.
        Called from the audio threads; a plain float store is atomic under the
        GIL, so no signal/lock is needed for this cosmetic value."""
        try:
            self._win.hud.set_audio_level(level)
        except Exception:
            pass

    def set_speech_level(self, level: float) -> None:
        """Thread-safe output speech amplitude hook for TTS playback."""
        try:
            self._win.hud.set_speech_level(level)
        except Exception:
            pass

    def glance(self, dx: float, dy: float, hold: float = 1.1) -> None:
        """Thread-safe gaze request for the optional animated avatar."""
        try:
            self._win.hud.glance(dx, dy, hold)
        except Exception:
            pass

    @property
    def ptt_hold(self):
        return self._win.ptt_hold

    @ptt_hold.setter
    def ptt_hold(self, cb):
        self._win.ptt_hold = cb

    @property
    def on_push_to_talk(self):
        return self._win.on_push_to_talk

    @on_push_to_talk.setter
    def on_push_to_talk(self, cb):
        self._win.on_push_to_talk = cb

    def push_visemes(self, frames, hop: float, at: float) -> None:
        """Thread-safe speech-shape schedule from the new audio engine."""
        try:
            self._win.hud.push_visemes(frames, hop, at)
        except Exception:
            pass

    def show_quiz(self, topic: str, questions, grade=None) -> None:
        self._win._quiz_sig.emit(str(topic or ""), list(questions or []), grade)

    def hide_quiz(self) -> None:
        self._win._quiz_hide_sig.emit()

    def show_review(self, title: str, summary: str, findings, unclear=None) -> None:
        self._win._review_sig.emit(str(title or ""), str(summary or ""), list(findings or []), list(unclear or []))

    def notify_phone_connected(self) -> None:
        self._win.notify_phone_connected()

    def set_state(self, state: str):
        self._win._state_sig.emit(state)

    def write_log(self, text: str):
        self._win._log_sig.emit(text)

    def wait_for_api_key(self):
        while not self._win._ready:
            time.sleep(0.1)

    def show_content(self, title: str, text: str):
        """Thread-safe: display content in the panel below the HUD."""
        self._win._content_sig.emit(title[:48], text[:4000])

    def prompt_reconfig(self):
        """Thread-safe: show the API key setup overlay (e.g. after an auth error)."""
        self._win._ready = False
        self._win._reconfig_sig.emit()

    def show_camera_frame(self, img_bytes: bytes):
        """Thread-safe: show a webcam frame in the small overlay (screen captures)."""
        self._win._camera_sig.emit(img_bytes)

    def start_camera_stream(self) -> None:
        """Thread-safe: start live camera feed in the full HUD area."""
        self._win.start_camera_stream()

    def stop_camera_stream(self) -> None:
        """Thread-safe: stop the live camera feed."""
        self._win.stop_camera_stream()

    @property
    def assistant_name(self) -> str:
        return self._win._assistant_name

    def start_speaking(self):
        self.set_state("SPEAKING")

    def stop_speaking(self):
        if not self.muted:
            self.set_state("LISTENING")