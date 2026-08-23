import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtWidgets import QApplication, QDialog

from fleasion.app import _FirstTimeSetupDialog


def _qapp():
    return QApplication.instance() or QApplication([])


def test_first_time_setup_dialog_keeps_acknowledgement_on_screen():
    app = _qapp()
    dialog = _FirstTimeSetupDialog()
    dialog.setText(('Welcome to Fleasion!\n\n' + 'Long setup instructions ' * 12 + '\n\n') * 40)
    dialog.ok_button.setText('OK (15s)')
    dialog.ok_button.setEnabled(False)

    dialog.show()
    app.processEvents()

    available = dialog.screen().availableGeometry()
    button_bottom = dialog.ok_button.mapTo(dialog, dialog.ok_button.rect().bottomRight()).y()

    assert dialog.width() <= int(available.width() * 0.90)
    assert dialog.height() <= int(available.height() * 0.85)
    assert dialog._body.verticalScrollBar().maximum() > 0
    assert dialog.ok_button.isVisible()
    assert button_bottom < dialog.height()

    dialog.accept()
    assert dialog.isVisible()

    dialog.allow_accept()
    dialog.ok_button.setEnabled(True)
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
