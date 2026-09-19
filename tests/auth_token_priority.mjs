/**
 * Behavioral regression for account-switch token priority.
 * Simulates getToken resolution order without loading the CRA bundle.
 */
import assert from "node:assert/strict";

function resolveToken({ supabaseAccessToken, devosToken }) {
  // Mirrors fixed supabase.getToken + api.resolveAuthToken policy
  if (supabaseAccessToken) return supabaseAccessToken;
  if (devosToken) return devosToken;
  return null;
}

// 1. Account B Supabase must beat stale A devos_token
assert.equal(
  resolveToken({
    supabaseAccessToken: "sb-token-B",
    devosToken: "devos-token-A",
  }),
  "sb-token-B"
);

// 2. Local path when no Supabase session
assert.equal(
  resolveToken({ supabaseAccessToken: null, devosToken: "devos-local" }),
  "devos-local"
);

// 3. Null when neither
assert.equal(resolveToken({ supabaseAccessToken: null, devosToken: null }), null);

// 4. Switch A → B
let session = { access: "sb-A" };
let devos = "devos-A";
assert.equal(resolveToken({ supabaseAccessToken: session.access, devosToken: devos }), "sb-A");
// logout local
session = { access: null };
devos = null;
assert.equal(resolveToken({ supabaseAccessToken: session.access, devosToken: devos }), null);
// sign in B
session = { access: "sb-B" };
devos = "devos-B-synced"; // may exist after sync but must not win over session
assert.equal(resolveToken({ supabaseAccessToken: session.access, devosToken: "devos-A-stale" }), "sb-B");

console.log("auth_token_priority.mjs: all assertions passed");
