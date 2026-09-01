import React, { Suspense } from "react";
const FileTree = React.lazy(() => import("../editor/FileTree"));
export default function FileTreeWrapper(props) {
  return (
    <Suspense fallback={<div className="text-slate-400 text-xs p-4">Loading files...</div>}>
      <FileTree {...props} />
    </Suspense>
  );
}
