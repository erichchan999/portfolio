import maya.cmds as cmds
import maya.mel as mel
import maya.OpenMayaUI as omui
import maya.api.OpenMaya as om
from PySide6 import QtWidgets, QtCore, QtGui
import shiboken6
from maya.app.general.mayaMixin import MayaQWidgetDockableMixin
import weakref
import re

WINDOW_TITLE="DagRenamer"
WINDOW_OBJECT_NAME="DagRenamer"
WORKSPACE_CONTROL_NAME=WINDOW_OBJECT_NAME + "WorkspaceControl"

SPECIAL_RENAME_PLACEHOLDER_TEXT="Enter text to special rename selected items..."
SPECIAL_RENAME_BRIEF_INSTRUCTION="Enter text to special rename selected items. Press Ctrl+Enter to apply."
SPECIAL_RENAME_DETAILED_INSTRUCTIONS="""\
This tool can rename multiple selected items in the outliner using a special syntax.
Special rename syntax:
1. Use # to denote numbers (e.g. ### -> 001, 002, 003, ...)
2. Use $ to denote letters (e.g. $$$ -> AAA, AAB, AAC, ...)
3. Use ! to denote itself (e.g. someprefix_!_somesuffix -> some_prefix_<original name>_somesuffix)
4. Use @ to denote parent name (e.g. @_### -> <parent name>_001, <parent_name>_002, ...)
Note that it labels the objects from top to bottom in the outliner, but can be reversed using the reversed checkbox.
"""

"""
TODO: When docked, all shortcuts will be consumed by the docked window, regardless of whether it has focus or not.
TODO: Use getInvisibleRootNode for the QTreeWidget to do traversal
TODO: Warning dialogs are super last min hacked in, they should be more elegant
"""

def getMayaMainWindow():
    """Return Maya's main window as a Python object."""
    mainWindowPtr = omui.MQtUtil.mainWindow()
    if mainWindowPtr:
        return shiboken6.wrapInstance(int(mainWindowPtr), QtWidgets.QWidget)
    return None

class QTreeWidgetTraverser:
    """Utility class to traverse a QTreeWidget in different orders"""
    # TODO: Convert to QTreeWidget invisible root node traversal
    @staticmethod
    def traversePostOrderItem(item: QtWidgets.QTreeWidgetItem) -> QtWidgets.QTreeWidgetItem:
        for i in range(item.childCount()):
            yield from QTreeWidgetTraverser.traversePostOrderItem(item.child(i))
        yield item

    @staticmethod
    def traversePostOrder(treeWidget: QtWidgets.QTreeWidget) -> QtWidgets.QTreeWidgetItem:
        for i in range(treeWidget.topLevelItemCount()):
            yield from QTreeWidgetTraverser.traversePostOrderItem(treeWidget.topLevelItem(i))

    @staticmethod
    def traversePreOrderItem(item: QtWidgets.QTreeWidgetItem) -> QtWidgets.QTreeWidgetItem:
        yield item
        for i in range(item.childCount()):
            yield from QTreeWidgetTraverser.traversePreOrderItem(item.child(i))

    @staticmethod
    def traversePreOrder(treeWidget: QtWidgets.QTreeWidget) -> QtWidgets.QTreeWidgetItem:
        for i in range(treeWidget.topLevelItemCount()):
            yield from QTreeWidgetTraverser.traversePreOrderItem(treeWidget.topLevelItem(i))

class CollapsibleSection(QtWidgets.QWidget):
    """A widget that can collapse/expand its content."""
    def __init__(self, contentWidget, buttonText, parent=None):
        super().__init__(parent)
        self.mainLayout = QtWidgets.QVBoxLayout(self)

        self.toggleButton = QtWidgets.QPushButton(buttonText)
        self.mainLayout.addWidget(self.toggleButton)
        self.toggleButton.setCheckable(True)
        self.toggleButton.setChecked(False)
        self.toggleButton.clicked.connect(self.toggleContent)

        self.contentWidget = contentWidget
        self.mainLayout.addWidget(self.contentWidget)
        
        # Initially hide the scroll area
        self.contentWidget.setVisible(False)
    
    def toggleContent(self):
        self.contentWidget.setVisible(self.toggleButton.isChecked())

class SpecialRenameTextEditWidget(QtWidgets.QTextEdit):
    """A QTextEdit widget that allows special renaming of selected items in the tree."""
    # Signal emitted when applying special text
    specialRenameApplied = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText(SPECIAL_RENAME_PLACEHOLDER_TEXT)
        # Disable word wrap and enable horizontal scroll bar if text is too long
        self.setLineWrapMode(QtWidgets.QTextEdit.NoWrap)
        self.applyShortcut = QtGui.QShortcut(QtGui.QKeySequence(QtCore.Qt.CTRL | QtCore.Qt.Key_Return), self)
        self.applyShortcut.activated.connect(self.applyText)
    
    def applyText(self):
        """Emit signal with the current text."""
        text = self.toPlainText().strip()
        if text:
            self.specialRenameApplied.emit(text)

    def focusSpecialRename(self):
        """Focus the special rename text edit and select all text if any exists."""
        self.setFocus()
        if self.toPlainText():
            cursor = self.textCursor()
            cursor.select(QtGui.QTextCursor.Document)
            self.setTextCursor(cursor)

class RenameCommand(QtGui.QUndoCommand):
    """Undoable command for single item rename operations."""
    def __init__(self, item, newName, oldName, description="Rename", parent=None):
        super().__init__(description, parent)
        self.item = item
        self.newName = newName
        self.oldName = oldName
        
    def undo(self):
        self.item.treeWidget().setTextProgrammatic(self.item, 0, self.oldName)
    
    def redo(self):
        self.item.treeWidget().setTextProgrammatic(self.item, 0, self.newName)

class MultiRenameCommand(QtGui.QUndoCommand):
    """Undoable command for multi item rename operations."""
    def __init__(self, items, newNames, oldNames, description="Multi Rename", parent=None):
        super().__init__(description, parent)
        self.changes = []
        
        for item, newName, oldName in zip(items, newNames, oldNames):
            self.changes.append({
                "item": item,
                "oldName": oldName,
                "newName": newName
            })
    
    def undo(self):
        for change in self.changes:
            change["item"].treeWidget().setTextProgrammatic(change["item"], 0, change["oldName"])
    
    def redo(self):
        for change in self.changes:
            change["item"].treeWidget().setTextProgrammatic(change["item"], 0, change["newName"])

class DagRenamerTreeWidget(QtWidgets.QTreeWidget):
    def __init__(self, parent=None, editable=False, unchangedColour=None, changedColour=None, errorColour=None):
        super().__init__(parent)
        self.unchangedColour = unchangedColour or self.palette().color(QtGui.QPalette.Base)
        self.changedColour = changedColour or QtGui.QColor(75, 0, 130)
        self.errorColour = errorColour or QtGui.QColor(0, 0, 128)
        self.programmaticTextChange = False
        # Connect internal signals
        self.itemExpanded.connect(self._onItemExpanded)
        self.itemCollapsed.connect(self._onItemCollapsed)
        if editable:
            self.setEditTriggers(
                QtWidgets.QAbstractItemView.DoubleClicked |
                QtWidgets.QAbstractItemView.SelectedClicked |
                QtWidgets.QAbstractItemView.EditKeyPressed
            )
            self.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
            self.itemChanged.connect(self._onItemChanged)
        else:
            self.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)

    def setUnchangedColour(self, colour):
        self.unchangedColour = colour

    def setChangedColour(self, colour):
        self.changedColour = colour

    def setErrorColour(self, colour):
        self.errorColour = colour

    def setTextProgrammatic(self, item, column, text):
        """Set the text of the item without adding an undo command to the stack."""
        # Maybe this functionality best belongs in a subclass of QTreeWidgetItem, but im lazy to make one
        self.programmaticTextChange = True
        item.setText(column, text)
        self.programmaticTextChange = False

    def _onItemExpanded(self, item):
        """Collapse all items below the expanded item if Shift key is held."""
        if QtGui.QGuiApplication.keyboardModifiers() & QtCore.Qt.ShiftModifier:
            self._expandAllBelow(item)

    def _expandAllBelow(self, item):
        """Expand all child items of the given item."""
        for childIndex in range(item.childCount()):
            child = item.child(childIndex)
            self.expandItem(child)
            self._expandAllBelow(child)

    def _onItemCollapsed(self, item):
        """Collapse all items below the collapsed item if Shift key is held."""
        if QtGui.QGuiApplication.keyboardModifiers() & QtCore.Qt.ShiftModifier:
            self._collapseAllBelow(item)

    def _collapseAllBelow(self, item):
        """Collapse all child items of the given item."""
        for childIndex in range(item.childCount()):
            child = item.child(childIndex)
            self.collapseItem(child)
            self._collapseAllBelow(child)

    def drawRow(self, painter, options, index):
        """
        Override the default drawRow method to fill the entire row with the background colour.
        This is called when setBackground is called.
        """
        # Try to retrieve the background colour from the model index.
        background = index.data(QtCore.Qt.BackgroundRole)
        if background:
            # Fill the entire row rectangle (including indentation area) with the background colour.
            painter.save()
            painter.fillRect(options.rect, background)
            painter.restore()
        # Let the default implementation draw the row text and other details.
        super().drawRow(painter, options, index)

    def _onItemChanged(self, item, column):
        """ Handle any item text changes, it updates the item colour and can add an undo command to the stack. """
        # This function is a little complicated because we have to deal with the fact
        # that any setText calls will trigger this function, including undo/redo operations
        # or user initiated changes from the editor.
        editableNewText = item.text(0).strip()
        editableOldText = item.data(0, QtCore.Qt.UserRole)["oldName"]
        originalItem = item.data(0, QtCore.Qt.UserRole)["otherTreeItem"]()
        originalText = originalItem.text(0).strip()

        self.blockSignals(True) # Block signals to prevent messy recursive calls that break the reasoning below
        # Set the item text back to the original text if the new text is empty
        if not editableNewText:
            # Block signals to prevent recursion
            item.setText(0, editableOldText) # Set text triggers an itemChanged signal
            return

        # Apply new colour to item
        if editableNewText != originalText:
            # Changed text gets changed colour
            colour = self.changedColour
        else:
            # Unchanged text gets default colour
            colour = self.unchangedColour
        item.setBackground(0, colour) # setBackground triggers an itemChanged signal

        # Update the old name in the item data
        newItemData = item.data(0, QtCore.Qt.UserRole)
        newItemData["oldName"] = editableNewText
        item.setData(0, QtCore.Qt.UserRole, newItemData) # setData triggers an itemChanged signal
        
        # Don't let undo/redo commands add themselves to the undo stack if the change was programmatic
        if not self.programmaticTextChange:
            # Add command to undo stack and push it to the undo stack
            command = RenameCommand(item, editableNewText, editableOldText)
            self.parent().undoStack.push(command) # This will call a setText on the item again due to Qt calling redo on push
        self.blockSignals(False)

class DagRenamer(MayaQWidgetDockableMixin, QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(QtCore.Qt.Window)
        self.setObjectName(WINDOW_OBJECT_NAME)
        self.setWindowTitle(WINDOW_TITLE)
        self.setMinimumSize(800, 600)
        self.callbackIds = []
        self.undoStack = QtGui.QUndoStack(self)
        self.warnedDagChange = False
        self.searchText = ""
        self._setupUi()
        self._setupShortcuts()
        self._setupMayaCallbacks()
        self._populateTrees()

    def _setupUi(self):
        mainLayout = QtWidgets.QVBoxLayout(self)

        # Add a warning label that is only visible when the DAG hierarchy has changed.
        self.warningLabel = QtWidgets.QLabel("**WARNING**: DAG hierarchy has changed. Please reimport before applying changes.")
        self.warningLabel.setStyleSheet("color: red; font-weight: bold;")
        self.warningLabel.setAlignment(QtCore.Qt.AlignCenter)
        self.warningLabel.setVisible(False)  # Initially hidden
        mainLayout.addWidget(self.warningLabel)

        gridLayout = QtWidgets.QGridLayout()
        mainLayout.addLayout(gridLayout)
        gridLayout.setColumnStretch(0, 7)
        gridLayout.setColumnStretch(1, 3)

        leftPanelLayout = QtWidgets.QVBoxLayout()
        gridLayout.addLayout(leftPanelLayout, 0, 0)

        rightPanelLayout = QtWidgets.QVBoxLayout()
        gridLayout.addLayout(rightPanelLayout, 0, 1)

        # ---------------------------------------------------------------------------- #
        #                                  Left panel                                  #
        # ---------------------------------------------------------------------------- #
        # ------------------------------- Tree widgets ------------------------------- #
        treeWidgetLayout = QtWidgets.QHBoxLayout()
        leftPanelLayout.addLayout(treeWidgetLayout)

        # Set colours for tree widget items
        treeWidgetUnchangedColour = QtWidgets.QApplication.palette().color(QtGui.QPalette.Base)
        treeWidgetChangedColour = QtGui.QColor(75, 0, 130)
        treeWidgetErrorColour = QtGui.QColor(0, 0, 128)

        # Create trees with appropriate settings
        self.editableTree = DagRenamerTreeWidget(
            editable=True, 
            unchangedColour=treeWidgetUnchangedColour,
            changedColour=treeWidgetChangedColour,
            errorColour=treeWidgetErrorColour
        )
        treeWidgetLayout.addWidget(self.editableTree)
        self.editableTree.setHeaderLabel("Editable Outliner")

        self.originalTree = DagRenamerTreeWidget(
            editable=False, 
            unchangedColour=treeWidgetUnchangedColour,
            changedColour=treeWidgetChangedColour,
            errorColour=treeWidgetErrorColour
        )
        treeWidgetLayout.addWidget(self.originalTree)
        self.originalTree.setHeaderLabel("Original Outliner")
        self.originalTree.setVisible(False)

        # ----------------------------- Undo/redo buttons ---------------------------- #
        undoRedoLayout = QtWidgets.QHBoxLayout()
        leftPanelLayout.addLayout(undoRedoLayout)
        
        self.undoButton = QtWidgets.QPushButton("Undo")
        self.undoButton.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_ArrowBack))
        self.undoButton.setToolTip("Undo last action")
        self.undoButton.clicked.connect(self.undoStack.undo)
        undoRedoLayout.addWidget(self.undoButton)
        
        self.redoButton = QtWidgets.QPushButton("Redo")
        self.redoButton.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_ArrowForward))
        self.redoButton.setToolTip("Redo last undone action")
        self.redoButton.clicked.connect(self.undoStack.redo)
        undoRedoLayout.addWidget(self.redoButton)
        
        # Connect undo stack signals to update button states
        self.undoStack.canUndoChanged.connect(self.undoButton.setEnabled)
        self.undoStack.canRedoChanged.connect(self.redoButton.setEnabled)
        
        # Initialize button states
        self.undoButton.setEnabled(self.undoStack.canUndo())
        self.redoButton.setEnabled(self.undoStack.canRedo())

        # ---------------------------------------------------------------------------- #
        #                                  Right panel                                 #
        # ---------------------------------------------------------------------------- #
        # ------------------------ Search and replace section ------------------------ #
        searchAndReplaceGroupBox = QtWidgets.QGroupBox("Search and Replace")
        rightPanelLayout.addWidget(searchAndReplaceGroupBox)
        
        searchAndReplaceGroupBoxLayout = QtWidgets.QVBoxLayout(searchAndReplaceGroupBox)
        
        searchAndReplaceFormLayout = QtWidgets.QFormLayout()
        searchAndReplaceGroupBoxLayout.addLayout(searchAndReplaceFormLayout)

        self.searchLineEdit = QtWidgets.QLineEdit()
        self.searchLineEdit.setPlaceholderText("Enter regex pattern to select matching items...")
        searchAndReplaceFormLayout.addRow("Search", self.searchLineEdit)
        self.searchLineEdit.textChanged.connect(lambda text: self._searchAndSelectItems(text, self.searchCaseSensitiveCheckbox.isChecked()))
        
        self.replaceLineEdit = QtWidgets.QLineEdit()
        self.replaceLineEdit.setPlaceholderText("Enter text to replace selected items...")
        searchAndReplaceFormLayout.addRow("Replace", self.replaceLineEdit)

        self.searchCaseSensitiveCheckbox = QtWidgets.QCheckBox("Case Sensitive")
        searchAndReplaceFormLayout.addRow("Case sensitivity:", self.searchCaseSensitiveCheckbox)

        replaceApplyButton = QtWidgets.QPushButton("Apply")
        searchAndReplaceGroupBoxLayout.addWidget(replaceApplyButton)
        replaceApplyButton.setToolTip("Replace the leftmost occurrence of the search text in the selected items with the replace text")
        replaceApplyButton.clicked.connect(lambda: self._replaceSearchTextInSelectedItems(self.replaceLineEdit.text(), self.searchCaseSensitiveCheckbox.isChecked()))

        # ------------------------ Special rename text editor ------------------------ #
        specialRenameGroupBox = QtWidgets.QGroupBox("Special Rename")
        rightPanelLayout.addWidget(specialRenameGroupBox)
        specialRenameLayout = QtWidgets.QVBoxLayout(specialRenameGroupBox)
        
        # Add the brief instructions label
        briefInstructionsLabel = QtWidgets.QLabel(SPECIAL_RENAME_BRIEF_INSTRUCTION)
        specialRenameLayout.addWidget(briefInstructionsLabel)
        briefInstructionsLabel.setWordWrap(True)

        # ----------------- Detailed instructions collapsible section ---------------- #
        # Create a scroll area to contain the content label
        contentWidget = QtWidgets.QWidget()
        contentWidgetLayout = QtWidgets.QVBoxLayout(contentWidget)
        
        scrollArea = QtWidgets.QScrollArea()
        contentWidgetLayout.addWidget(scrollArea)
        scrollArea.setWidgetResizable(True)
        
        # Create content label and set it as the widget for the scroll area
        self.contentLabel = QtWidgets.QLabel(SPECIAL_RENAME_DETAILED_INSTRUCTIONS)
        scrollArea.setWidget(self.contentLabel)
        self.contentLabel.setWordWrap(True)

        detailsSection = CollapsibleSection(contentWidget, "Expand Detailed Instructions")
        specialRenameLayout.addWidget(detailsSection)

        # Add a divider line
        dividerLine = QtWidgets.QFrame()
        dividerLine.setFrameShape(QtWidgets.QFrame.HLine)
        dividerLine.setFrameShadow(QtWidgets.QFrame.Sunken)
        specialRenameLayout.addWidget(dividerLine)

        # -------------------------- Special rename settings ------------------------- #
        specialRenameTextSettingsLayout = QtWidgets.QFormLayout()
        specialRenameLayout.addLayout(specialRenameTextSettingsLayout)
        # Add selection order reverse checkbox
        self.selectionOrderReverseCheckbox = QtWidgets.QCheckBox("Reverse")
        specialRenameTextSettingsLayout.addRow("Selection order:", self.selectionOrderReverseCheckbox)
        
        # ---------------------- Special rename text edit widget --------------------- #
        self.specialRenameTextEdit = SpecialRenameTextEditWidget()
        specialRenameLayout.addWidget(self.specialRenameTextEdit)
        self.specialRenameTextEdit.specialRenameApplied.connect(lambda text: self.applySpecialRenameToTree(text, self.selectionOrderReverseCheckbox.isChecked()))

        # Add a button to apply changes
        textApplyButton = QtWidgets.QPushButton("Apply")
        specialRenameLayout.addWidget(textApplyButton)
        textApplyButton.clicked.connect(self.specialRenameTextEdit.applyText)

        # ----------------------- View original tree button ---------------------- #
        viewOriginalTreeButton = QtWidgets.QPushButton("View Original Outliner")
        rightPanelLayout.addWidget(viewOriginalTreeButton)
        viewOriginalTreeButton.setCheckable(True)
        viewOriginalTreeButton.setChecked(False)
        viewOriginalTreeButton.clicked.connect(lambda: self.originalTree.setVisible(not self.originalTree.isVisible()))
        # TODO: Fix this haphazard checkable/viewable disconnected behaviour

        # --------------------------- Reset editable nodes button --------------------------- #
        resetButton = QtWidgets.QPushButton("Reset")
        rightPanelLayout.addWidget(resetButton)
        resetButton.setToolTip("Reset the editable tree items to their original names")
        resetButton.clicked.connect(self._resetNodes)

        # --------------------------- Reimport DAG hierarchy button --------------------------- #
        reimportButton = QtWidgets.QPushButton("Reimport")
        rightPanelLayout.addWidget(reimportButton)
        reimportButton.setToolTip("Reimport the DAG hierarchy from Maya.")
        reimportButton.clicked.connect(self._doReimportNodes)

        # ------------------------ Apply changes to DAG button ----------------------- #
        applyChangesButton = QtWidgets.QPushButton("Apply")
        rightPanelLayout.addWidget(applyChangesButton)
        applyChangesButton.setToolTip("Apply changes to the DAG nodes.")
        applyChangesButton.clicked.connect(self._doApplyChangesToDagNodes)

    def _confirmationDialog(self, title, message):
        reply = QtWidgets.QMessageBox.question(
            self,
            title,
            message,
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        )
        if reply == QtWidgets.QMessageBox.Yes:
            return True
        return False

    def _setupShortcuts(self):
        self.specialRenameShortcut = QtGui.QShortcut(QtCore.Qt.Key_QuoteLeft, self)
        self.specialRenameShortcut.activated.connect(self.specialRenameTextEdit.focusSpecialRename)

        self.undoShortcut = QtGui.QShortcut(QtGui.QKeySequence(QtCore.Qt.CTRL | QtCore.Qt.Key_Z), self)
        self.undoShortcut.activated.connect(self.undoStack.undo)
        
        self.redoShortcut = QtGui.QShortcut(QtGui.QKeySequence(QtCore.Qt.CTRL | QtCore.Qt.ShiftModifier | QtCore.Qt.Key_Z), self)
        self.redoShortcut.activated.connect(self.undoStack.redo)

        self.redoShortcut2 = QtGui.QShortcut(QtGui.QKeySequence(QtCore.Qt.CTRL | QtCore.Qt.Key_Y), self)
        self.redoShortcut2.activated.connect(self.undoStack.redo)

    def _setupMayaCallbacks(self):
        """Setup callbacks for DAG changes, undo, and redo Maya events."""
        # These callbacks are deferred as the DAG changes are not immediately available
        # upon receiving Maya events.
        changedCbId = om.MDagMessage.addAllDagChangesCallback(lambda *args: self._warnDagChanged())
        self.callbackIds.append(changedCbId)
        undoCbId = om.MEventMessage.addEventCallback("Undo", lambda *args: self._warnDagChanged())
        self.callbackIds.append(undoCbId)
        redoCbId = om.MEventMessage.addEventCallback("Redo", lambda *args: self._warnDagChanged())
        self.callbackIds.append(redoCbId)
        print("Maya callbacks registered.")
    
    def _warnDagChanged(self):
        if self.warnedDagChange:
            return
        self.warnedDagChange = True
        self.warningLabel.setVisible(True)
        self._warnDagChangedDialog()

    def _warnDagChangedDialog(self):
        QtWidgets.QMessageBox.warning(
            self,
            "DAG Changed",
            "The DAG hierarchy has changed. Please reimport the DAG hierarchy before applying changes.",
            QtWidgets.QMessageBox.Ok
        )

    # This overrides a method in the inherited class MayaQWidgetDockableMixin. 
    # This is necessary because Maya will filter out the closeEvent signal when inheriting
    # this class which is normally emitted when the window is closed. The mixin class provides a
    # dockCloseEventTriggered method which would be called instead.
    def dockCloseEventTriggered(self):
        # Clean up all registered Maya callbacks
        for cbId in self.callbackIds:
            om.MMessage.removeCallback(cbId)
        self.callbackIds = []
        print("Maya callbacks deregistered.")
        super().dockCloseEventTriggered()

    def _getDagData(self):
        """
        Traverses the DAG (pre-order traversal) and returns a list of tuples:
        (depth, dagNode, displayName)
        """
        dagData = []
        rootDagIterator = om.MItDag(om.MItDag.kDepthFirst, om.MFn.kTransform)
        for dagIterator in rootDagIterator:
            dagNode = om.MFnDagNode(dagIterator.currentItem())
            dagData.append((dagIterator.depth(), dagNode, dagNode.name()))
        return dagData

    def _populateTree(self, tree, dagData, editable=False):
        """
        Populates a single QTreeWidget (passed as tree) from the dagData.
        If editable is True, sets the item as editable and stores an 'oldName'.
        Returns a list of created QTreeWidgetItems (in pre-order traversal order)
        """
        tree.blockSignals(True)
        tree.clear()
        stack = {}
        items = []

        for depth, dagNode, displayName in dagData:
            item = QtWidgets.QTreeWidgetItem()
            data = {"node": dagNode}

            if editable:
                # Store the old name for undo purposes
                data["oldName"] = displayName

            defaultCams = ["persp", "front", "side", "top"]
            if displayName in defaultCams or cmds.lockNode(dagNode.uniqueName(), q=True)[0] or cmds.referenceQuery(dagNode.uniqueName(), isNodeReferenced=True):
                # If item is not editable, disable it
                item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEnabled)
            else:
                if editable:
                    item.setFlags(item.flags() | QtCore.Qt.ItemIsEditable)

            item.setData(0, QtCore.Qt.UserRole, data)
            item.setText(0, displayName)

            if depth == 1:
                tree.addTopLevelItem(item)
            else:
                parent = stack.get(depth - 1)
                if parent:
                    parent.addChild(item)
            stack[depth] = item
            items.append(item)

        tree.expandAll()
        tree.blockSignals(False)
        return items

    def _populateTrees(self):
        """
        Populates both the original and editable trees with the current DAG hierarchy.
        """
        dagData = self._getDagData()
        originalItems = self._populateTree(self.originalTree, dagData, editable=False)
        editableItems = self._populateTree(self.editableTree, dagData, editable=True)

        # Link items between trees: for each corresponding pair, store a weak reference
        # to the other tree's item in the UserRole data.
        self.originalTree.blockSignals(True)
        self.editableTree.blockSignals(True)
        for origItem, editItem in zip(originalItems, editableItems):
            origData = origItem.data(0, QtCore.Qt.UserRole)
            editData = editItem.data(0, QtCore.Qt.UserRole)
            origData["otherTreeItem"] = weakref.ref(editItem)
            editData["otherTreeItem"] = weakref.ref(origItem)
            origItem.setData(0, QtCore.Qt.UserRole, origData)
            editItem.setData(0, QtCore.Qt.UserRole, editData)
        self.originalTree.blockSignals(False)
        self.editableTree.blockSignals(False)

    def _doApplyChangesToDagNodes(self):
        """Apply changes to DAG nodes from items whose text has been modified."""
        if self.warnedDagChange:
            reply = self._confirmationDialog(
                "Force apply changes to DAG nodes",
                "The DAG hierarchy has changed. Are you sure you want to apply changes to the DAG nodes anyway? This could lead to unexpected results."
            )
        else:
            reply = self._confirmationDialog(
                "Apply changes to DAG nodes",
                "Are you sure you want to apply changes to the DAG nodes? This action cannot be undone."
            )
            if reply:
                self._applyChangesToDagNodes()
                # Clear the undo stack after applying changes
                self.undoStack.clear()
                # Repopulate the trees to reflect the changes
                cmds.evalDeferred(self._populateTrees)

    def _applyChangesToDagNodes(self):
        """Traverse the editable tree and apply changes to DAG nodes from items whose text has been modified."""
        for item in QTreeWidgetTraverser.traversePostOrder(self.editableTree):
            dagNode = item.data(0, QtCore.Qt.UserRole)["node"]
            originalItem = item.data(0, QtCore.Qt.UserRole)["otherTreeItem"]()
            originalText = originalItem.text(0).strip()
            editableText = item.text(0).strip()
            if editableText and editableText != originalText:
                dagNode.setName(editableText)

    def _doReimportNodes(self):
        """Reimport the DAG hierarchy from Maya."""
        if self._confirmationDialog(
            "Reimport DAG Hierarchy",
            "Are you sure you want to reimport the DAG hierarchy from Maya? This will discard any unsaved changes to the editable tree."
        ):
            self.warnedDagChange = False
            self.warningLabel.setVisible(False)
            self.undoStack.clear()
            cmds.evalDeferred(self._populateTrees)

    def _resetNodes(self):
        """Reset the editable tree items to their original names."""
        changedItems = []
        newEditableNames = []
        oldEditableNames = []
        editableTreeIterator = QTreeWidgetTraverser.traversePostOrder(self.editableTree)
        originalTreeIterator = QTreeWidgetTraverser.traversePostOrder(self.originalTree)
        for editableItem, originalItem in zip(editableTreeIterator, originalTreeIterator):
            if editableItem.text(0) != originalItem.text(0):
                changedItems.append(editableItem)
                newEditableNames.append(originalItem.text(0))
                oldEditableNames.append(editableItem.text(0))
        
        assert(len(changedItems) == len(newEditableNames) == len(oldEditableNames))
        if not changedItems:
            return
        
        # Create an undo command for the reset and push it to the undo stack.
        # It will also call redo for us
        command = MultiRenameCommand(
            changedItems,
            newEditableNames,
            oldEditableNames,
            "Reset Names"
        )
        self.undoStack.push(command)

    def applySpecialRenameToTree(self, specialText, selectionOrderReversed):
        """Apply a special rename to the selected items in the editable tree."""
        # TODO: implement multiline renaming
        # Only take the first line of special text, ignore the rest
        specialText = specialText.split("\n")[0].strip()

        # Get the selected items from the editable tree
        selectedItems = self.editableTree.selectedItems()
        if not selectedItems:
            return

        # Traverse the tree in pre-order and filter out only selected items
        # this gives us the correct pre-ordering of items that we want to rename in
        allItems = list(QTreeWidgetTraverser.traversePreOrder(self.editableTree))
        preOrderedItems = [item for item in allItems if item in selectedItems]

        if selectionOrderReversed:
            preOrderedItems.reverse()

        def intToLetters(num, width):
            # Convert an integer (1-indexed) to a letter sequence with a fixed width
            num -= 1  # Adjust to 0-index
            result = []
            for _ in range(width):
                result.append(chr((num % 26) + ord('A')))
                num //= 26
            return ''.join(reversed(result))

        def computeNewNameForItem(item, seq):
            # Compute a new name for a given item using regex substitution
            def replacer(match):
                token = match.group(0)
                if token[0] == '#':
                    digitCount = len(token)
                    return f"{seq:0{digitCount}d}"
                elif token[0] == '$':
                    letterCount = len(token)
                    return intToLetters(seq, letterCount)
                elif token[0] == '@':
                    parent = item.parent()
                    return parent.text(0) if parent else item.text(0)
                elif token[0] == '!':
                    return item.text(0)
                else:
                    assert(False) # Should never reach here, if we do that means the regex is wrong
            # Replace any occurrence of a sequence of #, $ or @ in the special text
            return re.sub(r"((?:[#\$])+|[!@])", replacer, specialText)

        oldNames = [item.text(0) for item in preOrderedItems]
        newNames = [computeNewNameForItem(item, i + 1) for i, item in enumerate(preOrderedItems)]
        
        # TODO: Fix the @ token to work with multiple levels of hierarchy, it should be the modified parent name not the direct parent name

        # Create and push the undo command for the multi-rename
        command = MultiRenameCommand(
            preOrderedItems,
            newNames,
            oldNames,
            f"Special Rename with '{specialText}'"
        )
        self.undoStack.push(command)

    def _searchAndSelectItems(self, pattern, caseSensitive=False):
        """Search for items matching the regex pattern and select them."""
        self.searchText = pattern
        # Clear current selection
        self.editableTree.clearSelection()
        if not pattern:
            return
        # Iterate through all items in the editable tree
        for item in QTreeWidgetTraverser.traversePreOrder(self.editableTree):
            try:
                if caseSensitive:
                    regexFlags = 0
                else:
                    regexFlags = re.IGNORECASE
                if re.search(pattern, item.text(0), flags=regexFlags):
                    # If the item text matches the pattern, select it
                    item.setSelected(True)
                    # Make sure the item is visible by expanding parents
                    parent = item.parent()
                    while parent:
                        parent.setExpanded(True)
                        parent = parent.parent()
            except re.error:
                # If the pattern is invalid, don't do anything
                pass
        
    def _replaceSearchTextInSelectedItems(self, replaceText, caseSensitive=False):
        """Replace the leftmost occurence matching the search text in the selected items with the replace text."""
        # Do nothing if searchText and replaceText are the same
        if self.searchText == replaceText:
            return
        # Get the selected items from the editable tree
        selectedItems = self.editableTree.selectedItems()
        if not selectedItems:
            return
        # Create and push the undo command for the multi-rename
        oldNames = [item.text(0) for item in selectedItems]
        if caseSensitive:
            regexFlags = 0
        else:
            regexFlags = re.IGNORECASE
        newNames = [re.sub(self.searchText, replaceText, item.text(0), flags=regexFlags) for item in selectedItems]
        command = MultiRenameCommand(
            selectedItems,
            newNames,
            oldNames,
            f"Replace '{self.searchText}' with '{replaceText}'"
        )
        self.undoStack.push(command)

def showWindow():
    if cmds.workspaceControl(WORKSPACE_CONTROL_NAME, exists=True):
        cmds.deleteUI(WORKSPACE_CONTROL_NAME)
    def createDagRenamer():
        dagRenamer = DagRenamer(getMayaMainWindow())
        dagRenamer.show(dockable=True)
    cmds.evalDeferred(createDagRenamer)

if __name__ == "__main__":
    showWindow()

