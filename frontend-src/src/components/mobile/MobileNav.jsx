import React from "react";
import { Code, MessageSquare, GitBranch, Bot, Terminal,
         Play, Search, Zap, Settings, Menu } from "lucide-react";
import useStore from "../../store/useStore";

/**
 * MobileNav — shown on screens < 768px
 * Replaces the status bar with a bottom tab bar.
 * Each tab activates a full-screen panel on mobile.
 */
export default function MobileNav() {
  const {
    activeTab: fileTab,
    chatOpen, setChatOpen,
    gitOpen, setGitOpen,
    agentOpen, setAgentOpen,
    terminalOpen, setTerminalOpen,
    flowOpen, setFlowOpen,
    searchOpen, setSearchOpen,
    composerOpen, setComposerOpen,
    mobileMenuOpen, setMobileMenuOpen,
  } = useStore();

  const closeAll = () => {
    setChatOpen(false);
    setGitOpen(false);
    setAgentOpen(false);
    setTerminalOpen(false);
    setFlowOpen(false);
    setSearchOpen(false);
    setComposerOpen(false);
  };

  const toggle = (panel, setter, current) => {
    closeAll();
    if (!current) setter(true);
  };

  const TABS = [
    {
      id: "editor",
      label: "Editor",
      icon: <Code size={18} />,
      active: !chatOpen && !gitOpen && !agentOpen && !terminalOpen && !flowOpen && !searchOpen,
      action: closeAll,
    },
    {
      id: "chat",
      label: "Chat",
      icon: <MessageSquare size={18} />,
      active: chatOpen,
      action: () => toggle("chat", setChatOpen, chatOpen),
    },
    {
      id: "flow",
      label: "Flow",
      icon: <Play size={18} />,
      active: flowOpen,
      action: () => toggle("flow", setFlowOpen, flowOpen),
    },
    {
      id: "git",
      label: "Git",
      icon: <GitBranch size={18} />,
      active: gitOpen,
      action: () => toggle("git", setGitOpen, gitOpen),
    },
    {
      id: "agent",
      label: "Agent",
      icon: <Bot size={18} />,
      active: agentOpen,
      action: () => toggle("agent", setAgentOpen, agentOpen),
    },
  ];

  return (
    <nav className="mobile-nav" role="navigation" aria-label="Main navigation">
      {TABS.map(tab => (
        <button
          key={tab.id}
          className={`mobile-nav-tab ${tab.active ? "active" : ""}`}
          onClick={tab.action}
          aria-label={tab.label}
          aria-pressed={tab.active}
        >
          {tab.icon}
          <span className="mobile-nav-label">{tab.label}</span>
        </button>
      ))}
      <button
        className={`mobile-nav-tab ${mobileMenuOpen ? "active" : ""}`}
        onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
        aria-label="More"
      >
        <Menu size={18} />
        <span className="mobile-nav-label">More</span>
      </button>

      {mobileMenuOpen && (
        <MobileMoreMenu onClose={() => setMobileMenuOpen(false)} />
      )}
    </nav>
  );
}

function MobileMoreMenu({ onClose }) {
  const { setTerminalOpen, setSearchOpen, setComposerOpen, setSettingsOpen } = useStore();
  const items = [
    { label: "Terminal", icon: <Terminal size={15} />, action: () => { setTerminalOpen(true); onClose(); } },
    { label: "Search Files", icon: <Search size={15} />, action: () => { setSearchOpen(true); onClose(); } },
    { label: "Composer", icon: <Zap size={15} />, action: () => { setComposerOpen(true); onClose(); } },
    { label: "Settings", icon: <Settings size={15} />, action: () => { setSettingsOpen(true); onClose(); } },
  ];

  return (
    <div className="mobile-more-menu">
      {items.map(item => (
        <button key={item.label} className="mobile-more-item" onClick={item.action}>
          {item.icon} {item.label}
        </button>
      ))}
      <button className="mobile-more-close" onClick={onClose}>Close</button>
    </div>
  );
}
