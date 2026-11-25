import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const html = await readFile(new URL("../docs/index.html", import.meta.url), "utf8");
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

function createIndexedDbSandbox() {
  const stored = new Map();
  const database = {
    createObjectStore(name) {
      assert.equal(name, "pdfs");
    },
    transaction(name) {
      assert.equal(name, "pdfs");
      return {
        objectStore(storeName) {
          assert.equal(storeName, "pdfs");
          return {
            put(value, key) {
              stored.set(key, value);
            },
            get(key) {
              const request = {};
              queueMicrotask(() => {
                request.result = stored.get(key);
                request.onsuccess?.();
              });
              return request;
            }
          };
        },
        set oncomplete(callback) {
          queueMicrotask(() => callback?.());
        },
        set onerror(callback) {
          this._onerror = callback;
        }
      };
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
