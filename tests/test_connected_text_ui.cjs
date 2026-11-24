const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

test("connected text overrides hide and restore the editor and node size", async () => {
    const extensions = [];
    const app = { registerExtension: extension => extensions.push(extension) };
    const source = fs.readFileSync("web/llmclient.js", "utf8").replace(/^import .*;\r?\n/gm, "");
    vm.runInNewContext(source, { app, api: {}, console, setTimeout });
    const originalSize = () => [200, 100];
    const text = { name: "text", value: "keep me", type: "customtext",
        element: { style: { display: "" } }, computeSize: originalSize };
    const patterns = { name: "patterns", element: { style: { display: "" } } };
    const node = {
        comfyClass: "LLMPatternExtract", widgets: [text, patterns],
        inputs: [{ name: "text_input", link: null }], size: [400, 300],
        computeSize() { return [200, text.type === "hidden" ? 160 : 260]; },
        setSize(size) { this.size = size; }, setDirtyCanvas() {},
    };
    extensions.find(e => e.name === "LLMClient.ConnectedText").nodeCreated(node);
    node.onConnectionsChange(1, 0, true);
    assert.equal(text.element.style.display, "none");
    assert.equal(text.type, "hidden");
    assert.equal(node.size[1], 160);
    assert.equal(patterns.element.style.display, "");
    node.onConnectionsChange(1, 0, false);
    assert.equal(text.element.style.display, "");
    assert.equal(text.computeSize, originalSize);
    assert.equal(text.value, "keep me");
    assert.equal(node.size[1], 300);
    node.inputs[0].link = 42;
    node.onConfigure();
    await new Promise(resolve => setTimeout(resolve, 5));
    assert.equal(text.element.style.display, "none");
});
