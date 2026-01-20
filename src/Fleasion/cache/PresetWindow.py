# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'PresetsWindow.ui'
##
## Created by: Qt User Interface Compiler version 6.10.0
##
## WARNING! All changes made in this file will be lost when recompiling UI file!
################################################################################

from PySide6.QtCore import (QCoreApplication, QDate, QDateTime, QLocale,
    QMetaObject, QObject, QPoint, QRect,
    QSize, QTime, QUrl, Qt)
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QCursor,
    QFont, QFontDatabase, QGradient, QIcon,
    QImage, QKeySequence, QLinearGradient, QPainter,
    QPalette, QPixmap, QRadialGradient, QTransform)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QHeaderView, QPushButton,
    QSizePolicy, QTreeView, QVBoxLayout, QWidget)

class Ui_Form(object):
    def setupUi(self, Form):
        if not Form.objectName():
            Form.setObjectName(u"Form")
        Form.resize(469, 411)
        self.verticalLayout = QVBoxLayout(Form)
        self.verticalLayout.setObjectName(u"verticalLayout")
        self.verticalLayout.setContentsMargins(9, 9, 9, 9)
        self.treeView = QTreeView(Form)
        self.treeView.setObjectName(u"treeView")
        self.treeView.setStyleSheet(u"QTreeView {\n"
"    background-color: #2d2d2d;\n"
"    color: #e0e0e0;\n"
"    border: 1px solid #3c3c3c;\n"
"    outline: none;\n"
"    border-radius: 0px;\n"
"    show-decoration-selected: 1;\n"
"}\n"
"\n"
"QTreeView::item {\n"
"    padding: 2px 8px;\n"
"    border: none;\n"
"    border-radius: 0px;\n"
"    border-right: 1px solid #3c3c3c;\n"
"}\n"
"\n"
"QTreeView::item:hover {\n"
"    background-color: #3d3d3d;\n"
"    border-radius: 0px;\n"
"}\n"
"\n"
"QTreeView::item:selected {\n"
"    background-color: #404040;\n"
"    color: #e0e0e0;\n"
"    border-radius: 0px;\n"
"}\n"
"\n"
"QTreeView::item:selected:active {\n"
"    background-color: #404040;\n"
"    border-radius: 0px;\n"
"}\n"
"\n"
"QTreeView::item:selected:!active {\n"
"    background-color: #404040;\n"
"    border-radius: 0px;\n"
"}\n"
"\n"
"QHeaderView::section {\n"
"    background-color: #1e1e1e;\n"
"    color: #e0e0e0;\n"
"    padding: 3px 8px;\n"
"    border: none;\n"
"    border-right: 1px solid #3c3c3c;\n"
"    border-bottom: 1px solid #3c3c3"
                        "c;\n"
"    font-weight: bold;\n"
"    text-align: left;\n"
"    border-radius: 0px;\n"
"}\n"
"\n"
"QHeaderView::section:hover {\n"
"    background-color: #252525;\n"
"}\n"
"\n"
"QScrollBar:vertical {\n"
"    background-color: #2b2b2b;\n"
"    width: 14px;\n"
"    border: none;\n"
"}\n"
"\n"
"QScrollBar::handle:vertical {\n"
"    background-color: #505050;\n"
"    min-height: 20px;\n"
"    border-radius: 7px;\n"
"    margin: 2px;\n"
"}\n"
"\n"
"QScrollBar::handle:vertical:hover {\n"
"    background-color: #606060;\n"
"}\n"
"\n"
"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {\n"
"    height: 0px;\n"
"}\n"
"\n"
"QScrollBar:horizontal {\n"
"    background-color: #2b2b2b;\n"
"    height: 14px;\n"
"    border: none;\n"
"}\n"
"\n"
"QScrollBar::handle:horizontal {\n"
"    background-color: #505050;\n"
"    min-width: 20px;\n"
"    border-radius: 7px;\n"
"    margin: 2px;\n"
"}\n"
"\n"
"QScrollBar::handle:horizontal:hover {\n"
"    background-color: #606060;\n"
"}\n"
"\n"
"QScrollBar::add-line:horizonta"
                        "l, QScrollBar::sub-line:horizontal {\n"
"    width: 0px;\n"
"}")
        self.treeView.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.treeView.setSortingEnabled(True)
        self.treeView.setAnimated(True)

        self.verticalLayout.addWidget(self.treeView)

        self.ApplyButton = QPushButton(Form)
        self.ApplyButton.setObjectName(u"ApplyButton")

        self.verticalLayout.addWidget(self.ApplyButton)


        self.retranslateUi(Form)

        QMetaObject.connectSlotsByName(Form)
    # setupUi

    def retranslateUi(self, Form):
        Form.setWindowTitle(QCoreApplication.translate("Form", u"Form", None))
        self.ApplyButton.setText(QCoreApplication.translate("Form", u"Apply", None))
    # retranslateUi

