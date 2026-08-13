pragma ComponentBehavior: Bound

import QtQuick
import QtTest

import "../src/fleasion/qml/screens/cache" as Cache

Item {
    id: root

    width: 600
    height: 180

    QtObject {
        id: controllerStub

        property int updateCalls: 0
        property int lastRow: -1
        property string lastValue: ''

        function updateProperty(row, value) {
            updateCalls += 1;
            lastRow = row;
            lastValue = value;
            return true;
        }

        function removeProperty(_row) {
            return true;
        }
    }

    Component {
        id: delegateComponent

        Cache.RobloxPropertyDelegate {
            width: 560
            controller: controllerStub
            ownerIdentity: qsTr('instance-1')
            rowIndex: 0
            propertyName: qsTr('Name')
            propertyTypeName: qsTr('STRING')
            propertyValueText: qsTr('Original value')
            editableValue: true
        }
    }

    TestCase {
        name: "RobloxPropertyDelegateTests"
        when: windowShown

        function init() {
            controllerStub.updateCalls = 0;
            controllerStub.lastRow = -1;
            controllerStub.lastValue = '';
        }

        function test_ownerChangeCancelsStaleDraft() {
            let delegate = createTemporaryObject(delegateComponent, root);
            verify(!!delegate, "Component exists");
            delegate.beginEditing();
            compare(delegate.editing, true);
            compare(delegate.draftText, qsTr('Original value'));
            delegate.draftText = qsTr('Stale draft');

            delegate.ownerIdentity = qsTr('instance-2');

            compare(delegate.editing, false);
            compare(delegate.draftText, qsTr(''));
            compare(delegate.commitEditing(), false);
            compare(controllerStub.updateCalls, 0);
        }

        function test_roleChangeCancelsStaleDraft() {
            let delegate = createTemporaryObject(delegateComponent, root);
            verify(!!delegate, "Component exists");
            delegate.beginEditing();
            delegate.draftText = qsTr('Stale draft');

            delegate.rowIndex = 1;
            delegate.propertyName = qsTr('Description');
            delegate.propertyTypeName = qsTr('CONTENT');
            delegate.propertyValueText = qsTr('Fresh value');

            compare(delegate.editing, false);
            compare(delegate.draftText, qsTr(''));
            compare(delegate.commitEditing(), false);
            compare(controllerStub.updateCalls, 0);
        }

        function test_valueRefreshCancelsActiveEdit() {
            let delegate = createTemporaryObject(delegateComponent, root);
            verify(!!delegate, "Component exists");
            delegate.beginEditing();
            delegate.draftText = qsTr('Unsaved value');

            delegate.propertyValueText = qsTr('Externally updated');

            compare(delegate.editing, false);
            compare(delegate.draftText, qsTr(''));
            compare(controllerStub.updateCalls, 0);
        }

        function test_reuseResetStartsWithCurrentValue() {
            let delegate = createTemporaryObject(delegateComponent, root);
            verify(!!delegate, "Component exists");
            delegate.beginEditing();
            delegate.draftText = qsTr('Old pooled draft');

            delegate.resetEditingState();
            delegate.ownerIdentity = qsTr('instance-2');
            delegate.rowIndex = 3;
            delegate.propertyName = qsTr('Description');
            delegate.propertyValueText = qsTr('Current value');
            delegate.beginEditing();

            compare(delegate.editing, true);
            compare(delegate.draftText, qsTr('Current value'));
            delegate.draftText = qsTr('Saved for current row');
            compare(delegate.commitEditing(), true);
            compare(controllerStub.updateCalls, 1);
            compare(controllerStub.lastRow, 3);
            compare(controllerStub.lastValue, qsTr('Saved for current row'));
        }
    }
}
