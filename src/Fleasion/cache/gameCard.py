# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'gameCard2.ui'
##
## Created by: Qt User Interface Compiler version 6.10.0
##
## WARNING! All changes made in this file will be lost when recompiling UI file!
################################################################################

from PyQt6.QtCore import (QCoreApplication, QDate, QDateTime, QLocale,
    QMetaObject, QObject, QPoint, QRect,
    QSize, QTime, QUrl, Qt)
from PyQt6.QtGui import (QBrush, QColor, QConicalGradient, QCursor,
    QFont, QFontDatabase, QGradient, QIcon,
    QImage, QKeySequence, QLinearGradient, QPainter,
    QPalette, QPixmap, QRadialGradient, QTransform)
from PyQt6.QtWidgets import (QApplication, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QPushButton, QSizePolicy, QVBoxLayout,
    QWidget)

class Ui_GameCard(object):
    def setupUi(self, GameCard):
        if not GameCard.objectName():
            GameCard.setObjectName(u"GameCard")
        GameCard.resize(175, 225)
        sizePolicy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(GameCard.sizePolicy().hasHeightForWidth())
        GameCard.setSizePolicy(sizePolicy)
        GameCard.setMinimumSize(QSize(175, 225))
        GameCard.setMaximumSize(QSize(16777215, 225))
        GameCard.setStyleSheet(u"")
        self.gridLayout = QGridLayout(GameCard)
        self.gridLayout.setObjectName(u"gridLayout")
        self.gridLayout.setVerticalSpacing(0)
        self.gridLayout.setContentsMargins(0, 0, 0, 0)
        self.frame = QFrame(GameCard)
        self.frame.setObjectName(u"frame")
        self.frame.setStyleSheet(u"QWidget#GameCard QFrame#frame:hover {\n"
"    background-color: rgba(255, 255, 255, 0.3);\n"
"}\n"
"\n"
"QWidget#GameCard QFrame#frame:hover QLabel#thumbLabel {\n"
"    background-color: rgba(255, 255, 255, 0.3);\n"
"}\n"
"")
        self.frame.setFrameShape(QFrame.Shape.StyledPanel)
        self.frame.setFrameShadow(QFrame.Shadow.Raised)
        self.verticalLayout = QVBoxLayout(self.frame)
        self.verticalLayout.setSpacing(0)
        self.verticalLayout.setObjectName(u"verticalLayout")
        self.verticalLayout.setContentsMargins(6, 6, 6, 6)
        self.ThumbnailContainer = QWidget(self.frame)
        self.ThumbnailContainer.setObjectName(u"ThumbnailContainer")
        self.ThumbnailContainer.setStyleSheet(u"background: transparent;")
        self.gridLayout_2 = QGridLayout(self.ThumbnailContainer)
        self.gridLayout_2.setObjectName(u"gridLayout_2")
        self.gridLayout_2.setContentsMargins(0, 0, 0, 0)
        self.thumbLabel = QLabel(self.ThumbnailContainer)
        self.thumbLabel.setObjectName(u"thumbLabel")
        sizePolicy1 = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        sizePolicy1.setHorizontalStretch(0)
        sizePolicy1.setVerticalStretch(0)
        sizePolicy1.setHeightForWidth(self.thumbLabel.sizePolicy().hasHeightForWidth())
        self.thumbLabel.setSizePolicy(sizePolicy1)
        self.thumbLabel.setMinimumSize(QSize(137, 0))
        self.thumbLabel.setMaximumSize(QSize(137, 16777215))
        self.thumbLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.gridLayout_2.addWidget(self.thumbLabel, 0, 0, 1, 1)


        self.verticalLayout.addWidget(self.ThumbnailContainer)

        self.nameLabel = QLabel(self.frame)
        self.nameLabel.setObjectName(u"nameLabel")
        sizePolicy2 = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        sizePolicy2.setHorizontalStretch(0)
        sizePolicy2.setVerticalStretch(0)
        sizePolicy2.setHeightForWidth(self.nameLabel.sizePolicy().hasHeightForWidth())
        self.nameLabel.setSizePolicy(sizePolicy2)
        font = QFont()
        font.setBold(True)
        self.nameLabel.setFont(font)
        self.nameLabel.setStyleSheet(u"background: transparent;")
        self.nameLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.verticalLayout.addWidget(self.nameLabel)

        self.updatedLabel = QLabel(self.frame)
        self.updatedLabel.setObjectName(u"updatedLabel")
        self.updatedLabel.setStyleSheet(u"background: transparent;")
        self.updatedLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.verticalLayout.addWidget(self.updatedLabel)

        self.createdLabel = QLabel(self.frame)
        self.createdLabel.setObjectName(u"createdLabel")
        self.createdLabel.setStyleSheet(u"background: transparent;")
        self.createdLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.verticalLayout.addWidget(self.createdLabel)

        self.horizontalLayout = QHBoxLayout()
        self.horizontalLayout.setObjectName(u"horizontalLayout")
        self.joinButton = QPushButton(self.frame)
        self.joinButton.setObjectName(u"joinButton")
        sizePolicy3 = QSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        sizePolicy3.setHorizontalStretch(0)
        sizePolicy3.setVerticalStretch(0)
        sizePolicy3.setHeightForWidth(self.joinButton.sizePolicy().hasHeightForWidth())
        self.joinButton.setSizePolicy(sizePolicy3)
        self.joinButton.setMaximumSize(QSize(16777215, 16777215))

        self.horizontalLayout.addWidget(self.joinButton)

        self.openButton = QPushButton(self.frame)
        self.openButton.setObjectName(u"openButton")
        sizePolicy2.setHeightForWidth(self.openButton.sizePolicy().hasHeightForWidth())
        self.openButton.setSizePolicy(sizePolicy2)
        self.openButton.setMinimumSize(QSize(0, 0))

        self.horizontalLayout.addWidget(self.openButton)

        self.horizontalLayout.setStretch(0, 1)
        self.horizontalLayout.setStretch(1, 2)

        self.verticalLayout.addLayout(self.horizontalLayout)


        self.gridLayout.addWidget(self.frame, 0, 0, 1, 1)


        self.retranslateUi(GameCard)

        QMetaObject.connectSlotsByName(GameCard)
    # setupUi

    def retranslateUi(self, GameCard):
        GameCard.setWindowTitle(QCoreApplication.translate("GameCard", u"Form", None))
        self.thumbLabel.setText(QCoreApplication.translate("GameCard", u"Thumbnail", None))
        self.nameLabel.setText(QCoreApplication.translate("GameCard", u"Game Name", None))
        self.updatedLabel.setText(QCoreApplication.translate("GameCard", u"GameID:", None))
        self.createdLabel.setText(QCoreApplication.translate("GameCard", u"Updated:", None))
        self.joinButton.setText(QCoreApplication.translate("GameCard", u"Join", None))
        self.openButton.setText(QCoreApplication.translate("GameCard", u"Open in browser", None))
    # retranslateUi

