import { createClient } from "@supabase/supabase-js";

const SUPABASE_URL  = process.env.REACT_APP_SUPABASE_URL  || "";
const SUPABASE_ANON = process.env.REACT_APP_SUPABASE_ANON_KEY || "";

export const supabase = SUPABASE_URL && SUPABASE_ANON
  ? createClient(SUPABASE_URL, SUPABASE_ANON, {
      auth: { persistSession: true, autoRefreshToken: true, storageKey: "devos-auth" },
    })
  : null;

export async function getToken() {
  // Prefer the locally-issued DevOS token when present; this is what
  // /api/auth/supabase/exchange issues after verifying a Supabase token.
  const local = localStorage.getItem("devos_token");
  if (local) return local;
  if (!supabase) return null;
  try {
    const { data: { session } } = await supabase.auth.getSession();
    return session?.access_token || null;
  } catch { return null; }
}

export async function signInWithGoogle() {
  if (!supabase) return { error: new Error("Supabase not configured") };
  return supabase.auth.signInWithOAuth({
    provider: "google",
    options: {
      // Redirect back to the same origin/path the app is served from.
      redirectTo: window.location.origin + window.location.pathname,
    },
  });
}

export async function signInWithPhone(phone) {
  if (!supabase) return { error: new Error("Supabase not configured") };
  return supabase.auth.signInWithOtp({ phone });
}

export async function verifyPhoneOtp(phone, token) {
  if (!supabase) return { error: new Error("Supabase not configured") };
  return supabase.auth.verifyOtp({ phone, token, type: "sms" });
}

export async function signOutSupabase() {
  if (!supabase) return { error: null };
  return supabase.auth.signOut();
}
