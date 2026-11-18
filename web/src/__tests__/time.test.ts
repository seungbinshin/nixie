import { describe, expect, it } from "vitest";
import { kstDay, kstFull, kstShort, kstTime } from "../time";

describe("KST display of UTC values", () => {
  it("shows UTC storage as KST (+9)", () => {
    expect(kstFull("2026-11-17T04:00:00Z")).toBe("2026-11-17 13:00 KST");
    expect(kstTime("2026-11-17T15:30:00.000Z")).toBe("00:30"); // next day in Seoul
    expect(kstDay("2026-11-17T15:30:00Z")).toMatch(/^11\/18 \(/);
  });
  it("uses the KST calendar day for short labels", () => {
    const now = new Date("2026-11-18T01:00:00Z"); // 10:00 KST on 11/18
    expect(kstShort("2026-11-17T23:59:00Z", now)).toBe("08:59"); // same KST day
    expect(kstShort("2026-11-17T14:00:00Z", now)).toBe("11/17");
    expect(kstShort("2025-11-17T14:00:00Z", now)).toBe("2025-11-17");
  });
});
