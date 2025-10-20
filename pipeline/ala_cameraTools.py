"""
This rewrites ALA's camera rig plugin into Qt with camera shake, it allows us to modify camera settings for both ALACamRigs plugin cameras and standard Maya cameras.
The GUI uses the MVVM architecture, which separates the view (CameraTools), view model (CameraToolsViewModel), and model (camera models).
Credit to Jonah and Ethan Lucas for the original script.
"""

import maya.cmds as cmds
import maya.OpenMayaUI as omui
import maya.api.OpenMaya as om
from PySide6 import QtWidgets, QtCore
import shiboken6
from maya.app.general.mayaMixin import MayaQWidgetDockableMixin

from abc import ABC
from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import Optional, Callable

from maya_pipeline.utils.ala_layout import createALACameraRig

WINDOW_TITLE="Camera Tools"
WINDOW_OBJECT_NAME="CameraTools"
WORKSPACE_CONTROL_NAME=WINDOW_OBJECT_NAME + "WorkspaceControl"

# Set focal length and f stops, these are currently Arri Master Prime's
PRESET_FOCAL_LENGTHS = [12, 14, 16, 18, 21, 25, 27, 32, 35, 40, 50, 65, 75, 100, 135, 150]
PRESET_F_STOPS = [1.3, 2, 2.8, 4, 5.6, 8, 11, 16, 22]

# For compatibility with tk_maya_playblast, not used in this script.
def isALACamRig(cameraShape):
    if cmds.nodeType(cameraShape) == "transform":
        cameraShapes = cmds.listRelatives(cameraShape, type="camera")
        if cameraShapes:
            cameraShape = cameraShapes[0]
    return cmds.attributeQuery('alaCamRig', node=cameraShape, exists=True)

# For compatibility with Maya shot builder, not used in this script.
def cinematicCameraSettings():
    cameraTransform = cmds.ls(selection=True)[0]
    CameraModelFactory.cameraModel(cameraTransform).applyCinematicCameraSettings()

# For compatibility with Maya shot builder, not used in this script.
def alexaCameraSettings():
    cameraTransform = cmds.ls(selection=True)[0]
    CameraModelFactory.cameraModel(cameraTransform).applyAlexaCameraSettings()

class CameraTools(MayaQWidgetDockableMixin, QtWidgets.QWidget):
    """
    Main window for CameraTools. Within the MVVM architecture, this serves as the view.
    However, it also provides a global context which declares the model and view model.
    This is slightly unconventional but helps us to bind the lifetime of this tool's instances
    to the creation/destruction of the main window. 
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName(WINDOW_OBJECT_NAME)
        self.setWindowTitle(WINDOW_TITLE)
        self.setWindowFlags(QtCore.Qt.Window)

        self.cameraToolsViewModel = CameraToolsViewModel(self)
        self._setupUi()

        self.cameraToolsViewModel.changeCamera() # Initialise model if it selected in Maya before this script runs

    def _setupUi(self):
        mainLayout = QtWidgets.QVBoxLayout(self)

        self.cameraCreation = CameraCreationWidget(self.cameraToolsViewModel, self)
        mainLayout.addWidget(self.cameraCreation)
        mainLayout.addWidget(QtUiUtils.newDivider())

        self.cameraSettings = CameraSettingsWidget(self.cameraToolsViewModel, self)
        mainLayout.addWidget(self.cameraSettings)

        mainLayout.addStretch()

    def dockCloseEventTriggered(self):
        """
        Creation/destruction of dockable widgets are handled differently in Maya. This function
        is the replacement for "closeEvent" when it is closed
        """
        self.cameraToolsViewModel.dockCloseEventTriggered()

class CameraModelFactory:
    @staticmethod
    def cameraModel(cameraTransform):
        assert(cmds.nodeType(cameraTransform) == "transform")
        cameraShapes = cmds.listRelatives(cameraTransform, type="camera")
        cameraShape = cameraShapes[0]
        assert(cmds.nodeType(cameraShape) == "camera")
        if cmds.attributeQuery('alaCamRig', node=cameraShape, exists=True):
            return ALACameraModel(cameraTransform, cameraShape)
        else:
            return BaseCameraModel(cameraTransform, cameraShape)

    @staticmethod
    def alaCameraModelViaRig(node):
        if node is None:
            return None

        # If selected object is the camera rig group, return the camera model
        if node.split("|")[-1].endswith("_rig"):
            # Get the camera transform name
            cameraTransform = node.split("|")[-1].rstrip("_rig")
            # Get the unique path of cameraTransform if it exists
            cameraTransforms = cmds.ls(cameraTransform)
            if len(cameraTransforms) == 1:
                # Get the camera shape
                cameraShape = cmds.listRelatives(cameraTransforms[0], type="camera")
                if cameraShape:
                    cameraShape = cameraShape[0]
                    # Return the ALACameraModel instance
                    return ALACameraModel(cameraTransforms[0], cameraShape)

        # If the selected object is a child of the camera rig group, return the camera model
        parents = cmds.ls(node, long=True)[0].split("|")[1:-1]
        if not parents:
            return None

        for parent in parents:
            # Check if the parent is a camera rig
            if cmds.nodeType(parent) == "transform" and parent.endswith("_rig"):
                # Get the camera transform name
                cameraTransform = parent.split("|")[-1].rstrip("_rig")
                # Get the unique path of cameraTransform if it exists
                cameraTransforms = cmds.ls(cameraTransform)
                if len(cameraTransforms) == 1:
                    # Get the camera shape
                    cameraShape = cmds.listRelatives(cameraTransforms[0], type="camera")
                    if cameraShape:
                        cameraShape = cameraShape[0]
                        # Return the ALACameraModel instance
                        return ALACameraModel(cameraTransforms[0], cameraShape)

class AbstractCameraModel(ABC):
    def __init__(self, cameraTransform, cameraShape):
        self._cameraTransform = cameraTransform
        self._cameraShape = cameraShape
        self._presetFocalLengths = PRESET_FOCAL_LENGTHS
        self._presetFStops = PRESET_F_STOPS

    @property
    def cameraTransform(self):
        return self._cameraTransform
    
    @property
    def cameraShape(self):
        return self._cameraShape
    
    @property
    def presetFocalLengths(self):
        return self._presetFocalLengths.copy()
    
    @property
    def presetFStops(self):
        return self._presetFStops.copy()
    
class ALACameraModel(AbstractCameraModel):
    """
    ALACameraModel class for ALACamRigs plugin cameras. This class inherits from AbstractCameraModel and implements
    methods to apply camera settings specific to ALACamRigs cameras. It does so by getting and setting the custom attributes
    of the camera transform added by the ALACamRigs plugin.
    """
    class CameraSettingGridMode(IntEnum):
        NONE = 0
        THREE = 1
        TWO = 2

    def __init__(self, cameraTransform, cameraShape):
        super().__init__(cameraTransform, cameraShape)
        
        ALA_CAMERA_AIM_CTRL_NAME="cineCam_aim_ctrl"
        ALA_CAMERA_FOCUS_CTRL_NAME="cineCam_focusplane_ctrl"
        ALA_CAMERA_SHAKE_TRANSLATE_CTRL_NAME="cineCam_shake_ctrl"
        
        self._cameraAimCtrl = self._getCamRigObject(cameraTransform, ALA_CAMERA_AIM_CTRL_NAME)
        self._cameraFocusCtrl = self._getCamRigObject(cameraTransform, ALA_CAMERA_FOCUS_CTRL_NAME)
        self._cameraShakeCtrl = self._getCamRigObject(cameraTransform, ALA_CAMERA_SHAKE_TRANSLATE_CTRL_NAME)

        self._shakeCtrlTranslateX = "translateX"
        self._shakeCtrlTranslateY = "translateY"
        self._shakeCtrlTranslateZ = "translateZ"

        self._shakeTranslateXFrequency = "shakeTranslateXFreq"
        self._shakeTranslateYFrequency = "shakeTranslateYFreq"
        self._shakeTranslateZFrequency = "shakeTranslateZFreq"
        self._shakeTranslateXAmplitude = "shakeTranslateXAmp"
        self._shakeTranslateYAmplitude = "shakeTranslateYAmp"
        self._shakeTranslateZAmplitude = "shakeTranslateZAmp"

        self._shakeTranslateXAmplitudeDefault = 0
        self._shakeTranslateYAmplitudeDefault = 0
        self._shakeTranslateZAmplitudeDefault = 0
        self._shakeTranslateXFrequencyDefault = 0
        self._shakeTranslateYFrequencyDefault = 0
        self._shakeTranslateZFrequencyDefault = 0

        self._shakeCtrlRotateX = "rotateX"
        self._shakeCtrlRotateY = "rotateY"
        self._shakeCtrlRotateZ = "rotateZ"

        self._shakeRotateXFrequency = "shakeRotateXFreq"
        self._shakeRotateYFrequency = "shakeRotateYFreq"
        self._shakeRotateZFrequency = "shakeRotateZFreq"
        self._shakeRotateXAmplitude = "shakeRotateXAmp"
        self._shakeRotateYAmplitude = "shakeRotateYAmp"
        self._shakeRotateZAmplitude = "shakeRotateZAmp"

        self._shakeRotateXAmplitudeDefault = 0
        self._shakeRotateYAmplitudeDefault = 0
        self._shakeRotateZAmplitudeDefault = 0
        self._shakeRotateXFrequencyDefault = 0
        self._shakeRotateYFrequencyDefault = 0
        self._shakeRotateZFrequencyDefault = 0

    def _getCamRigObject(self, cameraTransform, object):
            camRig = cmds.ls(cameraTransform + "_rig", type="transform")
            if camRig:
                for childObject in cmds.listRelatives(camRig[0], allDescendents=True, fullPath=True):
                    if childObject.split("|")[-1] == object:
                        return childObject

    def applyAlexaCameraSettings(self):
        cmds.setAttr(self.cameraTransform + ".FarClipPlane", 100000)
        cmds.setAttr(self.cameraTransform + ".DisplayResolution", 1)
        cmds.setAttr(self.cameraTransform + ".GateMaskOpacity", 1)

        cmds.setAttr(self.cameraShape + ".horizontalFilmAperture", 1.247)
        cmds.setAttr(self.cameraShape + ".verticalFilmAperture", 0.702)
        cmds.setAttr(self.cameraShape + ".filmFit", 1) # Set Fit Resolution Gate to Horizontal
        cmds.setAttr(self.cameraShape + ".displayGateMaskColor", 0,0,0)
        
        cmds.setAttr('defaultResolution.width', 1920)
        cmds.setAttr('defaultResolution.height', 1080)
        cmds.setAttr('defaultResolution.deviceAspectRatio', 1.778)

    def applyCinematicCameraSettings(self):
        cmds.setAttr(self.cameraTransform + ".FarClipPlane", 100000)
        cmds.setAttr(self.cameraTransform + ".DisplayResolution", 1)
        cmds.setAttr(self.cameraTransform + ".GateMaskOpacity", 1)

        cmds.setAttr(self.cameraShape + ".horizontalFilmAperture", 2.408)
        cmds.setAttr(self.cameraShape + ".verticalFilmAperture", 1.007)
        cmds.setAttr(self.cameraShape + ".filmFit", 3) # Set Fit Resolution Gate to Overscan
        cmds.setAttr(self.cameraShape + ".displayGateMaskColor", 0,0,0)
                
        cmds.setAttr('defaultResolution.width', 1920)
        cmds.setAttr('defaultResolution.height', 1080)
        cmds.setAttr('defaultResolution.deviceAspectRatio', 3.216)
        cmds.setAttr('defaultResolution.pixelAspect', 1.809)

    def applyFocalLength(self, focalLength: int):
        cmds.setAttr(self.cameraTransform + ".FocalLength", focalLength)

    def getFocalLength(self) -> float:
        return cmds.getAttr(self.cameraTransform + ".FocalLength")

    def applyFStop(self, fStop: float):
        cmds.setAttr(self.cameraTransform + ".FStop", fStop)

    def getFStop(self) -> float:
        return cmds.getAttr(self.cameraTransform + ".FStop")

    def applyCameraLocatorScale(self, scale: float):
        cmds.setAttr(self.cameraTransform + ".Camera_Scale", scale)

    def getCameraLocatorScale(self) -> float:
        return cmds.getAttr(self.cameraTransform + ".Camera_Scale")

    def applyDof(self, dof: bool):
        cmds.setAttr(self.cameraTransform + ".DepthofField", dof)

    def getDof(self) -> bool:
        return bool(cmds.getAttr(self.cameraTransform + ".DepthofField"))

    def applyFocusPlane(self, focusPlane: bool):
        cmds.setAttr(self.cameraTransform + ".FocusPlane", focusPlane)

    def getFocusPlane(self) -> bool:
        return bool(cmds.getAttr(self.cameraTransform + ".FocusPlane"))

    def applySelectFocusPlane(self):
        cmds.select(self._cameraFocusCtrl)

    def applyGrid(self, grid):
        # assert(grid in ALACameraModel.CameraSettingGridMode)
        cmds.setAttr(self.cameraTransform + ".Grid_vis", int(grid))

    def getGrid(self):
        grid = cmds.getAttr(self.cameraTransform + ".Grid_vis")
        # assert(grid in ALACameraModel.CameraSettingGridMode)
        return ALACameraModel.CameraSettingGridMode(grid)

    def applyAim(self, aim: bool):
        cmds.setAttr(self.cameraTransform + ".CameraAIm", aim)

    def getAim(self) -> bool:
        return cmds.getAttr(self.cameraTransform + ".CameraAIm")

    def applyAimLocatorScale(self, scale: float):
        if cmds.getAttr(self._cameraAimCtrl + ".scaleX", lock=True):
            cmds.setAttr(self._cameraAimCtrl + ".scaleX", lock=False)
            cmds.setAttr(self._cameraAimCtrl + ".scaleX", scale, lock=True)
        if cmds.getAttr(self._cameraAimCtrl + ".scaleY", lock=True):
            cmds.setAttr(self._cameraAimCtrl + ".scaleY", lock=False)
            cmds.setAttr(self._cameraAimCtrl + ".scaleY", scale, lock=True)
        if cmds.getAttr(self._cameraAimCtrl + ".scaleZ", lock=True):
            cmds.setAttr(self._cameraAimCtrl + ".scaleZ", lock=False)
            cmds.setAttr(self._cameraAimCtrl + ".scaleZ", scale, lock=True)

    def getAimLocatorScale(self) -> float:
        scaleX = cmds.getAttr(self._cameraAimCtrl + ".scaleX")
        return scaleX # Scale sizes should be the same for all axes

    def enabledAimLocatorScale(self):
        return self.getAim()

    def applySelectAimLocator(self):
        cmds.select(self._cameraAimCtrl)

    def _shakeTranslateXExpression(self) -> str:
        randomSeed = 0
        amplitudeScale = 0.15
        frequencyScale = 0.003
        return f"{self._cameraShakeCtrl}.{self._shakeCtrlTranslateX} = (noise((frame + {randomSeed}) * {frequencyScale} * {self.cameraTransform}.{self._shakeTranslateXFrequency})/200 * {amplitudeScale} * {self.cameraTransform}.{self._shakeTranslateXAmplitude})*350"

    def _shakeTranslateYExpression(self) -> str:
        randomSeed = 50
        amplitudeScale = 0.15
        frequencyScale = 0.003
        return f"{self._cameraShakeCtrl}.{self._shakeCtrlTranslateY} = (noise((frame + {randomSeed}) * {frequencyScale} * {self.cameraTransform}.{self._shakeTranslateYFrequency})/200 * {amplitudeScale} * {self.cameraTransform}.{self._shakeTranslateYAmplitude})*350"

    def _shakeTranslateZExpression(self) -> str:
        randomSeed = 100
        amplitudeScale = 0.15
        frequencyScale = 0.003
        return f"{self._cameraShakeCtrl}.{self._shakeCtrlTranslateZ} = (noise((frame + {randomSeed}) * {frequencyScale} * {self.cameraTransform}.{self._shakeTranslateZFrequency})/200 * {amplitudeScale} * {self.cameraTransform}.{self._shakeTranslateZAmplitude})*350"

    def _shakeRotateXExpression(self) -> str:
        randomSeed = 0
        amplitudeScale = 0.15
        frequencyScale = 0.003
        return f"{self._cameraShakeCtrl}.{self._shakeCtrlRotateX} = (noise((frame + {randomSeed}) * {frequencyScale} * {self.cameraTransform}.{self._shakeRotateXFrequency})/200 * {amplitudeScale} * {self.cameraTransform}.{self._shakeRotateXAmplitude})*350"

    def _shakeRotateYExpression(self) -> str:
        randomSeed = 50
        amplitudeScale = 0.15
        frequencyScale = 0.003
        return f"{self._cameraShakeCtrl}.{self._shakeCtrlRotateY} = (noise((frame + {randomSeed}) * {frequencyScale} * {self.cameraTransform}.{self._shakeRotateYFrequency})/200 * {amplitudeScale} * {self.cameraTransform}.{self._shakeRotateYAmplitude})*350"
    
    def _shakeRotateZExpression(self) -> str:
        randomSeed = 100
        amplitudeScale = 0.15
        frequencyScale = 0.003
        return f"{self._cameraShakeCtrl}.{self._shakeCtrlRotateZ} = (noise((frame + {randomSeed}) * {frequencyScale} * {self.cameraTransform}.{self._shakeRotateZFrequency})/200 * {amplitudeScale} * {self.cameraTransform}.{self._shakeRotateZAmplitude})*350"

    def applyShake(self, shake: bool):
        """
        Apply shake to the camera by creating expressions that drive the camera's X, Y, Z translations and rotations.
        The shake is controlled by the shake attributes on the camera transform.
        """
        shakeAttributes = [
            self._shakeTranslateXAmplitude,
            self._shakeTranslateXFrequency,
            self._shakeTranslateYAmplitude,
            self._shakeTranslateYFrequency,
            self._shakeTranslateZAmplitude,
            self._shakeTranslateZFrequency,
            self._shakeRotateXAmplitude,
            self._shakeRotateXFrequency,
            self._shakeRotateYAmplitude,
            self._shakeRotateYFrequency,
            self._shakeRotateZAmplitude,
            self._shakeRotateZFrequency,
        ]
        shakeAttributesDefault = [
            self._shakeTranslateXAmplitudeDefault,
            self._shakeTranslateXFrequencyDefault,
            self._shakeTranslateYAmplitudeDefault,
            self._shakeTranslateYFrequencyDefault,
            self._shakeTranslateZAmplitudeDefault,
            self._shakeTranslateZFrequencyDefault,
            self._shakeRotateXAmplitudeDefault,
            self._shakeRotateXFrequencyDefault,
            self._shakeRotateYAmplitudeDefault,
            self._shakeRotateYFrequencyDefault,
            self._shakeRotateZAmplitudeDefault,
            self._shakeRotateZFrequencyDefault,
        ]
        # Create shake attributes on the camera transform if they don't exist
        for shakeAttribute, shakeAttributeDefault in zip(shakeAttributes, shakeAttributesDefault):
            if not cmds.attributeQuery(shakeAttribute, node=self.cameraTransform, exists=True):
                cmds.addAttr(self.cameraTransform, longName=shakeAttribute, attributeType="float", defaultValue=shakeAttributeDefault)

        # Delete all existing shake expressions driving the camera X, Y, Z translations and rotations
        shakeCtrlAttributes = [self._shakeCtrlTranslateX, self._shakeCtrlTranslateY, self._shakeCtrlTranslateZ, self._shakeCtrlRotateX, self._shakeCtrlRotateY, self._shakeCtrlRotateZ]
        for attribute in shakeCtrlAttributes:
            fullAttribute = f"{self._cameraShakeCtrl}.{attribute}"
            expressions = cmds.listConnections(fullAttribute, type="expression", skipConversionNodes=True) or []
            for expression in expressions:
                cmds.delete(expression)
            cmds.evalDeferred(f"cmds.setAttr(\"{fullAttribute}\", 0)")

        if shake:
            # Create expressions for shake X, Y, Z translations
            cmds.evalDeferred(f"cmds.expression(string=\"{self._shakeTranslateXExpression()}\", name=\"{self.cameraTransform}_{self._shakeCtrlTranslateX}_shake\")")
            cmds.evalDeferred(f"cmds.expression(string=\"{self._shakeTranslateYExpression()}\", name=\"{self.cameraTransform}_{self._shakeCtrlTranslateY}_shake\")")
            cmds.evalDeferred(f"cmds.expression(string=\"{self._shakeTranslateZExpression()}\", name=\"{self.cameraTransform}_{self._shakeCtrlTranslateZ}_shake\")")
            cmds.evalDeferred(f"cmds.expression(string=\"{self._shakeRotateXExpression()}\", name=\"{self.cameraTransform}_{self._shakeCtrlRotateX}_shake\")")
            cmds.evalDeferred(f"cmds.expression(string=\"{self._shakeRotateYExpression()}\", name=\"{self.cameraTransform}_{self._shakeCtrlRotateY}_shake\")")
            cmds.evalDeferred(f"cmds.expression(string=\"{self._shakeRotateZExpression()}\", name=\"{self.cameraTransform}_{self._shakeCtrlRotateZ}_shake\")")
        
        cmds.setAttr(self.cameraTransform + ".camerashake", shake)

    def getShake(self) -> bool:
        return cmds.getAttr(self.cameraTransform + ".camerashake")

    def enabledShakeControls(self):
        return self.getShake()

    def applyShakeTranslateXFrequency(self, frequency: float):
        cmds.setAttr(self.cameraTransform + "." + self._shakeTranslateXFrequency, frequency)

    def getShakeTranslateXFrequency(self) -> float:
        # Check if attribute exists
        if not cmds.attributeQuery(self._shakeTranslateXFrequency, node=self.cameraTransform, exists=True):
            return 0
        return cmds.getAttr(self.cameraTransform + "." + self._shakeTranslateXFrequency)

    def applyShakeTranslateXAmplitude(self, amplitude: float):
        cmds.setAttr(self.cameraTransform + "." + self._shakeTranslateXAmplitude, amplitude)

    def getShakeTranslateXAmplitude(self) -> float:
        if not cmds.attributeQuery(self._shakeTranslateXAmplitude, node=self.cameraTransform, exists=True):
            return 0
        return cmds.getAttr(self.cameraTransform + "." + self._shakeTranslateXAmplitude)

    def applyShakeTranslateYFrequency(self, frequency: float):
        cmds.setAttr(self.cameraTransform + "." + self._shakeTranslateYFrequency, frequency)

    def getShakeTranslateYFrequency(self) -> float:
        if not cmds.attributeQuery(self._shakeTranslateYFrequency, node=self.cameraTransform, exists=True):
            return 0
        return cmds.getAttr(self.cameraTransform + "." + self._shakeTranslateYFrequency)

    def applyShakeTranslateYAmplitude(self, amplitude: float):
        cmds.setAttr(self.cameraTransform + "." + self._shakeTranslateYAmplitude, amplitude)

    def getShakeTranslateYAmplitude(self) -> float:
        if not cmds.attributeQuery(self._shakeTranslateYAmplitude, node=self.cameraTransform, exists=True):
            return 0
        return cmds.getAttr(self.cameraTransform + "." + self._shakeTranslateYAmplitude)

    def applyShakeTranslateZFrequency(self, frequency: float):
        cmds.setAttr(self.cameraTransform + "." + self._shakeTranslateZFrequency, frequency)

    def getShakeTranslateZFrequency(self) -> float:
        if not cmds.attributeQuery(self._shakeTranslateZFrequency, node=self.cameraTransform, exists=True):
            return 0
        return cmds.getAttr(self.cameraTransform + "." + self._shakeTranslateZFrequency)

    def applyShakeTranslateZAmplitude(self, amplitude: float):
        cmds.setAttr(self.cameraTransform + "." + self._shakeTranslateZAmplitude, amplitude)

    def getShakeTranslateZAmplitude(self) -> float:
        if not cmds.attributeQuery(self._shakeTranslateZAmplitude, node=self.cameraTransform, exists=True):
            return 0
        return cmds.getAttr(self.cameraTransform + "." + self._shakeTranslateZAmplitude)

    def applyShakeRotateXFrequency(self, frequency: float):
        cmds.setAttr(self.cameraTransform + "." + self._shakeRotateXFrequency, frequency)

    def getShakeRotateXFrequency(self) -> float:
        if not cmds.attributeQuery(self._shakeRotateXFrequency, node=self.cameraTransform, exists=True):
            return 0
        return cmds.getAttr(self.cameraTransform + "." + self._shakeRotateXFrequency)
    
    def applyShakeRotateXAmplitude(self, amplitude: float):
        cmds.setAttr(self.cameraTransform + "." + self._shakeRotateXAmplitude, amplitude)

    def getShakeRotateXAmplitude(self) -> float:
        if not cmds.attributeQuery(self._shakeRotateXAmplitude, node=self.cameraTransform, exists=True):
            return 0
        return cmds.getAttr(self.cameraTransform + "." + self._shakeRotateXAmplitude)
    
    def applyShakeRotateYFrequency(self, frequency: float):
        cmds.setAttr(self.cameraTransform + "." + self._shakeRotateYFrequency, frequency)

    def getShakeRotateYFrequency(self) -> float:
        if not cmds.attributeQuery(self._shakeRotateYFrequency, node=self.cameraTransform, exists=True):
            return 0
        return cmds.getAttr(self.cameraTransform + "." + self._shakeRotateYFrequency)
    
    def applyShakeRotateYAmplitude(self, amplitude: float):
        cmds.setAttr(self.cameraTransform + "." + self._shakeRotateYAmplitude, amplitude)

    def getShakeRotateYAmplitude(self) -> float:
        if not cmds.attributeQuery(self._shakeRotateYAmplitude, node=self.cameraTransform, exists=True):
            return 0
        return cmds.getAttr(self.cameraTransform + "." + self._shakeRotateYAmplitude)
    
    def applyShakeRotateZFrequency(self, frequency: float):
        cmds.setAttr(self.cameraTransform + "." + self._shakeRotateZFrequency, frequency)

    def getShakeRotateZFrequency(self) -> float:
        if not cmds.attributeQuery(self._shakeRotateZFrequency, node=self.cameraTransform, exists=True):
            return 0
        return cmds.getAttr(self.cameraTransform + "." + self._shakeRotateZFrequency)
    
    def applyShakeRotateZAmplitude(self, amplitude: float):
        cmds.setAttr(self.cameraTransform + "." + self._shakeRotateZAmplitude, amplitude)

    def getShakeRotateZAmplitude(self) -> float:
        if not cmds.attributeQuery(self._shakeRotateZAmplitude, node=self.cameraTransform, exists=True):
            return 0
        return cmds.getAttr(self.cameraTransform + "." + self._shakeRotateZAmplitude)

class BaseCameraModel(AbstractCameraModel):
    """
    BaseCameraModel class for standard Maya cameras. This class inherits from AbstractCameraModel and implements
    methods to apply camera settings specific to standard Maya cameras. It does so by getting and setting the attributes
    on the camera shape.
    """
    def __init__(self, cameraTransform, cameraShape):
        super().__init__(cameraTransform, cameraShape)

    def applyAlexaCameraSettings(self):
        cmds.setAttr(self.cameraShape + ".farClipPlane", 100000)
        cmds.setAttr(self.cameraShape + ".displayResolution", 1)
        cmds.setAttr(self.cameraShape + ".displayGateMaskOpacity", 1)

        cmds.setAttr(self.cameraShape + ".horizontalFilmAperture", 1.247)
        cmds.setAttr(self.cameraShape + ".verticalFilmAperture", 0.702)
        cmds.setAttr(self.cameraShape + ".filmFit", 1) # Set Fit Resolution Gate to Horizontal
        cmds.setAttr(self.cameraShape + ".displayGateMaskColor", 0,0,0)
        
        cmds.setAttr('defaultResolution.width', 1920)
        cmds.setAttr('defaultResolution.height', 1080)
        cmds.setAttr('defaultResolution.deviceAspectRatio', 1.778)

    def applyCinematicCameraSettings(self):
        cmds.setAttr(self.cameraShape + ".farClipPlane", 100000)
        cmds.setAttr(self.cameraShape + ".displayResolution", 1)
        cmds.setAttr(self.cameraShape + ".displayGateMaskOpacity", 1)

        cmds.setAttr(self.cameraShape + ".horizontalFilmAperture", 2.408)
        cmds.setAttr(self.cameraShape + ".verticalFilmAperture", 1.007)
        cmds.setAttr(self.cameraShape + ".filmFit", 3) # Set Fit Resolution Gate to Overscan
        cmds.setAttr(self.cameraShape + ".displayGateMaskColor", 0,0,0)
                
        cmds.setAttr('defaultResolution.width', 1920)
        cmds.setAttr('defaultResolution.height', 1080)
        cmds.setAttr('defaultResolution.deviceAspectRatio', 3.216)
        cmds.setAttr('defaultResolution.pixelAspect', 1.809)

    def applyFocalLength(self, focalLength):
        cmds.setAttr(self.cameraShape + ".fl", focalLength)

    def getFocalLength(self):
        return cmds.getAttr(self.cameraShape + ".fl")

    def applyFStop(self, fStop):
        cmds.setAttr(self.cameraShape + ".fStop", fStop)

    def getFStop(self):
        return cmds.getAttr(self.cameraShape + ".fStop")

    def applyCameraLocatorScale(self, scale):
        cmds.setAttr(self.cameraShape + ".locatorScale", scale)

    def getCameraLocatorScale(self):
        return cmds.getAttr(self.cameraShape + ".locatorScale")

    def applyDof(self, dof):
        cmds.setAttr(self.cameraShape + ".dof", dof)

    def getDof(self):
        return cmds.getAttr(self.cameraShape + ".dof")

class CameraToolsViewModel(QtCore.QObject):
    """
    The view model for CameraTools. This class is responsible for managing the data and logic of the camera tools view.
    It communicates with the model (camera models) and updates the view (CameraTools) accordingly. When the user selects a new
    camera in Maya, this is considered a change from the model side, and the view model will update the view accordingly.

    The whole CameraSetting enum approach is slightly questionable rn... But it is a start to creating more generic data binding
    logic. The next step would be to enable configuring of views and its data binding via some in-house designed markup language.
    Views call applyCameraSetting, getCameraSetting, enabledCameraSetting to establish the data binding.
    """
    # Qt Signal to notify the view when the camera model has received setting changes
    cameraSettingChanged = QtCore.Signal.cameraSettingChanged = QtCore.Signal()

    class CameraSetting(Enum):
        FOCAL_LENGTH =                              "focalLength"
        F_STOP =                                    "fStop"
        CAMERA_LOCATOR_SCALE =                      "cameraLocatorScale"
        DOF =                                       "dof"
        FOCUS_PLANE =                               "focusPlane"
        FOCUS_PLANE_SELECT =                        "focusPlaneSelect"
        GRID =                                      "grid"
        AIM =                                       "aim"
        AIM_LOCATOR_SCALE =                         "aimLocatorScale"
        AIM_LOCATOR_SELECT =                        "aimLocatorSelect"
        SHAKE =                                     "shake"
        SHAKE_TRANSLATE_X_FREQUENCY =               "shakeTranslateXFrequency"
        SHAKE_TRANSLATE_X_AMPLITUDE =               "shakeTranslateXAmplitude"
        SHAKE_TRANSLATE_Y_FREQUENCY =               "shakeTranslateYFrequency"
        SHAKE_TRANSLATE_Y_AMPLITUDE =               "shakeTranslateYAmplitude"
        SHAKE_TRANSLATE_TRANSLATE_Z_FREQUENCY =     "shakeTranslateZFrequency"
        SHAKE_TRANSLATE_TRANSLATE_Z_AMPLITUDE =     "shakeTranslateZAmplitude"
        SHAKE_ROTATION_X_FREQUENCY =                "shakeRotateXFrequency"
        SHAKE_ROTATION_X_AMPLITUDE =                "shakeRotateXAmplitude"
        SHAKE_ROTATION_Y_FREQUENCY =                "shakeRotateYFrequency"
        SHAKE_ROTATION_Y_AMPLITUDE =                "shakeRotateYAmplitude"
        SHAKE_ROTATION_Z_FREQUENCY =                "shakeRotateZFrequency"
        SHAKE_ROTATION_Z_AMPLITUDE =                "shakeRotateZAmplitude"
        CAMERA_TEMPLATE_ALEXA =                     "cameraTemplateAlexa"
        CAMERA_TEMPLATE_CINEMATIC =                 "cameraTemplateCinematic"

    class CameraSettingGridMode(Enum):
        NONE =          0
        TWO =           1
        THREE =         2

    @dataclass
    class CameraSettingFn:
        applyFn: Optional[Callable] = None
        getFn: Optional[Callable] = None
        enabledFn: Optional[Callable] = lambda: True

    def __init__(self, parent=None):
        super().__init__(parent)
        self._currentCamera: AbstractCameraModel = None
        self.callbackIds = []
        self._setupCallbacks()

        self.gridToAlaEnum = {
            CameraToolsViewModel.CameraSettingGridMode.NONE : ALACameraModel.CameraSettingGridMode.NONE,
            CameraToolsViewModel.CameraSettingGridMode.TWO: ALACameraModel.CameraSettingGridMode.TWO,
            CameraToolsViewModel.CameraSettingGridMode.THREE: ALACameraModel.CameraSettingGridMode.THREE,
        }
        self.alaEnumToGrid = {v: k for k, v in self.gridToAlaEnum.items()}

        self._alaCameraModelFunctionMappings = {  
            CameraToolsViewModel.CameraSetting.FOCAL_LENGTH:                            CameraToolsViewModel.CameraSettingFn(applyFn=ALACameraModel.applyFocalLength, getFn=ALACameraModel.getFocalLength),
            CameraToolsViewModel.CameraSetting.F_STOP:                                  CameraToolsViewModel.CameraSettingFn(applyFn=ALACameraModel.applyFStop, getFn=ALACameraModel.getFStop),
            CameraToolsViewModel.CameraSetting.CAMERA_LOCATOR_SCALE:                    CameraToolsViewModel.CameraSettingFn(applyFn=ALACameraModel.applyCameraLocatorScale, getFn=ALACameraModel.getCameraLocatorScale),
            CameraToolsViewModel.CameraSetting.DOF:                                     CameraToolsViewModel.CameraSettingFn(applyFn=ALACameraModel.applyDof, getFn=ALACameraModel.getDof),
            CameraToolsViewModel.CameraSetting.FOCUS_PLANE:                             CameraToolsViewModel.CameraSettingFn(applyFn=ALACameraModel.applyFocusPlane, getFn=ALACameraModel.getFocusPlane),
            CameraToolsViewModel.CameraSetting.FOCUS_PLANE_SELECT:                      CameraToolsViewModel.CameraSettingFn(applyFn=ALACameraModel.applySelectFocusPlane),
            CameraToolsViewModel.CameraSetting.GRID:                                    CameraToolsViewModel.CameraSettingFn(applyFn=ALACameraModel.applyGrid, getFn=ALACameraModel.getGrid),
            CameraToolsViewModel.CameraSetting.AIM:                                     CameraToolsViewModel.CameraSettingFn(applyFn=ALACameraModel.applyAim, getFn=ALACameraModel.getAim),
            CameraToolsViewModel.CameraSetting.AIM_LOCATOR_SCALE:                       CameraToolsViewModel.CameraSettingFn(applyFn=ALACameraModel.applyAimLocatorScale, getFn=ALACameraModel.getAimLocatorScale, enabledFn=ALACameraModel.enabledAimLocatorScale),
            CameraToolsViewModel.CameraSetting.AIM_LOCATOR_SELECT:                      CameraToolsViewModel.CameraSettingFn(applyFn=ALACameraModel.applySelectAimLocator),
            CameraToolsViewModel.CameraSetting.SHAKE:                                   CameraToolsViewModel.CameraSettingFn(applyFn=ALACameraModel.applyShake, getFn=ALACameraModel.getShake),
            CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_X_FREQUENCY:             CameraToolsViewModel.CameraSettingFn(applyFn=ALACameraModel.applyShakeTranslateXFrequency, getFn=ALACameraModel.getShakeTranslateXFrequency, enabledFn=ALACameraModel.enabledShakeControls),
            CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_X_AMPLITUDE:             CameraToolsViewModel.CameraSettingFn(applyFn=ALACameraModel.applyShakeTranslateXAmplitude, getFn=ALACameraModel.getShakeTranslateXAmplitude, enabledFn=ALACameraModel.enabledShakeControls),
            CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_Y_FREQUENCY:             CameraToolsViewModel.CameraSettingFn(applyFn=ALACameraModel.applyShakeTranslateYFrequency, getFn=ALACameraModel.getShakeTranslateYFrequency, enabledFn=ALACameraModel.enabledShakeControls),
            CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_Y_AMPLITUDE:             CameraToolsViewModel.CameraSettingFn(applyFn=ALACameraModel.applyShakeTranslateYAmplitude, getFn=ALACameraModel.getShakeTranslateYAmplitude, enabledFn=ALACameraModel.enabledShakeControls),
            CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_TRANSLATE_Z_FREQUENCY:   CameraToolsViewModel.CameraSettingFn(applyFn=ALACameraModel.applyShakeTranslateZFrequency, getFn=ALACameraModel.getShakeTranslateZFrequency, enabledFn=ALACameraModel.enabledShakeControls),
            CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_TRANSLATE_Z_AMPLITUDE:   CameraToolsViewModel.CameraSettingFn(applyFn=ALACameraModel.applyShakeTranslateZAmplitude, getFn=ALACameraModel.getShakeTranslateZAmplitude, enabledFn=ALACameraModel.enabledShakeControls),
            CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_X_FREQUENCY:              CameraToolsViewModel.CameraSettingFn(applyFn=ALACameraModel.applyShakeRotateXFrequency, getFn=ALACameraModel.getShakeRotateXFrequency, enabledFn=ALACameraModel.enabledShakeControls),
            CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_X_AMPLITUDE:              CameraToolsViewModel.CameraSettingFn(applyFn=ALACameraModel.applyShakeRotateXAmplitude, getFn=ALACameraModel.getShakeRotateXAmplitude, enabledFn=ALACameraModel.enabledShakeControls),
            CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_Y_FREQUENCY:              CameraToolsViewModel.CameraSettingFn(applyFn=ALACameraModel.applyShakeRotateYFrequency, getFn=ALACameraModel.getShakeRotateYFrequency, enabledFn=ALACameraModel.enabledShakeControls),
            CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_Y_AMPLITUDE:              CameraToolsViewModel.CameraSettingFn(applyFn=ALACameraModel.applyShakeRotateYAmplitude, getFn=ALACameraModel.getShakeRotateYAmplitude, enabledFn=ALACameraModel.enabledShakeControls),
            CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_Z_FREQUENCY:              CameraToolsViewModel.CameraSettingFn(applyFn=ALACameraModel.applyShakeRotateZFrequency, getFn=ALACameraModel.getShakeRotateZFrequency, enabledFn=ALACameraModel.enabledShakeControls),
            CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_Z_AMPLITUDE:              CameraToolsViewModel.CameraSettingFn(applyFn=ALACameraModel.applyShakeRotateZAmplitude, getFn=ALACameraModel.getShakeRotateZAmplitude, enabledFn=ALACameraModel.enabledShakeControls),
            CameraToolsViewModel.CameraSetting.CAMERA_TEMPLATE_ALEXA:                   CameraToolsViewModel.CameraSettingFn(applyFn=ALACameraModel.applyAlexaCameraSettings),
            CameraToolsViewModel.CameraSetting.CAMERA_TEMPLATE_CINEMATIC:               CameraToolsViewModel.CameraSettingFn(applyFn=ALACameraModel.applyCinematicCameraSettings),
        }

        self._baseCameraModelFunctionMappings = {
            CameraToolsViewModel.CameraSetting.FOCAL_LENGTH:                    CameraToolsViewModel.CameraSettingFn(applyFn=BaseCameraModel.applyFocalLength, getFn=BaseCameraModel.getFocalLength),
            CameraToolsViewModel.CameraSetting.F_STOP:                          CameraToolsViewModel.CameraSettingFn(applyFn=BaseCameraModel.applyFStop, getFn=BaseCameraModel.getFStop),
            CameraToolsViewModel.CameraSetting.CAMERA_LOCATOR_SCALE:            CameraToolsViewModel.CameraSettingFn(applyFn=BaseCameraModel.applyCameraLocatorScale, getFn=BaseCameraModel.getCameraLocatorScale),
            CameraToolsViewModel.CameraSetting.DOF:                             CameraToolsViewModel.CameraSettingFn(applyFn=BaseCameraModel.applyDof, getFn=BaseCameraModel.getDof),
            CameraToolsViewModel.CameraSetting.CAMERA_TEMPLATE_ALEXA:           CameraToolsViewModel.CameraSettingFn(applyFn=BaseCameraModel.applyAlexaCameraSettings),
            CameraToolsViewModel.CameraSetting.CAMERA_TEMPLATE_CINEMATIC:       CameraToolsViewModel.CameraSettingFn(applyFn=BaseCameraModel.applyCinematicCameraSettings),
        }

        self._cameraModelFunctionMappings = {
            CameraToolsViewModel.CameraSetting.FOCAL_LENGTH:                                CameraToolsViewModel.CameraSettingFn(applyFn=self.applyFocalLength, getFn=self.getFocalLength),
            CameraToolsViewModel.CameraSetting.F_STOP:                                      CameraToolsViewModel.CameraSettingFn(applyFn=self.applyFStop, getFn=self.getFStop),
            CameraToolsViewModel.CameraSetting.CAMERA_LOCATOR_SCALE:                        CameraToolsViewModel.CameraSettingFn(applyFn=self.applyCameraLocatorScale, getFn=self.getCameraLocatorScale),
            CameraToolsViewModel.CameraSetting.DOF:                                         CameraToolsViewModel.CameraSettingFn(applyFn=self.applyDof, getFn=self.getDof),
            CameraToolsViewModel.CameraSetting.FOCUS_PLANE:                                 CameraToolsViewModel.CameraSettingFn(applyFn=self.applyFocusPlane, getFn=self.getFocusPlane),
            CameraToolsViewModel.CameraSetting.FOCUS_PLANE_SELECT:                          CameraToolsViewModel.CameraSettingFn(applyFn=self.applyFocusPlaneSelect),
            CameraToolsViewModel.CameraSetting.GRID:                                        CameraToolsViewModel.CameraSettingFn(applyFn=self.applyGrid, getFn=self.getGrid),
            CameraToolsViewModel.CameraSetting.AIM:                                         CameraToolsViewModel.CameraSettingFn(applyFn=self.applyAim, getFn=self.getAim),
            CameraToolsViewModel.CameraSetting.AIM_LOCATOR_SCALE:                           CameraToolsViewModel.CameraSettingFn(applyFn=self.applyAimLocatorScale, getFn=self.getAimLocatorScale, enabledFn=self.enabledAimLocatorScale),
            CameraToolsViewModel.CameraSetting.AIM_LOCATOR_SELECT:                          CameraToolsViewModel.CameraSettingFn(applyFn=self.applySelectAimLocator),
            CameraToolsViewModel.CameraSetting.SHAKE:                                       CameraToolsViewModel.CameraSettingFn(applyFn=self.applyShake, getFn=self.getShake),
            CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_X_FREQUENCY:                 CameraToolsViewModel.CameraSettingFn(applyFn=self.applyShakeTranslateXFrequency, getFn=self.getShakeTranslateXFrequency, enabledFn=self.enabledShakeTranslateXFrequency),
            CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_X_AMPLITUDE:                 CameraToolsViewModel.CameraSettingFn(applyFn=self.applyShakeTranslateXAmplitude, getFn=self.getShakeTranslateXAmplitude, enabledFn=self.enabledShakeTranslateXAmplitude),
            CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_Y_FREQUENCY:                 CameraToolsViewModel.CameraSettingFn(applyFn=self.applyShakeTranslateYFrequency, getFn=self.getShakeTranslateYFrequency, enabledFn=self.enabledShakeTranslateYFrequency),
            CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_Y_AMPLITUDE:                 CameraToolsViewModel.CameraSettingFn(applyFn=self.applyShakeTranslateYAmplitude, getFn=self.getShakeTranslateYAmplitude, enabledFn=self.enabledShakeTranslateYAmplitude),
            CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_TRANSLATE_Z_FREQUENCY:       CameraToolsViewModel.CameraSettingFn(applyFn=self.applyShakeTranslateZFrequency, getFn=self.getShakeTranslateZFrequency, enabledFn=self.enabledShakeTranslateZFrequency),
            CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_TRANSLATE_Z_AMPLITUDE:       CameraToolsViewModel.CameraSettingFn(applyFn=self.applyShakeTranslateZAmplitude, getFn=self.getShakeTranslateZAmplitude, enabledFn=self.enabledShakeTranslateZAmplitude),
            CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_X_FREQUENCY:                  CameraToolsViewModel.CameraSettingFn(applyFn=self.applyShakeRotateXFrequency, getFn=self.getShakeRotateXFrequency, enabledFn=self.enabledShakeRotateXFrequency),
            CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_X_AMPLITUDE:                  CameraToolsViewModel.CameraSettingFn(applyFn=self.applyShakeRotateXAmplitude, getFn=self.getShakeRotateXAmplitude, enabledFn=self.enabledShakeRotateXAmplitude),
            CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_Y_FREQUENCY:                  CameraToolsViewModel.CameraSettingFn(applyFn=self.applyShakeRotateYFrequency, getFn=self.getShakeRotateYFrequency, enabledFn=self.enabledShakeRotateYFrequency),
            CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_Y_AMPLITUDE:                  CameraToolsViewModel.CameraSettingFn(applyFn=self.applyShakeRotateYAmplitude, getFn=self.getShakeRotateYAmplitude, enabledFn=self.enabledShakeRotateYAmplitude),
            CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_Z_FREQUENCY:                  CameraToolsViewModel.CameraSettingFn(applyFn=self.applyShakeRotateZFrequency, getFn=self.getShakeRotateZFrequency, enabledFn=self.enabledShakeRotateZFrequency),
            CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_Z_AMPLITUDE:                  CameraToolsViewModel.CameraSettingFn(applyFn=self.applyShakeRotateZAmplitude, getFn=self.getShakeRotateZAmplitude, enabledFn=self.enabledShakeRotateZAmplitude),
            CameraToolsViewModel.CameraSetting.CAMERA_TEMPLATE_ALEXA:                       CameraToolsViewModel.CameraSettingFn(applyFn=self.applyAlexaCameraSettings),
            CameraToolsViewModel.CameraSetting.CAMERA_TEMPLATE_CINEMATIC:                   CameraToolsViewModel.CameraSettingFn(applyFn=self.applyCinematicCameraSettings),
        }

    @property
    def currentCamera(self):
        return self._currentCamera

    def _setupCallbacks(self):
        selectionChangedCbId = om.MEventMessage.addEventCallback("SelectionChanged", lambda *args: self.changeCamera())
        self.callbackIds.append(selectionChangedCbId)
        print("cameraTools: Maya callbacks registered.")

    def dockCloseEventTriggered(self):
        for cbId in self.callbackIds:
            om.MMessage.removeCallback(cbId)
        self.callbackIds = []
        print("cameraTools: Maya callbacks deregistered.")

    def changeCamera(self):
        selections = cmds.ls(selection=True)
        if not selections:
            self._currentCamera = None
            self.cameraSettingChanged.emit()
            return
        for selection in reversed(selections):
            camera = CameraModelFactory.alaCameraModelViaRig(selection)
            if camera:
                self._currentCamera = camera
                self.cameraSettingChanged.emit()
                return
            cameraShape = cmds.listRelatives(selection, type="camera")
            if cameraShape:
                self._currentCamera = CameraModelFactory.cameraModel(selection)
                self.cameraSettingChanged.emit()
                return
        # No valid camera found, set current camera to None
        self._currentCamera = None
        self.cameraSettingChanged.emit()

    def _getMappingFromCameraType(self, cameraType: type):
        if not isinstance(cameraType, type):
            cmds.error("cameraTools: Error: No type provided.")
            return None
        if cameraType == ALACameraModel:
            return self._alaCameraModelFunctionMappings
        elif cameraType == BaseCameraModel:
            return self._baseCameraModelFunctionMappings
        else:
            cmds.error("cameraTools: Error: Unknown camera model type.")
            return None

    def _getModelApplyFn(self, cameraType, setting):
        if not cameraType:
            cmds.error("cameraTools: Error: No camera type provided.")
            return None
        mapping = self._getMappingFromCameraType(cameraType)
        if not mapping:
            cmds.error("cameraTools: Error: No mapping found for the camera type.")
            return None
        if setting in mapping:
            return mapping[setting].applyFn
        else:
            cmds.error(f"cameraTools: The requested camera setting '{setting}' does not exist.")
            return None
   
    def _getModelGetFn(self, cameraType, setting):
        if not cameraType:
            cmds.error("cameraTools: Error: No camera type provided.")
            return None
        mapping = self._getMappingFromCameraType(cameraType)
        if not mapping:
            cmds.error("cameraTools: Error: No mapping found for the camera type.")
            return None
        if setting in mapping:
            return mapping[setting].getFn
        else:
            cmds.error(f"cameraTools: The requested camera setting '{setting}' does not exist.")
            return None

    def _getModelEnabledFn(self, cameraType, setting):
        if not cameraType:
            cmds.error("cameraTools: Error: No camera type provided.")
            return None
        mapping = self._getMappingFromCameraType(cameraType)
        if not mapping:
            cmds.error("cameraTools: Error: No mapping found for the camera type.")
            return None
        if setting in mapping:
            return mapping[setting].enabledFn
        else:
            cmds.error(f"cameraTools: The requested camera setting '{setting}' does not exist.")
            return None

    def _getSettingSupported(self, cameraType, setting):
        if not cameraType:
            cmds.error("cameraTools: Error: No camera type provided.")
            return False
        mapping = self._getMappingFromCameraType(cameraType)
        if not mapping:
            cmds.error("cameraTools: Error: No mapping found for the camera type.")
            return False
        return setting in mapping

    def applyCameraSetting(self, setting, *args):
        if self.isCameraSelected():
            cameraType = type(self._currentCamera)
            if self._getSettingSupported(cameraType, setting):
                if setting in self._cameraModelFunctionMappings:
                    applyFn = self._cameraModelFunctionMappings[setting].applyFn
                else:
                    print("cameraTools: Error: No apply function found for the requested camera setting.")
                    return
                if applyFn:
                    applyFn(*args)
                self.cameraSettingChanged.emit()
            else:
                print(f"cameraTools: Error: The requested camera setting '{setting}' is not supported for the current camera.")
        else:
            print("cameraTools: Error: No camera selected to apply settings.")

    def getCameraSetting(self, setting, *args):
        if self.isCameraSelected():
            cameraType = type(self._currentCamera)
            if self._getSettingSupported(cameraType, setting):
                if setting in self._cameraModelFunctionMappings:
                    getFn = self._cameraModelFunctionMappings[setting].getFn
                else:
                    print("cameraTools: Error: No get function found for the requested camera setting.")
                    return None
                if getFn:
                    return getFn(*args)
                else:
                    return None
            else:
                print(f"cameraTools: Error: The requested camera setting '{setting}' is not supported for the current camera.")
                return None
        else:
            print("cameraTools: Error: No camera selected to get settings.")
            return None

    def enabledCameraSetting(self, setting, *args):
        if self.isCameraSelected():
            cameraType = type(self._currentCamera)
            if self._getSettingSupported(cameraType, setting):
                if setting in self._cameraModelFunctionMappings:
                    enabledFn = self._cameraModelFunctionMappings[setting].enabledFn
                else:
                    print("cameraTools: Error: No enabled function found for the requested camera setting.")
                    return False
                if enabledFn:
                    return enabledFn(*args)
                else:
                    return False
            else:
                print(f"cameraTools: Error: The requested camera setting '{setting}' is not supported for the current camera.")
                return False
        else:
            print("cameraTools: Error: No camera selected to check settings.")
            return False

    def isCameraSelectedAndSettingSupported(self, setting):
        if self.isCameraSelected():
            cameraType = type(self._currentCamera)
            if self._getSettingSupported(cameraType, setting):
                return True
            else:
                return False
        else:
            return False

    def isCameraSelected(self):
        return bool(self._currentCamera)

    def cameraTransform(self):
        if self.isCameraSelected():
            return self._currentCamera.cameraTransform
        else:
            print("cameraTools: Error: No camera selected.")
            return None

    def cameraFocalLengthPresets(self):
        if self.isCameraSelected():
            return self._currentCamera.presetFocalLengths
        else:
            print(f"cameraTools: Error: No camera selected to get focal length presets.")
            return []

    def cameraFStopPresets(self):
        if self.isCameraSelected():
            return self._currentCamera.presetFStops
        else:
            print(f"cameraTools: Error: No camera selected to get f-stop presets.")
            return []

    def createBaseCamera(self):
        cameraNames = cmds.camera()
        cmds.evalDeferred(cmds.select(cameraNames[0]))

    def createALACamera(self):
        createALACameraRig()

    def applyAlexaCameraSettings(self):
        if self.isCameraSelected():
            self._getModelApplyFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.CAMERA_TEMPLATE_ALEXA)(self.currentCamera)
        else:
            print("cameraTools: Error: No camera selected to apply Alexa camera settings.")

    def applyCinematicCameraSettings(self):
        if self.isCameraSelected():
            self._getModelApplyFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.CAMERA_TEMPLATE_CINEMATIC)(self.currentCamera)
        else:
            print("cameraTools: Error: No camera selected to apply cinematic camera settings.")

    def applyFocalLength(self, focalLength):
        if self.isCameraSelected():
            self._getModelApplyFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.FOCAL_LENGTH)(self.currentCamera, focalLength)
        else:
            print("cameraTools: Error: No camera selected to apply focal length.")

    def getFocalLength(self):
        if self.isCameraSelected():
            return self._getModelGetFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.FOCAL_LENGTH)(self.currentCamera)
        else:
            print("cameraTools: Error: No camera selected to get focal length.")
            return None

    def applyFStop(self, fStop):
        if self.isCameraSelected():
            self._getModelApplyFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.F_STOP)(self.currentCamera, fStop)
        else:
            print("cameraTools: Error: No camera selected to apply f-stop.")

    def getFStop(self):
        if self.isCameraSelected():
            return self._getModelGetFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.F_STOP)(self.currentCamera)
        else:
            print("cameraTools: Error: No camera selected to get f-stop.")
            return None

    def applyCameraLocatorScale(self, scale):
        if self.isCameraSelected():
            self._getModelApplyFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.CAMERA_LOCATOR_SCALE)(self.currentCamera, scale)
        else:
            print("cameraTools: Error: No camera selected to apply camera locator scale.")

    def getCameraLocatorScale(self):
        if self.isCameraSelected():
            return self._getModelGetFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.CAMERA_LOCATOR_SCALE)(self.currentCamera)
        else:
            print("cameraTools: Error: No camera selected to get camera locator scale.")
            return None

    def applyDof(self, dof):
        if self.isCameraSelected():
            self._getModelApplyFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.DOF)(self.currentCamera, dof)
        else:
            print("cameraTools: Error: No camera selected to apply DOF.")

    def getDof(self):
        if self.isCameraSelected():
            return self._getModelGetFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.DOF)(self.currentCamera)
        else:
            print("cameraTools: Error: No camera selected to get DOF.")
            return None

    def applyFocusPlane(self, focusPlane):
        if self.isCameraSelected():
            self._getModelApplyFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.FOCUS_PLANE)(self.currentCamera, focusPlane)
        else:
            print("cameraTools: Error: No camera selected to apply focus plane.")

    def getFocusPlane(self):
        if self.isCameraSelected():
            return self._getModelGetFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.FOCUS_PLANE)(self.currentCamera)
        else:
            print("cameraTools: Error: No camera selected to get focus plane.")
            return None

    def applyFocusPlaneSelect(self):
        if self.isCameraSelected():
            self._getModelApplyFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.FOCUS_PLANE_SELECT)(self.currentCamera)
        else:
            print("cameraTools: Error: No camera selected to select focus plane.")

    def applyGrid(self, grid):
        # TODO: This is a bit of a hack, but it works for now. We need to find a better way to handle this.
        if self.isCameraSelected():
            if type(self._currentCamera) == ALACameraModel:
                alaGridEnum = self.gridToAlaEnum[grid]
                self._getModelApplyFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.GRID)(self.currentCamera, alaGridEnum)
            else:
                self._getModelApplyFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.GRID)(self.currentCamera, grid)
        else:
            print("cameraTools: Error: No camera selected to apply grid.")

    def getGrid(self):
        # TODO: Hacky, same thing here
        if self.isCameraSelected():
            if type(self._currentCamera) == ALACameraModel:
                grid = self._getModelGetFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.GRID)(self.currentCamera)
                return self.alaEnumToGrid[grid]
            else:
                return self._getModelGetFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.GRID)(self.currentCamera)
        else:
            print("cameraTools: Error: No camera selected to get grid.")
            return None

    def applyAim(self, aim):
        if self.isCameraSelected():
            self._getModelApplyFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.AIM)(self.currentCamera, aim)
        else:
            print("cameraTools: Error: No camera selected to apply aim.")
 
    def getAim(self):
        if self.isCameraSelected():
            return self._getModelGetFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.AIM)(self.currentCamera)
        else:
            print("cameraTools: Error: No camera selected to get aim.")
            return None

    def applyAimLocatorScale(self, scale):
        if self.isCameraSelected():
            self._getModelApplyFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.AIM_LOCATOR_SCALE)(self.currentCamera, scale)
        else:
            print("cameraTools: Error: No camera selected to apply aim locator scale.")

    def getAimLocatorScale(self):
        if self.isCameraSelected():
            return self._getModelGetFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.AIM_LOCATOR_SCALE)(self.currentCamera)
        else:
            print("cameraTools: Error: No camera selected to get aim locator scale.")
            return None

    def enabledAimLocatorScale(self):
        if self.isCameraSelected():
            return self._getModelEnabledFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.AIM_LOCATOR_SCALE)(self.currentCamera)
        else:
            return False

    def applySelectAimLocator(self):
        if self.isCameraSelected():
            self._getModelApplyFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.AIM_LOCATOR_SELECT)(self.currentCamera)
        else:
            print("cameraTools: Error: No camera selected to select aim locator.")

    def applyShake(self, shake):
        if self.isCameraSelected():
            self._getModelApplyFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.SHAKE)(self.currentCamera, shake)
        else:
            print("cameraTools: Error: No camera selected to apply shake.")

    def getShake(self):
        if self.isCameraSelected():
            return self._getModelGetFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.SHAKE)(self.currentCamera)
        else:
            print("cameraTools: Error: No camera selected to get shake.")
            return None

    def applyShakeTranslateXFrequency(self, frequency):
        if self.isCameraSelected():
            self._getModelApplyFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_X_FREQUENCY)(self.currentCamera, frequency)
        else:
            print("cameraTools: Error: No camera selected to apply shake X frequency.")

    def getShakeTranslateXFrequency(self):
        if self.isCameraSelected():
            return self._getModelGetFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_X_FREQUENCY)(self.currentCamera)
        else:
            print("cameraTools: Error: No camera selected to get shake X frequency.")
            return None

    def enabledShakeTranslateXFrequency(self):
        if self.isCameraSelected():
            return self._getModelEnabledFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_X_FREQUENCY)(self.currentCamera)
        else:
            return False

    def applyShakeTranslateXAmplitude(self, amplitude):
        if self.isCameraSelected():
            self._getModelApplyFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_X_AMPLITUDE)(self.currentCamera, amplitude)
        else:
            print("cameraTools: Error: No camera selected to apply shake X amplitude.")

    def getShakeTranslateXAmplitude(self):
        if self.isCameraSelected():
            return self._getModelGetFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_X_AMPLITUDE)(self.currentCamera)
        else:
            print("cameraTools: Error: No camera selected to get shake X amplitude.")
            return None

    def enabledShakeTranslateXAmplitude(self):
        if self.isCameraSelected():
            return self._getModelEnabledFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_X_AMPLITUDE)(self.currentCamera)
        else:
            return False

    def applyShakeTranslateYFrequency(self, frequency):
        if self.isCameraSelected():
            self._getModelApplyFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_Y_FREQUENCY)(self.currentCamera, frequency)
        else:
            print("cameraTools: Error: No camera selected to apply shake Y frequency.")

    def getShakeTranslateYFrequency(self):
        if self.isCameraSelected():
            return self._getModelGetFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_Y_FREQUENCY)(self.currentCamera)
        else:
            print("cameraTools: Error: No camera selected to get shake Y frequency.")
            return None

    def enabledShakeTranslateYFrequency(self):
        if self.isCameraSelected():
            return self._getModelEnabledFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_Y_FREQUENCY)(self.currentCamera)
        else:
            return False

    def applyShakeTranslateYAmplitude(self, amplitude):
        if self.isCameraSelected():
            self._getModelApplyFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_Y_AMPLITUDE)(self.currentCamera, amplitude)
        else:
            print("cameraTools: Error: No camera selected to apply shake Y amplitude.")

    def getShakeTranslateYAmplitude(self):
        if self.isCameraSelected():
            return self._getModelGetFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_Y_AMPLITUDE)(self.currentCamera)
        else:
            print("cameraTools: Error: No camera selected to get shake Y amplitude.")
            return None

    def enabledShakeTranslateYAmplitude(self):
        if self.isCameraSelected():
            return self._getModelEnabledFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_Y_AMPLITUDE)(self.currentCamera)
        else:
            return False

    def applyShakeTranslateZFrequency(self, frequency):
        if self.isCameraSelected():
            self._getModelApplyFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_TRANSLATE_Z_FREQUENCY)(self.currentCamera, frequency)
        else:
            print("cameraTools: Error: No camera selected to apply shake Z frequency.")

    def getShakeTranslateZFrequency(self):
        if self.isCameraSelected():
            return self._getModelGetFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_TRANSLATE_Z_FREQUENCY)(self.currentCamera)
        else:
            print("cameraTools: Error: No camera selected to get shake Z frequency.")
            return None

    def enabledShakeTranslateZFrequency(self):
        if self.isCameraSelected():
            return self._getModelEnabledFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_TRANSLATE_Z_FREQUENCY)(self.currentCamera)
        else:
            return False

    def applyShakeTranslateZAmplitude(self, amplitude):
        if self.isCameraSelected():
            self._getModelApplyFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_TRANSLATE_Z_AMPLITUDE)(self.currentCamera, amplitude)
        else:
            print("cameraTools: Error: No camera selected to apply shake translate Z amplitude.")

    def getShakeTranslateZAmplitude(self):
        if self.isCameraSelected():
            return self._getModelGetFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_TRANSLATE_Z_AMPLITUDE)(self.currentCamera)
        else:
            print("cameraTools: Error: No camera selected to get shake translate Z amplitude.")
            return None

    def enabledShakeTranslateZAmplitude(self):
        if self.isCameraSelected():
            return self._getModelEnabledFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_TRANSLATE_Z_AMPLITUDE)(self.currentCamera)
        else:
            return False

    def applyShakeRotateXFrequency(self, frequency):
        if self.isCameraSelected():
            self._getModelApplyFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_X_FREQUENCY)(self.currentCamera, frequency)
        else:
            print("cameraTools: Error: No camera selected to apply shake X frequency.")

    def getShakeRotateXFrequency(self):
        if self.isCameraSelected():
            return self._getModelGetFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_X_FREQUENCY)(self.currentCamera)
        else:
            print("cameraTools: Error: No camera selected to get shake X frequency.")
            return None
        
    def enabledShakeRotateXFrequency(self):
        if self.isCameraSelected():
            return self._getModelEnabledFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_X_FREQUENCY)(self.currentCamera)
        else:
            return False
        
    def applyShakeRotateXAmplitude(self, amplitude):
        if self.isCameraSelected():
            self._getModelApplyFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_X_AMPLITUDE)(self.currentCamera, amplitude)
        else:
            print("cameraTools: Error: No camera selected to apply shake X amplitude.")

    def getShakeRotateXAmplitude(self):
        if self.isCameraSelected():
            return self._getModelGetFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_X_AMPLITUDE)(self.currentCamera)
        else:
            print("cameraTools: Error: No camera selected to get shake X amplitude.")
            return None
        
    def enabledShakeRotateXAmplitude(self):
        if self.isCameraSelected():
            return self._getModelEnabledFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_X_AMPLITUDE)(self.currentCamera)
        else:
            return False
        
    def applyShakeRotateYFrequency(self, frequency):
        if self.isCameraSelected():
            self._getModelApplyFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_Y_FREQUENCY)(self.currentCamera, frequency)
        else:
            print("cameraTools: Error: No camera selected to apply shake Y frequency.")

    def getShakeRotateYFrequency(self):
        if self.isCameraSelected():
            return self._getModelGetFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_Y_FREQUENCY)(self.currentCamera)
        else:
            print("cameraTools: Error: No camera selected to get shake Y frequency.")
            return None
        
    def enabledShakeRotateYFrequency(self):
        if self.isCameraSelected():
            return self._getModelEnabledFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_Y_FREQUENCY)(self.currentCamera)
        else:
            return False
        
    def applyShakeRotateYAmplitude(self, amplitude):
        if self.isCameraSelected():
            self._getModelApplyFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_Y_AMPLITUDE)(self.currentCamera, amplitude)
        else:
            print("cameraTools: Error: No camera selected to apply shake Y amplitude.")

    def getShakeRotateYAmplitude(self):
        if self.isCameraSelected():
            return self._getModelGetFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_Y_AMPLITUDE)(self.currentCamera)
        else:
            print("cameraTools: Error: No camera selected to get shake Y amplitude.")
            return None
        
    def enabledShakeRotateYAmplitude(self):
        if self.isCameraSelected():
            return self._getModelEnabledFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_Y_AMPLITUDE)(self.currentCamera)
        else:
            return False
        
    def applyShakeRotateZFrequency(self, frequency):
        if self.isCameraSelected():
            self._getModelApplyFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_Z_FREQUENCY)(self.currentCamera, frequency)
        else:
            print("cameraTools: Error: No camera selected to apply shake Z frequency.")

    def getShakeRotateZFrequency(self):
        if self.isCameraSelected():
            return self._getModelGetFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_Z_FREQUENCY)(self.currentCamera)
        else:
            print("cameraTools: Error: No camera selected to get shake Z frequency.")
            return None
        
    def enabledShakeRotateZFrequency(self):
        if self.isCameraSelected():
            return self._getModelEnabledFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_Z_FREQUENCY)(self.currentCamera)
        else:
            return False
        
    def applyShakeRotateZAmplitude(self, amplitude):
        if self.isCameraSelected():
            self._getModelApplyFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_Z_AMPLITUDE)(self.currentCamera, amplitude)
        else:
            print("cameraTools: Error: No camera selected to apply shake Z amplitude.")

    def getShakeRotateZAmplitude(self):
        if self.isCameraSelected():
            return self._getModelGetFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_Z_AMPLITUDE)(self.currentCamera)
        else:
            print("cameraTools: Error: No camera selected to get shake Z amplitude.")
            return None
        
    def enabledShakeRotateZAmplitude(self):
        if self.isCameraSelected():
            return self._getModelEnabledFn(type(self._currentCamera), CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_Z_AMPLITUDE)(self.currentCamera)
        else:
            return False

class CameraCreationWidget(QtWidgets.QWidget):
    def __init__(self, cameraToolsViewModel, parent=None):
        super().__init__(parent)
        self.cameraToolsViewModel = cameraToolsViewModel
        self._setupUi()

    def _setupUi(self):
        mainLayout = QtWidgets.QVBoxLayout(self)

        cameraCreationGroup = QtWidgets.QGroupBox("Camera Creation")
        mainLayout.addWidget(cameraCreationGroup)
        cameraCreationLayout = QtWidgets.QVBoxLayout()
        cameraCreationGroup.setLayout(cameraCreationLayout)

        cameraCreationHorizontalLayout = QtWidgets.QHBoxLayout()
        cameraCreationLayout.addLayout(cameraCreationHorizontalLayout)

        # ------------------------------- Base camera ------------------------------- #
        baseCameraLayout = QtWidgets.QVBoxLayout()
        cameraCreationHorizontalLayout.addLayout(baseCameraLayout)

        baseSubheading = QtWidgets.QLabel("Base Camera")
        baseSubheading.setAlignment(QtCore.Qt.AlignCenter)
        baseSubheading.setStyleSheet("color: #888888; font-weight: bold; font-size: 11px;")
        baseCameraLayout.addWidget(baseSubheading)

        createBaseCameraButton = QtWidgets.QPushButton("Create Base Camera")
        createBaseCameraButton.clicked.connect(self.cameraToolsViewModel.createBaseCamera)
        baseCameraLayout.addWidget(createBaseCameraButton)

        dividerVertical = QtWidgets.QFrame()
        dividerVertical.setFrameShape(QtWidgets.QFrame.VLine)
        dividerVertical.setFrameShadow(QtWidgets.QFrame.Sunken)
        cameraCreationHorizontalLayout.addWidget(dividerVertical)

        # ------------------------------- ALA camera ------------------------------- #
        alaCameraLayout = QtWidgets.QVBoxLayout()
        cameraCreationHorizontalLayout.addLayout(alaCameraLayout)

        alaSubheading = QtWidgets.QLabel("ALA Camera")
        alaSubheading.setAlignment(QtCore.Qt.AlignCenter)
        alaSubheading.setStyleSheet("color: #888888; font-weight: bold; font-size: 11px;")
        alaCameraLayout.addWidget(alaSubheading)

        createALACameraButton = QtWidgets.QPushButton("Create ALA Camera")            
        createALACameraButton.clicked.connect(self.cameraToolsViewModel.createALACamera)
        alaCameraLayout.addWidget(createALACameraButton)

class CameraSettingsWidget(QtWidgets.QWidget):
    def __init__(self, cameraToolsViewModel, parent=None):
        super().__init__(parent)
        self.cameraToolsViewModel = cameraToolsViewModel
        self.settingsWidgets = []
        self._setupUi()

    def _setupUi(self):
        mainLayout = QtWidgets.QVBoxLayout(self)

        self.cameraSettingsGroup = QtWidgets.QGroupBox("Camera Settings")
        mainLayout.addWidget(self.cameraSettingsGroup)
        cameraSettingsLayout = QtWidgets.QVBoxLayout()
        self.cameraSettingsGroup.setLayout(cameraSettingsLayout)

        cameraSettingsLayout.addWidget(CameraSelectionLabel(self.cameraToolsViewModel, self))
        cameraSettingsLayout.addWidget(QtUiUtils.newDivider())

        cameraSettingsLayout.addWidget(CameraGeneralSettings(self.cameraToolsViewModel, self))
        cameraSettingsLayout.addWidget(QtUiUtils.newDivider())

        cameraSettingsLayout.addWidget(CameraDofSettings(self.cameraToolsViewModel, self))
        cameraSettingsLayout.addWidget(QtUiUtils.newDivider())

        cameraSettingsLayout.addWidget(CameraGridSettings(self.cameraToolsViewModel, self))
        cameraSettingsLayout.addWidget(QtUiUtils.newDivider())

        cameraSettingsLayout.addWidget(CameraAimSettings(self.cameraToolsViewModel, self))
        cameraSettingsLayout.addWidget(QtUiUtils.newDivider())

        cameraSettingsLayout.addWidget(CameraShakeSettings(self.cameraToolsViewModel, self))
        cameraSettingsLayout.addWidget(QtUiUtils.newDivider())

        cameraSettingsLayout.addWidget(CameraTemplateSettings(self.cameraToolsViewModel, self))
        cameraSettingsLayout.addWidget(QtUiUtils.newDivider())

class CameraSelectionLabel(QtWidgets.QWidget):
    def __init__(self, cameraToolsViewModel, parent=None):
        super().__init__(parent)
        self.cameraToolsViewModel = cameraToolsViewModel
        self._setupUi()

    def _setupUi(self):
        formLayout = QtWidgets.QFormLayout(self)
        formLayout.setFormAlignment(QtCore.Qt.AlignCenter)

        cameraSelectionLabel = QtWidgets.QLabel("")

        def onCameraSettingChanged():
            if self.cameraToolsViewModel.isCameraSelected():
                cameraSelectionLabel.setText(self.cameraToolsViewModel.cameraTransform())
            else:
                cameraSelectionLabel.setText("No camera selected")
        self.cameraToolsViewModel.cameraSettingChanged.connect(onCameraSettingChanged)

        formLayout.addRow("Selected Camera:", cameraSelectionLabel)

class CameraGeneralSettings(QtWidgets.QWidget):
    def __init__(self, cameraToolsViewModel, parent=None):
        super().__init__(parent)
        self.cameraToolsViewModel = cameraToolsViewModel
        self._setupUi()

    def _setupUi(self):
        mainLayout = QtWidgets.QVBoxLayout(self)
        formLayout = QtWidgets.QFormLayout()
        mainLayout.addLayout(formLayout)
        formLayout.setFormAlignment(QtCore.Qt.AlignCenter)

        # ------------------------------- Focal Length ------------------------------- #
        focalLengthSlider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        focalLengthSlider.setFixedWidth(150)
        focalLengthSlider.setMinimum(0)
        focalLengthSlider.setTickInterval(1)

        focalLengthLineEdit = QtWidgets.QLineEdit()
        focalLengthLineEdit.setFixedWidth(35)
        focalLengthLineEdit.setEnabled(False)
        focalLengthLineEdit.setAlignment(QtCore.Qt.AlignCenter)

        def onFocalLengthSliderValueChanged(value):
            presetFocalLengths = self.cameraToolsViewModel.cameraFocalLengthPresets()
            focalLengthLineEdit.setText(str(presetFocalLengths[value]))
            self.cameraToolsViewModel.applyCameraSetting(CameraToolsViewModel.CameraSetting.FOCAL_LENGTH, presetFocalLengths[value])
        focalLengthSlider.valueChanged.connect(onFocalLengthSliderValueChanged)
        def initialiseFocalLengthSlider():
            focalLengthSlider.blockSignals(True)
            if not self.cameraToolsViewModel.isCameraSelectedAndSettingSupported(CameraToolsViewModel.CameraSetting.FOCAL_LENGTH):
                focalLengthSlider.setEnabled(False)
                focalLengthSlider.setValue(0)
                focalLengthLineEdit.setText("0")
            else:
                presetFocalLengths = self.cameraToolsViewModel.cameraFocalLengthPresets()
                focalLength = self.cameraToolsViewModel.getCameraSetting(CameraToolsViewModel.CameraSetting.FOCAL_LENGTH)
                try:
                    focalLengthIdx = presetFocalLengths.index(focalLength)
                except ValueError:
                    focalLengthSlider.setEnabled(False)
                    focalLengthSlider.setValue(0)
                    focalLengthLineEdit.setText("0")
                else:
                    focalLengthSlider.setEnabled(True)
                    focalLengthSlider.setValue(focalLengthIdx)
                    focalLengthSlider.setMaximum(len(presetFocalLengths) - 1)
                    focalLengthLineEdit.setText(str(focalLength))
            focalLengthSlider.blockSignals(False)
        self.cameraToolsViewModel.cameraSettingChanged.connect(initialiseFocalLengthSlider)

        focalLengthLayout = QtWidgets.QHBoxLayout()
        focalLengthLayout.addWidget(focalLengthSlider)
        focalLengthLayout.addWidget(focalLengthLineEdit)
        formLayout.addRow("Focal Length (mm)", focalLengthLayout)

        # ----------------------------------- FStop ---------------------------------- #
        fStopSlider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        fStopSlider.setFixedWidth(150)
        fStopSlider.setMinimum(0)
        fStopSlider.setTickInterval(1)

        fStopLineEdit = QtWidgets.QLineEdit()
        fStopLineEdit.setFixedWidth(35)
        fStopLineEdit.setEnabled(False)
        fStopLineEdit.setAlignment(QtCore.Qt.AlignCenter)

        def onFStopSliderValueChanged(value):
            presetFStops = self.cameraToolsViewModel.cameraFStopPresets()
            fStopLineEdit.setText(str(presetFStops[value]))
            self.cameraToolsViewModel.applyCameraSetting(CameraToolsViewModel.CameraSetting.F_STOP, presetFStops[value])
        fStopSlider.valueChanged.connect(onFStopSliderValueChanged)
        def initialiseFStopSlider():
            fStopSlider.blockSignals(True)
            if not self.cameraToolsViewModel.isCameraSelectedAndSettingSupported(CameraToolsViewModel.CameraSetting.F_STOP):
                fStopSlider.setEnabled(False)
                fStopSlider.setValue(0)
                fStopLineEdit.setText("0")
            else:
                presetFStops = self.cameraToolsViewModel.cameraFStopPresets()
                fStop = self.cameraToolsViewModel.getCameraSetting(CameraToolsViewModel.CameraSetting.F_STOP)
                try:
                    fStopIdx = presetFStops.index(fStop)
                except ValueError:
                    fStopSlider.setEnabled(False)
                    fStopSlider.setValue(0)
                    fStopLineEdit.setText("0")
                else:
                    fStopSlider.setEnabled(True)
                    fStopSlider.setValue(fStopIdx)
                    fStopSlider.setMaximum(len(presetFStops) - 1)
                    fStopLineEdit.setText(str(fStop))
            fStopSlider.blockSignals(False)
        self.cameraToolsViewModel.cameraSettingChanged.connect(initialiseFStopSlider)

        fStopLayout = QtWidgets.QHBoxLayout()
        fStopLayout.addWidget(fStopSlider)
        fStopLayout.addWidget(fStopLineEdit)
        formLayout.addRow("FStop", fStopLayout)

        # --------------------------- Camera Locator Scale --------------------------- #
        cameraLocatorScaleSlider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        cameraLocatorScaleSlider.setFixedWidth(150)
        cameraLocatorScaleSlider.setMinimum(1)
        cameraLocatorScaleSlider.setMaximum(50)
        cameraLocatorScaleSlider.setTickInterval(1)

        cameraLocatorScaleLineEdit = QtWidgets.QLineEdit()
        cameraLocatorScaleLineEdit.setFixedWidth(35)
        cameraLocatorScaleLineEdit.setEnabled(False)
        cameraLocatorScaleLineEdit.setAlignment(QtCore.Qt.AlignCenter)

        def onCameraLocatorScaleSliderValueChanged(value):
            cameraLocatorScaleLineEdit.setText(str(value))
            self.cameraToolsViewModel.applyCameraSetting(CameraToolsViewModel.CameraSetting.CAMERA_LOCATOR_SCALE, value)
        cameraLocatorScaleSlider.valueChanged.connect(onCameraLocatorScaleSliderValueChanged)
        def initialiseCameraLocatorScaleSlider():
            cameraLocatorScaleSlider.blockSignals(True)
            if not self.cameraToolsViewModel.isCameraSelectedAndSettingSupported(CameraToolsViewModel.CameraSetting.CAMERA_LOCATOR_SCALE):
                cameraLocatorScaleSlider.setEnabled(False)
                cameraLocatorScaleSlider.setValue(0)
                cameraLocatorScaleLineEdit.setText("0")
            else:
                cameraLocatorScaleSlider.setEnabled(True)
                cameraLocatorScale = self.cameraToolsViewModel.getCameraSetting(CameraToolsViewModel.CameraSetting.CAMERA_LOCATOR_SCALE)
                cameraLocatorScaleSlider.setValue(cameraLocatorScale)
                cameraLocatorScaleLineEdit.setText(str(cameraLocatorScale))
            cameraLocatorScaleSlider.blockSignals(False)
        self.cameraToolsViewModel.cameraSettingChanged.connect(initialiseCameraLocatorScaleSlider)

        cameraLocatorScaleLayout = QtWidgets.QHBoxLayout()
        cameraLocatorScaleLayout.addWidget(cameraLocatorScaleSlider)
        cameraLocatorScaleLayout.addWidget(cameraLocatorScaleLineEdit)
        formLayout.addRow("Camera Locator Scale", cameraLocatorScaleLayout)

class CameraDofSettings(QtWidgets.QWidget):
    def __init__(self, cameraToolsViewModel, parent=None):
        super().__init__(parent)
        self.cameraToolsViewModel = cameraToolsViewModel
        self._setupUi()

    def _setupUi(self):
        mainLayout = QtWidgets.QVBoxLayout(self)
        formLayout = QtWidgets.QFormLayout()
        mainLayout.addLayout(formLayout)
        formLayout.setFormAlignment(QtCore.Qt.AlignCenter)

        # -------------------------------- DOF Enable -------------------------------- #
        dofEnableCheckBox = QtWidgets.QCheckBox("Enabled")

        def onDofEnableCheckboxChanged(checked):
            self.cameraToolsViewModel.applyCameraSetting(CameraToolsViewModel.CameraSetting.DOF, bool(checked))
        dofEnableCheckBox.stateChanged.connect(onDofEnableCheckboxChanged)
        def initialiseDofCheckbox():
            dofEnableCheckBox.blockSignals(True)
            if not self.cameraToolsViewModel.isCameraSelectedAndSettingSupported(CameraToolsViewModel.CameraSetting.DOF):
                dofEnableCheckBox.setEnabled(False)
                dofEnableCheckBox.setChecked(False)
            else:
                dofEnableCheckBox.setEnabled(True)
                dof = self.cameraToolsViewModel.getCameraSetting(CameraToolsViewModel.CameraSetting.DOF)
                dofEnableCheckBox.setChecked(dof)
            dofEnableCheckBox.blockSignals(False)
        self.cameraToolsViewModel.cameraSettingChanged.connect(initialiseDofCheckbox)

        formLayout.addRow("Depth of Field", dofEnableCheckBox)

        # -------------------------------- Focus Plane ------------------------------- #
        focusPlaneCheckbox = QtWidgets.QCheckBox("Enabled")
        
        def onFocusPlaneCheckboxChanged(checked):
            self.cameraToolsViewModel.applyCameraSetting(CameraToolsViewModel.CameraSetting.FOCUS_PLANE, bool(checked))
        focusPlaneCheckbox.stateChanged.connect(onFocusPlaneCheckboxChanged)
        def initialiseFocusPlaneCheckbox():
            focusPlaneCheckbox.blockSignals(True)
            if not self.cameraToolsViewModel.isCameraSelectedAndSettingSupported(CameraToolsViewModel.CameraSetting.FOCUS_PLANE):
                focusPlaneCheckbox.setEnabled(False)
                focusPlaneCheckbox.setChecked(False)
            else:
                focusPlaneCheckbox.setEnabled(True)
                focusPlane = self.cameraToolsViewModel.getCameraSetting(CameraToolsViewModel.CameraSetting.FOCUS_PLANE)
                focusPlaneCheckbox.setChecked(focusPlane)
            focusPlaneCheckbox.blockSignals(False)
        self.cameraToolsViewModel.cameraSettingChanged.connect(initialiseFocusPlaneCheckbox)

        formLayout.addRow("Focus Plane", focusPlaneCheckbox)

        # ------------------------------- Focus Plane Select ------------------------------- #
        focusPlaneSelectButton = QtWidgets.QPushButton("Select")

        def onFocusPlaneSelectButtonClicked():
            self.cameraToolsViewModel.applyCameraSetting(CameraToolsViewModel.CameraSetting.FOCUS_PLANE_SELECT)
        focusPlaneSelectButton.clicked.connect(onFocusPlaneSelectButtonClicked)
        def initialiseSelectFocusPlaneButton():
            focusPlaneSelectButton.blockSignals(True)
            if not self.cameraToolsViewModel.isCameraSelectedAndSettingSupported(CameraToolsViewModel.CameraSetting.FOCUS_PLANE_SELECT):
                focusPlaneSelectButton.setEnabled(False)
            else:
                focusPlaneSelectButton.setEnabled(True)
            focusPlaneSelectButton.blockSignals(False)
        self.cameraToolsViewModel.cameraSettingChanged.connect(initialiseSelectFocusPlaneButton)

        formLayout.addRow("Select Focus Plane", focusPlaneSelectButton)

class CameraGridSettings(QtWidgets.QWidget):
    def __init__(self, cameraToolsViewModel, parent=None):
        super().__init__(parent)
        self.cameraToolsViewModel = cameraToolsViewModel
        self._setupUi()

    def _setupUi(self):
        mainLayout = QtWidgets.QVBoxLayout(self)
        formLayout = QtWidgets.QFormLayout()
        mainLayout.addLayout(formLayout)
        formLayout.setFormAlignment(QtCore.Qt.AlignCenter)

        gridNoneRadio = QtWidgets.QRadioButton("None")
        def onGridNoneRadioClicked():
            self.cameraToolsViewModel.applyCameraSetting(CameraToolsViewModel.CameraSetting.GRID, CameraToolsViewModel.CameraSettingGridMode.NONE)
        gridNoneRadio.clicked.connect(onGridNoneRadioClicked)
        def initialiseGridNoneRadio():
            gridNoneRadio.blockSignals(True)
            if not self.cameraToolsViewModel.isCameraSelectedAndSettingSupported(CameraToolsViewModel.CameraSetting.GRID):
                gridNoneRadio.setEnabled(False)
                gridNoneRadio.setAutoExclusive(False)
                gridNoneRadio.setChecked(False)
                gridNoneRadio.setAutoExclusive(True)
            else:
                gridNoneRadio.setEnabled(True)
                grid = self.cameraToolsViewModel.getCameraSetting(CameraToolsViewModel.CameraSetting.GRID)
                if grid == CameraToolsViewModel.CameraSettingGridMode.NONE:
                    gridNoneRadio.setChecked(True)
            gridNoneRadio.blockSignals(False)
        self.cameraToolsViewModel.cameraSettingChanged.connect(initialiseGridNoneRadio)
        formLayout.addRow("Grid", gridNoneRadio)

        gridTwoRadio = QtWidgets.QRadioButton("2x2")
        def onGridOnRadioClicked():
            self.cameraToolsViewModel.applyCameraSetting(CameraToolsViewModel.CameraSetting.GRID, CameraToolsViewModel.CameraSettingGridMode.TWO)
        gridTwoRadio.clicked.connect(onGridOnRadioClicked)
        def initialiseGridTwoRadio():
            gridTwoRadio.blockSignals(True)
            if not self.cameraToolsViewModel.isCameraSelectedAndSettingSupported(CameraToolsViewModel.CameraSetting.GRID):
                gridTwoRadio.setEnabled(False)
                gridTwoRadio.setAutoExclusive(False)
                gridTwoRadio.setChecked(False)
                gridTwoRadio.setAutoExclusive(True)
            else:
                gridTwoRadio.setEnabled(True)
                grid = self.cameraToolsViewModel.getCameraSetting(CameraToolsViewModel.CameraSetting.GRID)
                if grid == CameraToolsViewModel.CameraSettingGridMode.TWO:
                    gridTwoRadio.setChecked(True)
            gridTwoRadio.blockSignals(False)
        self.cameraToolsViewModel.cameraSettingChanged.connect(initialiseGridTwoRadio)
        formLayout.addRow("", gridTwoRadio)

        gridThreeRadio = QtWidgets.QRadioButton("3x3")
        def onGridThreeRadioClicked():
            self.cameraToolsViewModel.applyCameraSetting(CameraToolsViewModel.CameraSetting.GRID, CameraToolsViewModel.CameraSettingGridMode.THREE)
        gridThreeRadio.clicked.connect(onGridThreeRadioClicked)
        def initialiseGridThreeRadio():
            gridThreeRadio.blockSignals(True)
            if not self.cameraToolsViewModel.isCameraSelectedAndSettingSupported(CameraToolsViewModel.CameraSetting.GRID):
                gridThreeRadio.setEnabled(False)
                gridThreeRadio.setAutoExclusive(False)
                gridThreeRadio.setChecked(False)
                gridThreeRadio.setAutoExclusive(True)
            else:
                gridThreeRadio.setEnabled(True)
                grid = self.cameraToolsViewModel.getCameraSetting(CameraToolsViewModel.CameraSetting.GRID)
                if grid == CameraToolsViewModel.CameraSettingGridMode.THREE:
                    gridThreeRadio.setChecked(True)
            gridThreeRadio.blockSignals(False)
        self.cameraToolsViewModel.cameraSettingChanged.connect(initialiseGridThreeRadio)
        formLayout.addRow("", gridThreeRadio)

class CameraAimSettings(QtWidgets.QWidget):
    def __init__(self, cameraToolsViewModel, parent=None):
        super().__init__(parent)
        self.cameraToolsViewModel = cameraToolsViewModel
        self._setupUi()

    def _setupUi(self):
        mainLayout = QtWidgets.QVBoxLayout(self)
        formLayout = QtWidgets.QFormLayout()
        mainLayout.addLayout(formLayout)
        formLayout.setFormAlignment(QtCore.Qt.AlignCenter)

        # ---------------------------------- Aim ----------------------------------- #
        aimEnableCheckBox = QtWidgets.QCheckBox("Enabled")

        def onAimEnableCheckboxChanged(checked):
            self.cameraToolsViewModel.applyCameraSetting(CameraToolsViewModel.CameraSetting.AIM, bool(checked))
        aimEnableCheckBox.stateChanged.connect(onAimEnableCheckboxChanged)
        def initialiseAimCheckbox():
            aimEnableCheckBox.blockSignals(True)
            if not self.cameraToolsViewModel.isCameraSelectedAndSettingSupported(CameraToolsViewModel.CameraSetting.AIM):
                aimEnableCheckBox.setEnabled(False)
                aimEnableCheckBox.setChecked(False)
            else:
                aimEnableCheckBox.setEnabled(True)
                aim = self.cameraToolsViewModel.getCameraSetting(CameraToolsViewModel.CameraSetting.AIM)
                aimEnableCheckBox.setChecked(aim)
            aimEnableCheckBox.blockSignals(False)
        self.cameraToolsViewModel.cameraSettingChanged.connect(initialiseAimCheckbox)

        formLayout.addRow("Aim", aimEnableCheckBox)

        # ------------------------------- Aim Locator Scale ------------------------------- #
        aimLocatorScaleSlider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        aimLocatorScaleSlider.setFixedWidth(150)
        aimLocatorScaleSlider.setMinimum(1)
        aimLocatorScaleSlider.setMaximum(50)
        aimLocatorScaleSlider.setTickInterval(1)

        aimLocatorScaleLineEdit = QtWidgets.QLineEdit()
        aimLocatorScaleLineEdit.setFixedWidth(35)
        aimLocatorScaleLineEdit.setEnabled(False)
        aimLocatorScaleLineEdit.setAlignment(QtCore.Qt.AlignCenter)

        def onAimLocatorScaleSliderValueChanged(value):
            aimLocatorScaleLineEdit.setText(str(value))
            self.cameraToolsViewModel.applyCameraSetting(CameraToolsViewModel.CameraSetting.AIM_LOCATOR_SCALE, value)
        aimLocatorScaleSlider.valueChanged.connect(onAimLocatorScaleSliderValueChanged)
        def initialiseAimLocatorScaleSlider():
            aimLocatorScaleSlider.blockSignals(True)
            if not self.cameraToolsViewModel.isCameraSelectedAndSettingSupported(CameraToolsViewModel.CameraSetting.AIM_LOCATOR_SCALE):
                aimLocatorScaleSlider.setEnabled(False)
                aimLocatorScaleSlider.setValue(0)
                aimLocatorScaleLineEdit.setText("0")
            elif not self.cameraToolsViewModel.enabledCameraSetting(CameraToolsViewModel.CameraSetting.AIM_LOCATOR_SCALE):
                aimLocatorScale = self.cameraToolsViewModel.getCameraSetting(CameraToolsViewModel.CameraSetting.AIM_LOCATOR_SCALE)
                aimLocatorScaleSlider.setEnabled(False)
                aimLocatorScaleSlider.setValue(aimLocatorScale)
                aimLocatorScaleLineEdit.setText(str(aimLocatorScale))
            else:
                aimLocatorScaleSlider.setEnabled(True)
                aimLocatorScale = self.cameraToolsViewModel.getCameraSetting(CameraToolsViewModel.CameraSetting.AIM_LOCATOR_SCALE)
                aimLocatorScaleSlider.setValue(aimLocatorScale)
                aimLocatorScaleLineEdit.setText(str(aimLocatorScale))
            aimLocatorScaleSlider.blockSignals(False)
        self.cameraToolsViewModel.cameraSettingChanged.connect(initialiseAimLocatorScaleSlider)

        aimLocatorScaleLayout = QtWidgets.QHBoxLayout()
        aimLocatorScaleLayout.addWidget(aimLocatorScaleSlider)
        aimLocatorScaleLayout.addWidget(aimLocatorScaleLineEdit)
        formLayout.addRow("Aim Locator Scale", aimLocatorScaleLayout)

        # ------------------------------- Aim Locator Select ------------------------------- #
        aimLocatorSelectButton = QtWidgets.QPushButton("Select")
        def onAimLocatorSelectButtonClicked():
            self.cameraToolsViewModel.applyCameraSetting(CameraToolsViewModel.CameraSetting.AIM_LOCATOR_SELECT)
        aimLocatorSelectButton.clicked.connect(onAimLocatorSelectButtonClicked)
        def initialiseSelectAimLocatorButton():
            aimLocatorSelectButton.blockSignals(True)
            if not self.cameraToolsViewModel.isCameraSelectedAndSettingSupported(CameraToolsViewModel.CameraSetting.AIM_LOCATOR_SELECT):
                aimLocatorSelectButton.setEnabled(False)
            else:
                aimLocatorSelectButton.setEnabled(True)
            aimLocatorSelectButton.blockSignals(False)
        self.cameraToolsViewModel.cameraSettingChanged.connect(initialiseSelectAimLocatorButton)
        formLayout.addRow("Select Aim Locator", aimLocatorSelectButton)

class CameraShakeSettings(QtWidgets.QWidget):
    def __init__(self, cameraToolsViewModel, parent=None):
        super().__init__(parent)
        self.cameraToolsViewModel = cameraToolsViewModel
        self._setupUi()

    def _setupUi(self):
        mainLayout = QtWidgets.QVBoxLayout(self)

        shakeEnableFormLayout = QtWidgets.QFormLayout()
        mainLayout.addLayout(shakeEnableFormLayout)
        shakeEnableFormLayout.setFormAlignment(QtCore.Qt.AlignCenter)

        doubleFormLayout = QtWidgets.QHBoxLayout()
        mainLayout.addLayout(doubleFormLayout)

        formLayout = QtWidgets.QFormLayout()
        doubleFormLayout.addLayout(formLayout)
        formLayout.setFormAlignment(QtCore.Qt.AlignCenter)

        formLayout2 = QtWidgets.QFormLayout()
        doubleFormLayout.addLayout(formLayout2)
        formLayout2.setFormAlignment(QtCore.Qt.AlignCenter)

        # ------------------------------- Shake Enable ------------------------------- #
        shakeEnableCheckBox = QtWidgets.QCheckBox("Enabled")
        def onShakeEnableCheckboxChanged(checked):
            self.cameraToolsViewModel.applyCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE, bool(checked))
        shakeEnableCheckBox.stateChanged.connect(onShakeEnableCheckboxChanged)
        def initialiseShakeCheckbox():
            shakeEnableCheckBox.blockSignals(True)
            if not self.cameraToolsViewModel.isCameraSelectedAndSettingSupported(CameraToolsViewModel.CameraSetting.SHAKE):
                shakeEnableCheckBox.setEnabled(False)
                shakeEnableCheckBox.setChecked(False)
            else:
                shakeEnableCheckBox.setEnabled(True)
                shake = self.cameraToolsViewModel.getCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE)
                shakeEnableCheckBox.setChecked(shake)
            shakeEnableCheckBox.blockSignals(False)
        self.cameraToolsViewModel.cameraSettingChanged.connect(initialiseShakeCheckbox)
        shakeEnableFormLayout.addRow("Shake", shakeEnableCheckBox)

        # ----------------------------- Shake Translate X Frequency ---------------------------- #
        shakeFrequencySlider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        shakeFrequencySlider.setFixedWidth(150)
        shakeFrequencySlider.setMinimum(0)
        shakeFrequencySlider.setTickInterval(1)

        shakeFrequencyLineEdit = QtWidgets.QLineEdit()
        shakeFrequencyLineEdit.setFixedWidth(35)
        shakeFrequencyLineEdit.setEnabled(False)
        shakeFrequencyLineEdit.setAlignment(QtCore.Qt.AlignCenter)

        def onShakeFrequencySliderValueChanged(value):
            shakeFrequencyLineEdit.setText(str(value))
            self.cameraToolsViewModel.applyCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_X_FREQUENCY, value)
        shakeFrequencySlider.valueChanged.connect(onShakeFrequencySliderValueChanged)
        def initialiseShakeFrequencySlider():
            shakeFrequencySlider.blockSignals(True)
            if not self.cameraToolsViewModel.isCameraSelectedAndSettingSupported(CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_X_FREQUENCY):
                shakeFrequencySlider.setEnabled(False)
                shakeFrequencySlider.setValue(0)
                shakeFrequencyLineEdit.setText("0")
            elif not self.cameraToolsViewModel.enabledCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_X_FREQUENCY):
                shakeFrequencySlider.setEnabled(False)
                shakeFrequency = self.cameraToolsViewModel.getCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_X_FREQUENCY)
                shakeFrequencySlider.setValue(shakeFrequency)
                shakeFrequencyLineEdit.setText(str(shakeFrequency))
            else:
                shakeFrequencySlider.setEnabled(True)
                shakeFrequency = self.cameraToolsViewModel.getCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_X_FREQUENCY)
                shakeFrequencySlider.setValue(shakeFrequency)
                shakeFrequencyLineEdit.setText(str(shakeFrequency))
            shakeFrequencySlider.blockSignals(False)
        self.cameraToolsViewModel.cameraSettingChanged.connect(initialiseShakeFrequencySlider)

        shakeFrequencyLayout = QtWidgets.QHBoxLayout()
        shakeFrequencyLayout.addWidget(shakeFrequencySlider)
        shakeFrequencyLayout.addWidget(shakeFrequencyLineEdit)
        formLayout.addRow("Shake Translate X Frequency", shakeFrequencyLayout)

        # ------------------------------ Shake Translate X Amplitude ----------------------------- #
        shakeAmplitudeSlider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        shakeAmplitudeSlider.setFixedWidth(150)
        shakeAmplitudeSlider.setMinimum(0)
        shakeAmplitudeSlider.setTickInterval(1)

        shakeAmplitudeLineEdit = QtWidgets.QLineEdit()
        shakeAmplitudeLineEdit.setFixedWidth(35)
        shakeAmplitudeLineEdit.setEnabled(False)
        shakeAmplitudeLineEdit.setAlignment(QtCore.Qt.AlignCenter)
        def onShakeAmplitudeSliderValueChanged(value):
            shakeAmplitudeLineEdit.setText(str(value))
            self.cameraToolsViewModel.applyCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_X_AMPLITUDE, value)
        shakeAmplitudeSlider.valueChanged.connect(onShakeAmplitudeSliderValueChanged)
        def initialiseShakeAmplitudeSlider():
            shakeAmplitudeSlider.blockSignals(True)
            if not self.cameraToolsViewModel.isCameraSelectedAndSettingSupported(CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_X_AMPLITUDE):
                shakeAmplitudeSlider.setEnabled(False)
                shakeAmplitudeSlider.setValue(0)
                shakeAmplitudeLineEdit.setText("0")
            elif not self.cameraToolsViewModel.enabledCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_X_AMPLITUDE):
                shakeAmplitudeSlider.setEnabled(False)
                shakeAmplitude = self.cameraToolsViewModel.getCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_X_AMPLITUDE)
                shakeAmplitudeSlider.setValue(shakeAmplitude)
                shakeAmplitudeLineEdit.setText(str(shakeAmplitude))
            else:
                shakeAmplitudeSlider.setEnabled(True)
                shakeAmplitude = self.cameraToolsViewModel.getCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_X_AMPLITUDE)
                shakeAmplitudeSlider.setValue(shakeAmplitude)
                shakeAmplitudeLineEdit.setText(str(shakeAmplitude))
            shakeAmplitudeSlider.blockSignals(False)
        self.cameraToolsViewModel.cameraSettingChanged.connect(initialiseShakeAmplitudeSlider)

        shakeAmplitudeLayout = QtWidgets.QHBoxLayout()
        shakeAmplitudeLayout.addWidget(shakeAmplitudeSlider)
        shakeAmplitudeLayout.addWidget(shakeAmplitudeLineEdit)
        formLayout.addRow("Shake Translate X Amplitude", shakeAmplitudeLayout)

        # ----------------------------- Shake Translate Y Frequency ---------------------------- #
        shakeTranslateYFrequencySlider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        shakeTranslateYFrequencySlider.setFixedWidth(150)
        shakeTranslateYFrequencySlider.setMinimum(0)
        shakeTranslateYFrequencySlider.setTickInterval(1)

        shakeTranslateYFrequencyLineEdit = QtWidgets.QLineEdit()
        shakeTranslateYFrequencyLineEdit.setFixedWidth(35)
        shakeTranslateYFrequencyLineEdit.setEnabled(False)
        shakeTranslateYFrequencyLineEdit.setAlignment(QtCore.Qt.AlignCenter)
        def onShakeTranslateYFrequencySliderValueChanged(value):
            shakeTranslateYFrequencyLineEdit.setText(str(value))
            self.cameraToolsViewModel.applyCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_Y_FREQUENCY, value)
        shakeTranslateYFrequencySlider.valueChanged.connect(onShakeTranslateYFrequencySliderValueChanged)
        def initialiseShakeTranslateYFrequencySlider():
            shakeTranslateYFrequencySlider.blockSignals(True)
            if not self.cameraToolsViewModel.isCameraSelectedAndSettingSupported(CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_Y_FREQUENCY):
                shakeTranslateYFrequencySlider.setEnabled(False)
                shakeTranslateYFrequencySlider.setValue(0)
                shakeTranslateYFrequencyLineEdit.setText("0")
            elif not self.cameraToolsViewModel.enabledCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_Y_FREQUENCY):
                shakeTranslateYFrequencySlider.setEnabled(False)
                shakeTranslateYFrequency = self.cameraToolsViewModel.getCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_Y_FREQUENCY)
                shakeTranslateYFrequencySlider.setValue(shakeTranslateYFrequency)
                shakeTranslateYFrequencyLineEdit.setText(str(shakeTranslateYFrequency))
            else:
                shakeTranslateYFrequencySlider.setEnabled(True)
                shakeTranslateYFrequency = self.cameraToolsViewModel.getCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_Y_FREQUENCY)
                shakeTranslateYFrequencySlider.setValue(shakeTranslateYFrequency)
                shakeTranslateYFrequencyLineEdit.setText(str(shakeTranslateYFrequency))
            shakeTranslateYFrequencySlider.blockSignals(False)
        self.cameraToolsViewModel.cameraSettingChanged.connect(initialiseShakeTranslateYFrequencySlider)

        shakeTranslateYFrequencyLayout = QtWidgets.QHBoxLayout()
        shakeTranslateYFrequencyLayout.addWidget(shakeTranslateYFrequencySlider)
        shakeTranslateYFrequencyLayout.addWidget(shakeTranslateYFrequencyLineEdit)
        formLayout.addRow("Shake Translate Y Frequency", shakeTranslateYFrequencyLayout)

        # ------------------------------ Shake Translate Y Amplitude ----------------------------- #
        shakeTranslateYAmplitudeSlider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        shakeTranslateYAmplitudeSlider.setFixedWidth(150)
        shakeTranslateYAmplitudeSlider.setMinimum(0)
        shakeTranslateYAmplitudeSlider.setTickInterval(1)

        shakeTranslateYAmplitudeLineEdit = QtWidgets.QLineEdit()
        shakeTranslateYAmplitudeLineEdit.setFixedWidth(35)
        shakeTranslateYAmplitudeLineEdit.setEnabled(False)
        shakeTranslateYAmplitudeLineEdit.setAlignment(QtCore.Qt.AlignCenter)
        def onShakeTranslateYAmplitudeSliderValueChanged(value):
            shakeTranslateYAmplitudeLineEdit.setText(str(value))
            self.cameraToolsViewModel.applyCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_Y_AMPLITUDE, value)
        shakeTranslateYAmplitudeSlider.valueChanged.connect(onShakeTranslateYAmplitudeSliderValueChanged)
        def initialiseShakeTranslateYAmplitudeSlider():
            shakeTranslateYAmplitudeSlider.blockSignals(True)
            if not self.cameraToolsViewModel.isCameraSelectedAndSettingSupported(CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_Y_AMPLITUDE):
                shakeTranslateYAmplitudeSlider.setEnabled(False)
                shakeTranslateYAmplitudeSlider.setValue(0)
                shakeTranslateYAmplitudeLineEdit.setText("0")
            elif not self.cameraToolsViewModel.enabledCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_Y_AMPLITUDE):
                shakeTranslateYAmplitudeSlider.setEnabled(False)
                shakeTranslateYAmplitude = self.cameraToolsViewModel.getCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_Y_AMPLITUDE)
                shakeTranslateYAmplitudeSlider.setValue(shakeTranslateYAmplitude)
                shakeTranslateYAmplitudeLineEdit.setText(str(shakeTranslateYAmplitude))
            else:
                shakeTranslateYAmplitudeSlider.setEnabled(True)
                shakeTranslateYAmplitude = self.cameraToolsViewModel.getCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_Y_AMPLITUDE)
                shakeTranslateYAmplitudeSlider.setValue(shakeTranslateYAmplitude)
                shakeTranslateYAmplitudeLineEdit.setText(str(shakeTranslateYAmplitude))
            shakeTranslateYAmplitudeSlider.blockSignals(False)
        self.cameraToolsViewModel.cameraSettingChanged.connect(initialiseShakeTranslateYAmplitudeSlider)

        shakeTranslateYAmplitudeLayout = QtWidgets.QHBoxLayout()
        shakeTranslateYAmplitudeLayout.addWidget(shakeTranslateYAmplitudeSlider)
        shakeTranslateYAmplitudeLayout.addWidget(shakeTranslateYAmplitudeLineEdit)
        formLayout.addRow("Shake Translate Y Amplitude", shakeTranslateYAmplitudeLayout)

        # ------------------------------ Shake Translate Z Frequency ----------------------------- #
        shakeTranslateZFrequencySlider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        shakeTranslateZFrequencySlider.setFixedWidth(150)
        shakeTranslateZFrequencySlider.setMinimum(0)
        shakeTranslateZFrequencySlider.setTickInterval(1)

        shakeTranslateZFrequencyLineEdit = QtWidgets.QLineEdit()
        shakeTranslateZFrequencyLineEdit.setFixedWidth(35)
        shakeTranslateZFrequencyLineEdit.setEnabled(False)
        shakeTranslateZFrequencyLineEdit.setAlignment(QtCore.Qt.AlignCenter)

        def onShakeTranslateZFrequencySliderValueChanged(value):
            shakeTranslateZFrequencyLineEdit.setText(str(value))
            self.cameraToolsViewModel.applyCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_TRANSLATE_Z_FREQUENCY, value)
        shakeTranslateZFrequencySlider.valueChanged.connect(onShakeTranslateZFrequencySliderValueChanged)

        def initialiseShakeTranslateZFrequencySlider():
            shakeTranslateZFrequencySlider.blockSignals(True)
            if not self.cameraToolsViewModel.isCameraSelectedAndSettingSupported(CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_TRANSLATE_Z_FREQUENCY):
                shakeTranslateZFrequencySlider.setEnabled(False)
                shakeTranslateZFrequencySlider.setValue(0)
                shakeTranslateZFrequencyLineEdit.setText("0")
            elif not self.cameraToolsViewModel.enabledCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_TRANSLATE_Z_FREQUENCY):
                shakeTranslateZFrequencySlider.setEnabled(False)
                shakeTranslateZFrequency = self.cameraToolsViewModel.getCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_TRANSLATE_Z_FREQUENCY)
                shakeTranslateZFrequencySlider.setValue(shakeTranslateZFrequency)
                shakeTranslateZFrequencyLineEdit.setText(str(shakeTranslateZFrequency))
            else:
                shakeTranslateZFrequencySlider.setEnabled(True)
                shakeTranslateZFrequency = self.cameraToolsViewModel.getCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_TRANSLATE_Z_FREQUENCY)
                shakeTranslateZFrequencySlider.setValue(shakeTranslateZFrequency)
                shakeTranslateZFrequencyLineEdit.setText(str(shakeTranslateZFrequency))
            shakeTranslateZFrequencySlider.blockSignals(False)
        self.cameraToolsViewModel.cameraSettingChanged.connect(initialiseShakeTranslateZFrequencySlider)

        shakeTranslateZFrequencyLayout = QtWidgets.QHBoxLayout()
        shakeTranslateZFrequencyLayout.addWidget(shakeTranslateZFrequencySlider)
        shakeTranslateZFrequencyLayout.addWidget(shakeTranslateZFrequencyLineEdit)
        formLayout.addRow("Shake Translate Z Frequency", shakeTranslateZFrequencyLayout)

        # ------------------------------ Shake Translate Z Amplitude ----------------------------- #
        shakeTranslateZAmplitudeSlider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        shakeTranslateZAmplitudeSlider.setFixedWidth(150)
        shakeTranslateZAmplitudeSlider.setMinimum(0)
        shakeTranslateZAmplitudeSlider.setTickInterval(1)

        shakeTranslateZAmplitudeLineEdit = QtWidgets.QLineEdit()
        shakeTranslateZAmplitudeLineEdit.setFixedWidth(35)
        shakeTranslateZAmplitudeLineEdit.setEnabled(False)
        shakeTranslateZAmplitudeLineEdit.setAlignment(QtCore.Qt.AlignCenter)

        def onShakeTranslateZAmplitudeSliderValueChanged(value):
            shakeTranslateZAmplitudeLineEdit.setText(str(value))
            self.cameraToolsViewModel.applyCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_TRANSLATE_Z_AMPLITUDE, value)
        shakeTranslateZAmplitudeSlider.valueChanged.connect(onShakeTranslateZAmplitudeSliderValueChanged)

        def initialiseShakeTranslateZAmplitudeSlider():
            shakeTranslateZAmplitudeSlider.blockSignals(True)
            if not self.cameraToolsViewModel.isCameraSelectedAndSettingSupported(CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_TRANSLATE_Z_AMPLITUDE):
                shakeTranslateZAmplitudeSlider.setEnabled(False)
                shakeTranslateZAmplitudeSlider.setValue(0)
                shakeTranslateZAmplitudeLineEdit.setText("0")
            elif not self.cameraToolsViewModel.enabledCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_TRANSLATE_Z_AMPLITUDE):
                shakeTranslateZAmplitudeSlider.setEnabled(False)
                shakeTranslateZAmplitude = self.cameraToolsViewModel.getCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_TRANSLATE_Z_AMPLITUDE)
                shakeTranslateZAmplitudeSlider.setValue(shakeTranslateZAmplitude)
                shakeTranslateZAmplitudeLineEdit.setText(str(shakeTranslateZAmplitude))
            else:
                shakeTranslateZAmplitudeSlider.setEnabled(True)
                shakeTranslateZAmplitude = self.cameraToolsViewModel.getCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_TRANSLATE_TRANSLATE_Z_AMPLITUDE)
                shakeTranslateZAmplitudeSlider.setValue(shakeTranslateZAmplitude)
                shakeTranslateZAmplitudeLineEdit.setText(str(shakeTranslateZAmplitude))
            shakeTranslateZAmplitudeSlider.blockSignals(False)
        self.cameraToolsViewModel.cameraSettingChanged.connect(initialiseShakeTranslateZAmplitudeSlider)

        shakeTranslateZAmplitudeLayout = QtWidgets.QHBoxLayout()
        shakeTranslateZAmplitudeLayout.addWidget(shakeTranslateZAmplitudeSlider)
        shakeTranslateZAmplitudeLayout.addWidget(shakeTranslateZAmplitudeLineEdit)
        formLayout.addRow("Shake Translate Z Amplitude", shakeTranslateZAmplitudeLayout)

        # ------------------------ Shake Rotate X Frequency ------------------------ #
        shakeRotateXFrequencySlider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        shakeRotateXFrequencySlider.setFixedWidth(150)
        shakeRotateXFrequencySlider.setMinimum(0)
        shakeRotateXFrequencySlider.setTickInterval(1)
        shakeRotateXFrequencyLineEdit = QtWidgets.QLineEdit()
        shakeRotateXFrequencyLineEdit.setFixedWidth(35)
        shakeRotateXFrequencyLineEdit.setEnabled(False)
        shakeRotateXFrequencyLineEdit.setAlignment(QtCore.Qt.AlignCenter)
        def onShakeRotateXFrequencySliderValueChanged(value):
            shakeRotateXFrequencyLineEdit.setText(str(value))
            self.cameraToolsViewModel.applyCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_X_FREQUENCY, value)
        shakeRotateXFrequencySlider.valueChanged.connect(onShakeRotateXFrequencySliderValueChanged)
        def initialiseShakeRotateXFrequencySlider():
            shakeRotateXFrequencySlider.blockSignals(True)
            if not self.cameraToolsViewModel.isCameraSelectedAndSettingSupported(CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_X_FREQUENCY):
                shakeRotateXFrequencySlider.setEnabled(False)
                shakeRotateXFrequencySlider.setValue(0)
                shakeRotateXFrequencyLineEdit.setText("0")
            elif not self.cameraToolsViewModel.enabledCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_X_FREQUENCY):
                shakeRotateXFrequencySlider.setEnabled(False)
                shakeRotateXFrequency = self.cameraToolsViewModel.getCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_X_FREQUENCY)
                shakeRotateXFrequencySlider.setValue(shakeRotateXFrequency)
                shakeRotateXFrequencyLineEdit.setText(str(shakeRotateXFrequency))
            else:
                shakeRotateXFrequencySlider.setEnabled(True)
                shakeRotateXFrequency = self.cameraToolsViewModel.getCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_X_FREQUENCY)
                shakeRotateXFrequencySlider.setValue(shakeRotateXFrequency)
                shakeRotateXFrequencyLineEdit.setText(str(shakeRotateXFrequency))
            shakeRotateXFrequencySlider.blockSignals(False)
        self.cameraToolsViewModel.cameraSettingChanged.connect(initialiseShakeRotateXFrequencySlider)
        shakeRotateXFrequencyLayout = QtWidgets.QHBoxLayout()
        shakeRotateXFrequencyLayout.addWidget(shakeRotateXFrequencySlider)
        shakeRotateXFrequencyLayout.addWidget(shakeRotateXFrequencyLineEdit)
        formLayout2.addRow("Shake Rotate X Frequency", shakeRotateXFrequencyLayout)

        # ------------------------ Shake Rotate X Amplitude ------------------------ #
        shakeRotateXAmplitudeSlider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        shakeRotateXAmplitudeSlider.setFixedWidth(150)
        shakeRotateXAmplitudeSlider.setMinimum(0)
        shakeRotateXAmplitudeSlider.setTickInterval(1)
        shakeRotateXAmplitudeLineEdit = QtWidgets.QLineEdit()
        shakeRotateXAmplitudeLineEdit.setFixedWidth(35)
        shakeRotateXAmplitudeLineEdit.setEnabled(False)
        shakeRotateXAmplitudeLineEdit.setAlignment(QtCore.Qt.AlignCenter)
        def onShakeRotateXAmplitudeSliderValueChanged(value):
            shakeRotateXAmplitudeLineEdit.setText(str(value))
            self.cameraToolsViewModel.applyCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_X_AMPLITUDE, value)
        shakeRotateXAmplitudeSlider.valueChanged.connect(onShakeRotateXAmplitudeSliderValueChanged)
        def initialiseShakeRotateXAmplitudeSlider():
            shakeRotateXAmplitudeSlider.blockSignals(True)
            if not self.cameraToolsViewModel.isCameraSelectedAndSettingSupported(CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_X_AMPLITUDE):
                shakeRotateXAmplitudeSlider.setEnabled(False)
                shakeRotateXAmplitudeSlider.setValue(0)
                shakeRotateXAmplitudeLineEdit.setText("0")
            elif not self.cameraToolsViewModel.enabledCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_X_AMPLITUDE):
                shakeRotateXAmplitudeSlider.setEnabled(False)
                shakeRotateXAmplitude = self.cameraToolsViewModel.getCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_X_AMPLITUDE)
                shakeRotateXAmplitudeSlider.setValue(shakeRotateXAmplitude)
                shakeRotateXAmplitudeLineEdit.setText(str(shakeRotateXAmplitude))
            else:
                shakeRotateXAmplitudeSlider.setEnabled(True)
                shakeRotateXAmplitude = self.cameraToolsViewModel.getCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_X_AMPLITUDE)
                shakeRotateXAmplitudeSlider.setValue(shakeRotateXAmplitude)
                shakeRotateXAmplitudeLineEdit.setText(str(shakeRotateXAmplitude))
            shakeRotateXAmplitudeSlider.blockSignals(False)
        self.cameraToolsViewModel.cameraSettingChanged.connect(initialiseShakeRotateXAmplitudeSlider)
        shakeRotateXAmplitudeLayout = QtWidgets.QHBoxLayout()
        shakeRotateXAmplitudeLayout.addWidget(shakeRotateXAmplitudeSlider)
        shakeRotateXAmplitudeLayout.addWidget(shakeRotateXAmplitudeLineEdit)
        formLayout2.addRow("Shake Rotate X Amplitude", shakeRotateXAmplitudeLayout)

        # ------------------------ Shake Rotate Y Frequency ------------------------ #
        shakeRotateYFrequencySlider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        shakeRotateYFrequencySlider.setFixedWidth(150)
        shakeRotateYFrequencySlider.setMinimum(0)
        shakeRotateYFrequencySlider.setTickInterval(1)
        shakeRotateYFrequencyLineEdit = QtWidgets.QLineEdit()
        shakeRotateYFrequencyLineEdit.setFixedWidth(35)
        shakeRotateYFrequencyLineEdit.setEnabled(False)
        shakeRotateYFrequencyLineEdit.setAlignment(QtCore.Qt.AlignCenter)
        def onShakeRotateYFrequencySliderValueChanged(value):
            shakeRotateYFrequencyLineEdit.setText(str(value))
            self.cameraToolsViewModel.applyCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_Y_FREQUENCY, value)
        shakeRotateYFrequencySlider.valueChanged.connect(onShakeRotateYFrequencySliderValueChanged)
        def initialiseShakeRotateYFrequencySlider():
            shakeRotateYFrequencySlider.blockSignals(True)
            if not self.cameraToolsViewModel.isCameraSelectedAndSettingSupported(CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_Y_FREQUENCY):
                shakeRotateYFrequencySlider.setEnabled(False)
                shakeRotateYFrequencySlider.setValue(0)
                shakeRotateYFrequencyLineEdit.setText("0")
            elif not self.cameraToolsViewModel.enabledCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_Y_FREQUENCY):
                shakeRotateYFrequencySlider.setEnabled(False)
                shakeRotateYFrequency = self.cameraToolsViewModel.getCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_Y_FREQUENCY)
                shakeRotateYFrequencySlider.setValue(shakeRotateYFrequency)
                shakeRotateYFrequencyLineEdit.setText(str(shakeRotateYFrequency))
            else:
                shakeRotateYFrequencySlider.setEnabled(True)
                shakeRotateYFrequency = self.cameraToolsViewModel.getCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_Y_FREQUENCY)
                shakeRotateYFrequencySlider.setValue(shakeRotateYFrequency)
                shakeRotateYFrequencyLineEdit.setText(str(shakeRotateYFrequency))
            shakeRotateYFrequencySlider.blockSignals(False)
        self.cameraToolsViewModel.cameraSettingChanged.connect(initialiseShakeRotateYFrequencySlider)
        shakeRotateYFrequencyLayout = QtWidgets.QHBoxLayout()
        shakeRotateYFrequencyLayout.addWidget(shakeRotateYFrequencySlider)
        shakeRotateYFrequencyLayout.addWidget(shakeRotateYFrequencyLineEdit)
        formLayout2.addRow("Shake Rotate Y Frequency", shakeRotateYFrequencyLayout)

        # ------------------------ Shake Rotate Y Amplitude ------------------------ #
        shakeRotateYAmplitudeSlider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        shakeRotateYAmplitudeSlider.setFixedWidth(150)
        shakeRotateYAmplitudeSlider.setMinimum(0)
        shakeRotateYAmplitudeSlider.setTickInterval(1)
        shakeRotateYAmplitudeLineEdit = QtWidgets.QLineEdit()
        shakeRotateYAmplitudeLineEdit.setFixedWidth(35)
        shakeRotateYAmplitudeLineEdit.setEnabled(False)
        shakeRotateYAmplitudeLineEdit.setAlignment(QtCore.Qt.AlignCenter)
        def onShakeRotateYAmplitudeSliderValueChanged(value):
            shakeRotateYAmplitudeLineEdit.setText(str(value))
            self.cameraToolsViewModel.applyCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_Y_AMPLITUDE, value)
        shakeRotateYAmplitudeSlider.valueChanged.connect(onShakeRotateYAmplitudeSliderValueChanged)
        def initialiseShakeRotateYAmplitudeSlider():
            shakeRotateYAmplitudeSlider.blockSignals(True)
            if not self.cameraToolsViewModel.isCameraSelectedAndSettingSupported(CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_Y_AMPLITUDE):
                shakeRotateYAmplitudeSlider.setEnabled(False)
                shakeRotateYAmplitudeSlider.setValue(0)
                shakeRotateYAmplitudeLineEdit.setText("0")
            elif not self.cameraToolsViewModel.enabledCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_Y_AMPLITUDE):
                shakeRotateYAmplitudeSlider.setEnabled(False)
                shakeRotateYAmplitude = self.cameraToolsViewModel.getCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_Y_AMPLITUDE)
                shakeRotateYAmplitudeSlider.setValue(shakeRotateYAmplitude)
                shakeRotateYAmplitudeLineEdit.setText(str(shakeRotateYAmplitude))
            else:
                shakeRotateYAmplitudeSlider.setEnabled(True)
                shakeRotateYAmplitude = self.cameraToolsViewModel.getCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_Y_AMPLITUDE)
                shakeRotateYAmplitudeSlider.setValue(shakeRotateYAmplitude)
                shakeRotateYAmplitudeLineEdit.setText(str(shakeRotateYAmplitude))
            shakeRotateYAmplitudeSlider.blockSignals(False)
        self.cameraToolsViewModel.cameraSettingChanged.connect(initialiseShakeRotateYAmplitudeSlider)
        shakeRotateYAmplitudeLayout = QtWidgets.QHBoxLayout()
        shakeRotateYAmplitudeLayout.addWidget(shakeRotateYAmplitudeSlider)
        shakeRotateYAmplitudeLayout.addWidget(shakeRotateYAmplitudeLineEdit)
        formLayout2.addRow("Shake Rotate Y Amplitude", shakeRotateYAmplitudeLayout)

        # ------------------------ Shake Rotate Z Frequency ------------------------ #
        shakeRotateZFrequencySlider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        shakeRotateZFrequencySlider.setFixedWidth(150)
        shakeRotateZFrequencySlider.setMinimum(0)
        shakeRotateZFrequencySlider.setTickInterval(1)

        shakeRotateZFrequencyLineEdit = QtWidgets.QLineEdit()
        shakeRotateZFrequencyLineEdit.setFixedWidth(35)
        shakeRotateZFrequencyLineEdit.setEnabled(False)
        shakeRotateZFrequencyLineEdit.setAlignment(QtCore.Qt.AlignCenter)

        def onShakeRotateZFrequencySliderValueChanged(value):
            shakeRotateZFrequencyLineEdit.setText(str(value))
            self.cameraToolsViewModel.applyCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_Z_FREQUENCY, value)
        shakeRotateZFrequencySlider.valueChanged.connect(onShakeRotateZFrequencySliderValueChanged)

        def initialiseShakeRotateZFrequencySlider():
            shakeRotateZFrequencySlider.blockSignals(True)
            if not self.cameraToolsViewModel.isCameraSelectedAndSettingSupported(CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_Z_FREQUENCY):
                shakeRotateZFrequencySlider.setEnabled(False)
                shakeRotateZFrequencySlider.setValue(0)
                shakeRotateZFrequencyLineEdit.setText("0")
            elif not self.cameraToolsViewModel.enabledCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_Z_FREQUENCY):
                shakeRotateZFrequencySlider.setEnabled(False)
                value = self.cameraToolsViewModel.getCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_Z_FREQUENCY)
                shakeRotateZFrequencySlider.setValue(value)
                shakeRotateZFrequencyLineEdit.setText(str(value))
            else:
                shakeRotateZFrequencySlider.setEnabled(True)
                value = self.cameraToolsViewModel.getCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_Z_FREQUENCY)
                shakeRotateZFrequencySlider.setValue(value)
                shakeRotateZFrequencyLineEdit.setText(str(value))
            shakeRotateZFrequencySlider.blockSignals(False)
        self.cameraToolsViewModel.cameraSettingChanged.connect(initialiseShakeRotateZFrequencySlider)

        shakeRotateZFrequencyLayout = QtWidgets.QHBoxLayout()
        shakeRotateZFrequencyLayout.addWidget(shakeRotateZFrequencySlider)
        shakeRotateZFrequencyLayout.addWidget(shakeRotateZFrequencyLineEdit)
        formLayout2.addRow("Shake Rotate Z Frequency", shakeRotateZFrequencyLayout)

        # ------------------------ Shake Rotate Z Amplitude ------------------------ #
        shakeRotateZAmplitudeSlider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        shakeRotateZAmplitudeSlider.setFixedWidth(150)
        shakeRotateZAmplitudeSlider.setMinimum(0)
        shakeRotateZAmplitudeSlider.setTickInterval(1)

        shakeRotateZAmplitudeLineEdit = QtWidgets.QLineEdit()
        shakeRotateZAmplitudeLineEdit.setFixedWidth(35)
        shakeRotateZAmplitudeLineEdit.setEnabled(False)
        shakeRotateZAmplitudeLineEdit.setAlignment(QtCore.Qt.AlignCenter)

        def onShakeRotateZAmplitudeSliderValueChanged(value):
            shakeRotateZAmplitudeLineEdit.setText(str(value))
            self.cameraToolsViewModel.applyCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_Z_AMPLITUDE, value)
        shakeRotateZAmplitudeSlider.valueChanged.connect(onShakeRotateZAmplitudeSliderValueChanged)

        def initialiseShakeRotateZAmplitudeSlider():
            shakeRotateZAmplitudeSlider.blockSignals(True)
            if not self.cameraToolsViewModel.isCameraSelectedAndSettingSupported(CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_Z_AMPLITUDE):
                shakeRotateZAmplitudeSlider.setEnabled(False)
                shakeRotateZAmplitudeSlider.setValue(0)
                shakeRotateZAmplitudeLineEdit.setText("0")
            elif not self.cameraToolsViewModel.enabledCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_Z_AMPLITUDE):
                shakeRotateZAmplitudeSlider.setEnabled(False)
                value = self.cameraToolsViewModel.getCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_Z_AMPLITUDE)
                shakeRotateZAmplitudeSlider.setValue(value)
                shakeRotateZAmplitudeLineEdit.setText(str(value))
            else:
                shakeRotateZAmplitudeSlider.setEnabled(True)
                value = self.cameraToolsViewModel.getCameraSetting(CameraToolsViewModel.CameraSetting.SHAKE_ROTATION_Z_AMPLITUDE)
                shakeRotateZAmplitudeSlider.setValue(value)
                shakeRotateZAmplitudeLineEdit.setText(str(value))
            shakeRotateZAmplitudeSlider.blockSignals(False)
        self.cameraToolsViewModel.cameraSettingChanged.connect(initialiseShakeRotateZAmplitudeSlider)

        shakeRotateZAmplitudeLayout = QtWidgets.QHBoxLayout()
        shakeRotateZAmplitudeLayout.addWidget(shakeRotateZAmplitudeSlider)
        shakeRotateZAmplitudeLayout.addWidget(shakeRotateZAmplitudeLineEdit)
        formLayout2.addRow("Shake Rotate Z Amplitude", shakeRotateZAmplitudeLayout)

class CameraTemplateSettings(QtWidgets.QWidget):
    def __init__(self, cameraToolsViewModel, parent=None):
        super().__init__(parent)
        self.cameraToolsViewModel = cameraToolsViewModel
        self._setupUi()

    def _setupUi(self):
        mainLayout = QtWidgets.QVBoxLayout(self)
        formLayout = QtWidgets.QFormLayout()
        mainLayout.addLayout(formLayout)
        formLayout.setFormAlignment(QtCore.Qt.AlignCenter)

        # ------------------------------- Alexa Camera ------------------------------- #
        alexaCameraButton = QtWidgets.QPushButton("Apply Alexa Camera Settings")
        def onAlexaCameraButtonClicked():
            self.cameraToolsViewModel.applyCameraSetting(CameraToolsViewModel.CameraSetting.CAMERA_TEMPLATE_ALEXA)
            self.cameraToolsViewModel.cameraSettingChanged.emit()
        alexaCameraButton.clicked.connect(onAlexaCameraButtonClicked)
        formLayout.addRow("Alexa LF Settings", alexaCameraButton)

        # ------------------------------- Cinematic Camera ------------------------------- #
        cinematicCameraButton = QtWidgets.QPushButton("Apply Cinematic Camera Settings")
        def onCinematicCameraButtonClicked():
            self.cameraToolsViewModel.applyCameraSetting(CameraToolsViewModel.CameraSetting.CAMERA_TEMPLATE_CINEMATIC)
            self.cameraToolsViewModel.cameraSettingChanged.emit()
        cinematicCameraButton.clicked.connect(onCinematicCameraButtonClicked)
        formLayout.addRow("Cinematic Settings", cinematicCameraButton)

class QtUiUtils:
    @staticmethod
    def newDivider():
        divider = QtWidgets.QFrame()
        divider.setFrameShape(QtWidgets.QFrame.HLine)
        divider.setFrameShadow(QtWidgets.QFrame.Sunken)
        return divider

def getMayaMainWindow():
    """Return Maya's main window as a Python object."""
    mainWindowPtr = omui.MQtUtil.mainWindow()
    if mainWindowPtr:
        return shiboken6.wrapInstance(int(mainWindowPtr), QtWidgets.QWidget)

def showCameraTools():
    if cmds.workspaceControl(WORKSPACE_CONTROL_NAME, exists=True):
        cmds.deleteUI(WORKSPACE_CONTROL_NAME)
    def createCameraTools():
        cameraTools = CameraTools(getMayaMainWindow())
        cameraTools.show(dockable=True)
    cmds.evalDeferred(createCameraTools)

if __name__ == "__main__":
    showCameraTools()

""" Create a distance locator for controlling depth of field focal point, for now no longer needed with the new ALA camera plugin """
# def createDistanceDimension(*args):
    # for each_cam_tf in cmds.ls(sl=True):
    #     cam_shp = cmds.listRelatives(each_cam_tf,type="camera")
    #     if cam_shp:
    #         camParent = cmds.listRelatives(cam_shp[0], parent=True)[0]
    #         if not any(camParent in o for o in cmds.ls(type="locator")) and not(isALACamRig(cam_shp[0])):
    #             distanceDim = cmds.distanceDimension(startPoint=cmds.getAttr(camParent+".translate")[0],endPoint=tuple(map(sum, zip(cmds.getAttr(camParent+".translate")[0], (0.0,0.0,-10.0)))))
    #             #cmds.parent(cmds.listConnections(distanceDim)[0], camParent)
    #             camLocator = cmds.listConnections(distanceDim)[0]
    #             objLocator = cmds.listConnections(distanceDim)[1]
    #             cmds.connectAttr(distanceDim+".distance", cam_shp[0]+".focusDistance")
    #             cmds.connectAttr(camParent+".translate", camLocator+".translate")
    #             cmds.rename(camLocator, camParent+"Locator")
    #             cmds.rename(objLocator, camParent+"DepthOfFieldDistanceLocator")
    #             cmds.rename(cmds.listRelatives(distanceDim, parent=True)[0], camParent+"DistanceDimension")

# def toggleDistanceShow(state, *args):
    # for each_obj in cmds.ls():
    #     #print(objectType(each_dis_dim)
    #     #find the distance dimensions and locators
    #     distanceDim = cmds.listRelatives(each_obj,type="distanceDimShape")
    #     locator = cmds.listRelatives(each_obj,type="locator")
    #     #if a distance dimension was found 
    #     if distanceDim:
    #         #Set visibility
    #         if state is True:
    #             cmds.showHidden(distanceDim)
    #         else:
    #             cmds.hide(distanceDim)
    #     #if a locator was found 
    #     if locator:
    #         #Set visibility
    #         if state is True:
    #             cmds.showHidden(locator)
    #         else:
    #             cmds.hide(locator)
        
    #     alaCamRigs = [x for x in cmds.ls(type="camera") if isALACamRig(x)]

    #     for cam in alaCamRigs:
    #         camParent = cmds.listRelatives(cam, parent=True)[0]
    #         cmds.setAttr(camParent+".FocusPlane", state)
    # cam_shapes = []
    # for each_cam_tf in cmds.ls(sl=True):
    #     cam_shp = cmds.listRelatives(each_cam_tf,type="camera")
    #     if cam_shp:
    #         cam_shapes.append(cam_shp)

    # # If one camera selected, update UI to match the camera.
    # if len(cam_shapes) == 1:
    #     for cam_shp in cam_shapes:
    #         cmds.text("currentSelection", edit=True, l="Current Selection: " + cmds.ls(sl=True)[0])
    #         #Update the sliders to reflect the camera's current settings
    #         if cmds.getAttr(cam_shp[0]+".focalLength") in focalLengths:
    #             cmds.intSliderGrp("FocalLengthSlider", edit=True,enable=True, value = focalLengths.index(cmds.getAttr(cam_shp[0]+".focalLength")))
    #             cmds.intField("FocalLengthValue", edit=True, value = focalLengths[cmds.intSliderGrp("FocalLengthSlider", q=True, v=1)])
               
    #         if cmds.getAttr(cam_shp[0]+".fStop") in fStops:
    #             cmds.intSliderGrp("FStopSlider", edit=True, enable=True, value = fStops.index(cmds.getAttr(cam_shp[0]+".fStop")))
    #             cmds.floatField("FStopValue", edit=True, value = fStops[cmds.intSliderGrp("FStopSlider", q=True, v=1)])
            
    #         cmds.floatSliderGrp("locatorSlider", edit=True, enable=True, value = cmds.getAttr(cam_shp[0]+".locatorScale"))
    #         cmds.floatField('locatorSliderValue',edit=True,value=cmds.getAttr(cam_shp[0]+".locatorScale"))

    #         cmds.text("dofLabel", edit=True, enable=True)
    #         cmds.checkBox("dofControl", edit=True, enable=True, value = cmds.getAttr(cam_shp[0]+".dof"))
    
    # # If multiple cameras selected, and all the cameras have the same value for a setting,
    # # update the setting to match the cameras. If the values for a setting differ,
    # # set them to default, but allow them to be changed.
    # elif len(cam_shapes) > 1:
    #     cmds.text("currentSelection", edit=True, l="Multiple cameras selected")

    #     # Focal length
    #     if all((cmds.getAttr(x[0]+'.focalLength') == cmds.getAttr(cam_shapes[0][0]+'.focalLength')) for x in cam_shapes):
    #         for cam_shp in cam_shapes:
    #             cmds.intSliderGrp("FocalLengthSlider", edit=True,enable=True, value = focalLengths.index(cmds.getAttr(cam_shp[0]+".focalLength")))
    #             cmds.intField("FocalLengthValue", edit=True, value = focalLengths[cmds.intSliderGrp("FocalLengthSlider", q=True, v=1)])
    #     else:
    #         cmds.intSliderGrp("FocalLengthSlider", edit=True,enable=True, value = 0)
    #         cmds.intField('FocalLengthValue',edit=True,value=0)

    #     # FStop
    #     if all((cmds.getAttr(x[0]+'.fStop') == cmds.getAttr(cam_shapes[0][0]+'.fStop')) for x in cam_shapes):
    #         for cam_shp in cam_shapes:
    #             cmds.intSliderGrp("FStopSlider", edit=True, enable=True, value = fStops.index(cmds.getAttr(cam_shp[0]+".fStop")))
    #             cmds.floatField("FStopValue", edit=True, value = fStops[cmds.intSliderGrp("FStopSlider", q=True, v=1)])
    #     else:
    #         cmds.intSliderGrp("FStopSlider", edit=True,enable=True, value = 0)
    #         cmds.floatField('FStopValue',edit=True,value=0)
        
    #     #Locator scale
    #     if all((cmds.getAttr(x[0]+'.locatorScale') == cmds.getAttr(cam_shapes[0][0]+'.locatorScale')) for x in cam_shapes):
    #         for cam_shp in cam_shapes:
    #             cmds.floatSliderGrp("locatorSlider", edit=True, enable=True, value = cmds.getAttr(cam_shp[0]+".locatorScale"))
    #             cmds.floatField('locatorSliderValue',edit=True,value=cmds.getAttr(cam_shp[0]+".locatorScale"))
    #     else:
    #         cmds.floatSliderGrp("locatorSlider", edit=True, enable=True, value = 1)
    #         cmds.floatField('locatorSliderValue',edit=True,value=1)

    #     #DOF
    #     if all((cmds.getAttr(x[0]+'.dof') == cmds.getAttr(cam_shapes[0][0]+'.dof')) for x in cam_shapes):
    #         for cam_shp in cam_shapes:
    #             cmds.text("dofLabel", edit=True, enable=True)
    #             cmds.checkBox("dofControl", edit=True, enable=True, value = cmds.getAttr(cam_shp[0]+".dof"))
    #     else:
    #         cmds.text("dofLabel", edit=True, enable=True)
    #         cmds.checkBox("dofControl", edit=True, enable=True, value = 0)
    
    # # If no cameras selected, disable controls.
    # else:
    #     disableUI()
