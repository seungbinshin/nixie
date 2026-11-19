import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { Markdown } from "../components/Markdown";
import { josa } from "../josa";
import { hrefFor, parseRoute } from "../route";
import { contrast, derive, PRESETS } from "../theme";

afterEach(cleanup);

describe("theme: any accent keeps AA contrast", () => {
  const colors = [...PRESETS.map((p) => p.color), "#ffff00", "#00ffff", "#7f7f7f", "#000000", "#ffffff", "#ffd1dc"];
  for (const dark of [false, true]) {
    it(`${dark ? "dark" : "light"} mode`, () => {
      for (const c of colors) {
        const v = derive(c, dark);
        expect(contrast(v["--accent"], v["--accent-text"])).toBeGreaterThanOrEqual(4.5); // button text
        for (const bg of [v["--bg"], v["--surface"], v["--accent-soft"], v["--surface-2"]]) {
          expect(contrast(v["--link"], bg)).toBeGreaterThanOrEqual(4.5); // links, focus ring
          expect(contrast(v["--muted"], bg)).toBeGreaterThanOrEqual(4.5);
          expect(contrast(v["--text"], bg)).toBeGreaterThanOrEqual(4.5);
        }
      }
    });
  }
});

describe("markdown", () => {
  it("renders bold, lists and tables; no raw HTML; no javascript links", () => {
    const { container } = render(<Markdown text={"**굵게** 그리고 *기울임*\n\n- 하나\n- 둘\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n<script>alert(1)</script><b>x</b>\n\n[나쁜 링크](javascript:alert(1)) [원문](https://corp.example/a)"} />);
    expect(container.querySelector("strong")?.textContent).toBe("굵게");
    expect(container.querySelectorAll("li")).toHaveLength(2);
    expect(container.querySelector("table")).not.toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
    const links = Array.from(container.querySelectorAll("a"));
    expect(links.map((a) => a.getAttribute("href"))).toEqual(["https://corp.example/a"]);
    expect(links[0].getAttribute("rel")).toBe("noopener noreferrer");
  });

  it("turns wiki links into in-app routes", () => {
    const { container } = render(<Markdown text={"[이전 문서](20261119-140500-ab12.md)"} />);
    expect(container.querySelector("a")?.getAttribute("href")).toBe("/?wiki=20261119-140500-ab12");
  });
});

describe("names and routes", () => {
  it("picks the Korean particle by the last syllable", () => {
    expect(josa("하댕이", "이/가")).toBe("하댕이가");
    expect(josa("콩", "이/가")).toBe("콩이");
    expect(josa("하냥이", "와/과")).toBe("하냥이와");
    expect(josa("별님", "와/과")).toBe("별님과");
  });
  it("wiki routes", () => {
    expect(parseRoute("?wiki")).toEqual({ view: "wiki", docId: null });
    expect(parseRoute("?wiki=abc")).toEqual({ view: "wiki", docId: "abc" });
    expect(hrefFor({ wiki: "" })).toBe("/?wiki");
    expect(hrefFor({ wiki: "abc" })).toBe("/?wiki=abc");
  });
});
