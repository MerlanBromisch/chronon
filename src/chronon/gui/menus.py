"""The macOS menu bar (Chronon, Ablage, Bearbeiten, Fenster, Hilfe) and the app's name in it.
Windows and Linux keep the window without a menu bar, as designed."""

from __future__ import annotations

import ctypes
import ctypes.util
import sys
from typing import TYPE_CHECKING

from PySide6.QtCore import QUrl
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWidgets import QApplication, QLineEdit, QMessageBox, QTextEdit

from chronon import __version__

if TYPE_CHECKING:
    from chronon.gui.window import Window

REPOSITORY = "https://github.com/MerlanBromisch/chronon"


def name_the_app(name: str) -> None:
    """Started as ``python …`` (``uv run chronon-app``), macOS names the menu bar's first menu
    after the interpreter ("python3"). Setting the bundle name before the app starts fixes
    that; the packaged app has it in its Info.plist anyway."""
    if sys.platform != "darwin":
        return
    try:
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        ctypes.cdll.LoadLibrary(ctypes.util.find_library("Foundation"))
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.sel_registerName.restype = ctypes.c_void_p
        send = objc.objc_msgSend
        send.restype = ctypes.c_void_p

        def call(target, selector: bytes, *args):
            send.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + [type(a) for a in args]
            return send(target, objc.sel_registerName(selector), *args)

        def string(text: str):
            cls = objc.objc_getClass(b"NSString")
            return ctypes.c_void_p(
                call(cls, b"stringWithUTF8String:", ctypes.c_char_p(text.encode()))
            )

        bundle = call(objc.objc_getClass(b"NSBundle"), b"mainBundle")
        info = call(ctypes.c_void_p(bundle), b"infoDictionary")
        if info:
            call(ctypes.c_void_p(info), b"setObject:forKey:", string(name), string("CFBundleName"))
    except (OSError, AttributeError, TypeError):  # not worth failing the start for
        pass


def install(win: Window) -> None:
    """The menu bar for ``win`` (macOS only)."""
    if sys.platform != "darwin":
        return
    from chronon.gui.window import SETTINGS  # loaded by now (it calls this)

    bar = win.menuBar()

    def action(menu, text: str, slot, keys=None, role=None) -> QAction:
        a = QAction(text, win)
        if keys is not None:
            a.setShortcut(QKeySequence(keys))
        if role is not None:
            a.setMenuRole(role)
        a.triggered.connect(slot)
        menu.addAction(a)
        return a

    app_menu = bar.addMenu("Chronon")  # Qt moves the roles below into the app's own menu
    action(app_menu, "Über Chronon", lambda: about(win), role=QAction.MenuRole.AboutRole)
    action(app_menu, "Einstellungen …", lambda: win.show_step(SETTINGS),
           QKeySequence.StandardKey.Preferences, QAction.MenuRole.PreferencesRole)  # fmt: skip
    action(app_menu, "Chronon beenden", win.close, QKeySequence.StandardKey.Quit,
           QAction.MenuRole.QuitRole)  # fmt: skip

    file_menu = bar.addMenu("Ablage")
    action(file_menu, "Neues Projekt", win.new_project, QKeySequence.StandardKey.New)
    file_menu.addSeparator()
    action(file_menu, "Dateien hinzufügen …", lambda: _add(win, folder=False),
           QKeySequence.StandardKey.Open)  # fmt: skip
    action(file_menu, "Ordner hinzufügen …", lambda: _add(win, folder=True), "Shift+Ctrl+O")
    file_menu.addSeparator()
    finder = action(file_menu, "Im Finder zeigen", win.export.show_folder)
    file_menu.aboutToShow.connect(lambda: finder.setEnabled(win.export.state == "done"))

    edit = bar.addMenu("Bearbeiten")
    for text, keys, method in (
        ("Ausschneiden", QKeySequence.StandardKey.Cut, "cut"),
        ("Kopieren", QKeySequence.StandardKey.Copy, "copy"),
        ("Einsetzen", QKeySequence.StandardKey.Paste, "paste"),
        ("Alles auswählen", QKeySequence.StandardKey.SelectAll, "selectAll"),
    ):
        action(edit, text, lambda _c=False, m=method: _edit(m), keys)

    window = bar.addMenu("Fenster")
    action(window, "Im Dock ablegen", win.showMinimized, "Ctrl+M")
    action(window, "Zoomen", lambda: win.showNormal() if win.isMaximized() else win.showMaximized())

    help_menu = bar.addMenu("Hilfe")
    action(help_menu, "Protokoll öffnen", win.settings_page.open_log)
    action(help_menu, "Chronon auf GitHub", lambda: QDesktopServices.openUrl(QUrl(REPOSITORY)))
    win.menus = bar


def _add(win: Window, folder: bool) -> None:
    win.show_step(0)
    (win.files.choose_folder if folder else win.files.choose_files)()


def _edit(method: str) -> None:
    """Cut / copy / paste / select all in the text field that has the focus."""
    widget = QApplication.focusWidget()
    if isinstance(widget, (QLineEdit, QTextEdit)):
        getattr(widget, method)()


def about(win: Window) -> None:
    QMessageBox.about(
        win,
        "Über Chronon",
        f"<b>Chronon</b> {__version__}<br>Synchronisiert Aufnahmen mehrerer Geräte über die "
        f"Wellenform und gleicht den Drift der Uhren aus.<br><br>"
        f"<a href='{REPOSITORY}'>{REPOSITORY}</a> · MIT-Lizenz",
    )
