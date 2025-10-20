"""
This script provides a UI for managing color bookmarks in Maya. Whilst this is technically a generic functionality,
it is designed for riggers and so you'll see extra functionality like categorising joints into display layers packaged
as a part of the script.
This script categorizes joints into display layers based on naming conventions defined by the riggers.
We use the naming convention of DAG objects in the scene as the source of truth for categorization.

TODO: Colors are inaccurate on the button because they are CSS style colors whilst Maya uses HDR colors.
"""

import maya.cmds as cmds
import maya.OpenMayaUI as omui
from PySide6 import QtWidgets, QtCore
import shiboken6
from maya.app.general.mayaMixin import MayaQWidgetDockableMixin
import json
import re

WINDOW_TITLE="RigColor"
WINDOW_OBJECT_NAME="RigColor"
WORKSPACE_CONTROL_NAME=WINDOW_OBJECT_NAME + "WorkspaceControl"

NUM_CURVE_COLOR_BOOKMARKS = 8
NUM_LAYER_COLOR_BOOKMARKS = 3

LAYER_NAME_JNT_BS = "BS"
LAYER_NAME_JNT_FK = "FK"
LAYER_NAME_JNT_IK = "IK"

REGEX_PATTERN_JNT_BS = r"JNT[E]?_BS"
REGEX_PATTERN_JNT_FK = r"JNT[E]?_FK"
REGEX_PATTERN_JNT_IK = r"JNT[E]?_IK"

LAYER_DEFAULT_COLOR_JNT_BS = [1, 1, 0] # Yellow
LAYER_DEFAULT_COLOR_JNT_FK = [0, 0, 1] # Blue
LAYER_DEFAULT_COLOR_JNT_IK = [1, 0, 0] # Red

def getMayaMainWindow() -> QtWidgets.QWidget | None:
    mainWindowPtr = omui.MQtUtil.mainWindow()
    if mainWindowPtr:
        return shiboken6.wrapInstance(int(mainWindowPtr), QtWidgets.QWidget)

class ColorBookmarkSaver:
    """Store color bookmarks in a custom Maya node."""
    @staticmethod
    def loadOrCreateColorBookmarks(dataNodeName, dataNodeAttrName, defaultColors: list[list[float]]) -> list[list[float]]:
        """Load or create bookmarked colors in the Maya scene.

        If the data node or attribute does not exist or is corrupted, create or reinitialize it with default colors.

        Args:
            dataNodeName (str): The name of the data node to load or create.
            dataNodeAttrName (str): The name of the data node attribute to load or create.
            defaultColors (list[list[float]]): Default colors to use if the node or attribute is missing or invalid.

        Returns:
            list[list[float]]: The list of colors stored in the data node.
        """
        if not cmds.objExists(dataNodeName):
            cmds.createNode("transform", name=dataNodeName)
            cmds.addAttr(dataNodeName, longName=dataNodeAttrName, dataType="string")
            cmds.setAttr(f"{dataNodeName}.{dataNodeAttrName}", json.dumps(defaultColors), type="string")
            return defaultColors
        else:
            if not cmds.attributeQuery(dataNodeAttrName, node=dataNodeName, exists=True):
                cmds.addAttr(dataNodeName, longName=dataNodeAttrName, dataType="string")
                cmds.setAttr(f"{dataNodeName}.{dataNodeAttrName}", json.dumps(defaultColors), type="string")
                return defaultColors
            else:
                try:
                    colors = json.loads(cmds.getAttr(f"{dataNodeName}.{dataNodeAttrName}"))
                    if len(colors) != len(defaultColors): # Use the number of default colors given to us as the source of truth in terms of number of colors
                        cmds.warning(f"Failed to load color bookmarks from {dataNodeName}.{dataNodeAttrName}. Mismatch in number of color entries. Using default colors.")
                        cmds.setAttr(f"{dataNodeName}.{dataNodeAttrName}", json.dumps(defaultColors), type="string")
                        return defaultColors
                    return colors
                except ValueError:
                    cmds.warning(f"Failed to load color bookmarks from {dataNodeName}.{dataNodeAttrName}. Could not recognise JSON. Using default colors.")
                    cmds.setAttr(f"{dataNodeName}.{dataNodeAttrName}", json.dumps(defaultColors), type="string")
                    return defaultColors

    @staticmethod
    def saveColorBookmarks(dataNodeName, dataNodeAttrName, bookmarklist: list[list[float]]):
        """Save the list of bookmark colors back into the custom Maya node.

        Args:
            dataNodeName (str): The name of the data node to save the colors to.
            dataNodeAttrName (str): The name of the data node attribute to save the colors to.
            bookmarklist (list[list[float]]): The list of colors to save.
        """
        ColorBookmarkSaver.loadOrCreateColorBookmarks(dataNodeName, dataNodeAttrName, bookmarklist) # Here we recreate the data node in case it's been deleted
        cmds.setAttr(f"{dataNodeName}.{dataNodeAttrName}", json.dumps(bookmarklist), type="string")

class ColorBookmarkButton(QtWidgets.QPushButton):
    """Button for displaying and storing a color bookmark."""
    colorChanged = QtCore.Signal()

    def __init__(self, index, color=[0,0,0], checkable=True, parent=None):
        """
        index: Index of the button
        color: Color of the button in [R, G, B] format
        checkable: Whether the button is checkable
        """
        super().__init__(parent)
        self.index = index
        self.setFixedSize(32, 32)
        if checkable:
            self.setCheckable(True)
            self.setChecked(False)
        self.setColor(color)

    # TODO: Chuck this to a util instead
    def _convertNormalizedColorToRGB(self, normalizedColor: list[float]) -> list[int]:
        return [int(round(normalizedColor[0] * 255)),
                int(round(normalizedColor[1] * 255)),
                int(round(normalizedColor[2] * 255))]

    def _generateBackgroundColorStyleSheet(self, color: list[float]) -> str:
        """Generate stylesheet for the button based on its color"""
        rgbColor = self._convertNormalizedColorToRGB(color)
        return (
            f"QPushButton {{ background-color: rgb({rgbColor[0]}, {rgbColor[1]}, {rgbColor[2]}); border: 1px solid black; }}"
            "QPushButton:checked { border: 3px solid white; }" 
        )

    def getColor(self) -> list[float]:
        return self.color

    def setColor(self, color: list[float]):
        """Update the button's color. Will emit a signal"""
        self.color = color
        self.setStyleSheet(self._generateBackgroundColorStyleSheet(color))
        self.colorChanged.emit()

    def mouseDoubleClickEvent(self, event):
        """Open Maya's color editor on double-click and update the bookmark color."""
        cmds.colorEditor(rgbValue=self.color)
        if cmds.colorEditor(query=True, result=True):
            selectedColor = cmds.colorEditor(query=True, rgbValue=True)
            self.setColor(selectedColor)            

class ColorBookmarkBar(QtWidgets.QWidget):
    """Widget containing a row of color bookmark buttons."""
    def __init__(self, numButtons, dataNodeAttrName, checkable=True, labels=[], defaultColors=[], parent=None):
        """
        numButtons: Number of color bookmark buttons to create
        dataNodeAttrName: Name of the attribute to store the color bookmarks
        checkable: Whether the buttons are checkable (only one can be selected at a time)
        labels: list of labels for the buttons. If empty, default to "0", "1", ...
        defaultColors: list of default colors for the buttons. If empty, default to black
        """

        # Ensure valid labels and defaultColors, that they match the number of buttons
        if len(labels) > 0 and len(labels) != numButtons:
            raise ValueError("Number of labels must be 0 or equal to the number of buttons.")
        if len(defaultColors) > 0 and len(defaultColors) != numButtons:
            raise ValueError("Number of default colors must be 0 or equal to the number of buttons.")

        super().__init__(parent)
        self.numButtons = numButtons
        self.dataNodeName = "colorBookmarksData" # TODO: This should be a constant
        self.dataNodeAttrName = dataNodeAttrName
        self.checkable = checkable
        self.labels = labels
        self.defaultColors = defaultColors if defaultColors else [[0, 0, 0] for _ in range(numButtons)]
        self.colorButtons: ColorBookmarkButton = []
        self.selectedColorButton: ColorBookmarkButton = None
        
        self._setupUi()
        
    def _setupUi(self):
        bookmarksLayout = QtWidgets.QHBoxLayout(self)

        # Create color bookmark buttons
        storedColorBookmarks = ColorBookmarkSaver.loadOrCreateColorBookmarks(self.dataNodeName, self.dataNodeAttrName, self.defaultColors)
        for index in range(self.numButtons):
            colorButtonLayout = QtWidgets.QVBoxLayout()
            bookmarksLayout.addLayout(colorButtonLayout)

            # Create label for the color button
            if self.labels:
                label = QtWidgets.QLabel(self.labels[index])
            else:
                label = QtWidgets.QLabel(f"{index}")
            colorButtonLayout.addWidget(label)
            label.setAlignment(QtCore.Qt.AlignCenter)

            # Create the color button
            color = storedColorBookmarks[index]
            button = ColorBookmarkButton(index, color, checkable=self.checkable)
            colorButtonLayout.addWidget(button)
            if self.checkable:
                button.toggled.connect(lambda checked, button=button: self._selectColorButton(button))
            button.colorChanged.connect(lambda colorButtons=self.colorButtons: ColorBookmarkSaver.saveColorBookmarks(self.dataNodeName, self.dataNodeAttrName, [button.getColor() for button in colorButtons]))
            self.colorButtons.append(button)

        bookmarksLayout.addStretch()

    def getColorFromLabel(self, label) -> list[float]:
        """Get the color from a specific bookmark label"""
        index = self.labels.index(label)
        return self.colorButtons[index].getColor()
            
    def _selectColorButton(self, button):
        """Handle button toggle events"""
        if button.isChecked():
            self.selectedColorButton = button
            # Uncheck all other buttons
            for otherButton in self.colorButtons:
                if otherButton != button and otherButton.isChecked():
                    otherButton.blockSignals(True)
                    otherButton.setChecked(False)
                    otherButton.blockSignals(False)
        else:
            # If the button was unchecked, only clear selected if it was this button
            if self.selectedColorButton == button:
                self.selectedColorButton = None

    def getSelected(self) -> ColorBookmarkButton | None:
        """Return the currently selected color button or None if none selected"""
        return self.selectedColorButton
        
class RigColor(MayaQWidgetDockableMixin, QtWidgets.QWidget):
    """Main UI dockable window for rig color management."""
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowFlags(QtCore.Qt.Window)
        self.setObjectName(WINDOW_OBJECT_NAME)
        self.setWindowTitle(WINDOW_TITLE)
        self._setupUi()

    def _setupUi(self):
        mainLayout = QtWidgets.QVBoxLayout(self)
        self.setLayout(mainLayout)

        # ---------------------------------------------------------------------------- #
        #                               Coloring section                               #
        # ---------------------------------------------------------------------------- #
        colorGroupBox = QtWidgets.QGroupBox("Coloring section")
        mainLayout.addWidget(colorGroupBox)
        
        colorGroupBoxLayout = QtWidgets.QVBoxLayout(colorGroupBox)

        # Create a color bookmark bar for curve colors
        colorGroupBoxLayout.addWidget(QtWidgets.QLabel("Curve color bookmarks:"))
        self.curveColorBookmarkBar = ColorBookmarkBar(NUM_CURVE_COLOR_BOOKMARKS, "curve", checkable=True)
        colorGroupBoxLayout.addWidget(self.curveColorBookmarkBar)
        
        curveColorFormLayout = QtWidgets.QFormLayout()
        colorGroupBoxLayout.addLayout(curveColorFormLayout)

        # Create a button to apply the chosen colour to selected objects
        self.applyColorButton = QtWidgets.QPushButton("Apply")
        curveColorFormLayout.addRow("Apply color to selected curves:", self.applyColorButton)
        self.applyColorButton.clicked.connect(self._applyColorToCurve)

        # Create a button to save the color of chosen shape to color bookmark
        self.saveColorButton = QtWidgets.QPushButton("Save")
        curveColorFormLayout.addRow("Save selected curve color to bookmark:", self.saveColorButton)
        self.saveColorButton.clicked.connect(self._saveSelectedCurveColorToBookmark)

        # ---------------------------------------------------------------------------- #
        #                               Layering section                               #
        # ---------------------------------------------------------------------------- #
        layerGroupBox = QtWidgets.QGroupBox("Layering section")
        mainLayout.addWidget(layerGroupBox)

        layerGroupBoxLayout = QtWidgets.QVBoxLayout(layerGroupBox)

        # Create a color bookmark bar for display layers
        layerGroupBoxLayout.addWidget(QtWidgets.QLabel("Set layer color:"))
        self.layerColorBookmarkBar = ColorBookmarkBar(NUM_LAYER_COLOR_BOOKMARKS, "layer", checkable=False,
                                                        labels=[LAYER_NAME_JNT_BS, LAYER_NAME_JNT_FK, LAYER_NAME_JNT_IK],
                                                        defaultColors=[LAYER_DEFAULT_COLOR_JNT_BS, LAYER_DEFAULT_COLOR_JNT_FK, LAYER_DEFAULT_COLOR_JNT_IK])
        layerGroupBoxLayout.addWidget(self.layerColorBookmarkBar)

        # Create a button to auto categorise selected joints into respective layers
        layerColorFormLayout = QtWidgets.QFormLayout()
        layerGroupBoxLayout.addLayout(layerColorFormLayout)

        # Create checkbox for applying recursively to all descendents
        self.applyDescendentsCheckbox = QtWidgets.QCheckBox()
        self.applyDescendentsCheckbox.setChecked(True)
        layerColorFormLayout.addRow("Apply to all descendents:", self.applyDescendentsCheckbox)

        # Create checkbox for overwriting layer colors
        self.overwriteLayerColorCheckbox = QtWidgets.QCheckBox()
        self.overwriteLayerColorCheckbox.setChecked(False)
        layerColorFormLayout.addRow("Overwrite existing layer colors:", self.overwriteLayerColorCheckbox)

        self.categoriseJointsButton = QtWidgets.QPushButton("Apply")
        layerColorFormLayout.addRow("Categorise selected joints into layers:", self.categoriseJointsButton)
        self.categoriseJointsButton.clicked.connect(
            lambda: self._categoriseJointsIntoLayers(
                self.applyDescendentsCheckbox.isChecked(),
                self.overwriteLayerColorCheckbox.isChecked()
            )
        )

        # ---------------------------------------------------------------------------- #
        # Add stretch to push everything to the top
        mainLayout.addStretch()

    def _applyColorToCurve(self):
        """Apply the currently selected bookmark color to selected curve shapes."""
        selectedColor = self.curveColorBookmarkBar.getSelected().getColor()
        if not selectedColor:
            cmds.warning("Please select a color bookmark.")
            return
            
        # Get selected objects
        selectedObjects = cmds.ls(selection=True)
        if not selectedObjects:
            cmds.warning("Please select at least one object.")
            return
            
        # Find the shape child of the selected objects
        curves = cmds.listRelatives(selectedObjects, type="nurbsCurve", path=True)
        if not curves:
            cmds.warning("Selected objects do not have any curve nodes.")
            return

        # Apply index color to selected objects
        for curve in curves:
            print(f"Applying color to {curve}")
            cmds.setAttr(curve + ".overrideEnabled", 1) # Enable override
            cmds.setAttr(curve + ".overrideRGBColors", 1) # Set to index colour over RBG
            cmds.setAttr(curve + ".overrideColorRGB", *selectedColor, type="double3") # Set the colour

    def _saveSelectedCurveColorToBookmark(self):
        """Save the override color of a selected curve to the currently selected bookmark."""
        if not self.curveColorBookmarkBar.getSelected():
            cmds.warning("Please select a color bookmark.")
            return

        selectedObjects = cmds.ls(selection=True)
        if len(selectedObjects) != 1:
            cmds.warning("Please select one object.")
            return
        selectedCurves = cmds.listRelatives(selectedObjects, type="nurbsCurve", path=True)
        if len(selectedCurves) != 1:
            cmds.warning("Please select one curve object.")
            return
        selectedCurve = selectedCurves[0]

        if not cmds.getAttr(selectedCurve + ".overrideEnabled"):
            cmds.warning("Selected curve does not have color override enabled.")
            return

        if not cmds.getAttr(selectedCurve + ".overrideRGBColors"):
            selectedColor = cmds.colorIndex(cmds.getAttr(selectedCurve + ".overrideColor"), query=True)
        else:
            selectedColor = cmds.getAttr(selectedCurve + ".overrideColorRGB")[0]

        self.curveColorBookmarkBar.getSelected().setColor(list(selectedColor))

    def _categoriseJointsIntoLayers(self, applyDescendents: bool, overwriteLayerColor: bool):
        """Categorize selected joints into BS/FK/IK display layers based on name patterns.

        Args:
            applyDescendents (bool): Whether to include all descendant joints in the categorization.
            overwriteLayerColor (bool): Whether to overwrite existing layer colors.
        """
        selectedObjects = cmds.ls(selection=True, long=True)
        if not selectedObjects:
            cmds.warning("Please select at least one object.")
            return
        
        def setLayerColor(layerName, color):
            cmds.setAttr(layerName + ".enabled", 1)
            cmds.setAttr(layerName + ".overrideRGBColors", 1)
            cmds.setAttr(layerName + ".overrideColorRGB", *color, type="double3")
        
        # Create display layers if they don't exist
        if not cmds.objExists(LAYER_NAME_JNT_BS):
            cmds.createDisplayLayer(name="BS", empty=True)
            setLayerColor(LAYER_NAME_JNT_BS, self.layerColorBookmarkBar.getColorFromLabel(LAYER_NAME_JNT_BS))
        if not cmds.objExists(LAYER_NAME_JNT_FK):
            cmds.createDisplayLayer(name="FK", empty=True)
            setLayerColor(LAYER_NAME_JNT_FK, self.layerColorBookmarkBar.getColorFromLabel(LAYER_NAME_JNT_FK))
        if not cmds.objExists(LAYER_NAME_JNT_IK):
            cmds.createDisplayLayer(name="IK", empty=True)
            setLayerColor(LAYER_NAME_JNT_IK, self.layerColorBookmarkBar.getColorFromLabel(LAYER_NAME_JNT_IK))

        # Overwrite color if checkbox is checked
        if overwriteLayerColor:
            setLayerColor(LAYER_NAME_JNT_BS, self.layerColorBookmarkBar.getColorFromLabel(LAYER_NAME_JNT_BS))
            setLayerColor(LAYER_NAME_JNT_FK, self.layerColorBookmarkBar.getColorFromLabel(LAYER_NAME_JNT_FK))
            setLayerColor(LAYER_NAME_JNT_IK, self.layerColorBookmarkBar.getColorFromLabel(LAYER_NAME_JNT_IK))
        
        def categoriseJoint(joint):
            # Use regex to determine whether the node name starts with JNT_BS, JNT_FK, JNT_IK
            jointShortName = joint.split("|")[-1]
            if re.search(REGEX_PATTERN_JNT_BS, jointShortName):
                cmds.editDisplayLayerMembers(LAYER_NAME_JNT_BS, joint, noRecurse=True)
            elif re.search(REGEX_PATTERN_JNT_FK, jointShortName):
                cmds.editDisplayLayerMembers(LAYER_NAME_JNT_FK, joint, noRecurse=True)
            elif re.search(REGEX_PATTERN_JNT_IK, jointShortName):
                cmds.editDisplayLayerMembers(LAYER_NAME_JNT_IK, joint, noRecurse=True)
            else:
                cmds.warning(f"Could not categorise {joint} into any layer.")

        # Categorise selected joints into respective layers
        for object in selectedObjects:
            if applyDescendents:
                # If it has descendents, traverse down the tree and categorise them too
                objectDescendents = cmds.listRelatives(object, allDescendents=True, fullPath=True)
                for objectDescendent in objectDescendents:
                    if cmds.objectType(objectDescendent) == "joint":
                        categoriseJoint(objectDescendent)
            if cmds.objectType(object) == "joint":
                categoriseJoint(object)

def showRigColor():
    if cmds.workspaceControl(WORKSPACE_CONTROL_NAME, exists=True):
        cmds.deleteUI(WORKSPACE_CONTROL_NAME)
    def createRigColor():
        rigColor = RigColor(getMayaMainWindow())
        rigColor.show(dockable=True)
    cmds.evalDeferred(createRigColor)
