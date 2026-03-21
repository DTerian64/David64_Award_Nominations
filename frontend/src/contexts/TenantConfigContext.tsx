/**
 * TenantConfigContext.tsx
 * ───────────────────────
 * Fetches the per-tenant UI config from the backend (/api/tenant/config),
 * applies CSS custom properties to <html> for theming, and sets the i18next
 * language so the rest of the app is already in the right locale when it
 * first renders.
 *
 * "Block rendering" pattern
 * ─────────────────────────
 * The provider renders `null` (a blank screen) until the config fetch
 * completes or times out.  This prevents any English text or wrong-colour
 * flash before the Korean / themed UI paints.  The loading window is short
 * (one authenticated API call) and only happens on first authenticated page
 * load.
 */

import React, {
  createContext,
  useContext,
  useEffect,
  useState,
  useCallback,
  type ReactNode,
} from 'react';
import i18n from '../i18n';
import { getAccessToken } from '../services/api';

// ── Types ──────────────────────────────────────────────────────────────────

export interface TenantTheme {
  primaryColor:      string;  // e.g. "#0d9488"
  primaryHoverColor: string;  // e.g. "#0f766e"
  primaryLightColor: string;  // e.g. "#ccfbf1"
  primaryTextOnDark: string;  // e.g. "#ffffff"
}

export interface TenantConfig {
  locale:   string;        // BCP 47 tag, e.g. "en-US" | "ko-KR"
  currency: string;        // ISO 4217, e.g. "USD" | "KRW"
  theme:    TenantTheme;
}

/** Defaults used for tenant 1 (and any tenant without a Config row). */
const DEFAULT_CONFIG: TenantConfig = {
  locale:   'en-US',
  currency: 'USD',
  theme: {
    primaryColor:      '#4f46e5',   // indigo-600
    primaryHoverColor: '#4338ca',   // indigo-700
    primaryLightColor: '#e0e7ff',   // indigo-100
    primaryTextOnDark: '#ffffff',
  },
};

// ── Context ────────────────────────────────────────────────────────────────

interface TenantConfigContextValue {
  config:        TenantConfig;
  isLoading:     boolean;
  /** Format a monetary amount using the tenant's locale + currency. */
  formatCurrency: (amount: number) => string;
  /** Min/max award amounts (same values, but currency-aware label). */
  minAmount: number;
  maxAmount: number;
}

const TenantConfigContext = createContext<TenantConfigContextValue>({
  config:         DEFAULT_CONFIG,
  isLoading:      true,
  formatCurrency: (n) => `$${n}`,
  minAmount:      50,
  maxAmount:      5000,
});

// ── CSS variable injection ─────────────────────────────────────────────────

function applyTheme(theme: TenantTheme): void {
  const root = document.documentElement;
  root.style.setProperty('--color-primary',        theme.primaryColor);
  root.style.setProperty('--color-primary-hover',  theme.primaryHoverColor);
  root.style.setProperty('--color-primary-light',  theme.primaryLightColor);
  root.style.setProperty('--color-primary-text',   theme.primaryTextOnDark);
}

// ── Provider ───────────────────────────────────────────────────────────────

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

interface TenantConfigProviderProps {
  /** If false (unauthenticated) skip the fetch and use defaults immediately. */
  authenticated: boolean;
  children: ReactNode;
}

export const TenantConfigProvider: React.FC<TenantConfigProviderProps> = ({
  authenticated,
  children,
}) => {
  const [config, setConfig]       = useState<TenantConfig>(DEFAULT_CONFIG);
  const [isLoading, setIsLoading] = useState(true);

  const fetchConfig = useCallback(async () => {
    if (!authenticated) {
      console.info('[TenantConfig] Unauthenticated — using application defaults:', DEFAULT_CONFIG);
      applyTheme(DEFAULT_CONFIG.theme);
      setIsLoading(false);
      return;
    }

    console.info('[TenantConfig] Fetching tenant config from', `${API_BASE_URL}/api/tenant/config`);

    try {
      const token = await getAccessToken();
      const res = await fetch(`${API_BASE_URL}/api/tenant/config`, {
        headers: { Authorization: `Bearer ${token}` },
      });

      if (res.ok) {
        const raw = await res.json() as Partial<TenantConfig>;
        console.info('[TenantConfig] Raw response from backend:', raw);

        // An empty object ({}) means the backend found no config row — treat as defaults
        const hasConfig = raw.locale || raw.currency || raw.theme;
        if (!hasConfig) {
          console.warn(
            '[TenantConfig] Backend returned empty config (no row in DB or NULL). ' +
            'Falling back to application defaults:',
            DEFAULT_CONFIG,
          );
          applyTheme(DEFAULT_CONFIG.theme);
          // Config state stays as DEFAULT_CONFIG (already the initial value)
          return;
        }

        const merged: TenantConfig = {
          locale:   raw.locale   ?? DEFAULT_CONFIG.locale,
          currency: raw.currency ?? DEFAULT_CONFIG.currency,
          theme:    raw.theme    ? { ...DEFAULT_CONFIG.theme, ...raw.theme }
                                 : DEFAULT_CONFIG.theme,
        };

        setConfig(merged);
        applyTheme(merged.theme);

        // Switch i18next to the tenant's language (e.g. "ko" from "ko-KR")
        const lang = merged.locale.split('-')[0];
        if (i18n.language !== lang) {
          await i18n.changeLanguage(lang);
        }

        console.info(
          `[TenantConfig] Applied config — locale: ${merged.locale} | ` +
          `currency: ${merged.currency} | primaryColor: ${merged.theme.primaryColor} | ` +
          `i18n language: ${lang}`,
        );
      } else {
        console.error(
          `[TenantConfig] Failed to retrieve TenantConfiguration — HTTP ${res.status} ${res.statusText}. ` +
          'Falling back to application defaults:',
          DEFAULT_CONFIG,
        );
        applyTheme(DEFAULT_CONFIG.theme);
      }
    } catch (err) {
      console.error(
        '[TenantConfig] Failed to retrieve TenantConfiguration — network or token error:',
        err,
        '— Falling back to application defaults:',
        DEFAULT_CONFIG,
      );
      applyTheme(DEFAULT_CONFIG.theme);
    } finally {
      setIsLoading(false);
    }
  }, [authenticated]);

  useEffect(() => {
    fetchConfig();
  }, [fetchConfig]);

  const formatCurrency = useCallback(
    (amount: number): string =>
      new Intl.NumberFormat(config.locale, {
        style:    'currency',
        currency: config.currency,
        maximumFractionDigits: 0,
      }).format(amount),
    [config.locale, config.currency],
  );

  // KRW minimum is conceptually different from USD, but since DollarAmount
  // is stored as an integer and reflects whatever the admin configured, we
  // keep the same numeric bounds and just display them in the tenant currency.
  const minAmount = 50;
  const maxAmount = 5000;

  // Block rendering until config is resolved so there is no locale/theme flash
  if (isLoading) return null;

  return (
    <TenantConfigContext.Provider
      value={{ config, isLoading, formatCurrency, minAmount, maxAmount }}
    >
      {children}
    </TenantConfigContext.Provider>
  );
};

// ── Hook ───────────────────────────────────────────────────────────────────

export function useTenantConfig(): TenantConfigContextValue {
  return useContext(TenantConfigContext);
}
