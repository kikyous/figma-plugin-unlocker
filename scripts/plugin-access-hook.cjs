"use strict";

const { createHash } = require("node:crypto");
const EXPECTED_BUNDLE_SHA256 = "80daabc8111778c6605df1b7f717ed42b3f9c290bc19cc9909fb2cdf1000889a";
const normalDesign = 'e.selectedView?.view==="fullscreen"&&e.selectedView?.editorType===1';
const PATCHES = [
  [
    'function y(e){return(0,_.$A)(e.selectedView)?(0,c.tn)(e)&&g({copyExportRestrictedArgs:e.openFile})',
    `function y(e){if(${normalDesign})e={...e,mirror:{...e.mirror,appModel:{...e.mirror.appModel,isReadOnly:false}}};return(0,_.$A)(e.selectedView)?(0,c.tn)(e)&&g({copyExportRestrictedArgs:e.openFile})`,
  ],
  [
    'function E(e){if((0,s.nl)()||(0,m.dk)(e.openFile))return!0;',
    `function E(e){if(${normalDesign}&&e.openFile)e={...e,openFile:{...e.openFile,canRunExtensions:true}};if((0,s.nl)()||(0,m.dk)(e.openFile))return!0;`,
  ],
  [
    'isReadOnly:e.mirror.appModel.isReadOnly,userCanViewPlugins:(0,T.Eh)(e)',
    `isReadOnly:(${normalDesign})?false:e.mirror.appModel.isReadOnly,userCanViewPlugins:(0,T.Eh)(e)`,
  ],
  [
    'isReadOnly:e.mirror.appModel.isReadOnly,userCanViewPlugins:(0,I.Eh)(e)',
    `isReadOnly:(${normalDesign})?false:e.mirror.appModel.isReadOnly,userCanViewPlugins:(0,I.Eh)(e)`,
  ],
  [
    'function k(e,t){let n=(0,f.M)(),i=[],d=!!e.isReadOnly,p=c.globalReduxStore.getState(),_=(0,g.Eh)(p)',
    'function k(e,t){if(e.editorType===a.n.Design)e={...e,isReadOnly:false};let n=(0,f.M)(),i=[],d=!!e.isReadOnly,p=c.globalReduxStore.getState(),_=(0,g.Eh)(p)',
  ],
];

function patchBundle(source) {
  const digest = createHash("sha256").update(source).digest("hex");
  if (digest !== EXPECTED_BUNDLE_SHA256) {
    throw new Error("Unsupported Figma frontend bundle: " + digest);
  }
  for (const [before, after] of PATCHES) {
    if (source.split(before).length !== 2) throw new Error("Missing or ambiguous frontend patch");
    source = source.replace(before, after);
  }
  return 'globalThis.__figmaPluginAccessExperiment={version:2,pluginSelectorsPatched:true};\n' + source;
}

function install() {
  const { app } = require("electron");
  app.on("web-contents-created", (_event, contents) => {
    const debuggerClient = contents.debugger;
    try {
      debuggerClient.attach("1.3");
    } catch (error) {
      console.error("[plugin-experiment] debugger attach failed:", error.message);
      return;
    }
    debuggerClient.on("message", async (_event, method, params) => {
      if (method !== "Fetch.requestPaused") return;
      const { requestId } = params;
      let completed = false;
      try {
        if (params.responseStatusCode !== 200) return;
        const response = await debuggerClient.sendCommand("Fetch.getResponseBody", { requestId });
        const original = response.base64Encoded ? Buffer.from(response.body, "base64").toString("utf8") : response.body;
        const modified = Buffer.from(patchBundle(original), "utf8");
        const responseHeaders = (params.responseHeaders ?? []).filter(header =>
          !["content-encoding", "content-length", "transfer-encoding"].includes(header.name.toLowerCase()),
        );
        responseHeaders.push({ name: "Content-Length", value: String(modified.length) });
        await debuggerClient.sendCommand("Fetch.fulfillRequest", {
          requestId, responseCode: 200, responseHeaders, body: modified.toString("base64"),
        });
        completed = true;
        console.error("[plugin-experiment] applied five frontend changes to webContents", contents.id);
      } catch (error) {
        console.error("[plugin-experiment] frontend unchanged:", error.message);
      } finally {
        if (!completed) {
          try { await debuggerClient.sendCommand("Fetch.continueRequest", { requestId }); } catch {}
        }
      }
    });
    debuggerClient.sendCommand("Fetch.enable", {
      patterns: [{
        urlPattern: "https://www.figma.com/webpack-artifacts/assets/figma_app-*.min.js.br",
        resourceType: "Script", requestStage: "Response",
      }],
    }).catch(error => console.error("[plugin-experiment] interception failed:", error.message));
  });
}

module.exports = { patchBundle, PATCHES, EXPECTED_BUNDLE_SHA256, install };
