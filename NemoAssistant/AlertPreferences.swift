import Foundation
import SwiftUI

/// Model for user alert preferences
struct AlertPreferences: Codable, Equatable {
    var transitEnabled: Bool = true
    var warnings311Enabled: Bool = true
    var collisionHotspotsEnabled: Bool = true
    var healthViolationsEnabled: Bool = true
    var culturalEnabled: Bool = true
    
    // Default standard configuration
    static let standard = AlertPreferences()
}

class AlertPreferencesManager: ObservableObject {
    static let shared = AlertPreferencesManager()
    
    @AppStorage("alertPreferences") private var preferencesData: Data = Data()
    
    @Published var preferences: AlertPreferences = .standard {
        didSet {
            if let encoded = try? JSONEncoder().encode(preferences) {
                preferencesData = encoded
            }
        }
    }
    
    init() {
        if let decoded = try? JSONDecoder().decode(AlertPreferences.self, from: preferencesData) {
            preferences = decoded
        } else {
            preferences = .standard
        }
    }
}
