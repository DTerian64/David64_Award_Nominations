/**
 * i18n/index.ts
 * ─────────────
 * Initialises i18next with English (default) and Korean translation bundles.
 *
 * Language selection order:
 *   1. Language explicitly set by TenantConfigContext (via i18n.changeLanguage)
 *   2. Browser language
 *   3. Fallback: en
 *
 * Import this file once — in main.tsx — before React renders.
 */

import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';

import en from './en.json';
import ko from './ko.json';

i18n
  .use(initReactI18next)
  .init({
    resources: {
      en: { translation: en },
      ko: { translation: ko },
    },
    lng: 'en',          // overridden at runtime by TenantConfigContext
    fallbackLng: 'en',
    interpolation: {
      escapeValue: false, // React already escapes
    },
  });

export default i18n;
