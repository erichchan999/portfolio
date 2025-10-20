import maya.cmds as cmds
import maya.api.OpenMaya as om
import maya.OpenMayaUI as omui
from PySide6 import QtWidgets, QtCore
import shiboken6
from maya.app.general.mayaMixin import MayaQWidgetDockableMixin

WINDOW_TITLE="Curve Extraction"
WINDOW_OBJECT_NAME="CurveExtraction"
WORKSPACE_CONTROL_NAME=WINDOW_OBJECT_NAME + "WorkspaceControl"

def getMayaMainWindow():
    """Return Maya's main window as a Python object."""
    mainWindowPtr = omui.MQtUtil.mainWindow()
    if mainWindowPtr:
        return shiboken6.wrapInstance(int(mainWindowPtr), QtWidgets.QWidget)

class CurveExtraction(MayaQWidgetDockableMixin, QtWidgets.QWidget):
    def __init__(self, parent=None):
        super(CurveExtraction, self).__init__(parent)
        self.setWindowFlags(QtCore.Qt.Window)
        self.setObjectName(WINDOW_OBJECT_NAME)
        self.setWindowTitle(WINDOW_TITLE)
    
        self.targetCurve = None

        self._setupUi()

    def _setupUi(self):
        # Create the UI elements
        layout = QtWidgets.QVBoxLayout(self)

        # Create a groupbox for the curve extraction
        groupBox = QtWidgets.QGroupBox("Curve Extraction")
        layout.addWidget(groupBox)

        groupBoxLayout = QtWidgets.QVBoxLayout(groupBox)

        # Create a label to show target curve
        self.targetCurveLabel = QtWidgets.QLabel("Target Curve: None")
        groupBoxLayout.addWidget(self.targetCurveLabel)

        # Create a button to select a target curve
        targetCurveButton = QtWidgets.QPushButton("Select Target Curve")
        targetCurveButton.clicked.connect(self._selectTargetCurve)
        groupBoxLayout.addWidget(targetCurveButton)

        # Create a button to extract the curve
        extractButton = QtWidgets.QPushButton("Extract Curve")
        extractButton.clicked.connect(self._extractCurve)
        groupBoxLayout.addWidget(extractButton)

        layout.addStretch()

    def _selectTargetCurve(self):
        selectedCurves = cmds.ls(selection=True)
        if not selectedCurves:
            cmds.error("Select a curve to extract.")
        if len(selectedCurves) > 1:
            cmds.error("Select only one curve to extract.")
        # Check if the selected object is a curve
        if not cmds.listRelatives(selectedCurves[0], type="nurbsCurve"):
            cmds.error("Selected object is not a curve.")
        self.targetCurve = selectedCurves[0]
        self.targetCurveLabel.setText(f"Target Curve: {self.targetCurve}")

    def _extractCurve(self):
        if not self.targetCurve:
            cmds.error("No target curve selected.")

        selections = cmds.ls(selection=True, flatten=True)
        if not selections:
            cmds.error("Select at least one vertex.")

        points = [cmds.pointPosition(v, world=True) for v in selections]
        if len(points) < 2:
            cmds.error("At least two vertices are required to create a curve.")

        # Adjust curve degree: Use degree=3 by default, but lower if too few points
        degree = 3
        if len(points) <= degree:
            degree = len(points) - 1

        curveShape = cmds.listRelatives(self.targetCurve, shapes=True)[0]

        # Get the curve function of the created curve.
        # This is necessary to use OpenMaya API for closestPoint calculation.
        selectionList = om.MSelectionList()
        selectionList.add(curveShape)
        dagPath = selectionList.getDagPath(0)
        curveFn = om.MFnNurbsCurve(dagPath)

        # Evaluate uValue based on closestPoint and map points and uValue into a list of tuples
        pointsAndUValues = []
        for pos in points:
            mPoint = om.MPoint(pos[0], pos[1], pos[2])
            _, param = curveFn.closestPoint(mPoint, space=om.MSpace.kWorld)
            pointsAndUValues.append((pos, param))
        # Sort the points based on their uValue on the curve
        pointsAndUValues.sort(key=lambda x: x[1])

        for i, (pos, uValue) in enumerate(pointsAndUValues):
            loc = cmds.spaceLocator(name=f"locator_{i+1}")[0]
            cmds.xform(loc, ws=True, t=pos)
            # Resize the locator
            locatorShape = cmds.listRelatives(loc, shapes=True)[0]
            cmds.setAttr(locatorShape + ".localScale", 0.2, 0.2, 0.2)
            # Attach the locator to the curve using a motion path
            motionPathNode = cmds.pathAnimation(loc, c=self.targetCurve)
            # Set the uValue of the motion path to the parameter value of the point on the curve
            cmds.setAttr(motionPathNode + ".uValue", uValue)
            # Disconnect the motion path's uValue from the locator to prevent keyframe animation.
            # This is enabled by default when using pathAnimation
            cmds.disconnectAttr(f"{motionPathNode}_uValue.output", f"{motionPathNode}.uValue")

def showWindow():
    if cmds.workspaceControl(WORKSPACE_CONTROL_NAME, exists=True):
        cmds.deleteUI(WORKSPACE_CONTROL_NAME)
    def createCurveExtraction():
        curveExtraction = CurveExtraction(getMayaMainWindow())
        curveExtraction.show(dockable=True)
    cmds.evalDeferred(createCurveExtraction)

if __name__ == "__main__":
    showWindow()
