"use client";
import { useState } from "react";

export function Counter() {
  const [n, setN] = useState(0);
  return (
    <button type="button" data-testid="counter" onClick={() => setN((x) => x + 1)}>
      Count: {n}
    </button>
  );
}
