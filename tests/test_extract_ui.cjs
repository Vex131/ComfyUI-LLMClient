const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

test("extract outputs track patterns and preserve linked legacy slots", () => {
    const extensions = [];
    const app = { registerExtension: extension => extensions.push(extension) };
    const source = fs.readFileSync("web/llmclient.js", "utf8").replace(/^import .*;\r?\n/gm, "");
    vm.runInNewContext(source, { app, api: {}, console });
    const patterns = { name: "patterns", value: "positive =\nnegative =" };
    const node = {
        comfyClass: "LLMPatternExtract", widgets: [patterns],
        outputs: Array.from({ length: 12 }, (_, i) => ({
            name: `field_${i + 1}`, label: `field_${i + 1}`,
        })),
        configure() {}, setDirtyCanvas() {},
        removeOutput(i) { this.outputs.splice(i, 1); },
        addOutput(name, type) { this.outputs.push({ name, type }); },
    };
    extensions.find(e => e.name === "LLMClient.Extract").nodeCreated(node);
    assert.deepEqual(node.outputs.map(o => o.name), ["positive", "negative"]);
    assert.deepEqual(node.outputs.map(o => o.label), ["positive", "negative"]);
    patterns.callback("first =\nnegative =");
    assert.deepEqual(node.outputs.map(o => o.label), ["first", "negative"]);
    patterns.value = "positive =\nnegative =\nseed =";
    patterns.callback(patterns.value);
    assert.deepEqual(node.outputs.map(o => o.name), ["positive", "negative", "seed"]);
    node.outputs[2].links = [4];
    patterns.value = "positive =";
    patterns.callback(patterns.value);
    assert.deepEqual(node.outputs.map(o => o.name), ["positive", "field_2", "field_3"]);
});
