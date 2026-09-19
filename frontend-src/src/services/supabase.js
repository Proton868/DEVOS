/**
 * Browser Supabase client — publishable/anon key only.
 * Never import or embed SUPABASE_SERVICE_ROLE_KEY or JWT secrets here.
 */
import { createClient } from "@supabase/supabase-js";

let _client = null;
let _publicAppUrl = "";
let _initPromise = null;

function clientFrom(url, anon) {
  if (!url || !anon) return null;
  return createClient(url, anon, {
    auth: {
      persistSession: true,
      autoRefreshToken: true,
      detectSessionInUrl: true,
      storageKey: "devos-auth",
    },
  });
}

/** Build-time env (CRA) when present. */
function envClient() {
  const url = process.env.REACT_APP_SUPABASE_URL || "";
  const anon = process.env.REACT_APP_SUPABASE_ANON_KEY || "";
  return clientFrom(url, anon);
}

_client = envClient();

/**
 * Ensure client is ready. Fetches /api/auth/public-config when build-time
 * env is missing so production can inject anon key without a secret rebuild.
 */
export async function ensureSupabase() {
  if (_client) return _client;
  if (_initPromise) return _initPromise;
  _initPromise = (async () => {
    try {
      const r = await fetch("/api/auth/public-config", { credentials: "same-origin" });
      if (!r.ok) return null;
      const cfg = await r.json();
      if (cfg.public_app_url) {
        _publicAppUrl = String(cfg.public_app_url).replace(/\/$/, "");
      }
      if (cfg.supabase_url && cfg.supabase_anon_key) {
        _client = clientFrom(cfg.supabase_url, cfg.supabase_anon_key);
      }
      return _client;
    } catch {
      return null;
    } finally {
      _initPromise = null;
    }
  })();
  return _initPromise;
}

/**
 * Live singleton accessor. Always returns current _client (may be null until
 * ensureSupabase() completes). Never capture a stale snapshot at module load.
 */
export function getSupabaseClient() {
  return _client;
}

/**
 * Auth token resolution: Supabase session is authoritative when present.
 * Only fall back to localStorage devos_token when no active Supabase session.
 */
export async function getToken() {
  const client = (await ensureSupabase()) || _client;
  if (client) {
    try {
      const {
        data: { session },
      } = await client.auth.getSession();
      if (session?.access_token) {
        return session.access_token;
      }
    } catch {
      // fall through to local DevOS token
    }
  }
  try {
    return localStorage.getItem("devos_token") || null;
  } catch {
    return null;
  }
}

export async function signInWithPassword(email, password) {
  const client = (await ensureSupabase()) || _client;
  if (!client) return { data: null, error: new Error("Supabase Auth is not configured") };
  return client.auth.signInWithPassword({ email: email.trim(), password });
}

/**
 * Email/password registration via Supabase Auth (anon key only).
 * May return a session immediately, or user without session when email
 * confirmation is required — callers must not invent success.
 */
export async function signUpWithPassword(email, password) {
  const client = (await ensureSupabase()) || _client;
  if (!client) return { data: null, error: new Error("Supabase Auth is not configured") };
  const origin =
    (_publicAppUrl && _publicAppUrl.replace(/\/$/, "")) ||
    (typeof window !== "undefined" ? window.location.origin : "");
  const path =
    typeof window !== "undefined" ? window.location.pathname || "/" : "/";
  const emailRedirectTo = origin ? `${origin}${path}` : undefined;
  return client.auth.signUp({
    email: email.trim(),
    password,
    options: emailRedirectTo ? { emailRedirectTo } : {},
  });
}


export async function signInWithGoogle() {
  const client = (await ensureSupabase()) || _client;
  if (!client) return { error: new Error("Supabase not configured") };
  const origin =
    (_publicAppUrl && _publicAppUrl.replace(/\/$/, "")) ||
    (typeof window !== "undefined" ? window.location.origin : "");
  const path =
    typeof window !== "undefined" ? window.location.pathname || "/" : "/";
  const redirectTo = origin ? `${origin}${path}` : undefined;
  return client.auth.signInWithOAuth({
    provider: "google",
    options: redirectTo ? { redirectTo } : {},
  });
}

export async function signInWithPhone(phone) {
  const client = (await ensureSupabase()) || _client;
  if (!client) return { error: new Error("Supabase not configured") };
  return client.auth.signInWithOtp({ phone });
}

export async function verifyPhoneOtp(phone, token) {
  const client = (await ensureSupabase()) || _client;
  if (!client) return { error: new Error("Supabase not configured") };
  return client.auth.verifyOtp({ phone, token, type: "sms" });
}

export async function signOutSupabase() {
  const client = (await ensureSupabase()) || getSupabaseClient();
  if (!client) return { error: null };
  // Local scope only — do not attempt global/server-wide sign-out from the browser.
  return client.auth.signOut({ scope: "local" });
}

export async function getSession() {
  const client = (await ensureSupabase()) || _client;
  if (!client) return null;
  const { data } = await client.auth.getSession();
  return data?.session || null;
}
