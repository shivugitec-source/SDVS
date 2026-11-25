import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const html = await readFile(new URL("../docs/index.html", import.meta.url), "utf8");
const pagesWorkflow = await readFile(new URL("../.github/workflows/pages.yml", import.meta.url), "utf8");
const adminHtml = await readFile(new URL("../docs/admin.html", import.meta.url), "utf8");
const backendMain = await readFile(new URL("../backend/app/main.py", import.meta.url), "utf8");
const validator = html.match(/function hasInvalidMarks\(list\)\s*\{[^\n]*\}/);
assert.ok(validator, "Question mark validation helper is missing.");

const sandbox = {};
vm.runInNewContext(validator[0] + "\nglobalThis.hasInvalidMarks = hasInvalidMarks;", sandbox);
const hasInvalidMarks = sandbox.hasInvalidMarks;

test("unanswered questions remain valid while drafting", () => {
  assert.equal(hasInvalidMarks([{ mark: "", max: 10 }]), false);
});

test("zero, fractional, and maximum-boundary marks are accepted", () => {
  assert.equal(hasInvalidMarks([
    { mark: "0", max: 10 },
    { mark: "5.5", max: 10 },
    { mark: "10", max: 10 }
  ]), false);
});

test("negative and above-maximum marks are rejected", () => {
  assert.equal(hasInvalidMarks([
    { mark: "-0.5", max: 10 },
    { mark: "10.5", max: 10 }
  ]), true);
});

test("non-numeric and non-finite marks are rejected", () => {
  assert.equal(hasInvalidMarks([
    { mark: "abc", max: 10 },
    { mark: "Infinity", max: 10 },
    { mark: "NaN", max: 10 }
  ]), true);
});

test("entered marks require a finite positive question maximum", () => {
  assert.equal(hasInvalidMarks([
    { mark: "0", max: 0 },
    { mark: "2", max: "not-a-number" }
  ]), true);
});

test("demo sample remains wired to IndexedDB storage and retrieval", () => {
  assert.match(html, /id="loadDemoSample"/);
  assert.match(html, /\$\(["']loadDemoSample["']\)\.onclick=loadDemoSample;/);
  assert.match(html, /const raw=atob\(DEMO_PDF_B64\)/);
  assert.match(html, /await putPDF\(id,blob\)/);
  assert.match(html, /indexedDB\.open\('sdvs_files_v2',1\)/);
  assert.match(html, /objectStore\('pdfs'\)\.put\(blob,id\)/);
  assert.match(html, /objectStore\('pdfs'\)\.get\(id\)/);
  assert.match(html, /r\.pdfKey\?await getPDF\(r\.pdfKey\):null/);
});

test("draft and evaluation actions remain connected to persisted state", () => {
  assert.match(html, /\$\('saveDraft'\)\.onclick=\(\)=>saveEvaluation\('Pending'\)/);
  assert.match(html, /\$\('completeEval'\)\.onclick=\(\)=>\{if\(hasInvalidMarks\(questions\)\)/);
  assert.match(html, /function confirmComplete\(\)\{closeModal\(\);saveEvaluation\('Evaluated'\)/);
});

test("JSON export and mobile responsive layout remain available", () => {
  assert.match(html, /sdvs-valuation-export\.json/);
  assert.match(html, /@media\(max-width:650px\)/);
});


const indexedDbFunctions = {
  open: html.match(/function openDB\(\)\{[^\n]*\}/)?.[0],
  put: html.match(/async function putPDF\([^\n]*\}/)?.[0],
  get: html.match(/async function getPDF\([^\n]*\}/)?.[0]
};
for (const [name, source] of Object.entries(indexedDbFunctions)) {
  assert.ok(source, `IndexedDB ${name} implementation is missing.`);
}

function createIndexedDbSandbox({ writeError = false, writeAbort = false, readError = false, readAbort = false } = {}) {
  const stored = new Map();
  const database = {
    createObjectStore(name) {
      assert.equal(name, "pdfs");
    },
    transaction(name, mode) {
      assert.equal(name, "pdfs");
      const writing = mode === "readwrite";
      const shouldError = writing && writeError;
      const shouldAbort = writing ? writeAbort : readAbort;
      const failure = shouldError
        ? new Error("IndexedDB write failed")
        : shouldAbort
          ? new Error(writing ? "IndexedDB write aborted" : "IndexedDB read aborted")
          : null;
      const transaction = {
        error: failure,
        objectStore(storeName) {
          assert.equal(storeName, "pdfs");
          return {
            put(value, key) {
              if (!shouldError && !shouldAbort) stored.set(key, value);
            },
            get(key) {
              const request = {};
              queueMicrotask(() => {
                if (readError) {
                  request.error = new Error("IndexedDB read failed");
                  request.onerror?.();
                  return;
                }
                if (!readAbort) {
                  request.result = stored.get(key);
                  request.onsuccess?.();
                }
              });
              return request;
            }
          };
        }
      };
      Object.defineProperty(transaction, "oncomplete", {
        set(callback) {
          queueMicrotask(() => {
            if (!shouldError && !shouldAbort) callback?.();
          });
        }
      });
      Object.defineProperty(transaction, "onerror", {
        set(callback) {
          if (shouldError) queueMicrotask(() => callback?.());
        }
      });
      Object.defineProperty(transaction, "onabort", {
        set(callback) {
          if (shouldAbort) queueMicrotask(() => callback?.());
        }
      });
      return transaction;
    }
  };
  const indexedDB = {
    open(name, version) {
      assert.equal(name, "sdvs_files_v2");
      assert.equal(version, 1);
      const request = { result: database };
      queueMicrotask(() => {
        request.onupgradeneeded?.();
        request.onsuccess?.();
      });
      return request;
    }
  };
  const sandbox = { indexedDB, queueMicrotask };
  vm.runInNewContext(
    `let dbPromise;\n${indexedDbFunctions.open}\n${indexedDbFunctions.put}\n${indexedDbFunctions.get}\nglobalThis.api = { putPDF, getPDF };`,
    sandbox
  );
  return sandbox.api;
}
test("IndexedDB adapter stores and retrieves PDF bytes", async () => {
  const api = createIndexedDbSandbox();
  const payload = new Uint8Array([37, 80, 68, 70, 45, 49, 46, 55]);
  await api.putPDF("sdvs-demo-script-001", payload);
  const restored = await api.getPDF("sdvs-demo-script-001");
  assert.deepEqual(Array.from(restored), Array.from(payload));
});

test("IndexedDB adapter returns null for a missing PDF key", async () => {
  const api = createIndexedDbSandbox();
  assert.equal(await api.getPDF("missing-script"), null);
});


test("IndexedDB write transaction errors reject the save", async () => {
  const api = createIndexedDbSandbox({ writeError: true });
  await assert.rejects(api.putPDF("write-error", new Uint8Array([1])), /IndexedDB write failed/);
});

test("IndexedDB aborted write transactions reject instead of hanging", async () => {
  const api = createIndexedDbSandbox({ writeAbort: true });
  await assert.rejects(api.putPDF("write-abort", new Uint8Array([1])), /IndexedDB write aborted/);
});

test("IndexedDB read request errors reject the retrieval", async () => {
  const api = createIndexedDbSandbox({ readError: true });
  await assert.rejects(api.getPDF("read-error"), /IndexedDB read failed/);
});

test("IndexedDB aborted read transactions reject instead of hanging", async () => {
  const api = createIndexedDbSandbox({ readAbort: true });
  await assert.rejects(api.getPDF("read-abort"), /IndexedDB read aborted/);
});


test("Pages deployment waits for the fresh artifact to become API-visible", () => {
  const cleanupIndex = pagesWorkflow.indexOf("id: cleanup-artifacts");
  const uploadIndex = pagesWorkflow.indexOf("name: Upload site artifact");
  const waitIndex = pagesWorkflow.indexOf("name: Wait for fresh Pages artifact to become visible");
  const deployIndex = pagesWorkflow.indexOf("name: Deploy to GitHub Pages");
  assert.ok(cleanupIndex >= 0 && cleanupIndex < uploadIndex, "Artifact cleanup must precede upload.");
  assert.ok(uploadIndex >= 0 && uploadIndex < waitIndex, "Artifact visibility polling must follow upload.");
  assert.ok(waitIndex >= 0 && waitIndex < deployIndex, "Artifact visibility polling must precede deployment.");
  assert.ok(pagesWorkflow.includes('core.setOutput("artifact_cutoff", new Date().toISOString())'));
  assert.ok(pagesWorkflow.includes("steps.cleanup-artifacts.outputs.artifact_cutoff"));
  assert.match(pagesWorkflow, /listWorkflowRunArtifacts/);
  assert.ok(pagesWorkflow.includes("Date.parse(artifact.created_at) >= cutoff"));
  assert.ok(pagesWorkflow.includes("attempt <= 20"));
  assert.ok(pagesWorkflow.includes("setTimeout(resolve, 3000)"));
});


test("admin API URL normalization removes a trailing slash", () => {
  const helper = adminHtml.match(/function url\\(path\\)\\{[^}]*\\}/)?.[0];
  assert.ok(helper, "Admin API URL helper is missing.");
  const normalization = String.raw`replace(/\\/$/,'')`;
  assert.ok(helper.includes(normalization), "API URL helper must remove a trailing slash.");
  assert.equal(adminHtml.split(normalization).length - 1, 3, "All API URL inputs must use the same normalization.");

  const sandbox = {};
  vm.runInNewContext(`let api = "https://api.example.test/"; ${helper}; globalThis.buildUrl = url;`, sandbox);
  assert.equal(sandbox.buildUrl("/health"), "https://api.example.test/health");

  const unconfigured = {};
  vm.runInNewContext(`let api = ""; ${helper}; globalThis.buildUrl = url;`, unconfigured);
  assert.throws(() => unconfigured.buildUrl("/health"), /Set the SDVS API URL first/);
});

test("admin portal preserves master-admin gating, server authorization, and safe session handling", () => {
  assert.ok(adminHtml.includes("if(d.user?.role!=='SUPER_ADMIN')"), "OTP sign-in must reject non-master-admin roles.");
  assert.ok(adminHtml.includes("async function resumeSession(){try{const users=await call('/admin/users');"), "A saved session must be re-authorized by the backend before showing admin controls.");
  assert.ok(adminHtml.includes("if(token)resumeSession();"), "Saved tokens must pass through the authorization check.");
  assert.ok(adminHtml.includes("function esc(s)"));
  assert.ok(adminHtml.includes("esc(u.name)"));
  assert.ok(adminHtml.includes("esc(u.role)"));
  assert.ok(adminHtml.includes("esc(u.email||'—')"));
  assert.ok(adminHtml.includes("esc(u.phone||'—')"));
  assert.ok(adminHtml.includes("sessionStorage.setItem('sdvs_token',token)"));
  assert.ok(!adminHtml.includes("localStorage.setItem('sdvs_token'"));
  assert.ok(backendMain.includes('def admin_users(db:Session=Depends(get_db),user:User=Depends(require_roles("SUPER_ADMIN"))):'));
  assert.ok(backendMain.includes('def admin_create_user(body:AdminUserCreateIn,db:Session=Depends(get_db),user:User=Depends(require_roles("SUPER_ADMIN"))):'));
  assert.ok(backendMain.includes('def admin_set_user_active(user_id:str,active:bool,db:Session=Depends(get_db),user:User=Depends(require_roles("SUPER_ADMIN"))):'));
});
