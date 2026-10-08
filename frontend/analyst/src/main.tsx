import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { AnalystSession } from "./AnalystSession";
import { AppErrorBoundary } from "./components/AppErrorBoundary";
import "./styles/tokens.css";
import "./styles/app.css";
import "./styles/dashboard.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AppErrorBoundary>
      <AnalystSession />
    </AppErrorBoundary>
  </StrictMode>,
);
