"""Colors and the style sheet of the app, in a light and a dark variant that follow the system."""

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette


@dataclass(frozen=True)
class Palette:
    window: str
    panel: str  # search field, small buttons
    panel_border: str
    card: str
    card_border: str
    text: str
    secondary: str
    record: str  # record button when idle
    accent: str
    highlight: str  # search match background


DARK = Palette("#323232", "#3a3a3a", "#474747", "#1f1f1f", "#3d3d3d", "#ececec", "#9a9a9a", "#ffffff", "#4a90e2",
               "#8a6d00")
LIGHT = Palette("#ffffff", "#f2f4fa", "#dbe0eb", "#ffffff", "#dbe0eb", "#1d1d1f", "#6e6e73", "#5999eb", "#2f6fd6",
                "#ffe680")
RECORDING = "#e5483f"


def is_dark() -> bool:
    return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark


def current() -> Palette:
    return DARK if is_dark() else LIGHT


def style_sheet(p: Palette) -> str:
    return f"""
    QWidget {{ color: {p.text}; font-size: 13px; }}
    QMainWindow, QDialog, #page {{ background: {p.window}; }}
    QScrollArea, #list {{ background: transparent; border: none; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    #card {{ background: {p.card}; border: 1px solid {p.card_border}; border-radius: 8px; }}
    #card:hover {{ border-color: {p.secondary}; }}
    #cardText {{ font-size: 14px; }}
    #secondary, #cardDate, #hint {{ color: {p.secondary}; font-size: 12px; }}
    #rule {{ background: {p.card_border}; max-height: 1px; min-height: 1px; }}
    #title {{ font-size: 22px; font-weight: 600; }}
    #heading {{ font-size: 15px; font-weight: 600; }}
    #iconButton {{ background: {p.panel}; border: 1px solid {p.panel_border}; border-radius: 8px; padding: 4px; }}
    #iconButton:hover {{ border-color: {p.secondary}; }}
    #flatIcon {{ background: transparent; border: none; padding: 2px; }}
    #link {{ background: transparent; border: none; color: {p.secondary}; font-size: 12px; text-align: left; padding: 0; }}
    #link:hover {{ color: {p.text}; text-decoration: underline; }}
    QPushButton#primary {{ background: {p.accent}; color: white; border: none; border-radius: 6px; padding: 6px 16px; }}
    QPushButton#primary:disabled {{ background: {p.panel_border}; color: {p.secondary}; }}
    QPushButton {{ background: {p.panel}; border: 1px solid {p.panel_border}; border-radius: 6px; padding: 5px 12px; }}
    QPushButton:hover {{ border-color: {p.secondary}; }}
    QLineEdit, QComboBox, QPlainTextEdit, QTextBrowser {{ background: {p.panel}; border: 1px solid {p.panel_border};
        border-radius: 6px; padding: 4px 6px; }}
    QLineEdit#search {{ background: {p.panel}; border: 1px solid {p.panel_border}; border-radius: 16px; padding: 6px 10px; min-height: 22px; }}
    QTextBrowser#transcript, QTextBrowser#liveText {{ background: {p.card}; border: 1px solid {p.card_border};
        border-radius: 8px; padding: 8px; font-size: 14px; }}
    QProgressBar {{ background: {p.panel}; border: 1px solid {p.panel_border}; border-radius: 4px; max-height: 8px; }}
    QProgressBar::chunk {{ background: {p.accent}; border-radius: 4px; }}
    QTabWidget::pane {{ border: 1px solid {p.panel_border}; border-radius: 6px; }}
    QTabBar::tab {{ padding: 6px 14px; }}
    QTabBar::tab:selected {{ font-weight: 600; }}
    #dropOverlay {{ background: rgba(74, 144, 226, 40); border: 2px dashed {p.accent}; border-radius: 12px; }}
    """


def palette(p: Palette) -> QPalette:
    """A Qt palette with the same colors, for the parts the style sheet does not reach (Fusion style)."""
    result = QPalette()
    for role, color in ((QPalette.Window, p.window), (QPalette.WindowText, p.text), (QPalette.Base, p.panel),
                        (QPalette.AlternateBase, p.card), (QPalette.Text, p.text), (QPalette.Button, p.panel),
                        (QPalette.ButtonText, p.text), (QPalette.Highlight, p.accent),
                        (QPalette.HighlightedText, "#ffffff"), (QPalette.PlaceholderText, p.secondary),
                        (QPalette.ToolTipBase, p.card), (QPalette.ToolTipText, p.text)):
        result.setColor(role, QColor(color))
    return result


def apply(app) -> None:
    """Set the palette and style sheet of the current system color scheme."""
    colors = current()
    app.setPalette(palette(colors))
    app.setStyleSheet(style_sheet(colors))
