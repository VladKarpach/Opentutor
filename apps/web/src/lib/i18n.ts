import en from "@/locales/en.json";
import ru from "@/locales/ru.json";

export type Locale = "ru" | "en";

const SUPPORTED_LOCALES: Locale[] = ["ru", "en"];

/**
 * Russian is the default and the fallback order's first stop, because it is the
 * product's primary language. English remains available, and anything missing
 * from ru.json falls back to English rather than showing a raw key.
 */
const translations: Record<Locale, Record<string, string>> = { ru, en };

let currentLocale: Locale = "ru";

export function setLocale(locale: Locale): void {
  currentLocale = locale;
  if (typeof window !== "undefined") {
    try { localStorage.setItem("opentutor-locale", locale); } catch { /* quota */ }
  }
}

export function getLocale(): Locale {
  if (typeof window !== "undefined") {
    const saved = localStorage.getItem("opentutor-locale") as Locale | null;
    if (saved && SUPPORTED_LOCALES.includes(saved)) {
      currentLocale = saved;
    }
  }
  return currentLocale;
}

export function t(key: string): string {
  return translations[currentLocale]?.[key] ?? translations.ru[key] ?? translations.en[key] ?? key;
}

export function tf(
  key: string,
  vars?: Record<string, string | number | null | undefined>,
): string {
  let message = t(key);
  if (!vars) return message;
  for (const [name, value] of Object.entries(vars)) {
    const token = `{${name}}`;
    message = message.split(token).join(value == null ? "" : String(value));
  }
  return message;
}

export function initLocale(): void {
  if (typeof window !== "undefined") {
    const saved = localStorage.getItem("opentutor-locale") as Locale | null;
    if (saved && SUPPORTED_LOCALES.includes(saved)) {
      currentLocale = saved;
    } else {
      currentLocale = "ru";
    }
  }
}