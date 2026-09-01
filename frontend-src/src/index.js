import React from "react";
import ReactDOM from "react-dom/client";
import "./index.css";
import "./devos-panels.css";
import "./devos-workflow.css";
import "./components/panels/registerPanels";
import App from "./App";

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(<React.StrictMode><App /></React.StrictMode>);
