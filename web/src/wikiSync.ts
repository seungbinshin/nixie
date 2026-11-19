// Local wiki folder on the owner's computer (File System Access API: Edge/Chrome on Windows).
// The server archive is the original. Sync writes every document; a document that was changed on the computer is
// written back to the original ("restored"), so archived documents stay unchanged. Hubs (index.md, topics/) are rebuilt.

export interface BundleFile { path: string; content: string; sha256: string; read_only: boolean }
export interface Bundle { seq: number; count: number; files: BundleFile[]; integrity_ok: boolean }
export interface SyncResult { written: number; restored: number; unchanged: number; folder: string; at: string }

type PermissionMode = { mode: "readwrite" };
type DirHandle = FileSystemDirectoryHandle & {
  queryPermission?(d: PermissionMode): Promise<PermissionState>;
  requestPermission?(d: PermissionMode): Promise<PermissionState>;
};
type Picker = (o?: { id?: string; mode?: "read" | "readwrite"; startIn?: string }) => Promise<DirHandle>;

export function folderSupported(): boolean {
  return typeof (window as unknown as { showDirectoryPicker?: Picker }).showDirectoryPicker === "function";
}

// ---- tiny IndexedDB key-value store (a folder handle cannot go into localStorage)
function db(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const r = indexedDB.open("nixie-wiki", 1);
    r.onupgradeneeded = () => r.result.createObjectStore("kv");
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });
}
async function kv<T>(mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest): Promise<T> {
  const d = await db();
  return new Promise((resolve, reject) => {
    const req = fn(d.transaction("kv", mode).objectStore("kv"));
    req.onsuccess = () => resolve(req.result as T);
    req.onerror = () => reject(req.error);
  });
}

export async function chooseFolder(): Promise<DirHandle> {
  const pick = (window as unknown as { showDirectoryPicker: Picker }).showDirectoryPicker;
  const h = await pick({ id: "nixie-wiki", mode: "readwrite", startIn: "documents" });
  await kv("readwrite", (s) => s.put(h, "folder"));
  return h;
}

export async function savedFolder(): Promise<DirHandle | null> {
  try {
    return (await kv<DirHandle | undefined>("readonly", (s) => s.get("folder"))) ?? null;
  } catch {
    return null;
  }
}

export async function forgetFolder(): Promise<void> {
  await kv("readwrite", (s) => s.delete("folder"));
}

/** true when writing is allowed. `ask` shows the browser prompt (needs a user action, for example a click). */
export async function canWrite(h: DirHandle, ask: boolean): Promise<boolean> {
  const mode: PermissionMode = { mode: "readwrite" };
  if ((await h.queryPermission?.(mode)) === "granted") return true;
  return ask ? (await h.requestPermission?.(mode)) === "granted" : false;
}

export async function sha256(text: string): Promise<string> {
  const d = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(d)).map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function fileAt(root: DirHandle, path: string): Promise<FileSystemFileHandle> {
  const parts = path.split("/").filter(Boolean);
  if (parts.some((p) => p === ".." || p === ".")) throw new Error(`bad path ${path}`);
  let dir: FileSystemDirectoryHandle = root;
  for (const p of parts.slice(0, -1)) dir = await dir.getDirectoryHandle(p, { create: true });
  return dir.getFileHandle(parts[parts.length - 1], { create: true });
}

export async function syncBundle(root: DirHandle, bundle: Bundle): Promise<SyncResult> {
  const out: SyncResult = { written: 0, restored: 0, unchanged: 0, folder: root.name, at: new Date().toISOString() };
  for (const f of bundle.files) {
    const fh = await fileAt(root, f.path);
    const cur = await (await fh.getFile()).text();
    if (cur && (await sha256(cur)) === f.sha256) {
      out.unchanged++;
      continue;
    }
    const w = await fh.createWritable();
    await w.write(f.content);
    await w.close();
    if (cur && f.read_only) out.restored++;
    else out.written++;
  }
  return out;
}
