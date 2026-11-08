import { describe, expect, it } from "vitest";

import { localeFrom, t } from "./i18n";

describe("mobile i18n", () => {
  it("falls back to English and substitutes variables", () => {
    expect(t("de", "home.title")).toBe("Ihre Belege");
    expect(t("fr", "home.title")).toBe("Your documents");
    expect(t("en", "home.open_questions", { n: 2 })).toBe("2 open question(s) from your accountant");
  });
  it("maps device locales to launch locales", () => {
    expect(localeFrom("de-DE")).toBe("de");
    expect(localeFrom("pt-BR")).toBe("en");
    expect(localeFrom(null)).toBe("en");
  });
});
