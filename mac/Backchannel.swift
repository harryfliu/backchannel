// Backchannel for Mac: a native window around the Backchannel web app.
//
// On launch it connects to the local server (http://127.0.0.1:<port>), starting
// `.venv/bin/python -m server` from the repo if nothing is running yet, and shows
// the UI in a WKWebView. It adds the Mac parts the browser can't: a Dock badge for
// unread texts, notifications, a file picker for attachments, menus, and Open at Login.
//
// Build and install with `./backchannel app` (needs Xcode Command Line Tools).

import Cocoa
import ServiceManagement
import UniformTypeIdentifiers
@preconcurrency import UserNotifications
@preconcurrency import WebKit

// MARK: - Settings read from the bundle and the repo

struct AppConfig {
    let repo: URL
    let port: Int

    static func load() -> AppConfig {
        let info = Bundle.main.infoDictionary ?? [:]
        var repo = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Documents/claude_projects/backchannel")
        if let path = info["BCRepoPath"] as? String, !path.isEmpty, !path.hasPrefix("__") {
            repo = URL(fileURLWithPath: path)
        }
        var port = 5556
        // config.local.json overrides config.json, the same way the server reads them.
        for name in ["config.local.json", "config.json"] {
            let url = repo.appendingPathComponent(name)
            if let data = try? Data(contentsOf: url),
               let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let value = object["port"] as? Int {
                port = value
                break
            }
        }
        if let value = ProcessInfo.processInfo.environment["BACKCHANNEL_PORT"], let number = Int(value) {
            port = number
        }
        return AppConfig(repo: repo, port: port)
    }
}

// MARK: - Notification clicks

/// Kept separate from the app delegate: notification callbacks can arrive on any thread.
final class NotificationRouter: NSObject, UNUserNotificationCenterDelegate {
    var onOpen: ((String) -> Void)?

    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                willPresent notification: UNNotification,
                                withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .sound])
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                didReceive response: UNNotificationResponse,
                                withCompletionHandler completionHandler: @escaping () -> Void) {
        let id = response.notification.request.content.userInfo["conversationId"] as? String ?? ""
        let open = onOpen
        DispatchQueue.main.async { open?(id) }
        completionHandler()
    }
}

// MARK: - App

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, NSMenuItemValidation,
    WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler {

    let config = AppConfig.load()
    let notifications = NotificationRouter()
    var window: NSWindow!
    var webView: WKWebView!
    var server: Process?
    var weStartedServer = false
    var quitting = false
    var appLoaded = false
    var loadFailures = 0

    var baseURL: URL { URL(string: "http://127.0.0.1:\(config.port)/")! }
    var logURL: URL { config.repo.appendingPathComponent("logs/backchannel.log") }

    // MARK: Lifecycle

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenu()
        buildWindow()
        notifications.onOpen = { [weak self] id in self?.openConversation(id) }
        UNUserNotificationCenter.current().delegate = notifications
        connect()
    }

    /// Closing the window keeps Backchannel running, like Slack. Click the Dock icon to bring it back.
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !flag { showMainWindow() }
        return true
    }

    func applicationWillTerminate(_ notification: Notification) {
        quitting = true
        if weStartedServer, let server, server.isRunning {
            server.terminate()
            server.waitUntilExit()  // the server stops within ~2 seconds
        }
    }

    // MARK: Window

    func buildWindow() {
        let frame = NSRect(x: 0, y: 0, width: 1280, height: 820)
        window = NSWindow(contentRect: frame,
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered,
                          defer: false)
        window.title = "Backchannel"
        window.titleVisibility = .hidden
        window.titlebarAppearsTransparent = true
        window.backgroundColor = NSColor(hex: "#2a0c2e") ?? .windowBackgroundColor
        window.minSize = NSSize(width: 760, height: 520)
        window.isReleasedWhenClosed = false
        window.tabbingMode = .disallowed
        window.delegate = self
        window.center()
        _ = window.setFrameAutosaveName("BackchannelMainWindow")

        let contentController = WKUserContentController()
        contentController.add(self, name: "bc")
        // Lets the web app know it's inside the Mac app (Dock badge, notifications, theme color).
        let marker = "window.__BC_NATIVE__ = { version: 1 };"
        contentController.addUserScript(WKUserScript(source: marker, injectionTime: .atDocumentStart, forMainFrameOnly: true))

        let configuration = WKWebViewConfiguration()
        configuration.userContentController = contentController
        configuration.websiteDataStore = .default()

        webView = WKWebView(frame: frame, configuration: configuration)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.allowsBackForwardNavigationGestures = false
        webView.underPageBackgroundColor = window.backgroundColor
        if #available(macOS 13.3, *) {
            webView.isInspectable = true
        }
        window.contentView = webView
        showMainWindow()
    }

    @objc func showMainWindow() {
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    // MARK: Menus

    func buildMenu() {
        let main = NSMenu()

        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "About Backchannel",
                        action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(menuItem("Settings…", #selector(openSettings), ","))
        appMenu.addItem(menuItem("Open at Login", #selector(toggleOpenAtLogin(_:)), ""))
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Hide Backchannel", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        let hideOthers = appMenu.addItem(withTitle: "Hide Others",
                                         action: #selector(NSApplication.hideOtherApplications(_:)), keyEquivalent: "h")
        hideOthers.keyEquivalentModifierMask = [.command, .option]
        appMenu.addItem(withTitle: "Show All", action: #selector(NSApplication.unhideAllApplications(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit Backchannel", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        attach(appMenu, to: main)

        let fileMenu = NSMenu(title: "File")
        fileMenu.addItem(menuItem("New Message", #selector(newMessage), "n"))
        fileMenu.addItem(.separator())
        fileMenu.addItem(withTitle: "Close Window", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        attach(fileMenu, to: main)

        let editMenu = NSMenu(title: "Edit")
        editMenu.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        let redo = editMenu.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "z")
        redo.keyEquivalentModifierMask = [.command, .shift]
        editMenu.addItem(.separator())
        editMenu.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        attach(editMenu, to: main)

        let viewMenu = NSMenu(title: "View")
        viewMenu.addItem(menuItem("Reload", #selector(reloadApp), "r"))
        viewMenu.addItem(.separator())
        viewMenu.addItem(menuItem("Actual Size", #selector(zoomReset), "0"))
        viewMenu.addItem(menuItem("Zoom In", #selector(zoomIn), "="))
        viewMenu.addItem(menuItem("Zoom Out", #selector(zoomOut), "-"))
        viewMenu.addItem(.separator())
        let fullScreen = viewMenu.addItem(withTitle: "Enter Full Screen",
                                          action: #selector(NSWindow.toggleFullScreen(_:)), keyEquivalent: "f")
        fullScreen.keyEquivalentModifierMask = [.command, .control]
        attach(viewMenu, to: main)

        let windowMenu = NSMenu(title: "Window")
        windowMenu.addItem(withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        windowMenu.addItem(withTitle: "Zoom", action: #selector(NSWindow.performZoom(_:)), keyEquivalent: "")
        windowMenu.addItem(.separator())
        windowMenu.addItem(menuItem("Backchannel", #selector(showMainWindow), "1"))
        windowMenu.addItem(withTitle: "Bring All to Front", action: #selector(NSApplication.arrangeInFront(_:)), keyEquivalent: "")
        attach(windowMenu, to: main)
        NSApp.windowsMenu = windowMenu

        let helpMenu = NSMenu(title: "Help")
        helpMenu.addItem(menuItem("Keyboard Shortcuts", #selector(showShortcuts), "/"))
        helpMenu.addItem(menuItem("Show Server Log", #selector(showLog), ""))
        attach(helpMenu, to: main)
        NSApp.helpMenu = helpMenu

        NSApp.mainMenu = main
    }

    private func menuItem(_ title: String, _ action: Selector, _ key: String) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: key)
        item.target = self
        return item
    }

    private func attach(_ menu: NSMenu, to main: NSMenu) {
        let holder = NSMenuItem()
        holder.submenu = menu
        main.addItem(holder)
    }

    func validateMenuItem(_ menuItem: NSMenuItem) -> Bool {
        if menuItem.action == #selector(toggleOpenAtLogin(_:)) {
            menuItem.state = SMAppService.mainApp.status == .enabled ? .on : .off
        }
        return true
    }

    @objc func openSettings() { runCommand("settings") }
    @objc func newMessage() { runCommand("compose") }
    @objc func showShortcuts() { runCommand("shortcuts") }
    @objc func showLog() { NSWorkspace.shared.open(logURL) }
    @objc func zoomReset() { webView.pageZoom = 1 }
    @objc func zoomIn() { webView.pageZoom = min(webView.pageZoom + 0.1, 2) }
    @objc func zoomOut() { webView.pageZoom = max(webView.pageZoom - 0.1, 0.6) }

    @objc func reloadApp() {
        if appLoaded { webView.reload() } else { connect() }
    }

    @objc func toggleOpenAtLogin(_ sender: NSMenuItem) {
        let service = SMAppService.mainApp
        do {
            if service.status == .enabled {
                try service.unregister()
            } else {
                try service.register()
            }
        } catch {
            showAlert("Couldn’t change Open at Login", error.localizedDescription)
        }
    }

    /// Ask the web app to do something (open settings, start a new message…).
    func runCommand(_ name: String) {
        showMainWindow()
        guard appLoaded else { return }
        webView.evaluateJavaScript("window.__bcCommand && window.__bcCommand(\(jsString(name)))", completionHandler: nil)
    }

    func openConversation(_ id: String) {
        showMainWindow()
        guard appLoaded, !id.isEmpty else { return }
        webView.evaluateJavaScript("window.__bcOpen && window.__bcOpen(\(jsString(id)))", completionHandler: nil)
    }

    // MARK: Server

    /// Use a server that's already running (say, `./backchannel on`), or start our own.
    func connect() {
        appLoaded = false
        showPage(.starting)
        ping { [weak self] up in
            guard let self else { return }
            if up { self.loadApp() } else { self.startServer() }
        }
    }

    func ping(_ done: @escaping (Bool) -> Void) {
        var request = URLRequest(url: baseURL.appendingPathComponent("api/session"))
        request.timeoutInterval = 1.5
        request.cachePolicy = .reloadIgnoringLocalCacheData
        URLSession.shared.dataTask(with: request) { _, response, _ in
            let up = response is HTTPURLResponse
            DispatchQueue.main.async { done(up) }
        }.resume()
    }

    func startServer() {
        let python = config.repo.appendingPathComponent(".venv/bin/python")
        guard FileManager.default.isExecutableFile(atPath: python.path) else {
            showPage(.notSetUp)
            return
        }
        let process = Process()
        process.executableURL = python
        process.arguments = ["-m", "server"]
        process.currentDirectoryURL = config.repo
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONUNBUFFERED"] = "1"
        process.environment = environment

        try? FileManager.default.createDirectory(at: logURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        if !FileManager.default.fileExists(atPath: logURL.path) {
            FileManager.default.createFile(atPath: logURL.path, contents: nil)
        }
        if let handle = try? FileHandle(forWritingTo: logURL) {
            _ = try? handle.seekToEnd()
            process.standardOutput = handle
            process.standardError = handle
        }
        process.terminationHandler = { [weak self] finished in
            let status = finished.terminationStatus
            Task { @MainActor in self?.serverExited(status) }
        }
        do {
            try process.run()
        } catch {
            showPage(.failed("Couldn’t start the server: \(error.localizedDescription)"))
            return
        }
        server = process
        weStartedServer = true
        waitForServer(attemptsLeft: 80)
    }

    func waitForServer(attemptsLeft: Int) {
        ping { [weak self] up in
            guard let self else { return }
            if up {
                self.loadApp()
                return
            }
            guard let server = self.server, server.isRunning else { return }  // serverExited shows why
            if attemptsLeft <= 0 {
                self.showPage(.failed("The server didn’t answer within 20 seconds."))
                return
            }
            Task { @MainActor [weak self] in
                try? await Task.sleep(nanoseconds: 250_000_000)
                self?.waitForServer(attemptsLeft: attemptsLeft - 1)
            }
        }
    }

    func serverExited(_ status: Int32) {
        server = nil
        weStartedServer = false
        if quitting { return }
        showPage(.failed("The server stopped (exit code \(status))."))
    }

    func loadApp() {
        appLoaded = true
        webView.load(URLRequest(url: baseURL))
    }

    // MARK: Status pages (shown before the web app loads)

    enum Page {
        case starting
        case notSetUp
        case failed(String)
    }

    func showPage(_ page: Page) {
        appLoaded = false
        let title: String
        var body: String
        var buttons = ""
        let retry = #"<button onclick="webkit.messageHandlers.bc.postMessage({type:'retry'})">Try again</button>"#
        let log = #"<button class="quiet" onclick="webkit.messageHandlers.bc.postMessage({type:'showLog'})">Show log</button>"#
        switch page {
        case .starting:
            title = "Starting Backchannel…"
            body = #"<div class="spinner"></div>"#
        case .notSetUp:
            title = "Backchannel isn’t set up yet"
            body = "<p>In Terminal, run:</p><pre>cd \(html(config.repo.path))\n./backchannel setup</pre><p>Then click Try again.</p>"
            buttons = retry
        case .failed(let reason):
            title = "Backchannel couldn’t start"
            body = "<p>\(html(reason))</p><pre>\(html(logTail()))</pre>"
            buttons = retry + log
        }
        let page = #"""
        <!doctype html><html><head><meta charset="utf-8"><style>
        :root { color-scheme: dark; }
        body { margin: 0; height: 100vh; display: grid; place-items: center; background: #2a0c2e; color: #f4ebf5;
               font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Helvetica Neue", sans-serif; }
        main { max-width: 560px; padding: 32px; text-align: center; }
        h1 { font-size: 22px; margin: 0 0 12px; }
        p { color: rgba(244,235,245,.8); }
        pre { text-align: left; white-space: pre-wrap; background: rgba(0,0,0,.3); padding: 12px 14px; border-radius: 8px;
              font: 12px/1.5 ui-monospace, Menlo, monospace; max-height: 240px; overflow: auto; }
        button { font: inherit; font-weight: 700; padding: 8px 16px; margin: 8px 4px 0; border-radius: 8px; border: 0;
                 background: #f4ede4; color: #2a0c2e; cursor: pointer; }
        button.quiet { background: rgba(255,255,255,.14); color: #fff; }
        .spinner { width: 28px; height: 28px; margin: 8px auto; border-radius: 50%; border: 3px solid rgba(255,255,255,.2);
                   border-top-color: #fff; animation: spin .8s linear infinite; }
        @keyframes spin { to { transform: rotate(360deg); } }
        </style></head><body><main><h1>\#(html(title))</h1>\#(body)<div>\#(buttons)</div></main></body></html>
        """#
        webView.loadHTMLString(page, baseURL: nil)
    }

    func logTail() -> String {
        guard let data = try? Data(contentsOf: logURL) else { return "No log yet at \(logURL.path)" }
        let text = String(decoding: data.suffix(4000), as: UTF8.self)
        return text.split(separator: "\n", omittingEmptySubsequences: false).suffix(14).joined(separator: "\n")
    }

    // MARK: Web view: navigation

    func isOurs(_ url: URL) -> Bool {
        (url.host == "127.0.0.1" || url.host == "localhost") && url.port == config.port
    }

    func isAttachment(_ url: URL) -> Bool {
        url.path.hasPrefix("/api/attachments/")
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else {
            decisionHandler(.cancel)
            return
        }
        if url.scheme == "about" || url.scheme == "blob" || url.scheme == "data" {
            decisionHandler(.allow)
            return
        }
        if isOurs(url) {
            if isAttachment(url) && navigationAction.navigationType == .linkActivated {
                openAttachment(url)
                decisionHandler(.cancel)
            } else {
                decisionHandler(.allow)
            }
            return
        }
        // Everything else (web links, FaceTime, phone, System Settings) opens outside the app.
        NSWorkspace.shared.open(url)
        decisionHandler(.cancel)
    }

    /// Links that ask for a new window (target=_blank).
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = navigationAction.request.url {
            if isOurs(url) && isAttachment(url) {
                openAttachment(url)
            } else if !isOurs(url) {
                NSWorkspace.shared.open(url)
            }
        }
        return nil
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        if appLoaded { loadFailures = 0 }
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        guard appLoaded else { return }
        loadFailures += 1
        if loadFailures > 3 {
            showPage(.failed("Couldn’t load Backchannel: \(error.localizedDescription)"))
        } else {
            connect()
        }
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        connect()
    }

    /// The file picker for attachments (WKWebView shows none without this).
    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.beginSheetModal(for: window) { response in
            completionHandler(response == .OK ? panel.urls : nil)
        }
    }

    /// Downloads an attachment with the app's session and opens it in the right app (Preview, QuickTime…).
    func openAttachment(_ url: URL) {
        let host = url.host ?? "127.0.0.1"
        webView.configuration.websiteDataStore.httpCookieStore.getAllCookies { cookies in
            var request = URLRequest(url: url)
            let ours = cookies.filter { $0.domain == host || $0.domain == "127.0.0.1" || $0.domain == "localhost" }
            for (field, value) in HTTPCookie.requestHeaderFields(with: ours) {
                request.setValue(value, forHTTPHeaderField: field)
            }
            URLSession.shared.downloadTask(with: request) { temporary, response, error in
                guard let temporary, let http = response as? HTTPURLResponse, http.statusCode == 200 else {
                    let reason = error?.localizedDescription ?? "The server couldn’t find that file."
                    Task { @MainActor in self.showAlert("Couldn’t open the attachment", reason) }
                    return
                }
                var name = http.suggestedFilename ?? url.lastPathComponent
                if (name as NSString).pathExtension.isEmpty,
                   let mime = http.mimeType,
                   let ext = UTType(mimeType: mime)?.preferredFilenameExtension {
                    name += ".\(ext)"
                }
                let folder = FileManager.default.temporaryDirectory.appendingPathComponent("Backchannel", isDirectory: true)
                try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
                let destination = folder.appendingPathComponent(name)
                try? FileManager.default.removeItem(at: destination)
                do {
                    try FileManager.default.moveItem(at: temporary, to: destination)
                } catch {
                    let reason = error.localizedDescription
                    Task { @MainActor in self.showAlert("Couldn’t open the attachment", reason) }
                    return
                }
                Task { @MainActor in _ = NSWorkspace.shared.open(destination) }
            }.resume()
        }
    }

    // MARK: Web view: messages from the page

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let body = message.body as? [String: Any], let type = body["type"] as? String else { return }
        switch type {
        case "badge":
            let count = (body["count"] as? NSNumber)?.intValue ?? 0
            NSApp.dockTile.badgeLabel = count > 0 ? (count > 99 ? "99+" : String(count)) : nil
        case "theme":
            if let hex = body["frame"] as? String, let color = NSColor(hex: hex) {
                window.backgroundColor = color
                webView.underPageBackgroundColor = color
            }
        case "notify":
            notify(title: body["title"] as? String ?? "Backchannel",
                   subtitle: body["subtitle"] as? String,
                   text: body["body"] as? String ?? "",
                   conversationId: body["conversationId"] as? String ?? "")
        case "requestNotifications":
            requestNotificationPermission()
        case "attention":
            NSApp.requestUserAttention(.informationalRequest)
        case "retry":
            connect()
        case "showLog":
            showLog()
        case "quit":
            NSApp.terminate(nil)
        default:
            break
        }
    }

    // MARK: Notifications

    func requestNotificationPermission() {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge]) { granted, error in
            if let error {
                NSLog("Backchannel: notification permission failed: \(error.localizedDescription)")
            }
            Task { @MainActor [weak self] in
                self?.webView.evaluateJavaScript("window.__bcNotifyPermission && window.__bcNotifyPermission(\(granted))",
                                                 completionHandler: nil)
            }
        }
    }

    func notify(title: String, subtitle: String?, text: String, conversationId: String) {
        let content = UNMutableNotificationContent()
        content.title = title
        if let subtitle, !subtitle.isEmpty {
            content.subtitle = subtitle
        }
        content.body = text
        content.sound = .default
        content.threadIdentifier = conversationId
        content.userInfo = ["conversationId": conversationId]
        let request = UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: nil)
        UNUserNotificationCenter.current().add(request) { error in
            if let error {
                NSLog("Backchannel: notification failed: \(error.localizedDescription)")
            }
        }
    }

    // MARK: Helpers

    func showAlert(_ title: String, _ text: String) {
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = text
        alert.runModal()
    }

    func jsString(_ value: String) -> String {
        guard let data = try? JSONSerialization.data(withJSONObject: [value]),
              let array = String(data: data, encoding: .utf8) else { return "\"\"" }
        return String(array.dropFirst().dropLast())
    }

    func html(_ text: String) -> String {
        text.replacingOccurrences(of: "&", with: "&amp;")
            .replacingOccurrences(of: "<", with: "&lt;")
            .replacingOccurrences(of: ">", with: "&gt;")
    }
}

extension NSColor {
    /// "#2a0c2e" → color. Returns nil for anything else.
    convenience init?(hex: String) {
        var text = hex.trimmingCharacters(in: .whitespacesAndNewlines)
        if text.hasPrefix("#") { text.removeFirst() }
        guard text.count == 6, let value = UInt32(text, radix: 16) else { return nil }
        self.init(srgbRed: CGFloat((value >> 16) & 0xff) / 255,
                  green: CGFloat((value >> 8) & 0xff) / 255,
                  blue: CGFloat(value & 0xff) / 255,
                  alpha: 1)
    }
}

// MARK: - Entry point

@main
enum BackchannelMain {
    @MainActor
    static func main() {
        let app = NSApplication.shared
        let delegate = AppDelegate()
        app.delegate = delegate
        app.setActivationPolicy(.regular)
        withExtendedLifetime(delegate) {
            app.run()
        }
    }
}
