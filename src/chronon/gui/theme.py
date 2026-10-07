"""Colours, fonts and the style sheet of docs/design/ui/README.md ("Tokens", "Layout")."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QPalette

TOKENS = {
    "dark": {
        "background": "#151618",
        "sidebar": "#1B1C1F",
        "panel": "#202124",
        "raised": "#2B2D31",
        "table_header": "#1D1E21",
        "input": "#1A1B1E",
        "hover": "#26282C",
        "card_border": "#34363B",
        "divider": "#2C2E33",
        "button_border": "#4A4D54",
        "control_border": "#5A5D64",
        "text": "#E6E7E9",
        "text2": "#9A9DA4",
        "text3": "#7E8188",
        "wave": "#6E727A",
        "accent": "#6AA7F5",
        "on_accent": "#0E1114",
        "green": "#8FD6A9",
        "green_border": "#3F7A58",
        "amber": "#F0C06A",
        "amber_border": "#8A6A2A",
        "red": "#F0958E",
        "red_border": "#8A403B",
    },
    "light": {
        "background": "#F3F4F6",
        "sidebar": "#E9EBEE",
        "panel": "#FFFFFF",
        "raised": "#FFFFFF",
        "table_header": "#F0F1F4",
        "input": "#F6F7F9",
        "hover": "#E8EAEE",
        "card_border": "#D5D8DD",
        "divider": "#E1E3E7",
        "button_border": "#BCC0C7",
        "control_border": "#A5A9B1",
        "text": "#1B1C1F",
        "text2": "#5E626A",
        "text3": "#7B7F87",
        "wave": "#8E939B",
        "accent": "#2F7DE1",
        "on_accent": "#FFFFFF",
        "green": "#1F7A47",
        "green_border": "#86C3A0",
        "amber": "#8A5A00",
        "amber_border": "#D4AE5E",
        "red": "#B3342B",
        "red_border": "#E2A29C",
    },
}

UI_FONTS = ["IBM Plex Sans", "Inter", "Segoe UI", "Helvetica Neue", "Arial"]
MONO_FONTS = ["IBM Plex Mono", "SF Mono", "Menlo", "Consolas", "DejaVu Sans Mono"]


def system_scheme() -> str:
    hints = QGuiApplication.styleHints()
    return "dark" if hints.colorScheme() == Qt.ColorScheme.Dark else "light"


def resolve(appearance: str) -> str:
    """``system`` / ``light`` / ``dark`` (the settings row) to a token set."""
    return system_scheme() if appearance == "system" else appearance


def _family(candidates: list[str]) -> str:
    """The first installed family (IBM Plex is not bundled yet)."""
    installed = set(QFontDatabase.families())
    return next((f for f in candidates if f in installed), candidates[-1])


def mono_font(size: int = 12) -> QFont:
    font = QFont(_family(MONO_FONTS))
    font.setPixelSize(size)
    font.setStyleHint(QFont.StyleHint.Monospace)
    return font


def palette(t: dict[str, str]) -> QPalette:
    p = QPalette()
    roles = {
        QPalette.ColorRole.Window: t["background"],
        QPalette.ColorRole.Base: t["panel"],
        QPalette.ColorRole.AlternateBase: t["input"],
        QPalette.ColorRole.Button: t["raised"],
        QPalette.ColorRole.Text: t["text"],
        QPalette.ColorRole.WindowText: t["text"],
        QPalette.ColorRole.ButtonText: t["text"],
        QPalette.ColorRole.PlaceholderText: t["text3"],
        QPalette.ColorRole.Highlight: t["accent"],
        QPalette.ColorRole.HighlightedText: t["on_accent"],
        QPalette.ColorRole.ToolTipBase: t["raised"],
        QPalette.ColorRole.ToolTipText: t["text"],
    }
    for role, colour in roles.items():
        p.setColor(role, QColor(colour))
    return p


def _mix(a: str, b: str, share: float) -> str:
    """Colour ``a`` laid over ``b`` at ``share`` opacity (badge and row fills)."""
    ca, cb = QColor(a), QColor(b)
    pairs = zip(ca.getRgb()[:3], cb.getRgb()[:3], strict=True)
    mixed = [round(x * share + y * (1 - share)) for x, y in pairs]
    return QColor(*mixed).name()


def stylesheet(t: dict[str, str]) -> str:
    """Widgets are styled by object name (``#sidebar``) and the ``role`` property."""
    ui = _family(UI_FONTS)
    mono = _family(MONO_FONTS)
    return f"""
* {{ font-family: "{ui}"; font-size: 13px; color: {t["text"]}; }}
QMainWindow, #content {{ background: {t["background"]}; }}
#sidebar {{ background: {t["sidebar"]}; border-right: 1px solid {t["divider"]}; }}
#appname {{ font-size: 18px; font-weight: 600; padding: 4px 10px 14px 10px; }}
QPushButton[role="step"] {{
    text-align: left; border: none; border-radius: 4px; padding: 0 10px; min-height: 36px;
    background: transparent; color: {t["text2"]};
}}
QPushButton[role="step"]:hover {{ background: {t["hover"]}; }}
QPushButton[role="step"]:checked {{ background: {t["raised"]}; }}
#stepnumber {{
    border: 1px solid {t["button_border"]}; border-radius: 4px; font-size: 11px;
    color: {t["text2"]}; background: transparent;
}}
#stepname {{ color: {t["text2"]}; background: transparent; }}
#stepnumber[current="true"] {{
    background: {t["accent"]}; border-color: {t["accent"]}; color: {t["on_accent"]};
}}
#stepname[current="true"] {{ color: {t["text"]}; }}
#header {{ background: {t["background"]}; border-bottom: 1px solid {t["divider"]}; }}
#steplabel {{ font-size: 11px; letter-spacing: 1px; color: {t["text2"]}; }}
#title {{ font-size: 18px; font-weight: 600; }}
#headerright {{ font-size: 12px; color: {t["text2"]}; }}
#footer {{ background: {t["background"]}; border-top: 1px solid {t["divider"]}; }}
QPushButton {{
    background: {t["raised"]}; border: 1px solid {t["button_border"]}; border-radius: 4px;
    padding: 0 14px; min-height: 30px;
}}
QPushButton:hover {{ background: {t["hover"]}; }}
QPushButton[role="primary"] {{
    background: {t["accent"]}; color: {t["on_accent"]}; border: 1px solid {t["accent"]};
    font-weight: 600; min-width: 112px;
}}
QPushButton[role="primary"]:disabled {{
    background: {
        QColor(t["accent"]).lighter(140).name()
        if t is TOKENS["light"]
        else QColor(t["accent"]).darker(190).name()
    };
    border-color: transparent; color: {t["on_accent"]};
}}
QPushButton:disabled {{ color: {t["text3"]}; }}
#card {{ background: {t["panel"]}; border: 1px solid {t["card_border"]}; border-radius: 4px; }}
#dropzone {{ border: 2px dashed {t["button_border"]}; border-radius: 4px; }}
#dropzone[hover="true"] {{ border-color: {t["accent"]}; }}
#dropicon {{
    background: {t["raised"]}; border: 1px solid {t["card_border"]}; border-radius: 4px;
    font-size: 22px; color: {t["text2"]};
}}
#headline {{ font-size: 22px; font-weight: 600; }}
#hint {{ font-size: 13px; color: {t["text2"]}; }}
#counter {{ font-family: "{mono}"; font-size: 22px; }}
#current {{ font-family: "{mono}"; font-size: 12px; color: {t["text2"]}; }}
QProgressBar {{ background: {t["divider"]}; border: none; border-radius: 2px; max-height: 8px; }}
QProgressBar::chunk {{ background: {t["accent"]}; border-radius: 2px; }}
QTableView {{
    background: {t["panel"]}; border: 1px solid {t["card_border"]}; border-radius: 4px;
    gridline-color: transparent; selection-background-color: {t["hover"]};
    selection-color: {t["text"]}; font-size: 12px;
}}
QTableView::item {{ border-bottom: 1px solid {t["divider"]}; padding: 0 6px; }}
QHeaderView::section {{
    background: {t["table_header"]}; color: {t["text2"]}; border: none;
    border-bottom: 1px solid {t["divider"]}; font-size: 11px; letter-spacing: 1px;
    padding: 0 12px; min-height: 30px;
}}
QDialog {{ background: {t["panel"]}; }}
#dialogicon {{
    background: transparent; border: 1px solid {t["red_border"]}; border-radius: 4px;
    color: {t["red"]}; font-weight: 700; font-size: 16px;
}}
#dialogtitle {{ font-size: 18px; font-weight: 600; }}
#detail {{
    font-family: "{mono}"; font-size: 12px; color: {t["text2"]}; background: {t["input"]};
    border: 1px solid {t["divider"]}; border-radius: 4px; padding: 8px 12px;
}}
#placeholder {{ color: {t["text2"]}; }}
#rule {{ background: {t["divider"]}; }}
#percent {{ font-size: 22px; }}
QProgressBar[stopped="true"]::chunk {{ background: {t["text3"]}; }}
#steprow {{ border-top: 1px solid {t["divider"]}; }}
#stepmark {{ border: 1px dashed {t["control_border"]}; border-radius: 4px; color: {t["green"]};
    font-weight: 700; }}
#stepmark[state="done"] {{ border: 1px solid {t["green_border"]};
    background: {_mix(t["green"], t["panel"], 0.12)}; }}
#stepmark[state="running"] {{ border: none; }}
QLabel[state="waiting"], QLabel[state="cancelled"] {{ color: {t["text3"]}; }}
#badge_ok, #badge_wanders, #badge_unsure, #badge_bad, #badge_ref {{
    border-radius: 4px; padding: 2px 9px; font-size: 12px; font-weight: 500; }}
#badge_ok {{ color: {t["green"]}; border: 1px solid {t["green_border"]};
    background: {_mix(t["green"], t["panel"], 0.12)}; }}
#badge_wanders {{ color: {t["amber"]}; border: 1px solid {t["amber_border"]};
    background: {_mix(t["amber"], t["panel"], 0.12)}; }}
#badge_unsure {{ color: {t["text2"]}; border: 1px dashed {t["control_border"]}; }}
#badge_bad {{ color: {t["red"]}; border: 1px solid {t["red_border"]};
    background: {_mix(t["red"], t["panel"], 0.12)}; }}
#badge_ref {{ color: {t["on_accent"]}; background: {t["accent"]}; border: 1px solid {t["accent"]};
    font-weight: 600; }}
#banner_warn {{ background: {_mix(t["amber"], t["background"], 0.10)};
    border: 1px solid {t["amber_border"]}; border-radius: 4px; }}
#banner_error {{ background: {_mix(t["red"], t["background"], 0.10)};
    border: 1px solid {t["red_border"]}; border-radius: 4px; }}
#bannericon_warn {{ color: {t["amber"]}; font-size: 18px; font-weight: 700; }}
#bannericon_error {{ color: {t["red"]}; font-size: 18px; font-weight: 700; }}
#square_ok {{ color: {t["green"]}; border: 1px solid {t["green_border"]}; border-radius: 4px;
    background: {_mix(t["green"], t["panel"], 0.12)}; font-size: 20px; font-weight: 700; }}
#square_bad {{ color: {t["red"]}; border: 1px solid {t["red_border"]}; border-radius: 4px;
    background: {_mix(t["red"], t["panel"], 0.12)}; font-size: 20px; font-weight: 700; }}
QPushButton[role="segment"] {{ border-radius: 0; min-height: 28px; }}
QPushButton[role="segment"][edge="first"] {{ border-top-left-radius: 4px;
    border-bottom-left-radius: 4px; }}
QPushButton[role="segment"][edge="last"] {{ border-top-right-radius: 4px;
    border-bottom-right-radius: 4px; }}
QPushButton[role="segment"]:checked {{ background: {t["accent"]}; color: {t["on_accent"]};
    border-color: {t["accent"]}; font-weight: 600; }}
#bigtime {{ font-family: "{mono}"; font-size: 15px; padding: 0 8px; }}
QSlider::groove:horizontal {{ height: 4px; background: {t["divider"]}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {t["text2"]}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {t["accent"]}; width: 12px; margin: -7px 0;
    border-radius: 2px; }}
#section {{ font-size: 11px; letter-spacing: 1px; color: {t["text2"]}; font-weight: 600; }}
#muted {{ font-size: 12px; color: {t["text2"]}; }}
#problem {{ font-size: 12px; color: {t["red"]}; }}
#mono {{ font-family: "{mono}"; font-size: 12px; }}
#cardtitle {{ font-size: 15px; font-weight: 600; }}
#colhead {{ font-size: 11px; letter-spacing: 1px; color: {t["text2"]}; }}
#tablehead {{ background: {t["table_header"]}; border-bottom: 1px solid {t["divider"]};
    border-top-left-radius: 4px; border-top-right-radius: 4px; }}
#devicerow {{ background: {t["panel"]}; border-bottom: 1px solid {t["divider"]}; }}
#devicerow[reference="true"] {{ background: {_mix(t["accent"], t["panel"], 0.08)}; }}
#handle {{ color: {t["text3"]}; font-size: 16px; }}
#badgeref {{ background: {t["accent"]}; color: {t["on_accent"]}; border-radius: 4px;
    padding: 3px 9px; font-size: 12px; font-weight: 600; }}
#badgesuggest {{ border: 1px solid {t["button_border"]}; border-radius: 4px; padding: 2px 8px;
    font-size: 12px; color: {t["text"]}; }}
QPushButton[role="small"] {{ min-height: 24px; padding: 0 10px; font-size: 12px; }}
QPushButton[role="small"][open="true"] {{ background: {t["hover"]}; }}
#filespanel {{ background: {t["input"]}; border-bottom: 1px solid {t["divider"]}; }}
#filerow {{ border-top: 1px solid {t["divider"]}; }}
#selectionbar {{ background: {t["table_header"]}; border-top: 1px solid {t["divider"]}; }}
QPushButton[role="chip"] {{ min-height: 30px; padding: 0 12px; font-size: 12px; }}
QPushButton[role="more"] {{ padding: 0; min-height: 24px; font-weight: 700; }}
QPushButton[role="chip"]:checked {{ background: {t["accent"]}; color: {t["on_accent"]};
    border-color: {t["accent"]}; font-weight: 600; }}
#choices {{ border: 1px solid {t["card_border"]}; border-radius: 4px; background: {t["input"]}; }}
#choice {{ border-bottom: 1px solid {t["divider"]}; }}
#choice:disabled QLabel {{ color: {t["text3"]}; }}
"""
