import { createRoot } from "react-dom/client";
import { AdminApp } from "./AdminApp";
import "./style.css";

const root = document.getElementById("admin-root");
if (root) createRoot(root).render(<AdminApp csrf={root.dataset.csrf || ""} />);
