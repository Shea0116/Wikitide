import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./style.css";

const root = document.getElementById("team-root");
if (root)
  createRoot(root).render(
    <App
      csrf={root.dataset.csrf || ""}
      username={root.dataset.username || ""}
    />,
  );
