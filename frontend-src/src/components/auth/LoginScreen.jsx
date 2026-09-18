import React, { useState, useRef, useEffect, useCallback } from "react";
import { Loader, AlertCircle, ArrowRight, Shield, Smartphone, Mail } from "lucide-react";
import useStore from "../../store/useStore";
import { login, syncSupabaseSession } from "../../services/api";
import {
  ensureSupabase,
  signInWithPassword as supabaseSignIn,
  signUpWithPassword as supabaseSignUp,
  signInWithGoogle,
  signInWithPhone,
  verifyPhoneOtp,
} from "../../services/supabase";
import MenorahLogo from "../../os/MenorahLogo";
import "./LoginScreen.css";

/* ── Animated particle canvas ─────────────────────────────── */
function ParticleField() {
  const canvasRef = useRef(null);
  const rafRef = useRef(null);

  const init = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    let w = (canvas.width = window.innerWidth);
    let h = (canvas.height = window.innerHeight);

    const particles = Array.from({ length: 60 }, () => ({
      x: Math.random() * w,
      y: Math.random() * h,
      vx: (Math.random() - 0.5) * 0.4,
      vy: (Math.random() - 0.5) * 0.4,
      r: Math.random() * 1.8 + 0.4,
      a: Math.random() * 0.5 + 0.2,
    }));

    const draw = () => {
      ctx.clearRect(0, 0, w, h);
      for (const p of particles) {
        p.x += p.vx;
        p.y += p.vy;
        if (p.x < 0) p.x = w;
        if (p.x > w) p.x = 0;
        if (p.y < 0) p.y = h;
        if (p.y > h) p.y = 0;
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(99, 179, 237, ${p.a})`;
        ctx.fill();
        // draw connections
        for (const q of particles) {
          const dx = p.x - q.x;
          const dy = p.y - q.y;
          const dist = Math.sqrt(dx * dx + dy * dy);
          if (dist < 120) {
            ctx.beginPath();
            ctx.moveTo(p.x, p.y);
            ctx.lineTo(q.x, q.y);
            ctx.strokeStyle = `rgba(99, 179, 237, ${0.08 * (1 - dist / 120)})`;
            ctx.lineWidth = 0.5;
            ctx.stroke();
          }
        }
      }
      rafRef.current = requestAnimationFrame(draw);
    };

    const onResize = () => {
      w = canvas.width = window.innerWidth;
      h = canvas.height = window.innerHeight;
    };
    window.addEventListener("resize", onResize);
    draw();

    return () => {
      cancelAnimationFrame(rafRef.current);
      window.removeEventListener("resize", onResize);
    };
  }, []);

  useEffect(() => {
    const cleanup = init();
    return () => cleanup?.();
  }, [init]);

  return <canvas ref={canvasRef} className="login-particle-canvas" aria-hidden="true" />;
}

const MODE = {
  PASSWORD: "password",
  PHONE: "phone",
};

/** Password panel: sign-in vs create-account (Supabase email path only). */
const AUTH_PANEL = {
  SIGN_IN: "sign_in",
  SIGN_UP: "sign_up",
};

function isValidEmailAddress(value) {
  const v = (value || "").trim();
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v);
}


export default function LoginScreen() {
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [phone, setPhone] = useState("");
  const [otp, setOtp] = useState("");
  const [mode, setMode] = useState(MODE.PASSWORD);
  const [otpSent, setOtpSent] = useState(false);
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [supabaseReady, setSupabaseReady] = useState(false);
  const [localLoginAvailable, setLocalLoginAvailable] = useState(true);
  const [useLocalLogin, setUseLocalLogin] = useState(false);
  const [authPanel, setAuthPanel] = useState(AUTH_PANEL.SIGN_IN);
  const [infoMessage, setInfoMessage] = useState(null);
  const identifierRef = useRef(null);
  const phoneRef = useRef(null);
  const setUser = useStore((s) => s.setUser);

  useEffect(() => {
    identifierRef.current?.focus();
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const client = await ensureSupabase();
        if (cancelled) return;
        setSupabaseReady(!!client);
        const r = await fetch("/api/auth/public-config");
        if (r.ok) {
          const cfg = await r.json();
          if (!cancelled) {
            setLocalLoginAvailable(!!cfg.local_login_available);
            if (cfg.supabase_configured) setSupabaseReady(true);
          }
        }
      } catch (_) {
        if (!cancelled) setSupabaseReady(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (mode === MODE.PHONE) phoneRef.current?.focus();
  }, [mode]);

  async function handleSupabaseSession() {
    const synced = await syncSupabaseSession();
    if (synced?.token) localStorage.setItem("devos_token", synced.token);
    setUser(synced.user || synced);
  }

  async function handleGoogleSignIn() {
    if (!supabaseReady) {
      setError("Supabase is not configured.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const { error: signInErr } = await signInWithGoogle();
      if (signInErr) throw signInErr;
      // On success, the redirect handler in App.jsx will pick up the session.
    } catch (err) {
      setError(err.message || "Google sign-in failed.");
      setSubmitting(false);
    }
  }

  async function handleSendPhoneOtp(e) {
    e.preventDefault();
    if (!phone.trim()) {
      setError("Enter a phone number.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const { error: otpErr } = await signInWithPhone(phone.trim());
      if (otpErr) throw otpErr;
      setOtpSent(true);
    } catch (err) {
      setError(err.message || "Failed to send OTP.");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleVerifyPhoneOtp(e) {
    e.preventDefault();
    if (!otp.trim()) {
      setError("Enter the verification code.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const { error: verifyErr } = await verifyPhoneOtp(phone.trim(), otp.trim());
      if (verifyErr) throw verifyErr;
      const user = await syncSupabaseSession();
      if (user?.token) localStorage.setItem("devos_token", user.token);
      setUser(user.user || user);
    } catch (err) {
      setError(err.message || "Invalid verification code.");
      setSubmitting(false);
    }
  }

  async function handlePasswordSubmit(e) {
    e.preventDefault();
    const id = identifier.trim();
    if (!id || !password) {
      setError(
        supabaseReady && !useLocalLogin
          ? "Enter email and password."
          : "Enter both a username and password."
      );
      return;
    }

    // Supabase Auth is the primary user-facing path when configured.
    if (supabaseReady && !useLocalLogin) {
      if (!isValidEmailAddress(id)) {
        setError(
          authPanel === AUTH_PANEL.SIGN_UP
            ? "Enter a valid email address to create an account."
            : "Enter a valid email address for Supabase sign-in."
        );
        return;
      }
      if (authPanel === AUTH_PANEL.SIGN_UP && password.length < 6) {
        setError("Password must be at least 6 characters.");
        return;
      }
      setSubmitting(true);
      setError(null);
      setInfoMessage(null);
      try {
        if (authPanel === AUTH_PANEL.SIGN_UP) {
          const { data, error: signUpErr } = await supabaseSignUp(id, password);
          if (signUpErr) {
            setError(signUpErr.message || "Could not create account.");
            setSubmitting(false);
            return;
          }
          // Email confirmation required: user object without session
          if (data?.user && !data?.session) {
            setInfoMessage(
              "Account created. Check your email to confirm the address, then sign in here. " +
              "Until you confirm, sign-in will not succeed."
            );
            setAuthPanel(AUTH_PANEL.SIGN_IN);
            setPassword("");
            setSubmitting(false);
            return;
          }
          if (!data?.session) {
            setError("Sign-up did not return a session. Confirm your email if required, then sign in.");
            setSubmitting(false);
            return;
          }
          // Immediate session (confirmations disabled in Supabase project)
          const user = await syncSupabaseSession();
          if (user?.token) {
            try { localStorage.setItem("devos_token", user.token); } catch (_) {}
          }
          const profile = user?.user || user;
          if (!profile || !(profile.id || profile.username)) {
            setError("Account created but DevOS session was incomplete. Try signing in.");
            setSubmitting(false);
            return;
          }
          setUser(profile);
          return;
        }

        const { data, error: supaErr } = await supabaseSignIn(id, password);
        if (supaErr) {
          // Do not fall back to local login — keep this a Supabase failure.
          const msg = (supaErr.message || "").toLowerCase();
          if (msg.includes("invalid login credentials") || msg.includes("invalid email or password")) {
            setError(
              "Invalid Supabase email or password. " +
                "Use the email registered in Supabase Auth (not a local DevOS username)."
            );
          } else if (msg.includes("email not confirmed")) {
            setError("This email has not been confirmed in Supabase Auth yet.");
          } else {
            setError(supaErr.message || "Supabase sign-in failed.");
          }
          setSubmitting(false);
          return;
        }
        if (!data?.session) {
          setError("Sign-in succeeded but no session was returned.");
          setSubmitting(false);
          return;
        }
        const user = await syncSupabaseSession();
        // syncSupabaseSession already persists DevOS JWT via setToken()
        if (user?.token) {
          try { localStorage.setItem("devos_token", user.token); } catch (_) {}
        }
        const profile = user?.user || user;
        if (!profile || !(profile.id || profile.username)) {
          setError("Signed in with Supabase but DevOS session was incomplete.");
          setSubmitting(false);
          return;
        }
        setUser(profile);
        return;
      } catch (err) {
        setError(err.message || "Supabase sign-in failed.");
        setSubmitting(false);
        return;
      }
    }

    // Local DevOS username/password only when explicitly selected or Supabase absent.
    setSubmitting(true);
    setError(null);
    try {
      const user = await login(id, password);
      setUser(user.user || user);
    } catch (err) {
      setError(err.message || "Login failed. Please try again.");
      setSubmitting(false);
    }
  }


  const supabaseConfigured = supabaseReady;
  const isSupabasePasswordMode = Boolean(supabaseConfigured && !useLocalLogin);



  return (
    <div className="login-screen">
      <ParticleField />
      {/* Subtle ambient background */}
      <div className="login-ambient">
        <div className="login-ambient-orb" />
        <div className="login-ambient-orb secondary" />
        <div className="login-ambient-orb tertiary" />
      </div>

      <form
        className="login-card"
        onSubmit={mode === MODE.PHONE ? (otpSent ? handleVerifyPhoneOtp : handleSendPhoneOtp) : handlePasswordSubmit}
        aria-label={authPanel === AUTH_PANEL.SIGN_UP && isSupabasePasswordMode ? "Create a DevOS account" : "Sign in to DevOS"}
      >
        <div className="login-brand">
          <div className="login-mark">
            <MenorahLogo size={26} id="login" />
          </div>
          <div className="login-wordmark">
            <span className="login-wordmark-name">DevOS</span>
            <span className="login-wordmark-version">v4</span>
          </div>
          <div className="login-carai" style={{ marginTop: 10, display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }}>
            <img src="/static/carai-agency-logo.png" alt="CARAI Agency" style={{ height: 42, width: "auto", filter: "drop-shadow(0 0 8px rgba(212,175,55,0.35))" }} />
            <span style={{ fontSize: 11, color: "rgba(212,175,55,0.75)", letterSpacing: "0.04em" }}>Where Caribbean spirit meets intelligent design.</span>
          </div>
        </div>

        <p className="login-subtitle">
          Your autonomous engineering team,<br />powered by 230+ specialized AI agents
        </p>

        {supabaseConfigured && (
          <button
            type="button"
            className="login-oauth-btn login-oauth-btn-disabled"
            disabled
            aria-label="Sign in with Google (unavailable)"
            title="Google sign-in is not available yet. Use email and password."
          >
            <svg className="login-google-icon" viewBox="0 0 24 24" aria-hidden="true">
              <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" />
              <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" />
              <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" />
              <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" />
            </svg>
            Google unavailable
          </button>
        )}

        {supabaseConfigured && (
          <div className="login-divider">
            <span>or</span>
          </div>
        )}

        <div className="login-mode-tabs" role="tablist" aria-label="Sign-in method">
          <button
            type="button"
            role="tab"
            aria-selected={mode === MODE.PASSWORD}
            className={mode === MODE.PASSWORD ? "active" : ""}
            onClick={() => { setMode(MODE.PASSWORD); setError(null); }}
          >
            <Mail size={13} /> Password
          </button>
          {supabaseConfigured && (
            <button
              type="button"
              role="tab"
              aria-selected={mode === MODE.PHONE}
              className={mode === MODE.PHONE ? "active" : ""}
              onClick={() => { setMode(MODE.PHONE); setError(null); setOtpSent(false); setOtp(""); }}
            >
              <Smartphone size={13} /> Phone
            </button>
          )}
        </div>

        <div className="login-fields">
          {mode === MODE.PASSWORD ? (
            <>
              <label className="login-field">
                <span className="login-field-label">
                  {isSupabasePasswordMode ? "Email" : "Username"}
                </span>
                <input
                  ref={identifierRef}
                  id="login-identifier"
                  type={isSupabasePasswordMode ? "email" : "text"}
                  name={isSupabasePasswordMode ? "email" : "username"}
                  autoComplete={isSupabasePasswordMode ? "email" : "username"}
                  inputMode={isSupabasePasswordMode ? "email" : "text"}
                  value={identifier}
                  onChange={(e) => setIdentifier(e.target.value)}
                  disabled={submitting}
                  placeholder={isSupabasePasswordMode ? "you@example.com" : "admin"}
                  required
                />
              </label>

              <label className="login-field">
                <span className="login-field-label">Password</span>
                <input
                  type="password"
                  name="password"
                  autoComplete={authPanel === AUTH_PANEL.SIGN_UP ? "new-password" : "current-password"}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  disabled={submitting}
                  placeholder="••••••••"
                  required
                />
              </label>
            </>
          ) : (
            <>
              <label className="login-field">
                <span className="login-field-label">Phone number</span>
                <input
                  ref={phoneRef}
                  type="tel"
                  name="phone"
                  autoComplete="tel"
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                  disabled={submitting || otpSent}
                  placeholder="+1 555 123 4567"
                  required
                />
              </label>

              {otpSent && (
                <label className="login-field">
                  <span className="login-field-label">Verification code</span>
                  <input
                    type="text"
                    name="otp"
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    value={otp}
                    onChange={(e) => setOtp(e.target.value)}
                    disabled={submitting}
                    placeholder="123456"
                    required
                  />
                </label>
              )}
            </>
          )}
        </div>

        {error && (
          <div className="login-error" role="alert" aria-live="polite">
            <AlertCircle size={14} />
            <span>{error}</span>
          </div>
        )}

        {infoMessage && (
          <div className="login-info" role="status" aria-live="polite">
            <Mail size={14} />
            <span>{infoMessage}</span>
          </div>
        )}


        {supabaseConfigured && localLoginAvailable && (
          <button
            type="button"
            className="login-local-toggle"
            onClick={() => { setUseLocalLogin((v) => !v); setError(null); }}
            disabled={submitting}
          >
            {useLocalLogin ? "Use Supabase email sign-in" : "Use local DevOS account"}
          </button>
        )}

        <button type="submit" className="login-submit" disabled={submitting}>
          {submitting ? (
            <><Loader size={15} className="spin-slow" /> Authenticating&hellip;</>
          ) : mode === MODE.PHONE && !otpSent ? (
            <>Send code <ArrowRight size={15} /></>
          ) : mode === MODE.PHONE && otpSent ? (
            <>Verify <ArrowRight size={15} /></>
          ) : authPanel === AUTH_PANEL.SIGN_UP && isSupabasePasswordMode ? (
            <>Create account <ArrowRight size={15} /></>
          ) : (
            <>Sign in <ArrowRight size={15} /></>
          )}
        </button>

        {isSupabasePasswordMode && mode === MODE.PASSWORD && (
          <button
            type="button"
            className="login-local-toggle"
            onClick={() => {
              setAuthPanel((p) =>
                p === AUTH_PANEL.SIGN_UP ? AUTH_PANEL.SIGN_IN : AUTH_PANEL.SIGN_UP
              );
              setError(null);
              setInfoMessage(null);
            }}
            disabled={submitting}
          >
            {authPanel === AUTH_PANEL.SIGN_UP
              ? "Already have an account? Sign in"
              : "Create account"}
          </button>
        )}

        <div className="login-footer">
          <Shield size={11} />
          <span>End-to-end encrypted &middot; UCIP-governed &middot; Sandboxed execution</span>
        </div>
      </form>
    </div>
  );
}
