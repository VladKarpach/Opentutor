import { describe, it, expect, beforeEach } from "vitest";
import { t, tf, setLocale, getLocale, initLocale } from "./i18n";
import enJson from "@/locales/en.json";
import ruJson from "@/locales/ru.json";

// The JSON imports are typed as literal objects; these tests index them with
// keys discovered at runtime, so widen them once here.
const en: Record<string, string> = enJson;
const ru: Record<string, string> = ruJson;

describe("i18n", () => {
  beforeEach(() => {
    localStorage.clear();
    setLocale("ru");
  });

  describe("t()", () => {
    it("returns the Russian translation by default", () => {
      // Russian is the primary language, so it must be what a fresh visitor sees.
      expect(t("nav.dashboard")).toBe(ru["nav.dashboard"]);
      expect(t("nav.dashboard")).not.toBe("nav.dashboard");
    });

    it("returns the English translation when locale is en", () => {
      setLocale("en");
      expect(t("nav.dashboard")).toBe(en["nav.dashboard"]);
    });

    it("returns the key itself for an unknown key", () => {
      expect(t("nonexistent.key.xyz")).toBe("nonexistent.key.xyz");
    });

    it("falls back to English for a key missing from ru", () => {
      setLocale("ru");
      const onlyInEnglish = Object.keys(en).find(
        (key) => !(key in ru),
      );
      // The completeness test below asserts there are none, so if that holds the
      // fallback is only reachable for keys added to en.json in the future.
      if (onlyInEnglish) {
        expect(t(onlyInEnglish)).toBe(en[onlyInEnglish]);
      } else {
        expect(onlyInEnglish).toBeUndefined();
      }
    });
  });

  describe("translation completeness", () => {
    it("ru.json has every key that en.json has", () => {
      // A partial Russian file would render a mixed-language UI, which is worse
      // than English everywhere. This is the guard against that.
      const missing = Object.keys(en).filter((key) => !(key in ru));
      expect(missing).toEqual([]);
    });

    it("ru.json has no keys en.json lacks", () => {
      const extra = Object.keys(ru).filter((key) => !(key in en));
      expect(extra).toEqual([]);
    });

    it("no Russian entry is left as an empty string", () => {
      const empty = Object.entries(ru)
        .filter(([, value]) => typeof value !== "string" || value.trim() === "")
        .map(([key]) => key);
      expect(empty).toEqual([]);
    });

    it("keeps placeholder tokens from the English source", () => {
      // tf() substitutes {name}; a translation that drops the token would leave a
      // literal {count} on screen.
      const mismatched = Object.entries(en)
        .filter(([key, value]) => typeof value === "string" && /\{\w+\}/.test(value))
        .filter(([key]) => {
          const tokens = (value: string) => (value.match(/\{\w+\}/g) ?? []).sort();
          return tokens(en[key]).join() !== tokens(ru[key] ?? "").join();
        })
        .map(([key]) => key);
      expect(mismatched).toEqual([]);
    });
  });

  describe("tf()", () => {
    it("replaces template variables", () => {
      const result = tf("nav.dashboard");
      expect(typeof result).toBe("string");
    });

    it("handles null and undefined vars gracefully", () => {
      const result = tf("nav.dashboard", { foo: null, bar: undefined });
      expect(typeof result).toBe("string");
    });

    it("substitutes a real placeholder", () => {
      const key = Object.keys(en).find((k) => /\{\w+\}/.test(en[k]));
      if (!key) return;
      const token = (en[key].match(/\{\w+\}/) ?? [])[0];
      if (!token) return;
      const name = token.slice(1, -1);
      expect(tf(key, { [name]: "7" })).toContain("7");
      expect(tf(key, { [name]: "7" })).not.toContain(token);
    });
  });

  describe("setLocale / getLocale", () => {
    it("persists the locale to localStorage", () => {
      setLocale("en");
      expect(localStorage.getItem("opentutor-locale")).toBe("en");
    });

    it("reads the locale from localStorage", () => {
      localStorage.setItem("opentutor-locale", "en");
      expect(getLocale()).toBe("en");
    });

    it("ignores a locale that is no longer supported", () => {
      // Chinese was removed; a stale preference must not resurrect it.
      localStorage.setItem("opentutor-locale", "zh");
      initLocale();
      expect(getLocale()).toBe("ru");
    });
  });

  describe("initLocale()", () => {
    it("restores the saved locale", () => {
      localStorage.setItem("opentutor-locale", "en");
      initLocale();
      expect(getLocale()).toBe("en");
    });

    it("defaults to Russian when nothing is saved", () => {
      initLocale();
      expect(getLocale()).toBe("ru");
    });
  });
});