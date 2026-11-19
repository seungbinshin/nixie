import { describe, expect, it } from "vitest";
import { sha256, syncBundle, type Bundle } from "../wikiSync";



// In-memory stand-in for a folder from showDirectoryPicker()
class FakeFile {
  constructor(public text = "") {}
}
class FakeDir {
  dirs = new Map<string, FakeDir>();
  files = new Map<string, FakeFile>();
  constructor(public name = "wiki") {}
  async getDirectoryHandle(n: string, o?: { create?: boolean }) {
    if (!this.dirs.has(n)) {
      if (!o?.create) throw new Error("NotFound");
      this.dirs.set(n, new FakeDir(n));
    }
    return this.dirs.get(n)!;
  }
  async getFileHandle(n: string, o?: { create?: boolean }) {
    if (!this.files.has(n)) {
      if (!o?.create) throw new Error("NotFound");
      this.files.set(n, new FakeFile());
    }
    const f = this.files.get(n)!;
    return {
      getFile: async () => ({ text: async () => f.text }),
      createWritable: async () => ({ write: async (t: string) => { f.text = t; }, close: async () => undefined }),
    };
  }
}

async function bundle(): Promise<Bundle> {
  const files = [
    { path: "docs/a.md", content: "# 휴가 신청 요청\n", read_only: true },
    { path: "index.md", content: "# 업무 위키\n", read_only: false },
  ];
  return { seq: 1, count: 1, integrity_ok: true,
           files: await Promise.all(files.map(async (f) => ({ ...f, sha256: await sha256(f.content) }))) };
}

describe("local wiki folder", () => {
  it("writes new files, keeps equal ones, and restores an archived document changed on the computer", async () => {
    const root = new FakeDir();
    const b = await bundle();
    const first = await syncBundle(root as unknown as FileSystemDirectoryHandle, b);
    expect([first.written, first.restored, first.unchanged]).toEqual([2, 0, 0]);
    expect((await root.getDirectoryHandle("docs")).files.get("a.md")!.text).toBe("# 휴가 신청 요청\n");

    (await root.getDirectoryHandle("docs")).files.get("a.md")!.text = "# 누군가 고친 내용\n";
    const second = await syncBundle(root as unknown as FileSystemDirectoryHandle, b);
    expect([second.written, second.restored, second.unchanged]).toEqual([0, 1, 1]);
    expect((await root.getDirectoryHandle("docs")).files.get("a.md")!.text).toBe("# 휴가 신청 요청\n");
  });

  it("refuses paths that leave the folder", async () => {
    const b = await bundle();
    b.files[0].path = "../x.md";
    await expect(syncBundle(new FakeDir() as unknown as FileSystemDirectoryHandle, b)).rejects.toThrow("bad path");
  });
});
