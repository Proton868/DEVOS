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

/** Sync accessor — may be null until ensureSupabase() resolves. */
export const supabase = _client;

export async function getToken() {
  const local = localStorage.getItem("devos_token");
  if (local) return local;
  const client = (await ensureSupabase()) || _client;
  if (!client) return null;
  try {
    const {
      data: { session },
    } = await client.auth.getSession();
    return session?.access_token || null;
  } catch {
    return null;
  }
}

export async function signInWithPassword(email, password) {
  const client = (await ensureSupabase()) || _client;
  if (!client) return { data: null, error: new Error("Supabase Auth is not configured") };
  return client.auth.signInWithPassword({ email: email.trim(), password });
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
  const client = (await ensureSupabase()) || _client;
  if (!client) return { error: null };
  return client.auth.signOut();
}

export async function getSession() {
  const client = (await ensureSupabase()) || _client;
  if (!client) return null;
  const { data } = await client.auth.getSession();
  return data?.session || null;
}
