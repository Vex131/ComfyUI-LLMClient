const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

test("API key manager handles providers, storage status and optional search", async () => {
    class Element {
        constructor(tag) {
            this.tag = tag;
            this.children = [];
            this.style = {};
            this.value = "";
            this.textContent = "";
            this.attributes = {};
            this.listeners = {};
        }
        append(...items) { this.children.push(...items); }
        replaceChildren(...items) { this.children = items; }
        addEventListener(name, callback) { this.listeners[name] = callback; }
        setAttribute(name, value) { this.attributes[name] = value; }
        getBoundingClientRect() { return { left: 10, right: 100, top: 10, bottom: 100 }; }
        close() { this.closed = true; }
        focus() {}
        showModal() {}
    }
    const registrations = [];
    const entries = new Map();
    let search = "";
    let searxng = "";
    let backend = "win-dpapi-file";
    const api = {
        async fetchApi(path, request) {
            if (request) {
                const body = JSON.parse(request.body);
                if (path.endsWith("/llm")) {
                    if (body.api_key) entries.set(body.base_url, { api_key: body.api_key, name: body.name });
                    else entries.delete(body.base_url);
                } else {
                    if ("api_key" in body) search = body.api_key;
                    if ("searxng_url" in body) searxng = body.searxng_url;
                }
            }
            return {
                ok: true,
                async json() {
                    return {
                        backend,
                        fallback_reason: "win-credential-manager: CredWriteW failed: 1312",
                        llm: Array.from(entries, ([base_url, entry]) => ({ base_url, name: entry.name })),
                        search: { set: !!search, preview: "***", searxng_url: searxng },
                    };
                },
            };
        },
    };
    const app = { registerExtension: (extension) => registrations.push(extension) };
    const document = { createElement: (tag) => new Element(tag), body: new Element("body") };
    const source = fs.readFileSync("web/llmclient.js", "utf8").replace(/^import .*;\r?\n/gm, "");
    vm.runInNewContext(source, { app, api, document, console });
    const provider = registrations.find((item) => item.name === "LLMClient.Provider");
    const widgets = [];
    provider.nodeCreated({
        comfyClass: "LLMProvider", widgets,
        addWidget: (type, name, value, callback) => { const widget = { name, value, callback }; widgets.push(widget); return widget; },
    });
    widgets.find((widget) => widget.name === "api_keys").callback();
    const dialog = document.body.children[0];
    dialog.listeners.click({ target: dialog, clientX: 50, clientY: 50 });
    assert.notEqual(dialog.closed, true);
    dialog.listeners.click({ target: dialog, clientX: 5, clientY: 50 });
    assert.equal(dialog.closed, true);
    const vault = registrations.find((item) => item.name === "LLMClient.Vault");
    const panel = new Element("div");
    vault.bottomPanelTabs[0].render(panel);
    const root = panel.children[0];
    const all = (element) => [element, ...element.children.flatMap(all)];
    const find = (text) => all(root).find((element) => element.textContent === text);
    const inputs = all(root).filter((element) => element.tag === "input");
    await new Promise(setImmediate);
    assert.ok(find("API key storage: encrypted Windows file"));
    assert.ok(find("Preferred storage unavailable: win-credential-manager: CredWriteW failed: 1312") === undefined);
    assert.ok(all(root).some((element) => element.textContent.includes("CredWriteW failed")));
    assert.equal(find("Remove Tavily API key").hidden, true);
    assert.equal(find("Remove SearXNG URL").hidden, true);
    inputs[0].value = "First provider";
    inputs[1].value = "https://one.example/v1";
    inputs[2].value = "first-key";
    await find("Save provider API key").onclick();
    assert.equal(inputs[2].value, "");
    assert.ok(find("First provider"));
    assert.equal(find("API key saved"), undefined);
    inputs[0].value = "";
    inputs[1].value = "https://two.example/v1";
    inputs[2].value = "second-key";
    await find("Save provider API key").onclick();
    assert.equal(entries.size, 2);
    inputs[3].value = "search-key";
    await find("Save Tavily API key").onclick();
    assert.equal(search, "search-key");
    assert.equal(inputs[3].value, "");
    assert.ok(find("Tavily API key saved"));
    assert.ok(find("Replace Tavily API key"));
    assert.equal(find("Remove Tavily API key").hidden, false);
    await find("Remove Tavily API key").onclick();
    assert.equal(search, "");
    assert.equal(find("Remove Tavily API key").hidden, true);
    assert.ok(find("Save Tavily API key"));
    inputs[4].value = "http://localhost:8888";
    await find("Save SearXNG URL").onclick();
    assert.equal(searxng, "http://localhost:8888");
    assert.ok(find("SearXNG URL saved."));
    assert.equal(find("Remove SearXNG URL").hidden, false);
    assert.ok(find("Update SearXNG URL"));
    await find("Remove SearXNG URL").onclick();
    assert.equal(searxng, "");
    assert.ok(find("No SearXNG URL configured."));
    assert.equal(find("Remove SearXNG URL").hidden, true);
    const remove = all(root).find((element) =>
        element.attributes["aria-label"] === "Remove API key for https://one.example/v1");
    await remove.onclick();
    assert.equal(entries.size, 1);
    assert.equal(entries.get("https://two.example/v1").api_key, "second-key");
    backend = "file";
    inputs[2].value = "replacement";
    await find("Save provider API key").onclick();
    assert.ok(find("API key storage: unencrypted local file — Warning: API keys are not encrypted."));
});
