/**
 * DevOS ChatPanel — Flexible AI chat with 6 modes.
 * Modes:
 *   - Docked Right: Traditional sidebar position
 *   - Floating: Like ChatGPT Desktop
 *   - Bottom: Like Discord
 *   - Inline: Attached to code/node/workflow
 *   - Detached: Separate browser window
 *   - AI Bubble: Collapsed icon, expands on click
 *
 * The user chooses.
 */
import React, { useState, useEffect, useRef, useCallback, lazy, Suspense } from "react";
import {
  MessageSquare, ExternalLink, ArrowDown, Pin, Sparkles, X, ChevronUp,
} from "lucide-react";
import { usePanelStore } from "../../store/panelStore";

// Lazy load the existing ChatSidebar
const ChatSidebar = lazy(() => import("../sidebar/ChatSidebar"));

export const CHAT_MODES = {
  DOCKED: "docked",
  FLOATING: "floating",
  BOTTOM: "bottom",
  INLINE: "inline",
  DETACHED: "detached",
  BUBBLE: "bubble",
};

const MODE_ICONS = {
  [CHAT_MODES.DOCKED]: Pin,
  [CHAT_MODES.FLOATING]: ExternalLink,
  [CHAT_MODES.BOTTOM]: ArrowDown,
  [CHAT_MODES.INLINE]: MessageSquare,
  [CHAT_MODES.DETACHED]: ExternalLink,
  [CHAT_MODES.BUBBLE]: Sparkles,
};

const MODE_LABELS = {
  [CHAT_MODES.DOCKED]: "Docked Right",
  [CHAT_MODES.FLOATING]: "Floating",
  [CHAT_MODES.BOTTOM]: "Bottom",
  [CHAT_MODES.INLINE]: "Inline",
  [CHAT_MODES.DETACHED]: "Detached Window",
  [CHAT_MODES.BUBBLE]: "AI Bubble",
};

export default function ChatPanel({ config = {} }) {
  const { floatPanel, dockPanel } = usePanelStore();
  const [chatMode, setChatMode] = useState(config.mode || CHAT_MODES.DOCKED);
  const [showModeMenu, setShowModeMenu] = useState(false);
  const [bubbleExpanded, setBubbleExpanded] = useState(false);

  // Persist chat mode
  useEffect(() => {
    localStorage.setItem("devos_chat_mode", chatMode);
  }, [chatMode]);

  // Load persisted mode on mount
  useEffect(() => {
    const saved = localStorage.getItem("devos_chat_mode");
    if (saved && Object.values(CHAT_MODES).includes(saved)) {
      setChatMode(saved);
    }
  }, []);

  // AI Bubble mode — collapsed icon that expands on click
  if (chatMode === CHAT_MODES.BUBBLE) {
    return (
      <>
        {bubbleExpanded && (
          <div className="devos-chat-bubble-panel">
            <div className="devos-chat-bubble-header">
              <span>AI Chat</span>
              <button onClick={() => setBubbleExpanded(false)} aria-label="Collapse chat">
                <ChevronUp size={14} />
              </button>
            </div>
            <ChatContent mode={chatMode} />
          </div>
        )}
        {!bubbleExpanded && (
          <button
            className="devos-chat-bubble"
            onClick={() => setBubbleExpanded(true)}
            aria-label="Open AI chat"
            title="AI Chat"
          >
            <Sparkles size={20} />
          </button>
        )}
      </>
    );
  }

  // Detached mode — open in new window
  if (chatMode === CHAT_MODES.DETACHED) {
    return (
      <div className="devos-chat-detached">
        <p className="text-xs" style={{ color: "var(--text-3)" }}>
          Chat is detached in a separate window.
        </p>
        <button
          onClick={() => {
            const features = "width=420,height=600,menubar=no,toolbar=no,location=no";
            window.open(window.location.href + "#chat-detached", "devos-chat", features);
            setChatMode(CHAT_MODES.DOCKED);
          }}
          className="devos-chat-detach-btn"
        >
          <ExternalLink size={12} /> Open Chat Window
        </button>
      </div>
    );
  }

  // Standard chat panel (docked, floating, bottom, inline)
  const containerClass =
    "devos-chat-panel devos-chat-" + chatMode;

  return (
    <div className={containerClass}>
      <ChatContent
        mode={chatMode}
        showModeMenu={showModeMenu}
        setShowModeMenu={setShowModeMenu}
        chatMode={chatMode}
        setChatMode={setChatMode}
        floatPanel={floatPanel}
        dockPanel={dockPanel}
      />
    </div>
  );
}

/**
 * Mode switcher menu.
 */
function ChatModeMenu({ chatMode, setChatMode, setShowModeMenu }) {
  return (
    <div className="devos-dropdown devos-chat-mode-menu" role="menu">
      {Object.values(CHAT_MODES).map((mode) => {
        const Icon = MODE_ICONS[mode];
        return (
          <button
            key={mode}
            onClick={() => {
              setChatMode(mode);
              setShowModeMenu(false);
              // Sync with panel store
              const ps = usePanelStore.getState();
              const chatPanel = ps.panels.find((p) => p.type === "chat");
              if (chatPanel) {
                if (mode === CHAT_MODES.FLOATING) {
                  ps.floatPanel(chatPanel.id, { x: 200, y: 150 });
                } else if (mode === CHAT_MODES.DOCKED) {
                  ps.dockPanel(chatPanel.id, "right");
                } else if (mode === CHAT_MODES.BOTTOM) {
                  ps.dockPanel(chatPanel.id, "bottom");
                }
                ps.updatePanelConfig(chatPanel.id, { mode });
              }
            }}
            className={"devos-dropdown-item" + (chatMode === mode ? " active" : "")}
            role="menuitem"
          >
            <Icon size={12} />
            {MODE_LABELS[mode]}
          </button>
        );
      })}
    </div>
  );
}

/**
 * Chat content — the actual chat interface.
 * Reuses ChatSidebar logic but in a flexible container.
 */
function ChatContent({ mode, showModeMenu, setShowModeMenu, chatMode, setChatMode }) {
  return (
    <>
      <div className="devos-chat-toolbar">
        <div className="devos-chat-toolbar-title">
          <MessageSquare size={13} />
          <span>AI Chat</span>
        </div>
        <div className="devos-chat-toolbar-actions" style={{ position: "relative" }}>
          <button
            onClick={() => setShowModeMenu && setShowModeMenu(!showModeMenu)}
            className="devos-chat-mode-btn"
            aria-label="Change chat mode"
            title={"Mode: " + MODE_LABELS[chatMode]}
          >
            <Sparkles size={12} />
          </button>
          {showModeMenu && chatMode && setChatMode && (
            <ChatModeMenu
              chatMode={chatMode}
              setChatMode={setChatMode}
              setShowModeMenu={setShowModeMenu}
            />
          )}
        </div>
      </div>
      <div className="devos-chat-content">
        <Suspense fallback={<div className="text-xs text-slate-400">Loading chat...</div>}>
          <ChatSidebar />
        </Suspense>
      </div>
    </>
  );
}
