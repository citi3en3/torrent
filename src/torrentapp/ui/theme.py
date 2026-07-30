"""Dark theme.

The palette matches the user's other tool (TIDALDOWNLOAD) so the two feel like
they belong to the same set: near-black background, off-white text, cyan accent.
Cyan is used sparingly -- selection, progress and focus only -- because at full
saturation across large areas it becomes hard to read.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette

COLORS = {
    "bg": "#121212",
    "surface": "#1a1a1a",
    "card": "#1e1e1e",
    "border": "#2c2c2c",
    "border_strong": "#3a3a3a",
    "fg": "#e0e0e0",
    "fg_dim": "#9a9a9a",
    "fg_faint": "#6a6a6a",
    "accent": "#00d4d4",
    "accent_bright": "#00ffff",
    "accent_dim": "#008b8b",
    "success": "#00e676",
    "warning": "#ffd740",
    "error": "#ff5252",
    "selection": "#10363a",
}

# Status label -> colour, used by the table's status column.
STATE_COLORS = {
    "downloading": COLORS["accent_bright"],
    "seeding": COLORS["success"],
    "finished": COLORS["success"],
    "paused": COLORS["fg_faint"],
    "checking": COLORS["warning"],
    "checking_resume": COLORS["warning"],
    "metadata": COLORS["warning"],
    "allocating": COLORS["warning"],
    "error": COLORS["error"],
    "unknown": COLORS["fg_dim"],
}

STATE_LABELS = {
    "downloading": "Downloading",
    "seeding": "Seeding",
    "finished": "Finished",
    "paused": "Paused",
    "checking": "Checking",
    "checking_resume": "Checking resume",
    "metadata": "Fetching metadata",
    "allocating": "Allocating",
    "error": "Error",
    "unknown": "Unknown",
}


def build_palette() -> QPalette:
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(COLORS["bg"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(COLORS["fg"]))
    palette.setColor(QPalette.ColorRole.Base, QColor(COLORS["surface"]))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(COLORS["card"]))
    palette.setColor(QPalette.ColorRole.Text, QColor(COLORS["fg"]))
    palette.setColor(QPalette.ColorRole.Button, QColor(COLORS["card"]))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(COLORS["fg"]))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(COLORS["selection"]))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(COLORS["accent_bright"]))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(COLORS["card"]))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(COLORS["fg"]))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(COLORS["fg_faint"]))
    palette.setColor(
        QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(COLORS["fg_faint"])
    )
    palette.setColor(
        QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(COLORS["fg_faint"])
    )
    return palette


STYLESHEET = f"""
QWidget {{
    background-color: {COLORS["bg"]};
    color: {COLORS["fg"]};
    font-size: 13px;
}}

QMainWindow, QDialog {{ background-color: {COLORS["bg"]}; }}

/* ---------------------------------------------------------------- toolbar */
QToolBar {{
    background-color: {COLORS["surface"]};
    border: none;
    border-bottom: 1px solid {COLORS["border"]};
    padding: 6px 8px;
    spacing: 4px;
}}
QToolButton {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 6px 12px;
    color: {COLORS["fg"]};
}}
QToolButton:hover:!disabled {{
    background-color: {COLORS["card"]};
    border-color: {COLORS["border_strong"]};
}}
QToolButton:pressed {{ background-color: {COLORS["selection"]}; }}
QToolButton:disabled {{ color: {COLORS["fg_faint"]}; }}

/* ------------------------------------------------------------------ table */
QTableView, QTreeWidget, QTreeView, QListView {{
    background-color: {COLORS["surface"]};
    alternate-background-color: {COLORS["card"]};
    border: 1px solid {COLORS["border"]};
    border-radius: 8px;
    gridline-color: {COLORS["border"]};
    selection-background-color: {COLORS["selection"]};
    selection-color: {COLORS["accent_bright"]};
    outline: none;
}}
QTableView::item, QTreeWidget::item {{ padding: 4px 6px; }}
QTableView::item:selected, QTreeWidget::item:selected {{
    background-color: {COLORS["selection"]};
    color: {COLORS["accent_bright"]};
}}
QHeaderView::section {{
    background-color: {COLORS["card"]};
    color: {COLORS["fg_dim"]};
    padding: 7px 8px;
    border: none;
    border-right: 1px solid {COLORS["border"]};
    border-bottom: 1px solid {COLORS["border"]};
    font-weight: 600;
}}
QHeaderView::section:hover {{ color: {COLORS["fg"]}; }}

/* ------------------------------------------------------------------ inputs */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit {{
    background-color: {COLORS["card"]};
    border: 1px solid {COLORS["border_strong"]};
    border-radius: 6px;
    padding: 6px 9px;
    selection-background-color: {COLORS["selection"]};
    selection-color: {COLORS["accent_bright"]};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {COLORS["accent"]};
}}
QLineEdit:disabled, QComboBox:disabled {{ color: {COLORS["fg_faint"]}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background-color: {COLORS["card"]};
    border: 1px solid {COLORS["border_strong"]};
    selection-background-color: {COLORS["selection"]};
}}

/* ----------------------------------------------------------------- buttons */
QPushButton {{
    background-color: {COLORS["card"]};
    border: 1px solid {COLORS["border_strong"]};
    border-radius: 6px;
    padding: 7px 16px;
    min-width: 78px;
}}
QPushButton:hover:!disabled {{ border-color: {COLORS["accent_dim"]}; }}
QPushButton:pressed {{ background-color: {COLORS["selection"]}; }}
QPushButton:disabled {{ color: {COLORS["fg_faint"]}; border-color: {COLORS["border"]}; }}
QPushButton:default {{
    border-color: {COLORS["accent"]};
    color: {COLORS["accent_bright"]};
}}

/* -------------------------------------------------------------- checkboxes */
QCheckBox, QRadioButton {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator, QTreeWidget::indicator {{
    width: 15px; height: 15px;
    border: 1px solid {COLORS["border_strong"]};
    border-radius: 3px;
    background-color: {COLORS["card"]};
}}
QCheckBox::indicator:checked, QTreeWidget::indicator:checked {{
    background-color: {COLORS["accent"]};
    border-color: {COLORS["accent"]};
}}
QCheckBox::indicator:indeterminate, QTreeWidget::indicator:indeterminate {{
    background-color: {COLORS["accent_dim"]};
    border-color: {COLORS["accent_dim"]};
}}

/* --------------------------------------------------------------- scrollbars */
QScrollBar:vertical {{ background: transparent; width: 11px; margin: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 0; }}
QScrollBar::handle {{ background: {COLORS["border_strong"]}; border-radius: 5px; }}
QScrollBar::handle:hover {{ background: {COLORS["accent_dim"]}; }}
QScrollBar::handle:vertical {{ min-height: 28px; }}
QScrollBar::handle:horizontal {{ min-width: 28px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ------------------------------------------------------------------ misc */
QProgressBar {{
    background-color: {COLORS["card"]};
    border: 1px solid {COLORS["border"]};
    border-radius: 5px;
    text-align: center;
    color: {COLORS["fg"]};
}}
QProgressBar::chunk {{ background-color: {COLORS["accent_dim"]}; border-radius: 4px; }}

QStatusBar {{
    background-color: {COLORS["surface"]};
    border-top: 1px solid {COLORS["border"]};
    color: {COLORS["fg_dim"]};
}}
QStatusBar::item {{ border: none; }}

QMenu {{
    background-color: {COLORS["card"]};
    border: 1px solid {COLORS["border_strong"]};
    border-radius: 6px;
    padding: 5px;
}}
QMenu::item {{ padding: 6px 24px 6px 14px; border-radius: 4px; }}
QMenu::item:selected {{ background-color: {COLORS["selection"]}; color: {COLORS["accent_bright"]}; }}
QMenu::separator {{ height: 1px; background: {COLORS["border"]}; margin: 5px 8px; }}

QTabWidget::pane {{ border: 1px solid {COLORS["border"]}; border-radius: 8px; top: -1px; }}
QTabBar::tab {{
    background: transparent;
    padding: 7px 16px;
    border-bottom: 2px solid transparent;
    color: {COLORS["fg_dim"]};
}}
QTabBar::tab:selected {{ color: {COLORS["accent_bright"]}; border-bottom-color: {COLORS["accent"]}; }}
QTabBar::tab:hover:!selected {{ color: {COLORS["fg"]}; }}

QSplitter::handle {{ background: {COLORS["border"]}; }}
QSplitter::handle:hover {{ background: {COLORS["accent_dim"]}; }}

QToolTip {{
    background-color: {COLORS["card"]};
    color: {COLORS["fg"]};
    border: 1px solid {COLORS["border_strong"]};
    padding: 5px 8px;
}}

QLabel#Heading {{ font-size: 15px; font-weight: 600; }}
QLabel#Subtle {{ color: {COLORS["fg_dim"]}; }}
QLabel#Reason {{ color: {COLORS["accent"]}; }}
QLabel#Warning {{ color: {COLORS["error"]}; font-weight: 600; }}
QFrame#Banner {{
    background-color: {COLORS["card"]};
    border: 1px solid {COLORS["accent_dim"]};
    border-radius: 8px;
}}
"""


def apply(app) -> None:
    """Apply palette + stylesheet to a QApplication."""
    app.setStyle("Fusion")
    app.setPalette(build_palette())
    app.setStyleSheet(STYLESHEET)
