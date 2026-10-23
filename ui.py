from __future__ import annotations

import json
import math
import os
import platform
import random
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

if platform.system() == "Windows":
    _WIN_HIDE: dict = {"creationflags": subprocess.CREATE_NO_WINDOW}
else:
    _WIN_HIDE: dict = {}

# Qt's video backend prints the ffmpeg stream banner — codec, bitrate, the
# whole signed googlevideo URL — to the console for every stream it opens. That
# is two screenfuls per video, it looks like an error to anyone reading the log,
# and it puts a URL carrying the viewer's IP address into a terminal people
# paste into bug reports. Silenced here, before Qt initialises its logging.
# An explicit setting from the user is left alone.
os.environ.setdefault("QT_LOGGING_RULES", "qt.multimedia.*=false")

from PyQt6.QtCore import (
    QEvent, QEasingCurve, QLineF, QMimeData, QObject, QParallelAnimationGroup, QPointF,
    QPoint, QPropertyAnimation, QRect, QRectF, QSize, QSizeF, Qt, QTimer,
    QUrl, pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush, QColor, QConicalGradient, QDragEnterEvent, QDropEvent, QFont,
    QFontDatabase, QKeyEvent, QKeySequence, QLinearGradient, QPainter, QPainterPath,
    QPen, QPixmap, QRadialGradient, QShortcut,
)
# Video playback for the HUD. Part of PyQt6, so it costs no new dependency —
# but the multimedia plugins are a separate piece of the Qt install and can be
# absent on a stripped-down system, so a failure here disables one feature
# rather than stopping JARVIS from starting.
try:
    from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PyQt6.QtMultimediaWidgets import QGraphicsVideoItem
    HAVE_VIDEO = True
except Exception as _e:            # noqa: BLE001 - reported, never fatal
    QAudioOutput = QMediaPlayer = QGraphicsVideoItem = None
    HAVE_VIDEO = False
    print(f"[Video] playback unavailable ({_e}) — the HUD will not show video.")

from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPushButton, QScrollArea,
    QSizePolicy, QSplitter, QGraphicsScene, QGraphicsView, QStackedWidget,
    QTextEdit, QVBoxLayout, QWidget, QProgressBar, QSlider,
)

try:
    from core.avatar import HoloAvatar
except Exception:      # pragma: no cover — HUD must never die over cosmetics
    HoloAvatar = None


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


# Single source of truth for the release name — the window title, the header
# badge and the readme must never disagree again.

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
    "left_width": 220,
    "right_width": 340,
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
        prefs["left_width"] = max(220, min(340, int(float(prefs.get("left_width", 220)))))
    except Exception:
        prefs["left_width"] = 220
    try:
        prefs["right_width"] = max(260, min(400, int(float(prefs.get("right_width", 340)))))
    except Exception:
        prefs["right_width"] = 340
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

_HUD_MODE_LABELS = {
    "face": "ANIMATED FACE",
    "reactor": "REACTOR CORE",
    "orbital": "ORBITAL HUD",
}

APP_VERSION  = "MARK LVI"
APP_PROTOCOL = APP_VERSION.split()[-1]

_DEFAULT_W, _DEFAULT_H = 980, 700
_MIN_W,     _MIN_H     = 820, 580
_LEFT_W  = 148
_RIGHT_W = 340

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"


class C:
    BG        = "#00060a"
    PANEL     = "#010d14"
    PANEL2    = "#010f18"
    BORDER    = "#0d3347"
    BORDER_B  = "#1a5c7a"
    BORDER_A  = "#0f4060"
    PRI       = "#00d4ff"
    PRI_DIM   = "#007a99"
    PRI_GHO   = "#001f2e"
    ACC       = "#ff6b00"
    ACC2      = "#ffcc00"
    GREEN     = "#00ff88"
    GREEN_D   = "#00aa55"
    RED       = "#ff3355"
    MUTED_C   = "#ff3366"
    TEXT      = "#8ffcff"
    TEXT_DIM  = "#3a8a9a"
    TEXT_MED  = "#5ab8cc"
    WHITE     = "#d8f8ff"
    DARK      = "#000d14"
    BAR_BG    = "#011520"


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
            time.sleep(2.0)

    def _update(self):
        cpu = psutil.cpu_percent(interval=None)
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

class HudCanvas(QWidget):
    """MARK LVI / JANE holographic core, designed around the reference HUD.

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

    def _on_screen(self) -> bool:
        """True only when this canvas can actually be seen by the user."""
        try:
            if not self.isVisible():
                return False
            win = self.window()
            return not (win.isMinimized() or win.isHidden())
        except Exception:
            return True      # never let a visibility check stop the HUD drawing

    # ── reactor core ─────────────────────────────────────────────────────────
    # The centrepiece for anyone who did not want a face looking back at them.
    # Built from the same budget as the head — software QPainter, no GPU — and
    # from the same principle: everything on it means something. The rings turn
    # at a rate the state sets, the spectrum ring is the real audio level, and
    # the core brightens with the voice. Nothing here is decoration that moves
    # for its own sake, which is what made the old glowing orb feel dead.

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
            if self._on_screen():
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
            #    JANE and a name someone renamed to "MAX" are very
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
                   f"MARK LVI  ·  {name}  ·  PERSONAL AI ASSISTANT")

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
            p.drawText(QRectF(0,H-22,W,12),Qt.AlignmentFlag.AlignCenter,f"MARK LVI  ·  {self._assistant_name or DEFAULT_ASSISTANT_NAME}  ·  PERSONAL AI ASSISTANT")
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
                   f"MARK LVI  ·  {name}  ·  PERSONAL AI ASSISTANT")

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

    def __init__(self, label: str, color: str = C.PRI, parent=None):
        super().__init__(parent)
        self._label = label
        self._color = color
        self._value = 0.0       # 0–100
        self._text  = "--"
        self.setFixedHeight(38)
        self.setMinimumWidth(80)

    def set_value(self, pct: float, text: str):
        v = max(0.0, min(100.0, pct))
        if v == self._value and text == self._text:
            return          # unchanged — skip the repaint
        self._value = v
        self._text  = text
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        p.setBrush(QBrush(qcol(C.PANEL2)))
        p.setPen(QPen(qcol(C.BORDER_A), 1))
        p.drawRoundedRect(QRectF(1, 1, W - 2, H - 2), 4, 4)

        bar_h   = 4
        bar_y   = H - bar_h - 5
        bar_w   = W - 12
        bar_x   = 6
        fill_w  = int(bar_w * self._value / 100)

        p.setBrush(QBrush(qcol(C.BAR_BG)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), 2, 2)

        if self._value > 85:
            bar_col = qcol(C.RED)
        elif self._value > 65:
            bar_col = qcol(C.ACC)
        else:
            bar_col = qcol(self._color)

        if fill_w > 0:
            p.setBrush(QBrush(bar_col))
            p.drawRoundedRect(QRectF(bar_x, bar_y, fill_w, bar_h), 2, 2)

        p.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(8, 5, 50, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._label)

        p.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        p.setPen(QPen(bar_col if self._text != "--" else qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(0, 4, W - 6, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._text)

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
        self.setFont(QFont("Courier New", 9))
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
        self._tmr.start(6)

    def _step(self):
        if self._pos < len(self._text):
            ch  = self._text[self._pos]
            cur = self.textCursor()
            fmt = cur.charFormat()
            col = {
                "you":  qcol(C.WHITE),
                "ai":   qcol(C.PRI),
                "err":  qcol(C.RED),
                "file": qcol(C.GREEN),
                # SYS lines are the bulk of the log. Amber fought the cyan HUD
                # and, being a fixed status colour rather than a hue-linked one,
                # stayed amber even after the accent picker retinted everything
                # else. TEXT_MED follows the theme and drops the contrast to a
                # level you can read past.
                "sys":  qcol(C.TEXT_MED),
            }.get(self._tag, qcol(C.TEXT))
            fmt.setForeground(QBrush(col))
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText(ch, fmt)
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            self._pos += 1
        else:
            self._tmr.stop()
            cur = self.textCursor()
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText("\n")
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            QTimer.singleShot(20, self._next)

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
            self, "Select a file for JARVIS", str(Path.home()),
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
        close_btn = QPushButton("✕")
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
        layout.addWidget(_lbl("Configure J.A.R.V.I.S. before first boot.", 9, color=C.PRI_DIM))
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
            btn = QPushButton(label)
            btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
            btn.setFixedHeight(32)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, k=key: self._sel(k))
            os_row.addWidget(btn)
            self._os_btns[key] = btn
        layout.addLayout(os_row)
        self._sel(detected)
        layout.addSpacing(12)

        init_btn = QPushButton("▸  INITIALISE SYSTEMS")
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
        close_btn = QPushButton("CLOSE")
        close_btn.setFixedHeight(30)
        close_btn.setFont(QFont("Courier New", 9))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
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

        btn = QPushButton()
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
                QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
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

        yes = QPushButton("▸  CONFIRM")
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

        no = QPushButton("CANCEL")
        no.setFixedHeight(32)
        no.setFont(QFont("Courier New", 9))
        no.setCursor(Qt.CursorShape.PointingHandCursor)
        no.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        no.clicked.connect(lambda: self.answered.emit(False))
        row.addWidget(no)
        lay.addLayout(row)

        # Default focus on CANCEL: if someone hits Enter without reading, the
        # safe answer wins.
        no.setDefault(True)
        no.setFocus()


class AudioDeviceOverlay(_HudOverlay):
    """Choose which microphone JARVIS listens to and which speakers it uses.

    Both audio streams used to open with no `device=` at all, so they always
    took the OS default — which on Windows moves by itself the moment a headset
    is plugged in. 'JARVIS can't hear me' is usually 'JARVIS is listening to the
    webcam'."""

    picked = pyqtSignal()      # emitted after Apply, when something changed

    # THESE EXIST BECAUSE QTimer.singleShot FROM A WORKER THREAD NEVER FIRES.
    #
    # The first version posted results back with `QTimer.singleShot(0, fn)` from
    # inside the audio thread. A QTimer belongs to the thread that creates it,
    # and that thread has no event loop — so the timer was created, never ran,
    # and the panel sat on "Speak now…" for ever while the recording finished
    # silently behind it. Reported as "I saw no sign of anything at all", which
    # is precisely what it looked like.
    #
    # A signal is the thread-safe way across: Qt queues it onto the receiving
    # object's thread, which for a widget is the GUI thread. Both carry only
    # plain values — never a widget — because the widgets belong to the GUI
    # thread and the worker must not touch them even to pass them along.
    _level_seen = pyqtSignal(int)          # 0-100, while recording
    _test_result = pyqtSignal(str, bool)   # sentence, was it good

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
            f"QComboBox:hover {{ border-color: {C.BORDER_B}; }}"
            f"QComboBox QAbstractItemView {{ background: #000d12; color: {C.TEXT}; "
            f"selection-background-color: {C.PRI_GHO}; border: 1px solid {C.BORDER}; }}"
        )

        _test_css = (
            f"QPushButton {{ background: transparent; color: {C.TEXT_DIM}; "
            f"border: 1px solid {C.BORDER}; border-radius: 3px; }}"
            f"QPushButton:hover {{ color: {C.PRI}; border-color: {C.PRI_DIM}; }}"
            f"QPushButton:disabled {{ color: {C.BORDER}; }}"
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

            # THE ONE CHECK THE STARTUP PROBE CANNOT MAKE.
            #
            # core/audio_devices decides at launch whether a device opens and
            # whether it moves audio, and it does that with silence. A muted
            # microphone, a level slider at zero and a privacy shutter all pass
            # every one of those tests and still deliver a flat line — the only
            # thing that separates them from a working microphone is a person
            # speaking into it.
            #
            # So the button is here, beside the dropdown, in the panel somebody
            # opens when JARVIS cannot hear them. It replaces a loose
            # `ses_testi.py` in the project root that nobody would ever find.
            btn_row = QHBoxLayout(); btn_row.setSpacing(6)
            btn_row.addWidget(box, 1)
            test = QPushButton("TEST")
            test.setFixedSize(64, 30)
            test.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            test.setCursor(Qt.CursorShape.PointingHandCursor)
            test.setStyleSheet(_test_css)
            btn_row.addWidget(test)
            lay.addLayout(btn_row)

            # A meter, because a static label is indistinguishable from a hang.
            # Three seconds of "Speak now" with nothing moving was reported as
            # "I saw no sign of anything" — correctly, since nothing on screen
            # responded to the user's voice.
            meter = QProgressBar()
            meter.setRange(0, 100)
            meter.setValue(0)
            meter.setTextVisible(False)
            meter.setFixedHeight(6)
            meter.setStyleSheet(
                f"QProgressBar {{ background: #000d12; border: 1px solid {C.BORDER}; "
                f"border-radius: 3px; }}"
                f"QProgressBar::chunk {{ background: {C.PRI}; border-radius: 2px; }}")
            meter.hide()
            lay.addWidget(meter)

            result = QLabel("")
            result.setWordWrap(True)
            result.setFont(QFont("Courier New", 7))
            result.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            lay.addWidget(result)

            test.clicked.connect(
                lambda _=False, k=kind, b=box, t=test, r=result, m=meter:
                    self._run_test(k, b, t, r, m))
            return box

        # Which row is being tested right now. One at a time — the button is
        # disabled for the duration — so a single slot is enough, and it keeps
        # widgets off the signal payload.
        self._active = None                # (button, label, meter)
        self._level_seen.connect(self._on_level)
        self._test_result.connect(self._on_result)

        self._in_box  = _row("MICROPHONE — what JARVIS hears you with",
                             "input", get_input_device())
        lay.addSpacing(4)
        self._out_box = _row("SPEAKERS — what JARVIS talks through",
                             "output", get_output_device())

        # ── Output volume, beside the output device ──────────────────────────
        #
        # Choosing "Realtek(R) Audio" here and finding it far too quiet used to
        # mean leaving this panel, opening the system sound settings, finding
        # the same endpoint again and moving a slider there. The volume belongs
        # next to the device it applies to.
        _vol_now = None
        try:
            from core.audio_devices import get_output_volume
            # The SELECTED output, not the system default. Somebody puts the
            # assistant on their speakers and keeps everything else in their
            # headphones; a slider labelled with one device that moves the
            # other is worse than no slider at all.
            _vol_now = get_output_volume(self._out_box.currentData() or "")
        except Exception:
            pass

        if _vol_now is not None:
            vcap = QLabel("OUTPUT VOLUME")
            vcap.setFont(QFont("Courier New", 8))
            vcap.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            lay.addSpacing(4)
            lay.addWidget(vcap)

            vrow = QHBoxLayout(); vrow.setSpacing(8)
            self._vol = QSlider(Qt.Orientation.Horizontal)
            self._vol.setRange(0, 100)
            self._vol.setValue(int(_vol_now))
            self._vol.setFixedHeight(22)
            self._vol.setStyleSheet(
                f"QSlider::groove:horizontal {{ background: #000d12; height: 4px; "
                f"border: 1px solid {C.BORDER}; border-radius: 2px; }}"
                f"QSlider::sub-page:horizontal {{ background: {C.PRI_DIM}; "
                f"border-radius: 2px; }}"
                f"QSlider::handle:horizontal {{ background: {C.PRI}; width: 10px; "
                f"margin: -5px 0; border-radius: 5px; }}")
            self._vol_lbl = QLabel(f"{int(_vol_now)}%")
            self._vol_lbl.setFont(QFont("Courier New", 8))
            self._vol_lbl.setFixedWidth(40)
            self._vol_lbl.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
            vrow.addWidget(self._vol, 1)
            vrow.addWidget(self._vol_lbl)
            lay.addLayout(vrow)

            # valueChanged fires for every pixel of a drag, and each one is a
            # COM call into the audio endpoint. sliderReleased fires once, when
            # the user has decided — the label still follows the handle so the
            # number moves while they drag.
            self._vol.valueChanged.connect(
                lambda v: self._vol_lbl.setText(f"{v}%"))
            self._vol.sliderReleased.connect(self._apply_volume)
            self._out_box.currentIndexChanged.connect(
                lambda _i: self._volume_follows_device())

        note = QLabel("Applying reconnects the session. Your conversation is kept.")
        note.setWordWrap(True)
        note.setFont(QFont("Courier New", 7))
        note.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        lay.addSpacing(6)
        lay.addWidget(note)

        row = QHBoxLayout(); row.setSpacing(8)
        ok = QPushButton("▸  APPLY")
        ok.setFixedHeight(32)
        ok.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        ok.setCursor(Qt.CursorShape.PointingHandCursor)
        ok.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px; }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
        """)
        ok.clicked.connect(self._apply)
        row.addWidget(ok)

        cancel = QPushButton("CLOSE")
        cancel.setFixedHeight(32)
        cancel.setFont(QFont("Courier New", 9))
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        cancel.clicked.connect(self.hide)
        row.addWidget(cancel)
        lay.addLayout(row)

    def _apply_volume(self):
        """Set the system output volume from the slider, off the Qt thread.

        The Windows path goes through COM, which can block for tens of
        milliseconds; that is not long enough to see but is long enough to
        stutter a drag, and there is no reason to make the interface wait for
        it.
        """
        import threading
        value = self._vol.value()
        device = self._out_box.currentData() or ""

        def _work():
            from core import audio_devices
            if not audio_devices.set_output_volume(value, device):
                print(f"[Audio] the volume of '{device}' could not be set "
                      f"to {value}%")

        threading.Thread(target=_work, daemon=True, name="audio-volume").start()

    def _volume_follows_device(self):
        """Re-read the slider when the chosen output changes.

        Each device keeps its own level, so leaving the handle where it was
        would show the previous device's volume against the new device's name —
        and the next drag would then jump it there.
        """
        if not hasattr(self, "_vol"):
            return
        try:
            from core.audio_devices import get_output_volume
            now = get_output_volume(self._out_box.currentData() or "")
        except Exception:
            now = None
        if now is None:
            return
        self._vol.blockSignals(True)
        self._vol.setValue(int(now))
        self._vol.blockSignals(False)
        self._vol_lbl.setText(f"{int(now)}%")

    def _run_test(self, kind, box, button, label, meter=None):
        """Record or play for real, off the Qt thread.

        Both calls block for seconds — recording waits for the user to speak,
        the tone waits for itself — and doing that on the Qt thread freezes the
        HUD, which is the mistake Mark LV already paid for once with a 2.1
        second wake-word import in the settings drawer.

        Results come back by signal, not by timer. See the note on
        `_level_seen` above for why that distinction is the whole feature.
        """
        import threading

        name = box.currentData() or ""
        self._active = (button, label, meter)
        button.setEnabled(False)
        label.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        label.setText("Speak now…" if kind == "input" else "Playing a tone…")
        if meter is not None and kind == "input":
            meter.setValue(0)
            meter.show()

        def _work():
            from core import audio_devices
            try:
                if kind == "input":
                    res = audio_devices.measure_input(
                        name,
                        on_level=lambda f: self._level_seen.emit(int(f * 100)))
                    text = f"peak {res['peak']}  —  {res['verdict']}"
                else:
                    res = audio_devices.play_tone(name)
                    text = res["verdict"]
                good = bool(res.get("ok", False))
            except Exception as e:
                text, good = f"The test itself failed: {e}", False
            self._test_result.emit(text, good)

        threading.Thread(target=_work, daemon=True, name=f"audio-test-{kind}").start()

    def _on_level(self, value: int):
        """A live level, already on the GUI thread thanks to the signal."""
        if self._active and self._active[2] is not None:
            self._active[2].setValue(max(0, min(100, value)))

    def _on_result(self, text: str, good: bool):
        if not self._active:
            return
        button, label, meter = self._active
        self._active = None
        button.setEnabled(True)
        if meter is not None:
            meter.setValue(0)
            meter.hide()
        label.setStyleSheet(
            f"color: {C.PRI if good else C.TEXT_DIM}; background: transparent;")
        label.setText(text)

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
    """Everything JARVIS has stored about you, and when it learned it.

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

        hdr = QLabel("🧠  WHAT JARVIS REMEMBERS")
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

                rm = QPushButton("✕")
                rm.setFixedSize(20, 20)
                rm.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
                rm.setCursor(Qt.CursorShape.PointingHandCursor)
                rm.setToolTip("Forget this")
                rm.setStyleSheet(f"""
                    QPushButton {{ background: transparent; color: {C.TEXT_DIM};
                        border: 1px solid {C.BORDER}; border-radius: 3px; }}
                    QPushButton:hover {{ color: {C.RED}; border-color: {C.RED}; }}
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

        close = QPushButton("CLOSE")
        close.setFixedHeight(30)
        close.setFont(QFont("Courier New", 9))
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
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


class _ScrollSafeCombo(QComboBox):
    """A dropdown that does not change when you scroll past it.

    Qt's default is that a combo box under the pointer swallows the wheel and
    steps its own value with it. Inside a scrolling panel that is not a
    convenience, it is a trap: scrolling down the list of settings silently
    changes whichever ones the pointer crossed on the way, and nothing on screen
    says so. It was reported as "selections move around when I scroll", and it
    is the same bug every Qt settings panel has until somebody fixes it.

    The wheel is only accepted when the box has been clicked into. Otherwise the
    event is passed up, which is what makes the panel scroll instead.
    """

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        # StrongFocus so it can still be tabbed to and used from the keyboard;
        # the wheel is gated on having focus, not on being focusable.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, e):
        if self.hasFocus():
            super().wheelEvent(e)
        else:
            e.ignore()


class ProvidersOverlay(_HudOverlay):
    """Choose what runs the assistant: Gemini Live, or your own stack.

    WHAT THE FIRST VERSION GOT WRONG, AND WHY IT IS WORTH WRITING DOWN
        It listed the PROVIDERS, and a provider here covers thirteen services at
        once because they all speak the same protocol. That is the right shape
        for the code — it is why `llm_openai.py` is one file instead of thirteen
        — and it is the wrong shape for a person.

        Watched in use: somebody who wanted to run a model on their own machine
        opened the dropdown, saw a single entry reading "OpenAI-compatible", and
        concluded the feature was not there. They were right to. Nobody looking
        for Ollama searches for the name of a protocol, and "OpenAI" is the last
        word you expect to find under "run it locally".

        So the panel now lists what people are actually looking for — Ollama, LM
        Studio, Groq, DeepSeek — from each provider's own `catalog`. One file
        behind the scenes, thirteen recognisable names in front of them.

    THE OTHER TWO CHANGES
        It was 560 pixels of stacked dropdowns with no grouping, which reads as
        a form to be endured rather than three decisions to be made. It is 760
        wide now, and the three parts of a conversation are three numbered boxes
        that say what they do in plain words.

        And every field a provider declares was shown at once — download folders,
        stability weights, thread counts. A setting almost nobody changes costs
        more in the confusion it adds to the five that matter than it saves for
        the one person who wants it, so `advanced` fields sit behind MORE.

    WHAT IT COSTS WHEN NOBODY OPENS IT
        Nothing. The button is built with the rest of the SETUP drawer, but the
        `providers` package is imported inside `__init__` of THIS class — on the
        click. Same lesson as the wake-word check that spent a measured 2.104
        seconds importing `openwakeword` on the Qt thread and made the drawer
        look broken.
    """

    changed = pyqtSignal()          # emitted on APPLY when the engine moved
    # Emitted from a worker thread when something the machine had to be asked
    # for — the installed voices — has arrived. A signal rather than a direct
    # call because the worker must not touch a widget.
    _live_ready = pyqtSignal()
    # (provider id, [(value, label), ...]) from a worker that went and asked an
    # endpoint what it offers.
    _models_ready = pyqtSignal(str, list)
    _OW = 760                       # the window's own minimum is 820

    # Which provider fills each slot, and what the slot is FOR — in the words
    # somebody configuring it would use, not the words the code uses.
    _SLOTS = (
        ("stt", "①", "HEARING",  "turns what you say into words"),
        ("llm", "②", "THINKING", "understands, decides, uses your tools"),
        ("tts", "③", "SPEAKING", "reads the answer out loud"),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        # Imported HERE, on the click — never at startup. See the class docstring.
        from memory.config_manager import (get_pipeline, get_provider_config,
                                           get_provider_mode, get_sidecar_first,
                                           get_sidecar_provider)
        from providers import registry

        self._registry = registry
        self._get_cfg = get_provider_config
        # Warm anything a provider can only learn by asking the machine, off
        # this thread. The installed voice list costs a PowerShell subprocess —
        # measured at 1.70 seconds — and the form asks for it on every rebuild,
        # so without this the panel freezes for most of two seconds every time
        # a dropdown is touched. Started here rather than at startup, because
        # nothing in this folder may run before somebody opens it.
        self._live_ready.connect(self._on_live_ready)
        self._models_ready.connect(self._on_models_ready)
        self._model_cache: dict[str, list] = {}
        self._fetching: set = set()
        for _kind in ("tts", "stt", "llm"):
            for _decl in registry.discover(_kind):
                try:
                    _mod = __import__(f"providers.{_decl['_module']}",
                                      fromlist=["prefetch"])
                    _pf = getattr(_mod, "prefetch", None)
                    if _pf:
                        _pf(self._live_ready.emit)
                except Exception:
                    pass
        self._edits: dict[str, dict] = {}       # provider id -> {key: (widget, Field)}
        self._status: dict[str, QLabel] = {}    # provider id -> status line
        self._more: dict[str, bool] = {}        # provider id -> advanced shown
        # Fields deliberately not drawn — the catalog already answered them, or
        # they are behind MORE. Tracked rather than asked of Qt: see _fields_into.
        self._hidden: dict[str, set] = {}
        self._picked: dict[str, str] = {}       # slot -> catalog entry id

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            ProvidersOverlay {{
                background: rgba(0, 6, 10, 249);
                border: 1px solid {C.BORDER_B};
                border-radius: 8px;
            }}
        """)
        self.setFixedWidth(self._OW)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 18, 22, 18)
        outer.setSpacing(10)

        title = QLabel("🔌  ENGINE  &  PROVIDERS")
        title.setFont(QFont("Courier New", 13, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        outer.addWidget(title)

        sub = QLabel("Choose what powers the conversation. "
                     "Gemini is the default and needs nothing set up here.")
        sub.setWordWrap(True)
        sub.setFont(QFont("Courier New", 8))
        sub.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        outer.addWidget(sub)

        # ── Mode: two cards, not a dropdown ─────────────────────────────────
        # A dropdown hides the option you did not choose. This is the one
        # decision on the panel that changes everything below it, and both
        # answers deserve to be readable at the same time.
        self._mode = get_provider_mode()
        cards = QHBoxLayout(); cards.setSpacing(10)
        self._card_gemini = self._mode_card(
            "☁  GEMINI LIVE",
            "One connection carries your voice, its voice, the transcript and "
            "the tools together. The fastest option — about half a second to "
            "the first word.",
            "gemini")
        self._card_local = self._mode_card(
            "🖥  MY OWN SETUP",
            "Your choice of hearing, thinking and speaking. Can run entirely on "
            "this computer, with nothing leaving it. Slower: 1.5 to 4 seconds.",
            "pipeline")
        cards.addWidget(self._card_gemini)
        cards.addWidget(self._card_local)
        outer.addLayout(cards)

        # ── Body ────────────────────────────────────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet(
            "QScrollArea { background: transparent; }"
            f"QScrollBar:vertical {{ background: {C.DARK}; width: 9px; "
            f"margin: 0; border-radius: 4px; }}"
            f"QScrollBar::handle:vertical {{ background: {C.BORDER_B}; "
            f"border-radius: 4px; min-height: 30px; }}"
            "QScrollBar::add-line, QScrollBar::sub-line { height: 0; }"
            "QScrollBar::add-page, QScrollBar::sub-page { background: none; }")
        self._scroll.setMinimumHeight(230)
        self._scroll.setMaximumHeight(360)
        outer.addWidget(self._scroll, 1)

        self._body = QWidget()
        self._body.setStyleSheet("background: transparent;")
        self._body_lay = QVBoxLayout(self._body)
        self._body_lay.setContentsMargins(0, 2, 8, 2)
        self._body_lay.setSpacing(10)
        self._scroll.setWidget(self._body)

        # Built BEFORE the body, because `_rebuild_body` writes into it — and
        # added to the layout after, so it still sits under the scroll area.
        self._footer = QLabel("")
        self._footer.setWordWrap(True)
        self._footer.setFont(QFont("Courier New", 7))
        self._footer.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")

        self._pipe = dict(get_pipeline())
        self._sidecar = get_sidecar_provider()
        self._sidecar_first = get_sidecar_first()
        self._rebuild_body()

        outer.addWidget(self._footer)

        row = QHBoxLayout(); row.setSpacing(8)
        apply_btn = QPushButton("▸  SAVE  &  APPLY")
        apply_btn.setFixedHeight(34)
        apply_btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        apply_btn.setStyleSheet(f"""
            QPushButton {{ background: {C.PRI_GHO}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 4px; }}
            QPushButton:hover {{ background: {C.BORDER}; border-color: {C.PRI}; }}
        """)
        apply_btn.clicked.connect(self._apply)
        row.addWidget(apply_btn, 2)

        close = QPushButton("CLOSE")
        close.setFixedHeight(34)
        close.setFont(QFont("Courier New", 9))
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 4px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        close.clicked.connect(self.hide)
        row.addWidget(close, 1)
        outer.addLayout(row)
        self._refresh_cards()

    # ── pieces ───────────────────────────────────────────────────────────────
    def _mode_card(self, title: str, body: str, value: str) -> QPushButton:
        b = QPushButton()
        b.setCheckable(True)
        b.setMinimumHeight(84)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setText(f"{title}\n\n{body}")
        b.setFont(QFont("Courier New", 7))
        b.clicked.connect(lambda _=0, v=value: self._set_mode(v))
        b.setProperty("modeValue", value)
        return b

    def _refresh_cards(self) -> None:
        for card in (self._card_gemini, self._card_local):
            on = card.property("modeValue") == self._mode
            card.setChecked(on)
            card.setStyleSheet(f"""
                QPushButton {{
                    text-align: left; padding: 10px 12px;
                    background: {C.PRI_GHO if on else 'transparent'};
                    color: {C.TEXT if on else C.TEXT_DIM};
                    border: 1px solid {C.PRI_DIM if on else C.BORDER};
                    border-radius: 5px;
                }}
                QPushButton:hover {{ border-color: {C.BORDER_B}; }}
            """)

    def _on_live_ready(self) -> None:
        """A machine-supplied list has arrived — redraw once so it appears."""
        if not self.isHidden():
            self._rebuild_body()

    # ── Asking an endpoint what it has, without being asked to ──────────────
    def _fetch_models(self, pid: str) -> None:
        """Go and get the model list in the background.

        Pressing LIST worked, and making somebody press it was still wrong: at
        the moment a local server is chosen, the one thing they need is the list
        of models on it, and the app can simply go and read it. A button whose
        only job is "now do the obvious next thing" is a button that should not
        exist.

        Off the interface thread, because it is a network call — a local server
        answers in milliseconds and a cloud one does not, and a panel that
        freezes while it finds out is worse than one that fills in a second
        later. Failure is silent: not reaching the endpoint yet is the normal
        state while somebody is still typing an address, and TEST is where a
        real answer about reachability belongs."""
        import threading

        if not pid or pid in self._fetching:
            return
        try:
            values = self._values(pid) or dict(self._get_cfg(pid))
        except Exception:
            values = dict(self._get_cfg(pid))
        if not values.get("base_url") and not values.get("api_key"):
            return
        self._fetching.add(pid)

        def work():
            items = []
            try:
                inst = self._registry.build(pid, values)
                if hasattr(inst, "models"):
                    items = [(m, m) for m in inst.models(timeout=6.0)]
                elif hasattr(inst, "voices"):
                    items = list(inst.voices())
            except Exception:
                items = []
            finally:
                self._fetching.discard(pid)
            if items:
                self._models_ready.emit(pid, items)

        threading.Thread(target=work, daemon=True).start()

    def _prepare(self, pid: str) -> None:
        """Let a provider get itself ready as soon as it is configured.

        Piper uses it to download the chosen voice. Generic on purpose: the
        panel asks whether the module has a `prepare`, and providers that do not
        need one are simply skipped — nothing here knows what a voice is."""
        import threading

        try:
            decl = self._registry.get_declaration(pid)
            mod = __import__(f"providers.{decl['_module']}", fromlist=["prepare"])
            fn = getattr(mod, "prepare", None)
        except Exception:
            fn = None
        if fn is None:
            return
        values = self._values(pid) or dict(self._get_cfg(pid))

        def report(ok, msg):
            colour = C.TEXT_DIM if ok is None else (C.GREEN if ok else C.RED)
            mark = "" if ok is None else ("✓  " if ok else "✕  ")
            QTimer.singleShot(0, lambda: self._say(pid, mark + msg, colour))

        threading.Thread(target=lambda: fn(values, report), daemon=True).start()

    def _on_models_ready(self, pid: str, items: list) -> None:
        self._model_cache[pid] = list(items)
        if self.isHidden():
            return
        key = "voice_id" if any(v != l for v, l in items) else "model"
        entry = self._edits.get(pid, {}).get(key)
        if entry is None:
            entry = self._edits.get(pid, {}).get("model")
        if entry is None or not isinstance(entry[0], QComboBox):
            return
        box = entry[0]
        keep = box.currentText()
        box.clear()
        for value, label in items:
            box.addItem(str(label), str(value))
        j = box.findData(keep)
        if j >= 0:
            box.setCurrentIndex(j)
        elif keep:
            box.setCurrentText(keep)
        self._say(pid, f"{len(items)} model{'s' if len(items) != 1 else ''} "
                       f"found on this server — pick one above.", C.TEXT_DIM)

    def _set_mode(self, value: str) -> None:
        if value == self._mode:
            self._refresh_cards()
            return
        self._mode = value
        self._remember_edits()
        self._refresh_cards()
        QTimer.singleShot(0, self._rebuild_body)

    def _box(self, title_widgets: list) -> tuple[QWidget, QVBoxLayout]:
        """A bordered section. Grouping is the difference between three
        decisions and eleven controls."""
        w = QWidget()
        w.setStyleSheet(f"background: {C.PANEL}; border: 1px solid {C.BORDER}; "
                        f"border-radius: 5px;")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 10, 12, 11)
        lay.setSpacing(5)
        for tw in title_widgets:
            lay.addWidget(tw)
        return w, lay

    def _label(self, text: str, size: int = 8, colour: str = None,
               bold: bool = False) -> QLabel:
        lb = QLabel(text)
        lb.setWordWrap(True)
        lb.setFont(QFont("Courier New", size,
                         QFont.Weight.Bold if bold else QFont.Weight.Normal))
        lb.setStyleSheet(f"color: {colour or C.TEXT_MED}; background: transparent; "
                         f"border: none;")
        return lb

    def _combo(self) -> QComboBox:
        box = _ScrollSafeCombo()
        box.setFont(QFont("Courier New", 8))
        box.setFixedHeight(29)
        box.setStyleSheet(
            f"QComboBox {{ background: {C.DARK}; color: {C.TEXT}; "
            f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 3px 9px; }}"
            f"QComboBox:hover {{ border-color: {C.BORDER_B}; }}"
            f"QComboBox::drop-down {{ border: none; width: 18px; }}"
            f"QComboBox QAbstractItemView {{ background: {C.DARK}; color: {C.TEXT}; "
            f"selection-background-color: {C.PRI_GHO}; "
            f"border: 1px solid {C.BORDER_B}; padding: 2px; }}")
        return box

    def _line(self, text: str = "") -> QLineEdit:
        w = QLineEdit(text)
        w.setFont(QFont("Courier New", 8))
        w.setFixedHeight(27)
        w.setStyleSheet(
            f"QLineEdit {{ background: {C.DARK}; color: {C.TEXT}; "
            f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 2px 9px; }}"
            f"QLineEdit:focus {{ border-color: {C.PRI_DIM}; }}")
        return w

    def _btn(self, text: str, slot, colour: str = None) -> QPushButton:
        b = QPushButton(text)
        b.setFixedHeight(25)
        b.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        col = colour or C.TEXT_MED
        b.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {col};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 0 12px; }}
            QPushButton:hover {{ border-color: {C.BORDER_B}; color: {C.TEXT}; }}
        """)
        b.clicked.connect(slot)
        return b

    # ── catalog ──────────────────────────────────────────────────────────────
    def _catalog(self, kind: str) -> list[tuple[str, str, dict, dict]]:
        """(provider id, entry id, entry, declaration) for one slot.

        A provider with no catalog contributes itself as a single entry, so
        nothing has to declare one to appear."""
        out = []
        for decl in self._registry.discover(kind):
            entries = decl.get("catalog") or (
                {"id": decl["id"], "label": decl["label"],
                 "note": decl.get("hint", ""), "local": None, "values": {}},)
            for e in entries:
                out.append((decl["id"], e["id"], e, decl))
        return out

    def _current_entry(self, kind: str, pid: str) -> str:
        """Which catalog entry is selected.

        STORED, not derived. The first version worked it out by comparing the
        saved address against each entry's — which is elegant and wrong, and it
        showed up immediately: pick "Whisper on this computer", reopen the
        panel, and the box reads "— not set —" while the form beneath it is
        fully drawn. That entry's address is the empty string, so any config
        whose address had ever been touched matched nothing and fell through to
        no selection at all.

        The entry is a CHOICE somebody made, not a fact about the settings.
        Choices get stored. The derivation is kept only as a fallback for a
        config written before this key existed."""
        if not pid:
            return ""
        cfg = self._get_cfg(pid)
        saved = str(cfg.get("catalog") or "").strip()
        entries = [(eid, entry) for _p, eid, entry, _d in self._catalog(kind)
                   if _p == pid]
        if saved and any(eid == saved for eid, _e in entries):
            return saved

        # Written before the key existed: work it out once, from the address.
        best = ""
        for eid, entry in entries:
            vals = entry.get("values") or {}
            if not vals:
                best = best or eid            # a catch-all like "Something else"
                continue
            if all(str(cfg.get(k, "")).rstrip("/") == str(v).rstrip("/")
                   for k, v in vals.items() if k in ("base_url", "backend")):
                return eid
        return best or (entries[0][0] if len(entries) == 1 else "")

    # ── body ─────────────────────────────────────────────────────────────────
    def _clear(self, lay) -> None:
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
            elif item.layout() is not None:
                self._clear(item.layout())

    def _rebuild_body(self) -> None:
        # Keep whatever is in the boxes before they are destroyed.
        #
        # A redraw can be triggered by things the person did not do: a model
        # list arriving from a background fetch, a slot changing, the panel
        # settling after a click. Every one of those replaces the widgets, and
        # anything typed but not yet saved went with them — most visibly a model
        # name entered by hand, which came back empty on SAVE with no
        # indication that it had been dropped.
        self._remember_edits()
        self._clear(self._body_lay)
        self._edits.clear()
        self._status.clear()

        if self._mode == "pipeline":
            for slot, num, name, what in self._SLOTS:
                self._body_lay.addWidget(self._slot_box(slot, num, name, what))
        else:
            w, lay = self._box([])
            lay.addWidget(self._label(
                "Nothing to set up. Your voice goes to Gemini and comes back as "
                "speech over one connection, with the transcript and every tool "
                "call on the same wire.", 8, C.TEXT_DIM))
            lay.addWidget(self._label(
                "Pick MY OWN SETUP above to run it on your own machine, or "
                "through another company's model.", 7, C.TEXT_DIM))
            self._body_lay.addWidget(w)

        self._body_lay.addWidget(self._sidecar_box_widget())
        self._body_lay.addStretch(1)
        self._update_footer()

    def _slot_box(self, slot: str, num: str, name: str, what: str) -> QWidget:
        head = self._label(f"{num}  {name}", 9, C.PRI, bold=True)
        w, lay = self._box([head, self._label(what, 7, C.TEXT_DIM)])

        entries = self._catalog(slot)
        box = self._combo()
        box.addItem("— not set —", ("", ""))
        for pid, eid, entry, decl in entries:
            label = entry["label"]
            if entry.get("local") is True:
                label = f"🖥  {label}"
            elif entry.get("local") is False:
                label = f"☁  {label}"
            else:
                label = f"    {label}"
            note = entry.get("note") or ""
            if not decl.get("_available", True):
                note = (note + " · needs install").strip(" ·")
            box.addItem(f"{label}{'   —  ' + note if note else ''}", (pid, eid))

        pid_now = self._pipe.get(slot, "")
        eid_now = self._picked.get(slot) or self._current_entry(slot, pid_now)
        idx = box.findData((pid_now, eid_now)) if pid_now else 0
        if pid_now and idx < 0:
            # A provider IS chosen — the form for it is drawn right below — so
            # the box must never read "not set". It did, and it was the first
            # thing anybody noticed: an entry whose settings no longer match
            # any catalog row (upgraded from an older layout, or edited by
            # hand) resolved to nothing at all, and the panel contradicted
            # itself on screen. Falling back to that provider's first row is
            # always closer to the truth than claiming nothing is selected.
            for i in range(box.count()):
                data = box.itemData(i)
                if data and data[0] == pid_now:
                    idx = i
                    self._picked[slot] = data[1]
                    break
        box.setCurrentIndex(idx if idx >= 0 else 0)
        box.currentIndexChanged.connect(
            lambda _i, s=slot, b=box: self._slot_changed(s, b))
        if pid_now:
            QTimer.singleShot(0, lambda p=pid_now: self._prepare(p))
        lay.addWidget(box)

        if pid_now:
            self._fields_into(lay, slot, pid_now, eid_now)
            # If a list was already fetched, use it; if not, go and get one.
            if self._model_cache.get(pid_now):
                QTimer.singleShot(0, lambda p=pid_now: self._on_models_ready(
                    p, self._model_cache[p]))
            else:
                QTimer.singleShot(0, lambda p=pid_now: self._fetch_models(p))
        return w

    def _slot_changed(self, slot: str, box: QComboBox) -> None:
        pid, eid = box.currentData() or ("", "")
        self._remember_edits()
        self._pipe[slot] = pid
        self._picked[slot] = eid
        if pid and eid:
            # Applying the shortcut is the entire point of the catalog: pick
            # Ollama and the address is already right. The choice itself is
            # saved alongside it — see _current_entry for why.
            from memory.config_manager import save_provider_config
            for _p, _e, entry, _d in self._catalog(slot):
                if _p == pid and _e == eid:
                    values = dict(entry.get("values") or {})
                    values["catalog"] = eid
                    save_provider_config(pid, values)
                    break
            self._fetch_models(pid)
        QTimer.singleShot(0, self._rebuild_body)

    def _fields_into(self, lay, slot: str, pid: str, eid: str) -> None:
        from providers.base import Field

        decl = self._registry.get_declaration(pid)
        if decl is None:
            return
        stored = dict(self._get_cfg(pid))
        entry = next((e for p, i, e, _ in self._catalog(slot)
                      if p == pid and i == eid), {})
        is_local = entry.get("local") is True
        entry_values = entry.get("values") or {}
        # An entry that MENTIONS the address has answered it — including
        # answering it with "", which is what the local speech recogniser does
        # because it does not use one at all. Testing the value rather than the
        # presence of the key is what put an empty "API address" box under
        # "Whisper on this computer", inviting somebody to fill in a field that
        # backend never reads.
        custom = "base_url" not in entry_values

        # Three buckets, tracked explicitly.
        #
        # `isHidden()` is NOT a usable test for "I hid this on purpose": a widget
        # that has not been added to a layout yet is already hidden, so asking it
        # returned True for every field and the form came up completely empty.
        # Which of these a field lands in is a decision made here, so it is kept
        # here rather than inferred from Qt afterwards.
        shown: dict = {}        # drawn now
        carried: dict = {}      # kept for saving, never drawn — answered by the catalog
        advanced: list = []     # drawn only behind MORE

        for f in decl.get("needs", ()):
            if not isinstance(f, Field):
                continue
            # Do not show what the catalog entry already answered. The address
            # somebody did not have to type is one more thing to read and one
            # more thing to break; a key a local server never asks for is a box
            # that makes people think they need an account.
            if f.key == "base_url" and not custom:
                carried[f.key] = (self._line(
                    str(stored.get(f.key, f.default) or "")), f)
                continue
            if f.key == "api_key" and is_local:
                carried[f.key] = (self._line(str(stored.get(f.key, "") or "")), f)
                continue
            if f.key == "backend":
                carried[f.key] = (self._line(
                    str(entry.get("values", {}).get("backend")
                        or stored.get(f.key, f.default) or "")), f)
                continue
            w = self._make_widget(pid, f, stored.get(f.key, f.default))
            if f.advanced:
                advanced.append((f, w))
            else:
                shown[f.key] = (w, f)

        for _key, (w, f) in shown.items():
            lay.addWidget(self._label(
                f.label + ("" if f.required else "   (optional)"), 7, C.TEXT_DIM))
            lay.addWidget(w)
            # A text box already shows its hint as placeholder text. Adding the
            # label as well printed every hint twice, which is how the panel
            # came to look careless.
            if f.hint and not isinstance(w, QLineEdit):
                lay.addWidget(self._label(f.hint, 7, C.TEXT_DIM))

        fields = dict(shown)
        fields.update(carried)
        self._hidden[pid] = set(carried) | {f.key for f, _w in advanced}

        # Buttons and status.
        status = self._label("", 7, C.TEXT_DIM)
        self._status[pid] = status

        brow = QHBoxLayout(); brow.setSpacing(6)
        brow.addWidget(self._btn("TEST", lambda _=0, p=pid: self._test(p), C.PRI))
        # Not "LIST" any more: the list arrives on its own when a service is
        # chosen. This is the button for when it did not — the server was
        # starting up, or a model has just been pulled.
        if any(k in shown for k in ("model", "voice_id", "voice")):
            brow.addWidget(self._btn("↻ REFRESH", lambda _=0, p=pid: self._list(p)))
        if advanced:
            brow.addWidget(self._btn(
                ("⌃ LESS" if self._more.get(pid) else "⌄ MORE"),
                lambda _=0, p=pid: self._toggle_more(p)))
        if not decl.get("_available", True) and decl.get("install"):
            brow.addWidget(self._btn(f"INSTALL {decl['install']}",
                                     lambda _=0, p=pid: self._install(p), C.ACC2))
        brow.addStretch(1)
        holder = QWidget()
        holder.setStyleSheet("background: transparent; border: none;")
        holder.setLayout(brow)
        lay.addWidget(holder)
        lay.addWidget(status)

        if self._more.get(pid):
            lay.addWidget(self._label("─" * 46, 7, C.BORDER))
            for f, w in advanced:
                lay.addWidget(self._label(f"{f.label}   (optional)", 7, C.TEXT_DIM))
                lay.addWidget(w)
                if f.hint and not isinstance(w, QLineEdit):
                    lay.addWidget(self._label(f.hint, 7, C.TEXT_DIM))
                fields[f.key] = (w, f)
                self._hidden[pid].discard(f.key)
        else:
            for f, w in advanced:
                fields[f.key] = (w, f)

        self._edits[pid] = fields

    def _make_widget(self, pid: str, f, value):
        if f.kind == "choice":
            w = self._combo()
            choices = list(self._live_choices(pid, f.key) or f.choices)
            if not f.required:
                w.addItem("— default —", "")
            for c in choices:
                w.addItem(str(c), str(c))
            w.setEditable(f.key in ("base_url", "model", "voice_id"))
            if w.isEditable():
                w.lineEdit().setStyleSheet(f"color: {C.TEXT}; background: transparent;")
            if value:
                j = w.findData(str(value))
                if j < 0:
                    w.addItem(str(value), str(value))
                    j = w.count() - 1
                w.setCurrentIndex(j)
                if w.isEditable():
                    w.setCurrentText(str(value))
            return w
        if f.kind == "bool":
            w = self._combo()
            w.addItem("no", "")
            w.addItem("yes", "1")
            w.setCurrentIndex(1 if str(value) in ("1", "True", "true") else 0)
            return w
        w = self._line(str(value if value is not None else ""))
        if f.kind == "secret":
            w.setEchoMode(QLineEdit.EchoMode.Password)
        if f.hint:
            w.setPlaceholderText(f.hint[:64])
        return w

    def _toggle_more(self, pid: str) -> None:
        self._more[pid] = not self._more.get(pid)
        self._remember_edits()
        QTimer.singleShot(0, self._rebuild_body)

    def _sidecar_box_widget(self) -> QWidget:
        head = self._label("⚡  BACKGROUND REQUESTS", 9, C.PRI, bold=True)
        w, lay = self._box([head])
        lay.addWidget(self._label(
            "Reading a document, looking at your screen, writing code — the "
            "assistant's own requests, not the conversation. These use Gemini's "
            "text quota, which runs out before the voice does. Sending them "
            "somewhere else keeps them working, and changes nothing you hear.",
            7, C.TEXT_DIM))

        # Read the stored choice BEFORE the box exists. The first version asked
        # `_sidecar_choice()`, which reads the combo — and at this point that is
        # the empty box being built, so the answer was always "nothing chosen"
        # and the form under it never appeared.
        cur_pid = self._sidecar
        cur_eid = self._current_entry("llm", cur_pid) if cur_pid else ""

        self._sidecar_combo = self._combo()
        self._sidecar_combo.addItem("Gemini only  —  the way it has always been",
                                    ("", ""))
        for pid, eid, entry, decl in self._catalog("llm"):
            mark = "🖥" if entry.get("local") else ("☁" if entry.get("local")
                                                   is False else "  ")
            self._sidecar_combo.addItem(
                f"{mark}  Fall back to {entry['label']}", (pid, eid))
        i = self._sidecar_combo.findData((cur_pid, cur_eid)) if cur_pid else 0
        if cur_pid and i < 0:
            for j in range(1, self._sidecar_combo.count()):
                if (self._sidecar_combo.itemData(j) or ("", ""))[0] == cur_pid:
                    i = j
                    break
        self._sidecar_combo.setCurrentIndex(max(0, i))
        self._sidecar_combo.currentIndexChanged.connect(self._sidecar_changed)
        lay.addWidget(self._sidecar_combo)

        self._first_combo = self._combo()
        self._first_combo.addItem("Try Gemini first  —  faster", "after")
        self._first_combo.addItem(
            "Try the other one first  —  keeps more on your machine", "before")
        self._first_combo.setCurrentIndex(1 if self._sidecar_first else 0)
        lay.addWidget(self._first_combo)

        # In pipeline mode the model chosen for ② THINKING answers these too, so
        # there is nothing more to fill in. In Gemini mode there IS no ② section
        # — and the first version still said "it uses whatever you set up under
        # ② THINKING", which named a box that was not on the screen. So a
        # service picked here gets its own form, right here.
        if cur_pid:
            if self._mode == "pipeline" and cur_pid == self._pipe.get("llm"):
                lay.addWidget(self._label(
                    "Uses the model you chose for ② THINKING.", 7, C.TEXT_DIM))
            else:
                self._fields_into(lay, "llm", cur_pid, cur_eid)
        else:
            lay.addWidget(self._label(
                "Left as it is, these keep using Gemini and stop working when "
                "its text quota runs out.", 7, C.TEXT_DIM))
        return w

    def _sidecar_choice(self) -> tuple:
        """(provider id, catalog entry id) for the background-request model."""
        box = getattr(self, "_sidecar_combo", None)
        if box is not None:
            data = box.currentData()
            if isinstance(data, tuple):
                return data
        pid = self._sidecar
        return (pid, self._current_entry("llm", pid) if pid else "")

    def _sidecar_changed(self, _ix) -> None:
        pid, eid = self._sidecar_choice()
        self._remember_edits()
        self._sidecar = pid
        if pid and eid:
            from memory.config_manager import save_provider_config
            for _p, _e, entry, _d in self._catalog("llm"):
                if _p == pid and _e == eid:
                    values = dict(entry.get("values") or {})
                    values["catalog"] = eid
                    save_provider_config(pid, values)
                    break
        QTimer.singleShot(0, self._rebuild_body)

    def _update_footer(self) -> None:
        if self._mode != "pipeline":
            self._footer.setText(
                "Gemini Live. Nothing on this panel changes the conversation.")
            return
        missing = [name for slot, _n, name, _w in self._SLOTS
                   if not self._pipe.get(slot)]
        if "THINKING" in missing:
            self._footer.setText(
                "⚠  ② THINKING is not set — without it the assistant falls back "
                "to Gemini Live and says so in the log.")
        elif missing:
            words = " and ".join(f"{m.lower()}" for m in missing)
            self._footer.setText(
                f"Only {words} is missing — it will run, but you will type "
                f"instead of speak, or read instead of listen.")
        else:
            self._footer.setText(
                "All three set. SAVE & APPLY reconnects; the conversation is kept.")

    # ── actions ──────────────────────────────────────────────────────────────
    def _live_choices(self, pid: str, key: str) -> tuple:
        try:
            decl = self._registry.get_declaration(pid)
            mod = __import__(f"providers.{decl['_module']}", fromlist=["choices_for"])
            fn = getattr(mod, "choices_for", None)
            return tuple(fn(key)) if fn else ()
        except Exception:
            return ()

    def _values(self, pid: str) -> dict:
        out = {}
        hidden = self._hidden.get(pid, set())
        stored = self._get_cfg(pid)
        for key, (w, _f) in self._edits.get(pid, {}).items():
            if isinstance(w, QComboBox):
                value = (w.currentText().strip() if w.isEditable()
                         else (w.currentData() or ""))
            else:
                value = w.text().strip()
            # A field nobody can see must never blank out a setting that has
            # one. These widgets are rebuilt on every redraw, and a redraw that
            # raced a background fetch could recreate a hidden address as empty
            # and then save that over a working one — leaving a pipeline that
            # had been configured correctly pointing at nothing, with no visible
            # cause. Clearing a value is still possible; it just has to be done
            # in a box that is on screen.
            if not value and key in hidden and stored.get(key):
                continue
            out[key] = value
        return out

    def _remember_edits(self) -> None:
        """Keep what has been typed when the form is rebuilt.

        Without it, changing the speech engine wiped an API key somebody had
        just entered into the box above."""
        from memory.config_manager import save_provider_config
        for pid in list(self._edits):
            try:
                save_provider_config(pid, self._values(pid))
            except Exception:
                pass

    def _say(self, pid: str, text: str, colour: str) -> None:
        lb = self._status.get(pid)
        if lb is not None:
            lb.setText(text)
            lb.setStyleSheet(f"color: {colour}; background: transparent; border: none;")

    def _test(self, pid: str) -> None:
        # Read the boxes BEFORE letting Qt run. `processEvents` will happily
        # deliver a queued rebuild, which deletes the very widgets this is
        # about to read — and a deleted QWidget raises rather than returning
        # something wrong, so it takes the click down with it.
        values = self._values(pid)
        self._say(pid, "checking…", C.TEXT_DIM)
        QApplication.processEvents()
        try:
            inst = self._registry.build(pid, values)
            probe = getattr(inst, "probe", None)
            if probe is None:
                self._say(pid, "Nothing to check for this one.", C.TEXT_DIM)
                return
            ok, msg = probe()
        except Exception as e:
            self._say(pid, f"✕  {type(e).__name__}: {str(e)[:150]}", C.RED)
            return
        self._say(pid, ("✓  " if ok else "✕  ") + msg, C.GREEN if ok else C.ACC)
        # TEST is also where "can this model see?" gets answered — by asking the
        # endpoint, not the user. Write the answer back into the stored settings
        # so the assistant is told the truth about its own eyes.
        seen = (getattr(inst, "caps", {}) or {}).get("vision")
        if ok and seen is not None:
            from memory.config_manager import save_provider_config
            save_provider_config(pid, {"vision": "1" if seen else ""})
            entry = self._edits.get(pid, {}).get("vision")
            if entry is not None and isinstance(entry[0], QComboBox):
                entry[0].setCurrentIndex(1 if seen else 0)

    def _list(self, pid: str) -> None:
        values = self._values(pid)          # before processEvents — see _test
        self._say(pid, "reading…", C.TEXT_DIM)
        QApplication.processEvents()
        try:
            inst = self._registry.build(pid, values)
            if hasattr(inst, "models"):
                items, key = [(m, m) for m in inst.models()], "model"
            elif hasattr(inst, "voices"):
                items, key = list(inst.voices()), "voice_id"
            else:
                self._say(pid, "Nothing to list for this one.", C.TEXT_DIM)
                return
        except Exception as e:
            self._say(pid, f"✕  {type(e).__name__}: {str(e)[:150]}", C.RED)
            return

        entry = self._edits.get(pid, {}).get(key)
        if entry is None or not items:
            self._say(pid, f"✓  {len(items)} found." if items else "None found.",
                      C.GREEN if items else C.TEXT_DIM)
            return
        box = entry[0]
        if isinstance(box, QComboBox):
            keep = box.currentText()
            box.clear()
            for value, label in items:
                box.addItem(str(label), str(value))
            j = box.findData(keep)
            box.setCurrentIndex(j if j >= 0 else 0)
        else:
            self._say(pid, f"✓  {len(items)} available: "
                           f"{', '.join(str(v) for v, _ in items[:8])}", C.GREEN)
            return
        self._say(pid, f"✓  {len(items)} available — pick one above.", C.GREEN)

    def _install(self, pid: str) -> None:
        """Fetch a provider's optional dependency, off the Qt thread — pip takes
        tens of seconds and a frozen window looks like a crashed one."""
        import subprocess
        import sys as _sys
        import threading

        decl = self._registry.get_declaration(pid) or {}
        pkg = decl.get("install")
        if not pkg:
            return
        self._say(pid, f"installing {pkg} — this can take a minute…", C.ACC2)

        def work():
            try:
                r = subprocess.run(
                    [_sys.executable, "-m", "pip", "install", pkg],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=900)
                good = r.returncode == 0
                tail = r.stdout.decode("utf-8", "replace").strip().splitlines()
                msg = ("✓  installed — close and reopen this panel"
                       if good else (tail[-1][:150] if tail else "pip failed"))
            except Exception as e:
                good, msg = False, f"{type(e).__name__}: {str(e)[:120]}"
            QTimer.singleShot(0, lambda: self._say(pid, msg,
                                                   C.GREEN if good else C.RED))

        threading.Thread(target=work, daemon=True).start()

    def _apply(self) -> None:
        from memory.config_manager import (get_pipeline, get_provider_mode,
                                           save_providers)

        before = (get_provider_mode(), dict(get_pipeline()))

        # ONE write, not five. See save_providers for what the five cost.
        creds = {pid: self._values(pid) for pid in list(self._edits)}
        for slot, eid in self._picked.items():
            pid = self._pipe.get(slot, "")
            if pid and eid:
                creds.setdefault(pid, {})["catalog"] = eid
        sidecar_pid, sidecar_eid = self._sidecar_choice()
        if sidecar_pid and sidecar_eid:
            creds.setdefault(sidecar_pid, {}).setdefault("catalog", sidecar_eid)

        save_providers(
            mode=self._mode,
            pipeline={"stt": self._pipe.get("stt", ""),
                      "llm": self._pipe.get("llm", ""),
                      "tts": self._pipe.get("tts", "")},
            creds=creds,
            sidecar=sidecar_pid,
            sidecar_first=self._first_combo.currentData() == "before",
        )

        # Providers are cached by configuration; a changed setting must not go
        # on being served by an object built from the old one.
        try:
            self._registry.reset()
        except Exception:
            pass

        after = (get_provider_mode(), dict(get_pipeline()))
        self.hide()
        # Background-request settings take effect on the next call with no
        # reconnect. Only the conversation engine needs the session rebuilt.
        if before != after:
            self.changed.emit()


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
        x_btn = QPushButton("✕")
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
               f"QPushButton:hover {{ color: {C.PRI}; border-color: {C.BORDER_B}; }}")
        for label, cmd_fmt in [
            ("TRANSLATE", "Translate this text to English: {text}"),
            ("SUMMARISE", "Summarise this: {text}"),
            ("EXPLAIN",   "Explain this: {text}"),
            ("FIX",       "Fix grammar and spelling: {text}"),
        ]:
            b = QPushButton(label)
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
            save_btn = QPushButton("▸  SAVE")
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

        close_btn = QPushButton("CLOSE")
        close_btn.setFixedHeight(34)
        close_btn.setFont(QFont("Courier New", 9))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
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
                w = QPushButton()
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
            ab = QPushButton(str(action.get("label") or "TEST"))
            ab.setFixedHeight(30)
            ab.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            ab.setCursor(Qt.CursorShape.PointingHandCursor)
            ab.setStyleSheet(f"""
                QPushButton {{ background: #00091a; color: {C.PRI};
                    border: 1px solid {C.PRI_DIM}; border-radius: 3px; }}
                QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
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
                 manual_url: str = "", expiry_secs: int = 600, parent=None,
                 bind_error: str = ""):
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

        # THE ONE FAILURE THAT LOOKS LIKE THE SERVER BEING OFF.
        #
        # Windows writes a BLOCK rule for an executable when its "Allow access?"
        # prompt is dismissed, and a block overrides every allow rule there is —
        # including the port rule this app carefully creates. The phone then
        # reports "refused to connect" while the server is running perfectly.
        #
        # It is invisible from the HUD, which is where the person scanning the
        # QR code is looking, and it is per-EXECUTABLE: the shortcut runs
        # pythonw.exe while an editor runs python.exe, so it can block one launch
        # method and not the other on the same machine. That difference is
        # unguessable without being told, so it is said here.
        # Whatever is stopping this from working, say it HERE — beside the QR
        # code, where the person about to scan it is looking. A dashboard that
        # cannot be reached and does not explain why is the single most
        # frustrating failure this app has.
        if bind_error:
            lay.addWidget(_lbl("⚠  " + bind_error, 8, color="#f4b942"))

        try:
            from dashboard.server import blocking_firewall_rules
            _blocked = blocking_firewall_rules()
        except Exception:
            _blocked = []
        if _blocked:
            warn = _lbl(
                "⚠  Windows is blocking incoming connections to this copy of "
                "Python, so your phone cannot reach this page. Restart JARVIS "
                "and accept the Windows prompt, or remove the rules named "
                + ", ".join(sorted({n for n, _ in _blocked}))
                + " under Windows Defender Firewall → Inbound Rules.",
                8, color="#f4b942")
            lay.addWidget(warn)

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
                req = Request(geocode_url, headers={"User-Agent":"JaneAI/56"})
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
                req2 = Request(weather_url, headers={"User-Agent":"JaneAI/56"})
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
        for key,label in (("show_activity_log","ACTIVITY LOG"),("show_file_upload","FILE UPLOAD / DROP ZONE"),("show_content_panel","CONTENT / BRIEFINGS / SEARCH"),("show_quiz_panel","QUIZ PANEL"),("show_footer","MARK 56 FOOTER")): chk(key,label)
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
        self._prefs=dict(_UI_PREF_DEFAULTS); self._layout_combo.setCurrentIndex(0); self._command_pos_combo.setCurrentIndex(0); self._widget_pos_combo.setCurrentIndex(0); self._left_width_slider.setValue(220); self._right_width_slider.setValue(340); self._center_y_slider.setValue(44); self._scale_slider.setValue(100); self._glow_slider.setValue(100); self._motion_slider.setValue(100); self._density_slider.setValue(100); self._particle_field_slider.setValue(1); self._react_slider.setValue(100); self._weather_input.clear()
        for key,c in self._checks.items():
            c.setChecked(bool(self._prefs.get(key,True)))
        self._checks["show_command_input"].setChecked(True); self._queue_live()

    def _save(self):
        p=self._collect(); self._prefs=_save_ui_preferences(p); self.saved.emit(dict(self._prefs)); self.hide()

class SettingsOverlay(QWidget):
    """Integrated MARK LVI command-deck settings panel.

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
        self._brand_note = QLabel(f"MARK LVI   ·   {getattr(owner, '_assistant_tagline', DEFAULT_ASSISTANT_TAGLINE)}")
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
                self._brand_note.setText(f"MARK LVI   ·   {getattr(owner, '_assistant_tagline', DEFAULT_ASSISTANT_TAGLINE)}")
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
    _video_open_sig  = pyqtSignal(str, str, bool, str)  # video, title, muted, audio
    _wake_btns_sig   = pyqtSignal()          # wake state resolved off-thread
    _video_close_sig = pyqtSignal()
    _video_mute_sig  = pyqtSignal(bool)
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
        self._assistant_name: str = (_cfg.get("assistant_name") or DEFAULT_ASSISTANT_NAME).strip()
        self._assistant_tagline: str = str(_cfg.get("assistant_tagline") or DEFAULT_ASSISTANT_TAGLINE).strip()
        self._user_name: str = str(_cfg.get("user_name") or "").strip()
        self._wake_word_phrase = _load_wake_word_phrase()
        self._ui_prefs = _load_ui_preferences()
        self._settings_overlay = None
        self._ui_workbench = None
        self._ptt_event_filter_installed = False
        self._ptt_local_enabled = False
        self._ptt_pressed = False
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
        self.on_interrupt      = None   # callable: () -> None — stop JARVIS mid-speech
        self.on_voice_change   = None   # callable: () -> None — rebuild session with new voice
        self.on_identity_change = None   # optional callback when assistant identity changes
        self.on_wake_word_change = None  # callable: (phrase: str) -> None — optional detector reconfiguration
        self.on_audio_device_change = None  # callable: () -> None — reopen audio streams
        self._confirm_overlay  = None   # live ConfirmBanner, if one is on screen
        self.get_plugins       = None   # callable: () -> list[dict], set by JarvisLive
        self.get_plugin_settings = None # callable: () -> list[dict] settings schemas, set by JarvisLive
        self.on_wake_toggle    = None   # callable: (enable: bool) -> str, set by JarvisLive
        self.on_wake_manual    = None   # callable: () -> None — manual sleep/wake
        self.on_push_to_talk   = None   # callable: (enable: bool) -> str scope
        self.ptt_hold          = None   # callable: (held: bool) -> None — windowed chord
        self.wake_get_state    = None   # callable: () -> dict {enabled, awake, ready}
        self._muted            = False
        self._current_file: str | None = None
        self._remote_overlay: RemoteKeyOverlay | None = None
        self._customize_overlay: CustomizeOverlay | None = None

        central = QWidget()
        central.setStyleSheet(f"background: {C.BG};")
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())
        self._sub_lbl.setText(self._assistant_tagline)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self._left_panel = self._build_left_panel()
        body.addWidget(self._left_panel, stretch=0)

        # Center column: HUD + resizable content panel via QSplitter
        self.hud = HudCanvas(face_path, _display)
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
        _cam_x = QPushButton("✕  CLOSE")
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

        # ── Video surface ────────────────────────────────────────────────
        # Built exactly like the camera page above and stacked beside it,
        # because it is the same idea: something takes the centre of the HUD
        # for a while and then gives it back. Sharing the stack rather than
        # inventing a second mechanism means the avatar, the camera and a video
        # can never be on screen at once.
        self._video_split = False        # is the sound a separate stream?
        self._video_auto_muted = False   # did JARVIS close the mic, or the user?
        # A plain flag rather than reading the widget. video_is_playing() is
        # called from plugin threads, and reading a widget's state from one is
        # not something to rely on; an attribute is.
        self._video_on = False
        self._video_cont = QWidget()
        self._video_cont.setStyleSheet(f"background: {C.BG};")
        _vid_v = QVBoxLayout(self._video_cont)
        _vid_v.setContentsMargins(8, 6, 8, 8)
        _vid_v.setSpacing(4)

        _vid_hdr = QHBoxLayout()
        self._video_title = QLabel("▶  VIDEO")
        self._video_title.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        self._video_title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        _vid_hdr.addWidget(self._video_title)
        _vid_hdr.addStretch()

        def _vid_btn(text: str) -> QPushButton:
            b = QPushButton(text)
            b.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(f"""
                QPushButton {{
                    color: {C.TEXT_DIM}; background: transparent;
                    border: none; padding: 2px 6px;
                }}
                QPushButton:hover {{ color: {C.PRI}; }}
            """)
            return b

        # Muted is the default and the button says so, because a soundtrack
        # talking over JARVIS is the one way this feature could make the
        # assistant worse rather than better.
        self._video_mute_btn = _vid_btn("🔇  SOUND OFF")
        self._video_mute_btn.clicked.connect(self._toggle_video_mute)
        _vid_hdr.addWidget(self._video_mute_btn)

        _vid_x = _vid_btn("✕  CLOSE")
        _vid_x.clicked.connect(self.stop_video)
        _vid_hdr.addWidget(_vid_x)
        _vid_v.addLayout(_vid_hdr)

        if HAVE_VIDEO:
            # A GRAPHICS ITEM, NOT A QVideoWidget.
            #
            # QVideoWidget gets a native window of its own, and a native window
            # is painted by the compositor above every ordinary widget that
            # shares the screen with it — so the settings drawer opened
            # *underneath* the video and raise_() could not help, because the
            # two are not in the same painting order at all. A video item drawn
            # into a QGraphicsView goes through Qt's own painter like any other
            # widget, and anything laid over it stays over it.
            self._video_scene = QGraphicsScene(self)
            self._video_item = QGraphicsVideoItem()
            self._video_scene.addItem(self._video_item)
            self._video_widget = QGraphicsView(self._video_scene)
            self._video_widget.setStyleSheet("background: #000; border: none;")
            self._video_widget.setFrameShape(QGraphicsView.Shape.NoFrame)
            self._video_widget.setHorizontalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._video_widget.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._video_widget.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
            )
            _vid_v.addWidget(self._video_widget, stretch=1)

            self._video_audio = QAudioOutput()
            self._video_audio.setMuted(True)
            self._video_player = QMediaPlayer()
            self._video_player.setVideoOutput(self._video_item)
            # Keep the picture filling the view as the window is resized.
            self._video_item.nativeSizeChanged.connect(self._fit_video)
            self._video_player.setAudioOutput(self._video_audio)
            self._video_player.errorOccurred.connect(self._on_video_error)

            # A SECOND player, for sound that arrives separately.
            #
            # YouTube no longer serves a single stream carrying both picture and
            # sound — not for new uploads and not for old ones; checked against
            # three videos including the oldest on the site, and every one of
            # them offered zero combined formats. Picture and sound are two
            # URLs, so they are two players, started together and nudged back
            # into line by the timer below.
            #
            # A local file or a direct video URL still uses the first player
            # alone: one stream, one player, nothing to synchronise.
            self._video_sound = QMediaPlayer()
            self._video_sound_out = QAudioOutput()
            self._video_sound_out.setMuted(True)
            self._video_sound.setAudioOutput(self._video_sound_out)

            self._video_sync = QTimer(self)
            self._video_sync.setInterval(1000)
            self._video_sync.timeout.connect(self._sync_video_sound)
        else:
            self._video_scene = None
            self._video_item = None
            self._video_widget = None
            self._video_player = None
            self._video_audio = None
            self._video_sound = None
            self._video_sound_out = None
            self._video_sync = None
            _miss = QLabel("Video playback is not available in this Qt install.")
            _miss.setAlignment(Qt.AlignmentFlag.AlignCenter)
            _miss.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            _vid_v.addWidget(_miss, stretch=1)

        # Stack: 0 = animated HUD, 1 = live camera, 2 = video
        self._hud_cam_stack = QStackedWidget()
        self._hud_cam_stack.addWidget(self.hud)
        self._hud_cam_stack.addWidget(_cam_cont)
        self._hud_cam_stack.addWidget(self._video_cont)

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
        body.addWidget(self._right_panel, stretch=0)

        root.addLayout(body, stretch=1)
        self._footer = self._build_footer()
        root.addWidget(self._footer)

        # Apply presentation preferences only after the relevant widgets exist.
        self._apply_ui_preferences(self._ui_prefs, log=False, refresh_weather=False, persist=False)
        self._ui_save_timer = QTimer(self)
        self._ui_save_timer.setSingleShot(True)
        self._ui_save_timer.setInterval(450)
        self._ui_save_timer.timeout.connect(self._persist_ui_preferences)

        # Quick-access drawer (floating overlay, built after central widget layout is done)
        self._quick_drawer = self._build_quick_drawer()
        self._ctrl_drawer = self._build_controls_drawer()
        self._install_shortcut_guard()
        self._warm_wake_state()
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
        self._metric_tmr.start(2000)
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
        self._wake_btns_sig.connect(self._refresh_wake_btns)
        self._video_open_sig.connect(self._on_video_open)
        self._video_close_sig.connect(self._on_video_close)
        self._video_mute_sig.connect(self._on_video_mute)
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

    def _show_camera_frame(self, img_bytes: bytes):
        """Slot — display camera preview overlay (main thread)."""
        self._cam_preview.show_frame(img_bytes)
        cw = self.centralWidget()
        pw = _CameraPreview._W
        ph = self._cam_preview.height()
        _right_width = self._right_panel.width() if hasattr(self, "_right_panel") else _RIGHT_W
        self._cam_preview.setGeometry(
            cw.width() - _right_width - pw - 12,
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

    # --- Video in the HUD area ---------------------------------------------
    #
    # Everything below runs on the Qt thread. Plugins and the assistant reach it
    # through the signals above, the same way every other panel is driven, so a
    # background thread never touches a widget.
    def _on_video_open(self, source: str, title: str, muted: bool,
                       audio_source: str = "") -> None:
        if not HAVE_VIDEO or not self._video_player:
            self.write_log("SYS: Video playback is not available in this Qt "
                           "install.")
            return
        # A video and the live camera cannot share the centre of the HUD.
        self._cam_stop.set()

        self._video_title.setText(f"▶  {(title or 'VIDEO')[:44].upper()}")
        self._video_split = bool(audio_source)
        self._set_video_muted(bool(muted))

        url = (QUrl.fromLocalFile(source) if Path(source).exists()
               else QUrl(source))
        self._video_player.setSource(url)
        if self._video_split:
            self._video_sound.setSource(QUrl(audio_source))
        self._hud_cam_stack.setCurrentIndex(2)
        self._video_on = True
        # Re-run now that the video counts as playing: _set_video_muted ran
        # before this line and saw no video, so its mic check was a no-op.
        self._sync_mic_for_video()
        self._video_player.play()
        if self._video_split:
            self._video_sound.play()
            self._video_sync.start()

    def _fit_video(self, *_a) -> None:
        """Size the picture to the panel, keeping its shape."""
        if not (self._video_item and self._video_widget):
            return
        try:
            native = self._video_item.nativeSize()
            if native.isEmpty():
                return
            view = self._video_widget.viewport().size()
            scale = min(view.width() / native.width(),
                        view.height() / native.height())
            w, h = native.width() * scale, native.height() * scale
            self._video_item.setSize(QSizeF(w, h))
            self._video_scene.setSceneRect(0, 0, w, h)
            self._video_widget.centerOn(self._video_item)
        except Exception:
            pass

    def _on_video_close(self) -> None:
        if self._video_sync:
            self._video_sync.stop()
        for p in (self._video_player, self._video_sound):
            if p:
                p.stop()
                p.setSource(QUrl())
        self._video_split = False
        self._video_on = False
        self._hud_cam_stack.setCurrentIndex(0)
        self._sync_mic_for_video()      # gives the microphone back

    def _set_video_muted(self, muted: bool) -> None:
        """Silence whichever output is carrying the sound for this video."""
        muted = bool(muted)
        if self._video_audio:
            # When the sound is a separate stream this player has none, but
            # muting it too costs nothing and keeps the two paths identical.
            self._video_audio.setMuted(muted)
        if self._video_sound_out:
            self._video_sound_out.setMuted(muted)
        self._sync_video_mute_btn()
        self._sync_mic_for_video()

    def _on_video_mute(self, muted: bool) -> None:
        self._set_video_muted(muted)

    def _sync_mic_for_video(self) -> None:
        """Close the microphone while the video is making sound.

        JARVIS subtracts its OWN output from the microphone — that is what
        core/echo.py does — but a video plays through a different output
        entirely, so the guard has never heard of it and the assistant answers
        the film. There is no arrangement in which an open microphone and a
        loudspeaker in the same room do not do that.

        So the microphone closes for exactly as long as the sound is on, and
        opens again by itself the moment it goes off or the video is closed.
        Only if JARVIS closed it: a microphone the user muted themselves stays
        muted, and pressing the mute key during a video hands the decision back
        to them for good.
        """
        sound_on = bool(self._video_on and self._video_sound_out
                        and not self._video_sound_out.isMuted())
        if sound_on and not self._muted:
            self._video_auto_muted = True
            self._set_muted(True, "The video's sound is on — silence it, close "
                                  "it, or press F4 to talk. Typing still works.")
        elif not sound_on and self._video_auto_muted:
            self._video_auto_muted = False
            self._set_muted(False, "The video is quiet again.")

    def _sync_video_sound(self) -> None:
        """Keep the separate soundtrack in step with the picture.

        Two players started at the same moment do not stay together: they buffer
        independently, and measured on a real stream they were about half a
        second apart after nine seconds. So the sound is nudged back to the
        picture whenever it drifts far enough to hear — and only then, because
        correcting a smaller gap is itself audible.
        """
        if not (self._video_split and self._video_sound and self._video_player):
            return
        try:
            if self._video_player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
                return
            drift = self._video_sound.position() - self._video_player.position()
            if abs(drift) > 300:
                self._video_sound.setPosition(self._video_player.position())
        except Exception:
            pass

    def _sync_video_mute_btn(self) -> None:
        out = self._video_sound_out if self._video_split else self._video_audio
        muted = bool(out and out.isMuted())
        self._video_mute_btn.setText("🔇  SOUND OFF" if muted else "🔊  SOUND ON")

    def _toggle_video_mute(self) -> None:
        out = self._video_sound_out if self._video_split else self._video_audio
        if out:
            self._set_video_muted(not out.isMuted())

    def _on_video_error(self, *_a) -> None:
        err = ""
        try:
            err = self._video_player.errorString()
        except Exception:
            pass
        self.write_log(f"SYS: The video could not be played{(' — ' + err) if err else ''}.")
        self._on_video_close()

    def stop_video(self) -> None:
        self._video_close_sig.emit()

    def video_is_playing(self) -> bool:
        return bool(self._video_on)

    # ------------------------------------------------------------------
    # Icon generation — arc-reactor style, rendered with Pillow
    # ------------------------------------------------------------------
    @staticmethod
    def _build_jarvis_icon(out_path: Path) -> bool:
        """
        Render a JARVIS arc-reactor icon at 4× resolution and downsample
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
                             work_dir: str, icon_loc: str) -> None:
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
            sc.Description      = "J.A.R.V.I.S AI Assistant"
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
            'sc.Description = "J.A.R.V.I.S AI Assistant"',
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
                                     capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5)
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
                lnk      = str(desktop / "J.A.R.V.I.S.lnk")
                icon_loc = str(ico_path) if ico_path.exists() else f"{target},0"
                self._create_lnk_windows(lnk, target, str(script),
                                         str(script.parent), icon_loc)

            # ── macOS — proper .app bundle (no Terminal window) ───────────────
            elif _os == "Darwin":
                app     = desktop / "J.A.R.V.I.S.app"
                mac_dir = app / "Contents" / "MacOS"
                res_dir = app / "Contents" / "Resources"
                mac_dir.mkdir(parents=True, exist_ok=True)
                res_dir.mkdir(exist_ok=True)

                # Launcher executable (bash — runs as background process,
                # macOS does NOT open Terminal for executables inside .app bundles)
                launcher = mac_dir / DEFAULT_ASSISTANT_NAME
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
                    '  <key>CFBundleExecutable</key><string>JARVIS</string>\n'
                    '  <key>CFBundleIdentifier</key>'
                    '<string>com.jarvis.assistant</string>\n'
                    '  <key>CFBundleName</key><string>J.A.R.V.I.S</string>\n'
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
                desk = desktop / "J.A.R.V.I.S.desktop"
                desk.write_text(
                    "[Desktop Entry]\n"
                    "Name=J.A.R.V.I.S\n"
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
        if hasattr(self, '_ctrl_drawer') and self._ctrl_drawer.isVisible():
            self._position_ctrl_drawer()
        self._fit_video()

        try:
            self._position_settings_overlay()
        except Exception:
            pass
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
        net_pct = min(100, net * 10)  # 10 MB/s = %100
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
        else:
            self._bar_tmp.set_value(0, "N/A")

        try:
            boot_t  = psutil.boot_time()
            elapsed = time.time() - boot_t
            h = int(elapsed // 3600)
            m = int((elapsed % 3600) // 60)
            self._uptime_lbl.setText(f"UP  {h:02d}:{m:02d}")
        except Exception:
            self._uptime_lbl.setText("UP  --:--")

        try:
            proc_count = len(psutil.pids())
            self._proc_lbl.setText(f"PROC  {proc_count}")
        except Exception:
            self._proc_lbl.setText("PROC  --")


    def _build_header(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(54)
        w.setStyleSheet(f"background: {C.DARK}; border-bottom: 1px solid {C.BORDER_B};")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(16, 0, 16, 0)

        def _badge(txt, color=C.TEXT_MED):
            l = QLabel(txt)
            l.setFont(QFont("Courier New", 8))
            l.setStyleSheet(f"color: {color}; background: transparent;")
            return l

        lay.addWidget(_badge(APP_VERSION, C.PRI_DIM))
        lay.addSpacing(8)
        self._drawer_btn = QPushButton("⚙")
        self._drawer_btn.setFixedSize(26, 26)
        self._drawer_btn.setFont(QFont("Courier New", 11))
        self._drawer_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._drawer_btn.setToolTip("Settings & Controls")
        self._drawer_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 4px;
            }}
            QPushButton:hover {{ color: {C.PRI}; border-color: {C.PRI_DIM}; }}
            QPushButton:checked {{ color: {C.PRI}; border-color: {C.PRI}; background: {C.PRI_GHO}; }}
        """)
        self._drawer_btn.setCheckable(True)
        self._drawer_btn.setToolTip("Setup — things you set once")
        self._drawer_btn.clicked.connect(self._toggle_drawer)
        lay.addWidget(self._drawer_btn)

        # A SECOND drawer, and the split is by how often a thing is touched.
        #
        # One panel held twelve buttons: the ones you press once when you set
        # the assistant up sitting next to the ones you flick on and off every
        # day. Separating them by that — setup behind ⚙, everyday behind 🎛 —
        # is what makes each list short enough to read at a glance.
        self._ctrl_btn = QPushButton("🎛")
        self._ctrl_btn.setFixedSize(26, 26)
        self._ctrl_btn.setFont(QFont("Courier New", 11))
        self._ctrl_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ctrl_btn.setToolTip("Controls — the everyday switches")
        self._ctrl_btn.setStyleSheet(self._drawer_btn.styleSheet())
        self._ctrl_btn.setCheckable(True)
        self._ctrl_btn.clicked.connect(self._toggle_controls)
        lay.addSpacing(4)
        lay.addWidget(self._ctrl_btn)
        lay.addStretch()

        mid = QVBoxLayout(); mid.setSpacing(1)
        _disp = self._assistant_name.upper()
        self._title_lbl = QLabel(_disp)
        self._title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title_lbl.setFont(QFont("Courier New", 17, QFont.Weight.Bold))
        self._title_lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        mid.addWidget(self._title_lbl)
        _sub_text = ("A Friendly Assistant"
                     if _disp in (DEFAULT_ASSISTANT_NAME, "J.A.R.V.I.S")
                     else "Personal AI Assistant")
        self._sub_lbl = QLabel(_sub_text)
        self._sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sub_lbl.setFont(QFont("Courier New", 7))
        self._sub_lbl.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent;")
        mid.addWidget(self._sub_lbl)
        lay.addLayout(mid)
        lay.addStretch()

        right_col = QVBoxLayout(); right_col.setSpacing(2)
        self._clock_lbl = QLabel("00:00:00")
        self._clock_lbl.setFont(QFont("Courier New", 14, QFont.Weight.Bold))
        self._clock_lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        self._clock_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        right_col.addWidget(self._clock_lbl)
        self._date_lbl = QLabel("")
        self._date_lbl.setFont(QFont("Courier New", 7))
        self._date_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        self._date_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        right_col.addWidget(self._date_lbl)
        lay.addLayout(right_col)
        return w

    def _tick_clock(self):
        self._clock_lbl.setText(time.strftime("%H:%M:%S"))
        self._date_lbl.setText(time.strftime("%a %d %b %Y"))

    def _build_left_panel(self) -> QWidget:
        w = QWidget()
        w.setFixedWidth(_LEFT_W)
        w.setStyleSheet(f"background: {C.DARK}; border-right: 1px solid {C.BORDER};")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 10, 8, 10)
        lay.setSpacing(6)
        self._left_layout = lay

        self._left_diag_group = QWidget()
        diag = QVBoxLayout(self._left_diag_group)
        diag.setContentsMargins(0, 0, 0, 0)
        diag.setSpacing(6)
        hdr = QLabel("◈ SYS MONITOR")
        hdr.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent; border-bottom: 1px solid {C.BORDER}; padding-bottom: 4px;")
        diag.addWidget(hdr)

        self._bar_cpu = MetricBar("CPU", C.PRI)
        self._bar_mem = MetricBar("MEM", C.ACC2)
        self._bar_net = MetricBar("NET", C.GREEN)
        self._bar_gpu = MetricBar("GPU", C.ACC)
        self._bar_tmp = MetricBar("TMP", "#ff6688")
        for bar in (self._bar_cpu, self._bar_mem, self._bar_net, self._bar_gpu, self._bar_tmp):
            diag.addWidget(bar)
        lay.addWidget(self._left_diag_group)

        self._io_panel = QWidget()
        self._io_panel.setStyleSheet(f"background: {C.PANEL2}; border: 1px solid {C.BORDER}; border-radius: 4px;")
        io = QVBoxLayout(self._io_panel)
        io.setContentsMargins(6, 5, 6, 5)
        io.setSpacing(3)
        self._uptime_lbl = QLabel("UP  --:--")
        self._uptime_lbl.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._uptime_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent; border: none;")
        io.addWidget(self._uptime_lbl)
        self._proc_lbl = QLabel("PROC  --")
        self._proc_lbl.setFont(QFont("Courier New", 8))
        self._proc_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; border: none;")
        io.addWidget(self._proc_lbl)
        lay.addWidget(self._io_panel)

        self._info_panel = QWidget()
        self._info_panel.setStyleSheet(f"background: {C.PANEL2}; border: 1px solid {C.BORDER}; border-radius: 4px;")
        info_lay = QVBoxLayout(self._info_panel)
        info_lay.setContentsMargins(6, 5, 6, 5)
        info_lay.setSpacing(3)
        os_name = {"Windows": "WIN", "Darwin": "macOS", "Linux": "LINUX"}.get(_OS, _OS.upper())
        os_lbl = QLabel(f"OS  {os_name}")
        os_lbl.setFont(QFont("Courier New", 8))
        os_lbl.setStyleSheet(f"color: {C.ACC2}; background: transparent; border: none;")
        info_lay.addWidget(os_lbl)
        lay.addWidget(self._info_panel)

        self._aux_panel = QWidget()
        self._aux_panel.setStyleSheet(f"background: {C.PANEL2}; border: 1px solid {C.BORDER}; border-radius: 4px;")
        aux = QVBoxLayout(self._aux_panel)
        aux.setContentsMargins(6, 5, 6, 5)
        aux.setSpacing(3)
        status_title = QLabel("SYSTEM STATUS")
        status_title.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        status_title.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        aux.addWidget(status_title)
        self._aux_labels = {}
        for key, label, value in (("audio", "AUDIO LINK", "READY"), ("ai", "AI CORE", "ACTIVE"), ("session", "SESSION", "ONLINE")):
            row = QHBoxLayout()
            a = QLabel(label)
            a.setFont(QFont("Courier New", 6, QFont.Weight.Bold))
            a.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            b = QLabel(value)
            b.setAlignment(Qt.AlignmentFlag.AlignRight)
            b.setFont(QFont("Courier New", 6, QFont.Weight.Bold))
            b.setStyleSheet(f"color: {C.PRI}; background: transparent;")
            row.addWidget(a)
            row.addStretch()
            row.addWidget(b)
            aux.addLayout(row)
            self._aux_labels[key] = b
        lay.addWidget(self._aux_panel)

        self._status_tiles_panel = QWidget()
        tiles = QGridLayout(self._status_tiles_panel)
        tiles.setContentsMargins(0, 0, 0, 0)
        tiles.setHorizontalSpacing(5)
        tiles.setVerticalSpacing(5)
        self._status_tiles = {}
        for col, (key, title, value, colour) in enumerate((
            ("core", "AI CORE", "ACTIVE", C.GREEN),
            ("sec", "SEC", "CLEARED", C.PRI),
            ("proto", "PROTO", APP_PROTOCOL, C.TEXT_DIM),
        )):
            tile = QWidget()
            tile.setStyleSheet(f"background: {C.PANEL2}; border: 1px solid {C.BORDER_A}; border-radius: 3px;")
            tl = QVBoxLayout(tile)
            tl.setContentsMargins(4, 4, 4, 4)
            tl.setSpacing(1)
            t = QLabel(title)
            t.setAlignment(Qt.AlignmentFlag.AlignCenter)
            t.setFont(QFont("Courier New", 5, QFont.Weight.Bold))
            t.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            v = QLabel(value)
            v.setAlignment(Qt.AlignmentFlag.AlignCenter)
            v.setFont(QFont("Courier New", 6, QFont.Weight.Bold))
            v.setStyleSheet(f"color: {colour}; background: transparent;")
            tl.addWidget(t)
            tl.addWidget(v)
            tiles.addWidget(tile, 0, col)
            self._status_tiles[key] = v
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
            QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
        """
        _BTN_STYLE_DIM = f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
                text-align: left; padding: 0 8px;
            }}
            QPushButton:hover {{ color: {C.PRI}; border-color: {C.BORDER_B}; }}
        """

        self._BTN_PRI, self._BTN_DIM = _BTN_STYLE_PRI, _BTN_STYLE_DIM
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

        hdr = QLabel("◈ SETUP")
        hdr.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent; "
                          f"border-bottom: 1px solid {C.BORDER}; padding-bottom: 4px;")
        lay.addWidget(hdr)

        remote_btn = QPushButton("◉  REMOTE CONTROL")
        remote_btn.setFixedHeight(30)
        remote_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        remote_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        remote_btn.setStyleSheet(_BTN_STYLE_PRI)
        remote_btn.clicked.connect(self._open_remote)
        lay.addWidget(remote_btn)

        sc_btn = QPushButton("⊞  CREATE DESKTOP SHORTCUT")
        sc_btn.setFixedHeight(26)
        sc_btn.setFont(QFont("Courier New", 7))
        sc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        sc_btn.setStyleSheet(_BTN_STYLE_DIM)
        sc_btn.clicked.connect(self._create_desktop_shortcut)
        lay.addWidget(sc_btn)

        self._autostart_btn = QPushButton("◉  AUTO-START: OFF")
        self._autostart_btn.setFixedHeight(26)
        self._autostart_btn.setFont(QFont("Courier New", 7))
        self._autostart_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._autostart_btn.clicked.connect(self._toggle_autostart)
        lay.addWidget(self._autostart_btn)

        cust_btn = QPushButton("⚙  CUSTOMISE ASSISTANT")
        cust_btn.setFixedHeight(26)
        cust_btn.setFont(QFont("Courier New", 7))
        cust_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cust_btn.setStyleSheet(_BTN_STYLE_DIM)
        cust_btn.clicked.connect(self._open_customize)
        lay.addWidget(cust_btn)

        settings_btn = QPushButton("⚙  MASTER SETTINGS")
        settings_btn.setFixedHeight(26)
        settings_btn.setFont(QFont("Courier New", 7))
        settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        settings_btn.setStyleSheet(_BTN_STYLE_DIM)
        settings_btn.clicked.connect(self._open_settings)
        lay.addWidget(settings_btn)

        workbench_btn = QPushButton("◈  UI WORKBENCH")
        workbench_btn.setFixedHeight(26)
        workbench_btn.setFont(QFont("Courier New", 7))
        workbench_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        workbench_btn.setStyleSheet(_BTN_STYLE_DIM)
        workbench_btn.clicked.connect(self._open_ui_workbench)
        lay.addWidget(workbench_btn)

        audio_btn = QPushButton("🎧  AUDIO DEVICES")
        audio_btn.setFixedHeight(26)
        audio_btn.setFont(QFont("Courier New", 7))
        audio_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        audio_btn.setStyleSheet(_BTN_STYLE_DIM)
        audio_btn.clicked.connect(self._open_audio_devices)
        lay.addWidget(audio_btn)

        mem_btn = QPushButton("🧠  MEMORY")
        mem_btn.setFixedHeight(26)
        mem_btn.setFont(QFont("Courier New", 7))
        mem_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        mem_btn.setStyleSheet(_BTN_STYLE_DIM)
        mem_btn.clicked.connect(self._open_memory_panel)
        lay.addWidget(mem_btn)

        plugin_btn = QPushButton("🧩  PLUGINS")
        plugin_btn.setFixedHeight(26)
        plugin_btn.setFont(QFont("Courier New", 7))
        plugin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        plugin_btn.setStyleSheet(_BTN_STYLE_DIM)
        plugin_btn.clicked.connect(self._open_plugin_manager)
        lay.addWidget(plugin_btn)

        settings_btn = QPushButton("⚙  PLUGIN SETTINGS")
        settings_btn.setFixedHeight(26)
        settings_btn.setFont(QFont("Courier New", 7))
        settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        settings_btn.setStyleSheet(_BTN_STYLE_DIM)
        settings_btn.clicked.connect(self._open_plugin_settings)
        lay.addWidget(settings_btn)

        # The button is built here with the rest of the drawer; the panel behind
        # it — and the whole providers package — is imported on the click. The
        # drawer used to stutter for a measured 2.104 seconds because a state
        # check imported openwakeword while it was being built, and adding a
        # second thing that does that would undo the fix rather than repeat it.
        prov_btn = QPushButton("🔌  ENGINE & PROVIDERS")
        prov_btn.setFixedHeight(26)
        prov_btn.setFont(QFont("Courier New", 7))
        prov_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        prov_btn.setStyleSheet(_BTN_STYLE_DIM)
        prov_btn.clicked.connect(self._open_providers)
        lay.addWidget(prov_btn)

        w.adjustSize()
        return w

    def _build_controls_drawer(self) -> QWidget:
        """The switches that get flicked every day, in their own panel."""
        w = QWidget(self.centralWidget())
        w.setObjectName("QuickDrawer")
        w.setStyleSheet(self._quick_drawer.styleSheet())
        w.hide()

        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(5)

        hdr = QLabel("◈ CONTROLS")
        hdr.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent; "
                          f"border-bottom: 1px solid {C.BORDER}; padding-bottom: 4px;")
        lay.addWidget(hdr)

        def _row(button, style=None, height=26):
            button.setFixedHeight(height)
            button.setFont(QFont("Courier New", 7))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            if style:
                button.setStyleSheet(style)
            lay.addWidget(button)
            return button

        fs_btn = _row(QPushButton("⛶  FULLSCREEN  [F11]"), self._BTN_DIM)
        fs_btn.clicked.connect(self._toggle_fullscreen)

        self._brief_btn = _row(QPushButton())
        self._brief_btn.clicked.connect(self._toggle_brief)

        self._wake_btn = _row(QPushButton())
        self._wake_btn.clicked.connect(self._toggle_wake_word)
        self._wake_sleep_btn = _row(QPushButton())
        self._wake_sleep_btn.clicked.connect(self._tap_wake_manual)
        # Neutral until the real state lands from the background warm-up.
        self._wake_btn.setText("🎙  WAKE WORD")
        self._wake_btn.setStyleSheet(self._BTN_DIM)
        self._wake_sleep_btn.hide()

        self._ptt_btn = _row(QPushButton())
        self._ptt_btn.clicked.connect(self._toggle_ptt)
        self._refresh_talk_btns()

        self._hud_btn = _row(QPushButton())
        self._hud_btn.clicked.connect(self._toggle_hud_style)
        self._refresh_hud_btn()

        w.adjustSize()
        return w

    def _warm_wake_state(self) -> None:
        """Work out the wake-word state off the UI thread, once.

        It used to be resolved when the drawer opened, and that made opening the
        drawer take two seconds the first time — measured at 2.10s, all of it
        `import openwakeword` dragging in onnxruntime behind it. The button
        count had nothing to do with it; the Qt thread was simply waiting on an
        import. Afterwards the same check costs about a millisecond, so it only
        ever needed to happen somewhere other than in front of the user.
        """
        def work():
            try:
                self._wake_state()
            except Exception:
                pass
            self._wake_btns_sig.emit()
        threading.Thread(target=work, daemon=True, name="wake-state-warm").start()

    def _toggle_drawer(self, checked: bool):
        if checked:
            self._close_controls()
            self._position_quick_drawer()
            self._quick_drawer.show()
            self._quick_drawer.raise_()
        else:
            self._quick_drawer.hide()

    def _toggle_controls(self, checked: bool):
        if checked:
            self._close_setup()
            self._refresh_wake_btns()   # cheap now: the state was warmed at boot
            self._position_ctrl_drawer()
            self._ctrl_drawer.show()
            self._ctrl_drawer.raise_()
        else:
            self._ctrl_drawer.hide()

    def _close_setup(self):
        if hasattr(self, "_quick_drawer"):
            self._quick_drawer.hide()
        if hasattr(self, "_drawer_btn"):
            self._drawer_btn.setChecked(False)

    def _close_controls(self):
        if hasattr(self, "_ctrl_drawer"):
            self._ctrl_drawer.hide()
        if hasattr(self, "_ctrl_btn"):
            self._ctrl_btn.setChecked(False)

    def _place_drawer(self, drawer, left: int):
        _W = 220
        drawer.setFixedWidth(_W)
        drawer.adjustSize()
        drawer.setGeometry(left, 54, _W, drawer.sizeHint().height())

    def _position_quick_drawer(self):
        if hasattr(self, "_quick_drawer"):
            self._place_drawer(self._quick_drawer, 12)

    def _position_ctrl_drawer(self):
        # Under its own header button rather than under the other one's.
        if hasattr(self, "_ctrl_drawer"):
            left = 12
            try:
                left = max(12, self._ctrl_btn.mapTo(self.centralWidget(),
                                                    QPoint(0, 0)).x() - 8)
            except Exception:
                pass
            self._place_drawer(self._ctrl_drawer, left)

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

        dismiss = QPushButton("DISMISS  ✕")
        dismiss.setFont(QFont("Courier New", 7))
        dismiss.setFixedHeight(18)
        dismiss.setCursor(Qt.CursorShape.PointingHandCursor)
        dismiss.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 2px; padding: 0 5px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
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
        import time as _time
        # The panel opens below the head, so the head looks down at it. It is a
        # tiny thing that answers "did that land?" before you read a word.
        self.hud.glance(0.0, -0.85, hold=1.3)
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

    # ── document review ──────────────────────────────────────────────────────
    # Rendered as rich text into the content panel that already exists, rather
    # than into a panel of its own. A review is read, not clicked, so QTextEdit
    # gives scrolling, selection and copy for nothing, and the HUD gains no
    # widget it has to lay out. Severity decides colour and order here because
    # that is presentation; the plugin supplies no styling and knows no palette,
    # which is also what lets a re-theme repaint a review correctly.

    # Severity is marked by a symbol and a colour, not by a word. The findings
    # themselves are in the user's language, and "[SERIOUS]" sitting inside a
    # Turkish sentence is the kind of seam this project tries not to have —
    # while translating the tag would mean a table per language, which is worse.
    # A shape carries it in every language, and shape plus colour still reads
    # for someone who cannot separate red from amber. What the marks mean
    # arrives the way everything else does: JARVIS says it out loud.
    _REVIEW_MARKS = {"serious": ("RED", "▲"), "caution": ("ACC2", "●"), "note": ("PRI_DIM", "·")}

    @staticmethod
    def _esc(s) -> str:
        return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace("\n", "<br>"))

    def _show_review(self, title: str, summary: str, findings, unclear):
        """Slot — Qt main thread. Lays a document review into the content panel."""
        e = self._esc
        parts = [f'<div style="color:{C.TEXT}; font-family:Courier New;">']

        if summary:
            parts.append(
                f'<div style="color:{C.WHITE}; border-left:2px solid {C.PRI};'
                f' padding-left:8px; margin-bottom:10px;">{e(summary)}</div>')

        for f in (findings or []):
            key, mark = self._REVIEW_MARKS.get(f.get("severity"), ("PRI_DIM", "·"))
            colour = getattr(C, key)
            parts.append(f'<div style="margin-bottom:11px;">')
            parts.append(
                f'<span style="color:{colour}; font-weight:bold;">{mark}</span> '
                f'<span style="color:{C.WHITE}; font-weight:bold;">'
                f'{e(f.get("heading"))}</span>')
            if f.get("detail"):
                parts.append(f'<div style="margin-left:12px;">{e(f["detail"])}</div>')
            if f.get("quote"):
                # The document's own wording, visually separated from the
                # explanation so the two are never mistaken for each other.
                parts.append(
                    f'<div style="margin-left:12px; color:{C.TEXT_DIM};'
                    f' border-left:1px solid {C.BORDER}; padding-left:7px;">'
                    f'&ldquo;{e(f["quote"])}&rdquo;</div>')
            if f.get("suggestion"):
                parts.append(
                    f'<div style="margin-left:12px; color:{C.PRI};">'
                    f'&rarr; {e(f["suggestion"])}</div>')
            parts.append('</div>')

        if unclear:
            parts.append(
                f'<div style="margin-top:6px; border-top:1px solid {C.BORDER};'
                f' padding-top:7px; color:{C.TEXT_MED};">'
                'The document does not settle:</div>')
            for u in unclear:
                parts.append(
                    f'<div style="margin-left:12px; color:{C.TEXT_MED};">'
                    f'&middot; {e(u)}</div>')
        parts.append('</div>')

        import time as _time
        self.hud.glance(0.0, -0.85, hold=1.3)
        # Left as written, not upper-cased. The other content-panel titles are
        # the app's own English labels, but this one is the document's name in
        # the user's language, and str.upper() applies English casing rules to
        # it: Turkish "Sözleşmesi" comes back "SÖZLEŞMESI", having lost the
        # dotted capital İ. Python has no locale-aware upper to reach for, and
        # imposing one language's rules on all of them is the bug, not the fix.
        self._content_title_lbl.setText((title or "Document")[:48])
        self._content_ts_lbl.setText(_time.strftime("%H:%M:%S"))
        self._content_display.setHtml("".join(parts))
        self._content_display.moveCursor(
            self._content_display.textCursor().MoveOperation.Start)
        first_show = not self._content_panel.isVisible()
        self._content_panel.show()
        if first_show:
            total = self._center_split.height()
            self._center_split.setSizes([max(total - 260, 120), 260, 0])

    # ── quiz panel ───────────────────────────────────────────────────────────
    # An interactive twin of the content panel. The plugin only ever hands over
    # questions; everything about asking, marking and reporting happens here,
    # and the finished result is pushed back into the conversation the same way
    # a dropped file is — as a message JARVIS reads and responds to. That keeps
    # the tool call short (it returns the moment the board is up) and leaves the
    # talking to the assistant, in the user's own language.

    def _quiz_btn(self, text: str, primary: bool = False) -> QPushButton:
        b = QPushButton(text)
        b.setFont(QFont("Courier New", 8))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setMinimumHeight(24)
        edge = C.BORDER_B if primary else C.BORDER
        col = C.PRI if primary else C.TEXT_MED
        b.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL2}; color: {col};
                border: 1px solid {edge}; border-radius: 2px;
                padding: 3px 9px; text-align: left;
            }}
            QPushButton:hover {{ color: {C.WHITE}; border-color: {C.PRI_DIM}; }}
            QPushButton:disabled {{ color: {C.TEXT_DIM}; border-color: {C.BORDER}; }}
        """)
        return b

    def _build_quiz_panel(self) -> QWidget:
        w = QWidget()
        w.setObjectName("QuizPanel")
        w.setStyleSheet(f"""
            QWidget#QuizPanel {{
                background: {C.PANEL};
                border-top: 1px solid {C.BORDER_B};
            }}
        """)
        w.hide()

        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 7, 12, 8)
        lay.setSpacing(6)

        hdr = QHBoxLayout(); hdr.setSpacing(6)
        dot = QLabel("◈")
        dot.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        dot.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(dot)

        self._quiz_title_lbl = QLabel("QUIZ")
        self._quiz_title_lbl.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._quiz_title_lbl.setStyleSheet(
            f"color: {C.PRI}; background: transparent; letter-spacing: 1px;")
        hdr.addWidget(self._quiz_title_lbl)
        hdr.addStretch()

        self._quiz_count_lbl = QLabel("")
        self._quiz_count_lbl.setFont(QFont("Courier New", 7))
        self._quiz_count_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        hdr.addWidget(self._quiz_count_lbl)

        quit_btn = QPushButton("DISMISS  ✕")
        quit_btn.setFont(QFont("Courier New", 7))
        quit_btn.setFixedHeight(18)
        quit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        quit_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 2px; padding: 0 5px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        quit_btn.clicked.connect(self._hide_quiz)
        hdr.addWidget(quit_btn)
        lay.addLayout(hdr)

        rule = QFrame(); rule.setFixedHeight(1)
        rule.setStyleSheet(f"background: {C.BORDER};")
        lay.addWidget(rule)

        self._quiz_q_lbl = QLabel("")
        self._quiz_q_lbl.setWordWrap(True)
        self._quiz_q_lbl.setFont(QFont("Courier New", 9))
        self._quiz_q_lbl.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        lay.addWidget(self._quiz_q_lbl)

        self._quiz_answers = QWidget()
        self._quiz_answers.setStyleSheet("background: transparent;")
        self._quiz_answers_lay = QVBoxLayout(self._quiz_answers)
        self._quiz_answers_lay.setContentsMargins(0, 2, 0, 0)
        self._quiz_answers_lay.setSpacing(4)
        lay.addWidget(self._quiz_answers)

        self._quiz_note_lbl = QLabel("")
        self._quiz_note_lbl.setWordWrap(True)
        self._quiz_note_lbl.setFont(QFont("Courier New", 8))
        self._quiz_note_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._quiz_note_lbl.hide()
        lay.addWidget(self._quiz_note_lbl)

        foot = QHBoxLayout()
        foot.addStretch()
        self._quiz_next_btn = self._quiz_btn("NEXT  →", primary=True)
        self._quiz_next_btn.setFixedWidth(110)
        self._quiz_next_btn.clicked.connect(self._quiz_next)
        self._quiz_next_btn.hide()
        foot.addWidget(self._quiz_next_btn)
        lay.addLayout(foot)

        self._quiz = None
        return w

    def _show_quiz(self, topic: str, questions, grader=None):
        """Slot — Qt main thread. Puts a fresh quiz on the board."""
        if not questions:
            return
        self._quiz = {
            "topic": topic or "",
            "questions": list(questions),
            "grader": grader,
            "i": 0,
            "results": [],
            "answered": False,
        }
        self._quiz_title_lbl.setText((topic or "quiz").upper()[:48])
        self.hud.glance(0.0, -0.85, hold=1.3)
        first_show = not self._quiz_panel.isVisible()
        self._quiz_panel.show()
        if first_show:
            total = self._center_split.height()
            self._center_split.setSizes([max(total - 250, 120), 0, 250])
        self._quiz_render()

    def _hide_quiz(self):
        self._quiz = None
        self._quiz_panel.hide()

    def _quiz_clear_answers(self):
        while self._quiz_answers_lay.count():
            item = self._quiz_answers_lay.takeAt(0)
            child = item.widget()
            if child is not None:
                child.setParent(None)
                child.deleteLater()

    def _quiz_render(self):
        q = self._quiz["questions"][self._quiz["i"]]
        n, total = self._quiz["i"] + 1, len(self._quiz["questions"])
        self._quiz_count_lbl.setText(f"{n} / {total}")
        self._quiz_q_lbl.setText(q.get("question", ""))
        self._quiz_note_lbl.hide()
        self._quiz_next_btn.hide()
        self._quiz["answered"] = False
        self._quiz_clear_answers()

        opts = q.get("options") or []
        if opts:
            for text in opts:
                b = self._quiz_btn("   " + text)
                b.clicked.connect(lambda _=False, t=text: self._quiz_submit(t))
                self._quiz_answers_lay.addWidget(b)
        else:
            row = QWidget(); row.setStyleSheet("background: transparent;")
            h = QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(6)
            field = QLineEdit()
            field.setFont(QFont("Courier New", 9))
            field.setPlaceholderText("your answer")
            field.setStyleSheet(f"""
                QLineEdit {{
                    background: {C.PANEL2}; color: {C.WHITE};
                    border: 1px solid {C.BORDER}; border-radius: 2px; padding: 4px 7px;
                }}
                QLineEdit:focus {{ border-color: {C.PRI_DIM}; }}
            """)
            send = self._quiz_btn("ANSWER", primary=True)
            send.setFixedWidth(90)
            field.returnPressed.connect(lambda: self._quiz_submit(field.text()))
            send.clicked.connect(lambda: self._quiz_submit(field.text()))
            h.addWidget(field, stretch=1)
            h.addWidget(send)
            self._quiz_answers_lay.addWidget(row)
            field.setFocus()

    def _quiz_submit(self, given: str):
        if self._quiz is None or self._quiz["answered"]:
            return
        self._quiz["answered"] = True
        q = self._quiz["questions"][self._quiz["i"]]
        grader = self._quiz.get("grader")
        verdict = None
        if callable(grader):
            try:
                verdict = grader(q, given)
            except Exception:
                verdict = None
        self._quiz["results"].append({
            "question": q.get("question", ""),
            "type": q.get("type", ""),
            "given": str(given or "").strip(),
            "answer": q.get("answer", ""),
            "correct": verdict,
        })

        for i in range(self._quiz_answers_lay.count()):
            wdg = self._quiz_answers_lay.itemAt(i).widget()
            if wdg is not None:
                wdg.setEnabled(False)

        if verdict is True:
            mark, colour = "✓  correct", C.GREEN
        elif verdict is False:
            mark, colour = "✕  " + str(q.get("answer", "")), C.RED
        else:
            # Open answers and near-miss gap-fills are JARVIS's to judge. Saying
            # so is honest; marking it wrong here would be a guess.
            mark, colour = "…  noted — I'll go over this one with you", C.ACC2
        note = q.get("note") or ""
        self._quiz_note_lbl.setText(mark + (("\n" + note) if note else ""))
        self._quiz_note_lbl.setStyleSheet(f"color: {colour}; background: transparent;")
        self._quiz_note_lbl.show()

        last = self._quiz["i"] >= len(self._quiz["questions"]) - 1
        self._quiz_next_btn.setText("FINISH  →" if last else "NEXT  →")
        self._quiz_next_btn.show()
        self._quiz_next_btn.setFocus()

    def _quiz_next(self):
        if self._quiz is None:
            return
        if self._quiz["i"] >= len(self._quiz["questions"]) - 1:
            self._quiz_finish()
        else:
            self._quiz["i"] += 1
            self._quiz_render()

    def _quiz_finish(self):
        if self._quiz is None:
            return
        topic = self._quiz["topic"]
        results = self._quiz["results"]
        right = sum(1 for r in results if r["correct"] is True)
        unsure = sum(1 for r in results if r["correct"] is None)
        total = len(results)
        self._quiz_panel.hide()
        self._quiz = None

        self._log.append_log(f"QUIZ: {topic or 'quiz'} — {right}/{total} correct")

        # Hand it back to JARVIS as a message, not as a tool return: the tool
        # call ended minutes ago. This is the same channel a dropped file uses.
        lines = [f"[QUIZ_DONE] topic={topic or 'general'} | "
                 f"auto-marked {right}/{total} correct"
                 + (f", {unsure} still need your marking" if unsure else "")]
        for i, r in enumerate(results, 1):
            state = ("correct" if r["correct"] is True
                     else "wrong" if r["correct"] is False else "NEEDS MARKING")
            lines.append(
                f"{i}. [{r['type']}] {r['question']} | they answered: "
                f"{r['given'] or '(blank)'} | expected: {r['answer']} | {state}")
        lines.append(
            "Mark every question flagged NEEDS MARKING yourself — accept an answer "
            "that means the same thing. Then tell them how they did in their own "
            "language: the score, what they got wrong and why, in a couple of "
            "sentences. Offer another round only if it fits. "
            "Remember something only if it would still matter next week — that they "
            "are working through a subject, or keep missing the same thing. A score "
            "from one session is not worth a memory, and a memory per quiz would "
            "bury the things that are.")
        msg = "\n".join(lines)
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(msg,), daemon=True).start()

    def _build_footer(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(22)
        w.setStyleSheet(f"background: {C.DARK}; border-top: 1px solid {C.BORDER};")
        lay = QHBoxLayout(w); lay.setContentsMargins(14, 0, 14, 0)

        def _fl(txt, color=C.TEXT_MED):
            l = QLabel(txt); l.setFont(QFont("Courier New", 7))
            l.setStyleSheet(f"color: {color}; background: transparent;")
            return l

        lay.addWidget(_fl("[F4] Mute  ·  [F11] Fullscreen"))
        lay.addStretch()
        lay.addWidget(_fl("By FatihMakes", C.PRI_DIM))
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
        berr   = result[4] if len(result) >= 5 else ""
        if self._remote_overlay:
            self._remote_overlay._do_close()
        cw  = self.centralWidget()
        ow, oh = RemoteKeyOverlay._OW, RemoteKeyOverlay._OH
        ov  = RemoteKeyOverlay(url, key, auto_login_url=auto, manual_url=manual,
                               expiry_secs=600, parent=cw,
                               bind_error=berr)
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
                    winreg.QueryValueEx(key, "JARVIS_AI")
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
                    winreg.DeleteValue(reg, "JARVIS_AI")
                else:
                    pythonw = Path(sys.executable).parent / "pythonw.exe"
                    exe = str(pythonw if pythonw.exists() else sys.executable)
                    winreg.SetValueEx(reg, "JARVIS_AI", 0, winreg.REG_SZ,
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

    # ── Wake word settings ───────────────────────────────────────────────────

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
    def _apply_ptt_shortcut(self, needed: bool):
        """Enable/disable local PTT while the Ctrl+Space guard remains installed."""
        self._install_shortcut_guard()
        self._ptt_local_enabled = bool(needed)
        if not needed:
            self._ptt_pressed = False
            self._ptt_hold(False)
    def _ptt_hold(self, held: bool):
        cb=getattr(self,"ptt_hold",None)
        if cb:
            try: cb(bool(held))
            except Exception: pass
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

    # ── Customization ────────────────────────────────────────────────────────────

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
        if hasattr(self, "_sub_brand_lbl"):
            self._sub_brand_lbl.setText(f"{APP_VERSION}  ·  {display}  ·  PERSONAL AI ASSISTANT")
        if hasattr(self, "_header_title_left"):
            self._header_title_left.setText(f"◩  {display} - {APP_VERSION}")
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
            _write_full_config(data)
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
        if getattr(self, "on_identity_change", None):
            try:
                self.on_identity_change()
            except Exception:
                pass
    def _centre_overlay(self, ov) -> None:
        """Centre a floating overlay and keep it inside the available client area."""
        cw = self.centralWidget()
        max_w = max(1, cw.width() - 16)
        max_h = max(1, cw.height() - 16)
        ov.adjustSize()
        # A few settings panes have taller size hints than the default window.
        # Their body is scrollable, so constraining the outer geometry is safer
        # than letting the bottom of the panel disappear below the desktop.
        width = min(max_w, max(1, ov.width()))
        height = min(max_h, max(1, ov.height()))
        ov.resize(width, height)
        ov.move(max(8, (cw.width() - width) // 2),
                max(8, (cw.height() - height) // 2))
        ov.show()
        ov.raise_()
    def _open_audio_devices(self):
        ov = AudioDeviceOverlay(parent=self.centralWidget())
        ov.picked.connect(self._on_audio_devices_applied)
        self._centre_overlay(ov)
        self._audio_overlay = ov            # keep a reference so it isn't GC'd

    def _on_audio_devices_applied(self):
        self._log.append_log("SYS: Audio devices updated.")
        if self.on_audio_device_change:
            self.on_audio_device_change()

    # ── Engine & providers ───────────────────────────────────────────────────

    def _open_providers(self):
        """Open the engine panel. Everything it needs is imported on this call.

        A failure here must not take the drawer with it — the folder can be
        deleted, which is the point of it being a folder, and the honest answer
        to that is a line in the log rather than a traceback.
        """
        try:
            ov = ProvidersOverlay(parent=self.centralWidget())
        except ImportError:
            self._log.append_log(
                "SYS: The providers folder is not installed — "
                "the assistant runs on Gemini Live.")
            return
        except Exception as e:
            self._log.append_log(f"SYS: Provider settings unavailable: {e}")
            return
        ov.changed.connect(self._on_providers_applied)
        self._centre_overlay(ov)
        self._providers_overlay = ov        # keep a reference so it isn't GC'd

    def _on_providers_applied(self):
        """Rebuild the session so the new engine takes over.

        Reuses the audio-device callback rather than adding a second one: both
        mean 'the conversation has to be rebuilt', main.py already keeps the
        transcript across such a rebuild through session resumption, and one
        path that is known to work is better than two that look alike."""
        self._log.append_log("SYS: Engine settings saved — reconnecting.")
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
        # A deliberate press settles the question: whatever the video did, or is
        # about to do, the user has now said what they want.
        self._video_auto_muted = False
        self._set_muted(not self._muted)

    def _set_muted(self, muted: bool, note: str = ""):
        muted = bool(muted)
        if muted == self._muted:
            return
        self._muted = muted
        self.hud.muted = muted
        self._style_mute_btn()
        if muted:
            self._apply_state("MUTED")
            self._log.append_log("SYS: Microphone muted." + (f" {note}" if note else ""))
        else:
            self._apply_state("LISTENING")
            self._log.append_log("SYS: Microphone active." + (f" {note}" if note else ""))

    def _style_mute_btn(self):
        if self._muted:
            self._mute_btn.setText("🔇  MICROPHONE MUTED")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #140006; color: {C.MUTED_C};
                    border: 1px solid {C.MUTED_C}; border-radius: 3px;
                }}
            """)
        else:
            self._mute_btn.setText("🎙  MICROPHONE ACTIVE")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #00140a; color: {C.GREEN};
                    border: 1px solid {C.GREEN}; border-radius: 3px;
                }}
                QPushButton:hover {{ background: #001f10; }}
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
        self._assistant_name = _read_full_config().get("assistant_name", DEFAULT_ASSISTANT_NAME) or DEFAULT_ASSISTANT_NAME
        self._log.append_log(f"SYS: Initialised. OS={os_name.upper()}. {self._assistant_name} online.")



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

    def _set_hud_style(self, mode: str):
        if mode not in _HUD_MODE_LABELS:
            mode = "orbital"
        try:
            _save_hud_ui_style(mode)
        except Exception:
            pass
        try:
            from memory.config_manager import save_hud_style
            save_hud_style("core" if mode == "reactor" else mode)
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

    def _apply_ui_preferences(self, prefs: dict, log: bool = True, refresh_weather: bool = True, persist: bool = True):
        """Apply UI-only settings without rebuilding the assistant or media engine."""
        old = dict(getattr(self, "_ui_prefs", {}) or {})
        new_prefs = _save_ui_preferences(prefs) if persist else _normalise_ui_preferences(prefs)
        new_prefs["show_command_input"] = True
        self._ui_prefs = new_prefs

        self._set_widget_position(self._ui_prefs.get("widget_position", "bottom_left"))
        self._set_command_position(self._ui_prefs.get("command_position", "bottom"))
        for attr, key in (("_left_panel", "left_width"), ("_right_panel", "right_width")):
            w = getattr(self, attr, None)
            if w is not None:
                try:
                    w.setFixedWidth(int(self._ui_prefs.get(key, w.width())))
                except Exception:
                    pass

        # System panel choices operate on the equivalent Mark 56 widgets.
        for attr, key in (
            ("_left_diag_group", "show_diagnostics"),
            ("_io_panel", "show_io"),
            ("_info_panel", "show_system_info"),
            ("_aux_panel", "show_system_status"),
            ("_status_tiles_panel", "show_status_tiles"),
            ("_activity_group", "show_activity_log"),
            ("_upload_group", "show_file_upload"),
            ("_command_group", "show_command_input"),
            ("_footer", "show_footer"),
        ):
            w = getattr(self, attr, None)
            if w is not None:
                w.setVisible(True if key == "show_command_input" else bool(self._ui_prefs.get(key, True)))

        for attr, key in (("_content_panel", "show_content_panel"), ("_quiz_panel", "show_quiz_panel")):
            w = getattr(self, attr, None)
            if w is not None:
                enabled = bool(self._ui_prefs.get(key, True))
                if not enabled:
                    w.hide()
                elif not old.get(key, True):
                    # Restore only when the user explicitly re-enables a hidden panel.
                    w.show()

        weather = getattr(self, "_weather_widget", None)
        if weather is not None:
            enabled_weather = bool(self._ui_prefs.get("show_weather", False))
            weather.setVisible(enabled_weather)
            weather.set_location(
                self._ui_prefs.get("weather_location", ""),
                refresh=bool(refresh_weather and enabled_weather and self._ui_prefs.get("weather_location")),
            )
        for attr, key in (
            ("_clock_widget", "show_clock"),
            ("_battery_widget", "show_battery"),
            ("_network_widget", "show_network_widget"),
            ("_quick_notes_widget", "show_quick_notes"),
        ):
            w = getattr(self, attr, None)
            if w is not None:
                w.setVisible(bool(self._ui_prefs.get(key, False)))

        deck = getattr(self, "_utility_deck", None)
        if deck is not None:
            any_utility = any(bool(self._ui_prefs.get(k, False)) for k in (
                "show_weather", "show_clock", "show_battery", "show_network_widget", "show_quick_notes"
            ))
            deck.setVisible(any_utility)
            if any_utility:
                deck.adjustSize()

        hud = getattr(self, "hud", None)
        if hud is not None:
            hud._ui_prefs = dict(self._ui_prefs)
            hud.update()
        if log and getattr(self, "_log", None) is not None:
            self._log.append_log("SYS: UI Workbench settings applied.")
        self._refresh_settings_overlay()

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

    def _refresh_settings_overlay(self):
        ov = getattr(self, "_settings_overlay", None)
        if ov is not None:
            try:
                ov._refresh_state()
            except Exception:
                pass

    # ── Master settings ─────────────────────────────────────────────────────────

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

    def _on_quick_notes_changed(self, text: str):
        self._ui_prefs["quick_notes"] = str(text or "")[:3000]
        try: self._ui_save_timer.start(650)
        except Exception: pass

    # ── UI Workbench ───────────────────────────────────────────────────────────

    def _reset_assistant_defaults(self):
        """Restore JANE identity defaults without touching providers, API keys, audio or behavior."""
        self._apply_name_update(DEFAULT_ASSISTANT_NAME, "", DEFAULT_UI_COLOR, "", DEFAULT_ASSISTANT_TAGLINE)
        self._log.append_log("SYS: Assistant identity reset to defaults.")

    def closeEvent(self, event):
        """Release local PTT and stop active media/camera work during shutdown."""
        try:
            self._ptt_hold(False)
            self._ptt_pressed = False
            self._ptt_local_enabled = False
            app = QApplication.instance()
            if app is not None and getattr(self, "_ptt_event_filter_installed", False):
                app.removeEventFilter(self)
                self._ptt_event_filter_installed = False
        except Exception:
            pass
        try:
            self.stop_video()
        except Exception:
            pass
        try:
            self.stop_camera_stream()
        except Exception:
            pass
        super().closeEvent(event)
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

    def set_audio_level(self, level: float) -> None:
        """Thread-safe: feed a 0.0–1.0 live audio level to the HUD waveform.
        Called from the audio threads; a plain float store is atomic under the
        GIL, so no signal/lock is needed for this cosmetic value."""
        try:
            self._win.hud.set_audio_level(level)
        except Exception:
            pass

    def glance(self, dx: float, dy: float, hold: float = 1.1) -> None:
        """Ask the avatar to look somewhere for a moment (see HoloAvatar.glance)."""
        try:
            if self._avatar is not None:
                self._avatar.glance(dx, dy, hold)
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
        """Thread-safe: post a schedule of (level, openness, width) mouth frames
        for JARVIS's own speech. `at` is the wall-clock time the batch begins to
        sound, not the time of the call. See HudCanvas.push_visemes()."""
        try:
            self._win.hud.push_visemes(frames, hop, at)
        except Exception:
            pass

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

    def show_quiz(self, topic: str, questions, grade=None) -> None:
        """Thread-safe: put an interactive quiz on the board.

        `grade(question, given)` decides each answer — the plugin supplies it so
        the marking rules live with the questions rather than being duplicated
        here. Returning None from it means "JARVIS should judge this one", which
        is how open answers and near-miss gap-fills are handled.

        Returns immediately: the user answers at their own pace and the finished
        result is delivered back through on_text_command.
        """
        self._win._quiz_sig.emit(str(topic or ""), list(questions or []), grade)

    def hide_quiz(self) -> None:
        """Thread-safe: clear any quiz currently on the board."""
        self._win._quiz_hide_sig.emit()

    def show_review(self, title: str, summary: str, findings, unclear=None) -> None:
        """Thread-safe: lay a document review into the panel below the HUD.

        `findings` is a list of {heading, detail, severity, quote, suggestion};
        severity is one of 'serious' / 'caution' / 'note' and decides colour and
        order here, so the caller supplies no styling of its own.
        """
        self._win._review_sig.emit(str(title or ""), str(summary or ""),
                                   list(findings or []), list(unclear or []))

    def prompt_reconfig(self):
        """Thread-safe: show the API key setup overlay (e.g. after an auth error)."""
        self._win._ready = False
        self._win._reconfig_sig.emit()

    def show_camera_frame(self, img_bytes: bytes):
        """Thread-safe: show a webcam frame in the small overlay (screen captures)."""
        self._win._camera_sig.emit(img_bytes)

    def show_video(self, source: str, title: str = "", muted: bool = True,
                   audio_source: str = "") -> None:
        """Thread-safe: play a video where the avatar normally is.

        `source` is a local path or a direct media URL. `audio_source` is for
        the case where the sound arrives as its own stream — YouTube serves no
        combined format any more — and when it is given the two are played
        together and kept in step.

        Muted by default, and that is a decision rather than a default: a
        soundtrack talking over JARVIS is the one way this could make the
        assistant worse. The user turns sound on from the header button or by
        asking, and closes it the same two ways.
        """
        self._win._video_open_sig.emit(str(source or ""), str(title or ""),
                                       bool(muted), str(audio_source or ""))

    def stop_video(self) -> None:
        """Thread-safe: close the video and give the HUD back to the avatar."""
        self._win._video_close_sig.emit()

    def set_video_muted(self, muted: bool) -> None:
        """Thread-safe: silence or unsilence whatever is playing."""
        self._win._video_mute_sig.emit(bool(muted))

    def video_is_playing(self) -> bool:
        return bool(self._win.video_is_playing())

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

    @property
    def on_identity_change(self):
        return self._win.on_identity_change

    @property
    def wake_word_phrase(self) -> str:
        return self._win._wake_word_phrase

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

    def set_speech_level(self, level: float) -> None:
        """Thread-safe output speech amplitude hook for TTS playback."""
        try:
            self._win.hud.set_speech_level(level)
        except Exception:
            pass