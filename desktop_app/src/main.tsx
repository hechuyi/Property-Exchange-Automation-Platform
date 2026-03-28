import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "antd/dist/reset.css";
import "./styles/app.css";

type DesktopBootstrapState = {
  ready: boolean;
  error: string;
};

function publishDesktopBootstrapState(state: DesktopBootstrapState) {
  const appWindow = window as typeof window & {
    __PEAP_DESKTOP_BOOTSTRAP_STATE?: DesktopBootstrapState;
  };
  appWindow.__PEAP_DESKTOP_BOOTSTRAP_STATE = state;
}

publishDesktopBootstrapState({
  ready: false,
  error: "",
});

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
