import Foundation
import UserNotifications
import Combine

/// Manages proactive safety alerts — sends GPS every 2 minutes and shows
/// local notifications when the middleware detects nearby hazards.
///
/// Flow:
///   Timer (2 min) → send GPS via WebSocket → middleware calls NemoClaw
///   → middleware filters through cooldown cache → sends proactiveAlert
///   → this manager fires a UNUserNotificationCenter local notification
class ProactiveAlertManager: ObservableObject {
  static let shared = ProactiveAlertManager()

  @Published var isEnabled = true
  @Published var lastAlertTime: Date?

  /// How often to send a location ping (seconds)
  private let pingInterval: TimeInterval = 120  // 2 minutes

  private var pingTimer: Timer?
  private var notificationCenter: UNUserNotificationCenter { .current() }

  // MARK: - Setup

  /// Request notification permissions — call once at app launch
  func requestNotificationPermission() {
    notificationCenter.requestAuthorization(options: [.alert, .sound, .badge]) { granted, error in
      if let error {
        NSLog("[ProactiveAlert] Notification permission error: %@", error.localizedDescription)
      }
      NSLog("[ProactiveAlert] Notification permission %@", granted ? "granted" : "denied")
    }
  }

  // MARK: - Timer

  /// Start the 2-minute location ping timer
  func startPingTimer() {
    guard pingTimer == nil else { return }

    // Fire immediately for first ping, then every 2 minutes
    sendLocationPing()

    pingTimer = Timer.scheduledTimer(withTimeInterval: pingInterval, repeats: true) { [weak self] _ in
      self?.sendLocationPing()
    }

    NSLog("[ProactiveAlert] Ping timer started (every %.0fs)", pingInterval)
  }

  /// Stop the location ping timer
  func stopPingTimer() {
    pingTimer?.invalidate()
    pingTimer = nil
    NSLog("[ProactiveAlert] Ping timer stopped")
  }

  // MARK: - Send Location Ping

  private func sendLocationPing() {
    guard isEnabled else { return }

    let location = LocationManager.shared
    let ws = WebSocketClient.shared

    guard ws.isReady else {
      NSLog("[ProactiveAlert] Skipping ping — WebSocket not ready")
      return
    }

    let lat = location.latitude
    let lon = location.longitude

    NSLog("[ProactiveAlert] Sending location ping: %.6f, %.6f", lat, lon)
    ws.sendLocationPing(lat: lat, lon: lon)
  }

  // MARK: - Handle Incoming Alert

  /// Called when the middleware sends a proactiveAlert message
  func handleAlert(bullets: [String]) {
    guard !bullets.isEmpty else {
      NSLog("[ProactiveAlert] Empty alert — skipping notification")
      return
    }

    NSLog("[ProactiveAlert] Received %d hazard(s)", bullets.count)
    lastAlertTime = Date()

    // Format bullet points for notification body
    let body = bullets.map { "• \($0)" }.joined(separator: "\n")

    // Fire local notification
    let content = UNMutableNotificationContent()
    content.title = "Nemo Safety Alert"
    content.body = body
    content.sound = .default
    content.categoryIdentifier = "PROACTIVE_ALERT"

    // Use a unique identifier so each alert is separate
    let identifier = "proactive-\(Int(Date().timeIntervalSince1970))"
    let request = UNNotificationRequest(identifier: identifier, content: content, trigger: nil)

    notificationCenter.add(request) { error in
      if let error {
        NSLog("[ProactiveAlert] Notification error: %@", error.localizedDescription)
      } else {
        NSLog("[ProactiveAlert] Notification posted: %@", identifier)
      }
    }
  }
}
