import SwiftUI

struct AlertPreferencesView: View {
    @ObservedObject var manager = AlertPreferencesManager.shared
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationView {
            Form {
                Section(header: Text("Safety Alerts")) {
                    Toggle("MTA Transit Disruptions", isOn: $manager.preferences.transitEnabled)
                    Toggle("NYC 311 Hazards", isOn: $manager.preferences.warnings311Enabled)
                    Toggle("Pedestrian Collision Hotspots", isOn: $manager.preferences.collisionHotspotsEnabled)
                }
                
                Section(header: Text("Neighborhood Intelligence")) {
                    Toggle("Restaurant Health Violations", isOn: $manager.preferences.healthViolationsEnabled)
                    Toggle("Cultural & Historic Narratives", isOn: $manager.preferences.culturalEnabled)
                }
                
                Section(footer: Text("These preferences are continuously synced to your local DGX edge node. Nemo will only proactively warn you about enabled hazard tracks.")) {
                    EmptyView()
                }
            }
            .navigationTitle("Alert Preferences")
            .navigationBarItems(trailing: Button("Done") {
                dismiss()
            })
            // Dark mode overrides to match app theme natively
            .preferredColorScheme(.dark)
        }
    }
}
