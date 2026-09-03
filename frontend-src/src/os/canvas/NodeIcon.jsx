import React from "react";
import {
  Github, Webhook, TerminalSquare, Bell, Bot, FileCode2, Braces, Zap,
} from "lucide-react";
import MenorahLogo from "../MenorahLogo";

/** DevOS spatial node iconography — no third-party product logos. */
export function NodeIcon({ kind, size = 18 }) {
  const s = { width: size, height: size };
  switch (kind) {
    case "trigger":
      return <Github style={s} />;
    case "webhook":
      return <Webhook style={s} />;
    case "runtime":
      return <TerminalSquare style={s} />;
    case "output":
      return <Bell style={s} />;
    case "agent":
      return <Bot style={s} />;
    case "menorah":
      return <MenorahLogo size={size} id={`mh-${Math.round(size)}`} />;
    default:
      return <Braces style={s} />;
  }
}

/** Deterministic accent class per node kind. */
export function slabClass(kind) {
  switch (kind) {
    case "trigger":
    case "webhook":
      return "c1";
    case "runtime":
      return "c2";
    case "agent":
    case "output":
      return "c3";
    default:
      return "c1";
  }
}