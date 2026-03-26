/**
 * Azure Application Insights — singleton initializer.
 *
 * Import this module once at the top of main.tsx (before React mounts) so the
 * SDK can begin capturing page-views, exceptions, and dependency calls from the
 * very first user interaction.
 *
 * The connection string is injected at Vite build time via the Static Web App
 * `app_settings` → VITE_APPINSIGHTS_CONNECTION_STRING (set in Terraform, sourced
 * from Key Vault).  In local dev, add it to your .env.local file:
 *   VITE_APPINSIGHTS_CONNECTION_STRING=InstrumentationKey=...;IngestionEndpoint=...
 */
import { ApplicationInsights } from "@microsoft/applicationinsights-web";

const connectionString = import.meta.env.VITE_APPINSIGHTS_CONNECTION_STRING;

let appInsights: ApplicationInsights | null = null;

if (connectionString) {
  appInsights = new ApplicationInsights({
    config: {
      connectionString,
      // Automatically track page views on route changes.
      enableAutoRouteTracking: true,
      // Capture unhandled JS errors as exceptions in App Insights.
      disableExceptionTracking: false,
      // Capture outgoing fetch/XHR calls as dependency telemetry.
      disableFetchTracking: false,
      // Include the correlation headers so end-to-end traces link
      // frontend requests to the FastAPI backend spans.
      enableCorsCorrelation: true,
      correlationHeaderExcludedDomains: [],
    },
  });

  appInsights.loadAppInsights();

  appInsights.addTelemetryInitializer((envelope) => {
  envelope.tags["ai.cloud.role"] = import.meta.env.AI_CLOUD_ROLE || "award-nomination-app-frontend";
});

  appInsights.trackPageView(); // record the initial page load
} else {
  console.warn(
    "VITE_APPINSIGHTS_CONNECTION_STRING is not set — Application Insights disabled."
  );
}

export { appInsights };
