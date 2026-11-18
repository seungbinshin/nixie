import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Approvals } from "../components/Approvals";
import type { Approval } from "../types";

afterEach(cleanup);

const item: Approval = {
  id: "a1", kind: "inbound_proposal", peer: "alice", status: "pending", created: "2026-11-17T04:00:00Z",
  title: "Alice님의 일정 제안", summary: "Alice님이 11/17 13:00–14:00 KST 'Lunch' 일정을 제안했습니다.", room_id: "alice~bob",
};

describe("Approvals", () => {
  it("has keyboard-reachable approve and decline buttons with the request as description", () => {
    const onDecide = vi.fn();
    render(<Approvals items={[item]} decisions={{}} onDecide={onDecide} onOpenRoom={() => undefined} />);
    const approve = screen.getByRole("button", { name: "승인" });
    const decline = screen.getByRole("button", { name: "거절" });
    expect(approve.getAttribute("aria-describedby")).toBe("ap-a1");
    approve.focus();
    expect(document.activeElement).toBe(approve);
    fireEvent.click(decline);
    expect(onDecide).toHaveBeenCalledWith(item, false);
  });

  it("disables both buttons while a decision runs", () => {
    render(<Approvals items={[item]} decisions={{ a1: { busy: true } }} onDecide={vi.fn()} onOpenRoom={() => undefined} />);
    expect((screen.getByRole("button", { name: "승인" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "거절" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows the result instead of buttons after the decision", () => {
    render(<Approvals items={[item]} decisions={{ a1: { busy: false, result: "승인했습니다." } }} onDecide={vi.fn()} onOpenRoom={() => undefined} />);
    expect(screen.queryByRole("button", { name: "승인" })).toBeNull();
    expect(screen.getByRole("status").textContent).toBe("승인했습니다.");
  });
});
