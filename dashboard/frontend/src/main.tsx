import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { InvestmentOverview } from "./InvestmentOverview";
import "./index.css";

const historicalView = ["/dual-shadow", "/expanded-shadow", "/expanded-partial", "/partial-validation", "/expanded-isolated-history"]
  .includes(window.location.pathname);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    {historicalView ? <App /> : <InvestmentOverview />}
  </React.StrictMode>
);
