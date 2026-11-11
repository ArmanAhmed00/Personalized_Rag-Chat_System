"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";

import { api, ApiError, hasToken, setToken } from "@/lib/api";
import { useEscape } from "@/lib/hooks";
import { S } from "@/lib/strings";
import type { Config } from "@/lib/types";

import { Icon } from "./Icon";
import { Logo } from "./Logo";
import { FeedbackProvider, Spinner } from "./ui";

interface ConfigState {
  config: Config | null;
  reload: () => Promise<void>;
}

const ConfigContext = createContext<ConfigState>({ config: null, reload: async () => undefined });

export function useConfig(): ConfigState {
  return useContext(ConfigContext);
}

interface NavItem {
  href: string;
  label: string;
  icon: string;
}

const WORKSPACE: NavItem[] = [
  { href: "/", label: S.nav.home, icon: "home" },
  { href: "/library/", label: S.nav.library, icon: "library" },
  { href: "/search/", label: S.nav.search, icon: "search" },
  { href: "/chat/", label: S.nav.chat, icon: "chat" },
  { href: "/analysis/", label: S.nav.analysis, icon: "analysis" },
  { href: "/preferences/", label: S.nav.preferences, icon: "preferences" },
  { href: "/settings/", label: S.nav.settings, icon: "settings" },
];

const SYSTEM: NavItem[] = [
  { href: "/system/", label: S.nav.overview, icon: "overview" },
  { href: "/system/metrics/", label: S.nav.metrics, icon: "metrics" },
  { href: "/system/traces/", label: S.nav.traces, icon: "traces" },
  { href: "/system/processing/", label: S.nav.processing, icon: "processing" },
  { href: "/system/evaluations/", label: S.nav.evaluations, icon: "evaluations" },
  { href: "/system/activity/", label: S.nav.activity, icon: "activity" },
  { href: "/system/data/", label: S.nav.data, icon: "data" },
];

const HEALTH_EVERY = 60_000;
const OFFLINE_RETRY = 5_000;

function normalise(path: string | null): string {
  if (!path) return "/";
  return path.endsWith("/") ? path : `${path}/`;
}

let setupRedirected = false;

export function Shell({ children }: { children: ReactNode }) {
  const pathname = normalise(usePathname());
  const router = useRouter();
  const [config, setConfig] = useState<Config | null>(null);
  const [offline, setOffline] = useState(false);
  const [needToken, setNeedToken] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const reload = useCallback(async () => {
    try {
      const value = await api<Config>("/api/config");
      setConfig(value);
      setOffline(false);
      setNeedToken(value.token_required && !hasToken());
    } catch (err) {
      if (err instanceof ApiError && err.status === 0) setOffline(true);
    }
  }, []);

  // First load, then a health check every minute (every few seconds while offline).
  useEffect(() => {
    let stopped = false;
    const check = async () => {
      let ok = false;
      try {
        await api("/api/health");
        ok = true;
      } catch {
        ok = false;
      }
      if (stopped) return;
      if (ok && (offline || config === null)) await reload();
      setOffline(!ok);
      timer.current = setTimeout(check, ok ? HEALTH_EVERY : OFFLINE_RETRY);
    };
    void check();
    return () => {
      stopped = true;
      if (timer.current) clearTimeout(timer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [offline]);

  useEffect(() => {
    const ask = () => setNeedToken(true);
    window.addEventListener("outlawz:token", ask);
    window.addEventListener("outlawz:config", reload);
    return () => {
      window.removeEventListener("outlawz:token", ask);
      window.removeEventListener("outlawz:config", reload);
    };
  }, [reload]);

  // First run: go to setup once, from wherever the app was opened.
  useEffect(() => {
    if (config?.needs_setup && !setupRedirected && pathname !== "/setup/" && !needToken) {
      setupRedirected = true;
      router.replace("/setup/");
    }
  }, [config, pathname, router, needToken]);

  useEffect(() => setMenuOpen(false), [pathname]);
  useEscape(() => setMenuOpen(false), menuOpen);

  const isCurrent = (href: string) => (href === "/" || href === "/system/" ? pathname === href : pathname.startsWith(href));

  const nav = (title: string, items: NavItem[]) => (
    <nav className="nav-group" aria-label={title}>
      <h2>{title}</h2>
      <ul>
        {items.map((item) => (
          <li key={item.href}>
            <Link className="nav-link" href={item.href} aria-current={isCurrent(item.href) ? "page" : undefined}>
              <Icon name={item.icon} />
              {item.label}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );

  return (
    <ConfigContext.Provider value={{ config, reload }}>
      <FeedbackProvider>
        <a href="#content" className="skip-link">
          {S.app.skip}
        </a>
        <div className="shell">
          <aside className={`sidebar${menuOpen ? " open" : ""}`} id="sidebar">
            <Link href="/" className="brand">
              <Logo size={30} />
              {S.app.name}
            </Link>
            {nav(S.nav.workspace, WORKSPACE)}
            {nav(S.nav.system, SYSTEM)}
            <footer className="sidebar-footer">
              {config ? (
                <>
                  <div>
                    <div>{S.footer.library}</div>
                    <div className="path" title={config.library}>
                      {config.library}
                    </div>
                  </div>
                  <div>
                    <div>{S.footer.model}</div>
                    <div className="row" style={{ gap: "0.4rem" }}>
                      <span className="mono">{config.model.llm_model || config.model.llm}</span>
                      <span
                        className={`badge ${config.cloud ? "warn" : "good"}`}
                        title={config.cloud ? S.footer.cloudHint : S.footer.localHint}
                      >
                        {config.cloud ? S.footer.cloud : S.footer.local}
                      </span>
                    </div>
                  </div>
                  <div className="xsmall">v{config.version}</div>
                </>
              ) : null}
            </footer>
          </aside>
          {menuOpen ? <div className="scrim" onClick={() => setMenuOpen(false)} aria-hidden="true" /> : null}
          <div className="main">
            <div className="topbar">
              <Link href="/" className="brand">
                <Logo size={26} />
                {S.app.name}
              </Link>
              <button
                type="button"
                className="btn ghost icon"
                aria-label={menuOpen ? S.app.closeMenu : S.app.menu}
                aria-expanded={menuOpen}
                aria-controls="sidebar"
                onClick={() => setMenuOpen((v) => !v)}
              >
                <Icon name={menuOpen ? "close" : "menu"} size={20} />
              </button>
            </div>
            {offline ? (
              <div className="banner danger" role="alert">
                <Icon name="warning" />
                <div>
                  <strong>{S.banners.offline}</strong> {S.banners.offlineHint}
                </div>
              </div>
            ) : null}
            {config?.cloud ? (
              <div className="banner cloud" role="note">
                <Icon name="warning" />
                <div>
                  <strong>{S.banners.cloud}</strong> {S.banners.cloudHint}
                </div>
              </div>
            ) : null}
            {config?.needs_setup && pathname !== "/setup/" ? (
              <div className="banner cloud" role="note">
                <div>
                  <strong>{S.banners.setup}</strong> <Link href="/setup/">{S.banners.setupLink}</Link>
                </div>
              </div>
            ) : null}
            <main id="content" tabIndex={-1} className={`content${pathname.startsWith("/chat/") ? " flush" : ""}`}>
              {needToken ? (
                <TokenGate
                  onAccepted={() => {
                    setNeedToken(false);
                    void reload();
                  }}
                />
              ) : config === null && !offline ? (
                <Spinner />
              ) : (
                children
              )}
            </main>
          </div>
        </div>
      </FeedbackProvider>
    </ConfigContext.Provider>
  );
}

function TokenGate({ onAccepted }: { onAccepted: () => void }) {
  const [value, setValue] = useState("");
  const [wrong, setWrong] = useState(false);
  const [busy, setBusy] = useState(false);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setToken(value);
    try {
      await api("/api/documents");
      setWrong(false);
      onAccepted();
    } catch {
      setWrong(true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="card" style={{ maxWidth: "28rem", margin: "4rem auto" }}>
      <form className="stack" onSubmit={submit}>
        <div className="row">
          <Icon name="lock" size={22} />
          <h1 style={{ margin: 0, fontSize: "var(--text-xl)" }}>{S.token.title}</h1>
        </div>
        <p className="muted">{S.token.body}</p>
        <label className="field">
          <span>{S.token.label}</span>
          <input
            className="input mono"
            type="password"
            autoComplete="off"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            autoFocus
            required
            aria-invalid={wrong}
            aria-describedby={wrong ? "token-wrong" : undefined}
          />
        </label>
        {wrong ? (
          <p id="token-wrong" className="note danger" role="alert">
            {S.token.wrong}
          </p>
        ) : null}
        <div className="row end">
          <button className="btn primary" type="submit" disabled={busy || !value.trim()}>
            {S.token.submit}
          </button>
        </div>
      </form>
    </div>
  );
}
