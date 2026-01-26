import React from "react";
import ReactDOM from "react-dom/client";
import { MsalProvider } from "@azure/msal-react";
import App from "./App";
import "./index.css";
import { msalInstance } from "./msalInstance";
import { ImpersonationProvider } from "./contexts/ImpersonationContext";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <MsalProvider instance={msalInstance}>
      <ImpersonationProvider>
        <App />
      </ImpersonationProvider>
    </MsalProvider>
  </React.StrictMode>
);
