import React from "react";
import { X, Columns2 } from "lucide-react";
import useOsStore from "../store/osStore";

export default function IdeTabBar() {
  const tabs = useOsStore((s) => s.ideWorkspace?.tabs || []);
  const activePath = useOsStore((s) => s.ideWorkspace?.activePath);
  const splitPath = useOsStore((s) => s.ideWorkspace?.splitPath);
  const setIdeActivePath = useOsStore((s) => s.setIdeActivePath);
  const closeIdeTab = useOsStore((s) => s.closeIdeTab);
  const setIdeSplitPath = useOsStore((s) => s.setIdeSplitPath);

  if (!tabs.length) return null;

  return (
    <div className="sp-ide-tabs" role="tablist" aria-label="Open editors">
      <div className="sp-ide-tabs-scroll">
        {tabs.map((t) => (
          <button
            key={t.path}
            type="button"
            role="tab"
            aria-selected={t.path === activePath}
            className={
              "sp-ide-filetab" +
              (t.path === activePath ? " active" : "") +
              (t.modified ? " modified" : "") +
              (t.path === splitPath ? " split" : "")
            }
            onClick={() => setIdeActivePath(t.path)}
            title={t.path}
          >
            <span className="sp-ide-filetab-title">
              {t.modified ? "● " : ""}
              {t.title || t.path.split("/").pop()}
            </span>
            <span
              className="sp-ide-filetab-close"
              role="button"
              tabIndex={0}
              title="Close"
              onClick={(e) => {
                e.stopPropagation();
                closeIdeTab(t.path);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.stopPropagation();
                  closeIdeTab(t.path);
                }
              }}
            >
              <X size={12} />
            </span>
          </button>
        ))}
      </div>
      <button
        type="button"
        className="sp-iconbtn"
        title="Split editor"
        disabled={!activePath}
        onClick={() => activePath && setIdeSplitPath(activePath)}
      >
        <Columns2 size={14} />
      </button>
    </div>
  );
}
