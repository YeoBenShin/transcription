"""Start the GUI: settings, the onboarding on first run, then the main window."""

import sys

from PySide6.QtWidgets import QApplication, QDialog

from desktop import paths
from desktop.models import catalog
from desktop.paths import APP_NAME
from desktop.settings import Settings, settings_path
from desktop.ui import theme
from desktop.ui.dialogs import OnboardingDialog
from desktop.ui.main_window import MainWindow
from desktop.ui.models_view import DownloadManager


def needs_onboarding(settings: Settings, models_dir) -> bool:
    model = catalog.find(settings.model_id)
    return not settings.onboarded or model is None or not catalog.is_installed(model, models_dir)


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    theme.apply(app)
    app.styleHints().colorSchemeChanged.connect(lambda scheme: theme.apply(app))

    path = settings_path()
    settings = Settings.load(path)
    manager = DownloadManager(paths.models_dir())
    if needs_onboarding(settings, manager.models_dir):
        if OnboardingDialog(settings, manager).exec() != QDialog.Accepted:
            settings.save(path)
            return 0
        settings.onboarded = True
        settings.save(path)
    window = MainWindow(settings, lambda: settings.save(path), manager)
    window.show()
    return app.exec()
