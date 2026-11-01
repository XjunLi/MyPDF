from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QWidget,QHBoxLayout,QPushButton,QWidgetAction,QColorDialog

COLORS=("#ffc33a","#66c58c","#5fa8ed","#c49ee8","#f38da9","#e66155","#25364c")


def add_palette(menu,parent,value,changed):
    widget=QWidget(); row=QHBoxLayout(widget); row.setContentsMargins(8,6,8,6); row.setSpacing(5)
    buttons=[]
    def selected(): return value() if callable(value) else value
    for color in COLORS:
        button=QPushButton(); button.setFixedSize(22,22); button.setToolTip(color.upper()); button.setAccessibleName("颜色 "+color)
        buttons.append((button,color))
        button.clicked.connect(lambda checked=False,c=color:(changed(QColor(c)),menu.close()))
        row.addWidget(button)
    def refresh():
        for button,color in buttons:
            border="#326ad5" if color==selected().name() else "#fff"
            button.setStyleSheet("QPushButton {background:"+color+";border:2px solid "+border+";border-radius:11px;}")
    menu.aboutToShow.connect(refresh); refresh()
    action=QWidgetAction(menu); action.setDefaultWidget(widget); menu.addAction(action)
    def custom():
        color=QColorDialog.getColor(selected(),parent,"选择颜色")
        if color.isValid(): changed(color)
    menu.addAction("其他颜色…",custom)
