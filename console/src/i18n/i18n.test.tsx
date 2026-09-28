import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import en from "./en.json";
import hi from "./hi.json";
import { I18nProvider, translate, useI18n } from "./i18n";

function Probe() {
  const { lang, setLang, t } = useI18n();
  return (
    <div>
      <p>{t("nav.sources")}</p>
      <button type="button" onClick={() => setLang(lang === "en" ? "hi" : "en")}>
        toggle
      </button>
    </div>
  );
}

describe("translate", () => {
  it("interpolates {vars}", () => {
    expect(translate("en", "overview.eps", { n: 42 })).toBe("42 events/s");
    expect(translate("hi", "sources.silent", { age: "1m 20s" })).toBe("1m 20s से कोई घटना नहीं");
  });

  it("falls back to English, then to the key itself", () => {
    expect(translate("hi", "no.such.key")).toBe("no.such.key");
  });

  it("keeps a placeholder whose variable is missing", () => {
    expect(translate("en", "overview.eps", {})).toBe("{n} events/s");
  });
});

describe("dictionaries", () => {
  it("define exactly the same keys in both languages", () => {
    expect(Object.keys(hi).sort()).toEqual(Object.keys(en).sort());
  });
});

describe("I18nProvider", () => {
  it("switches language and remembers it", async () => {
    render(
      <I18nProvider>
        <Probe />
      </I18nProvider>,
    );
    expect(screen.getByText("Sources")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "toggle" }));
    expect(screen.getByText("स्रोत")).toBeInTheDocument();
    expect(localStorage.getItem("veyra.lang")).toBe("hi");
    expect(document.documentElement.lang).toBe("hi");
  });

  it("starts in the remembered language", () => {
    localStorage.setItem("veyra.lang", "hi");
    render(
      <I18nProvider>
        <Probe />
      </I18nProvider>,
    );
    expect(screen.getByText("स्रोत")).toBeInTheDocument();
  });
});
