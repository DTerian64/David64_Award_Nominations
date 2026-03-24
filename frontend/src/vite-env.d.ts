/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_CLIENT_ID: string;
  readonly VITE_TENANT_ID: string;
  readonly VITE_API_CLIENT_ID: string;
  readonly VITE_API_URL: string;
  /** Azure Application Insights connection string (optional — SDK is disabled if absent). */
  readonly VITE_APPINSIGHTS_CONNECTION_STRING?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
