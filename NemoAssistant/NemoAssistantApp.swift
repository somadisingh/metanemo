import SwiftUI
import UserNotifications

@main
struct NemoAssistantApp: App {
  @UIApplicationDelegateAdaptor(AppDelegate.self) var appDelegate

  var body: some Scene {
    WindowGroup {
      ContentView()
    }
  }
}

// MARK: - AppDelegate for foreground notification display

class AppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
  func application(_ application: UIApplication,
                   didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
    // Set self as notification delegate to display alerts even when app is in foreground
    UNUserNotificationCenter.current().delegate = self
    return true
  }

  /// Show notification banners even when the app is in the foreground
  func userNotificationCenter(_ center: UNUserNotificationCenter,
                              willPresent notification: UNNotification,
                              withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
    completionHandler([.banner, .sound, .list])
  }

  /// Handle notification taps (user tapped on a proactive alert)
  func userNotificationCenter(_ center: UNUserNotificationCenter,
                              didReceive response: UNNotificationResponse,
                              withCompletionHandler completionHandler: @escaping () -> Void) {
    NSLog("[AppDelegate] Notification tapped: %@", response.notification.request.identifier)
    completionHandler()
  }
}
