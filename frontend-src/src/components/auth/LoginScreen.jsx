import React, { useState, useRef, useEffect, useCallback } from "react";
import { Sparkles, Loader, AlertCircle, ArrowRight, Shield, Smartphone, Mail } from "lucide-react";
import useStore from "../../store/useStore";
import { login, syncSupabaseSession } from "../../services/api";
import { supabase, signInWithGoogle, signInWithPhone, verifyPhoneOtp } from "../../services/supabase";
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

export default function LoginScreen() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [phone, setPhone] = useState("");
  const [otp, setOtp] = useState("");
  const [mode, setMode] = useState(MODE.PASSWORD);
  const [otpSent, setOtpSent] = useState(false);
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const usernameRef = useRef(null);
  const phoneRef = useRef(null);
  const setUser = useStore((s) => s.setUser);

  useEffect(() => {
    usernameRef.current?.focus();
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
    if (!supabase) {
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
    if (!username.trim() || !password) {
      setError("Enter both a username and password.");
      return;
    }
    setSubmitting(true);
    setError(null);

    if (supabase) {
      try {
        const { data, error: supaErr } = await supabase.auth.signInWithPassword({
          email: username.trim(),
          password,
        });
        if (supaErr) throw supaErr;
        if (data?.session) {
          const user = await syncSupabaseSession();
      if (user?.token) localStorage.setItem("devos_token", user.token);
          setUser(user.user || user);
          return;
        }
      } catch (err) {
        // Fall through to local auth below
      }
    }

    try {
      const user = await login(username.trim(), password);
      setUser(user.user || user);
    } catch (err) {
      setError(err.message || "Login failed. Please try again.");
      setSubmitting(false);
    }
  }

  const supabaseConfigured = !!supabase;

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
        aria-label="Sign in to DevOS"
      >
        <div className="login-brand">
          <div className="login-mark">
            <Sparkles size={22} />
          </div>
          <div className="login-wordmark">
            <span className="login-wordmark-name">DevOS</span>
            <span className="login-wordmark-version">v4</span>
          </div>
        </div>

        <p className="login-subtitle">
          Your autonomous engineering team,<br />powered by 230+ specialized AI agents
        </p>

        {supabaseConfigured && (
          <button
            type="button"
            className="login-oauth-btn"
            onClick={handleGoogleSignIn}
            disabled={submitting}
            aria-label="Sign in with Google"
          >
            <svg className="login-google-icon" viewBox="0 0 24 24" aria-hidden="true">
              <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" />
              <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" />
              <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" />
              <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" />
            </svg>
            Sign in with Google
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
                <span className="login-field-label">Username</span>
                <input
                  ref={usernameRef}
                  type="text"
                  name="username"
                  autoComplete="username"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  disabled={submitting}
                  placeholder="admin"
                  required
                />
              </label>

              <label className="login-field">
                <span className="login-field-label">Password</span>
                <input
                  type="password"
                  name="password"
                  autoComplete="current-password"
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

        <button type="submit" className="login-submit" disabled={submitting}>
          {submitting ? (
            <><Loader size={15} className="spin-slow" /> Authenticating&hellip;</>
          ) : mode === MODE.PHONE && !otpSent ? (
            <>Send code <ArrowRight size={15} /></>
          ) : mode === MODE.PHONE && otpSent ? (
            <>Verify <ArrowRight size={15} /></>
          ) : (
            <>Sign in <ArrowRight size={15} /></>
          )}
        </button>

        <div className="login-footer">
          <Shield size={11} />
          <span>End-to-end encrypted &middot; UCIP-governed &middot; Sandboxed execution</span>
        </div>
      </form>
    </div>
  );
}
