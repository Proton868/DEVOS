/**
 * DevOS OnboardingWizard — Interactive first-run tutorials.
 * Guided tours for panel management, workflow creation, chat modes, presets.
 */
import React, { useState } from "react";
import { X, ArrowRight, ArrowLeft, Check, Sparkles, Layout, Workflow, MessageSquare, Palette } from "lucide-react";
import { usePanelStore } from "../../store/panelStore";
import { useLayoutStore } from "../../store/layoutStore";
import { useThemeStore } from "../../store/themeStore";

const STEPS = [
  {
    id: "welcome",
    title: "Welcome to DevOS",
    icon: Sparkles,
    description: "An AI Operating System, not an AI Dashboard. Every panel is dockable, floating, pinnable, and remembered per workspace.",
    tips: [
      "Press Ctrl+K anytime to open the command palette",
      "Press ? to see all keyboard shortcuts",
      "Drag panels by their header to dock them anywhere",
    ],
  },
  {
    id: "panels",
    title: "Everything is a Panel",
    icon: Layout,
    description: "Instead of pages, DevOS consists of Panels. Every panel can be docked, floating, pinned, hidden, or fullscreen.",
    tips: [
      "Click the pin icon to keep a panel on top",
      "Click the maximize icon for fullscreen mode",
      "Drag a panel header to a dock zone (edges light up)",
      "Press Ctrl+\\ to toggle the active panel fullscreen",
    ],
    action: "Try opening a few panels from the sidebar",
  },
  {
    id: "presets",
    title: "Workspace Presets",
    icon: Layout,
    description: "Switch layouts instantly. Like Photoshop workspaces — Builder, Developer, Debug, Operations, and more.",
    tips: [
      "Click the preset name in the top bar to switch",
      "Save your current layout as a custom preset",
      "Each workspace remembers its own layout",
    ],
    action: "Try switching presets from the top bar",
  },
  {
    id: "workflow",
    title: "Workflow Canvas",
    icon: Workflow,
    description: "The heart of DevOS. Living nodes show health, CPU, RAM, and execution state through meaningful animations.",
    tips: [
      "Drag to pan, Ctrl+scroll to zoom",
      "Click a node's play button to run it",
      "Nodes breathe when idle, pulse when thinking",
      "Success shows a green ripple, failure a red glow",
    ],
  },
  {
    id: "chat",
    title: "Flexible AI Chat",
    icon: MessageSquare,
    description: "Chat works in 6 modes: Docked, Floating, Bottom, Inline, Detached, or AI Bubble. You choose.",
    tips: [
      "Click the sparkle icon in chat to change mode",
      "Bubble mode collapses chat to a floating icon",
      "Detached mode opens chat in a separate window",
    ],
  },
  {
    id: "themes",
    title: "Themes & Customization",
    icon: Palette,
    description: "16 built-in themes with full customization. Change accent, radius, glow, blur, density, and more.",
    tips: [
      "Press Ctrl+Shift+T to toggle dark/light",
      "Open Settings to access the Theme Studio",
      "Export and share your custom themes",
    ],
  },
];

export default function OnboardingWizard({ onComplete }) {
  const [step, setStep] = useState(0);
  const [completed, setCompleted] = useState(() => {
    return localStorage.getItem("devos_onboarded") === "true";
  });

  if (completed) return null;

  const current = STEPS[step];
  const Icon = current.icon;
  const isLast = step === STEPS.length - 1;

  const handleComplete = () => {
    localStorage.setItem("devos_onboarded", "true");
    setCompleted(true);
    if (onComplete) onComplete();
  };

  const handleSkip = () => {
    localStorage.setItem("devos_onboarded", "true");
    setCompleted(true);
  };

  return (
    <div className="devos-onboarding-overlay" role="dialog" aria-label="Onboarding wizard">
      <div className="devos-onboarding-modal">
        <button onClick={handleSkip} className="devos-onboarding-skip" aria-label="Skip onboarding">
          <X size={16} />
        </button>

        <div className="devos-onboarding-header">
          <div className="devos-onboarding-icon">
            <Icon size={28} />
          </div>
          <h2 className="devos-onboarding-title">{current.title}</h2>
        </div>

        <p className="devos-onboarding-description">{current.description}</p>

        {current.tips && (
          <ul className="devos-onboarding-tips">
            {current.tips.map((tip, i) => (
              <li key={i} className="devos-onboarding-tip">
                <Check size={11} className="devos-onboarding-tip-icon" />
                <span>{tip}</span>
              </li>
            ))}
          </ul>
        )}

        {current.action && (
          <div className="devos-onboarding-action">
            <Sparkles size={12} />
            <span>{current.action}</span>
          </div>
        )}

        <div className="devos-onboarding-progress">
          {STEPS.map((s, i) => (
            <div
              key={s.id}
              className={"devos-onboarding-dot" + (i === step ? " active" : "") + (i < step ? " done" : "")}
            />
          ))}
        </div>

        <div className="devos-onboarding-footer">
          <button
            onClick={() => setStep(Math.max(0, step - 1))}
            className="devos-onboarding-btn secondary"
            disabled={step === 0}
          >
            <ArrowLeft size={12} /> Back
          </button>
          <button
            onClick={() => (isLast ? handleComplete() : setStep(step + 1))}
            className="devos-onboarding-btn primary"
          >
            {isLast ? <><Check size={12} /> Get Started</> : <>Next <ArrowRight size={12} /></>}
          </button>
        </div>
      </div>
    </div>
  );
}
