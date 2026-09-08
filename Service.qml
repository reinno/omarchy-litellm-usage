import QtQuick
import Quickshell
import Quickshell.Io

Item {
    id: root
    visible: false

    readonly property string configPath: (Quickshell.env("XDG_CONFIG_HOME") || Quickshell.env("HOME") + "/.config") + "/omarchy/agents/litellm.json"
    readonly property string collectorPath: decodeURIComponent(Qt.resolvedUrl("collector.py").toString().replace(/^file:\/\//, ""))
    property int refreshIntervalSec: 300
    property bool discoveryRequested: false
    property bool pendingRefresh: false
    property string lastStatus: "Waiting for first refresh"

    function refresh() {
        if (collector.running) {
            pendingRefresh = true
            return
        }
        collector.running = true
    }

    FileView {
        path: root.configPath
        watchChanges: true
        printErrors: false
        onFileChanged: reload()
        onLoaded: {
            try {
                var config = JSON.parse(text())
                var seconds = Number(config.refreshIntervalSec || 300)
                root.refreshIntervalSec = isFinite(seconds) ? Math.max(60, Math.min(3600, seconds)) : 300
            } catch (e) {
                root.refreshIntervalSec = 300
            }
            root.refresh()
        }
    }

    Timer {
        interval: root.refreshIntervalSec * 1000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: root.refresh()
    }

    Process {
        id: collector
        command: ["python3", root.collectorPath, "--write", "--config", root.configPath]
        stdout: StdioCollector {
            onStreamFinished: root.lastStatus = text.trim()
        }
        stderr: StdioCollector {
            onStreamFinished: if (text.trim()) root.lastStatus = text.trim()
        }
        onExited: function(exitCode) {
            // Native Agents discovers new files after its updater completes.
            // Request that once, then its FileView watches our atomic updates.
            if (exitCode === 0 && !root.discoveryRequested) {
                discovery.running = true
            }
            if (root.pendingRefresh) {
                root.pendingRefresh = false
                Qt.callLater(root.refresh)
            }
        }
    }

    Process {
        id: discovery
        command: ["omarchy-shell", "omarchy.agents", "refresh"]
        onExited: function(exitCode) { root.discoveryRequested = exitCode === 0 }
    }

    IpcHandler {
        target: "reinno.omarchy-litellm-usage"
        function refresh(): string {
            root.refresh()
            return "Refresh requested"
        }
        function status(): string { return root.lastStatus }
    }
}
