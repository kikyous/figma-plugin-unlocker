#!/usr/bin/env python3
"""Build a reversible, version-pinned experiment from a local Figma install."""
import argparse
import hashlib
import json
import pathlib
import plistlib
import struct
import subprocess
import tempfile

VERSION = "126.11.7"
MAIN_HASH = "dba0d4be01933d65a87888461df9d1de5a78ed90a56e24346a98bc65379910af"
ROOT = pathlib.Path(__file__).resolve().parent.parent

# These only affect the desktop menu's plugin execution actions.
PATCHES = [
    (
        '"use strict";var Dee=Object.create;',
        '"use strict";if(process.env.FIGMA_PLUGIN_EXPERIMENT_DEBUG==="1")require("electron").app.on("ready",()=>console.error("[experiment] app ready"));var Dee=Object.create;',
    ),
    (
        'function Det(t,e){let r=e&&ae.fromBrowserWindow(e);t&&t.pluginMenuAction&&r&&r.postMessageToActiveWebBinding("handlePluginMenuAction",t.pluginMenuAction)}',
        'function Det(t,e){let r=e&&ae.fromBrowserWindow(e);if(t&&t.pluginMenuAction&&r){let a=t.pluginMenuAction;if(["run-local-plugin","run-installed-plugin","run-last"].includes(a.type)){a={...a};delete a.showError}r.postMessageToActiveWebBinding("handlePluginMenuAction",a)}}',
    ),
    (
        'enabled:!t.disabled,pluginMenuAction:t.menuAction',
        'enabled:!t.disabled||["run-local-plugin","run-installed-plugin","run-last"].includes(t.menuAction?.type),pluginMenuAction:t.menuAction',
    ),
    # An explicit profile override lets us smoke-test without touching the running session.
    (
        'var Jl=Jt.join(Li.app.getPath("appData"),"Figma"),',
        'var Jl=process.env.FIGMA_PLUGIN_EXPERIMENT_PROFILE||Jt.join(Li.app.getPath("appData"),"Figma"),',
    ),
    # Diagnostic access is opt-in and uses the loopback address.
    (
        'process.env.FIGMA_TEST||xe.app.commandLine.removeSwitch("remote-debugging-port");',
        'process.env.FIGMA_PLUGIN_EXPERIMENT_DEBUG==="1"?(xe.app.commandLine.appendSwitch("remote-debugging-address","127.0.0.1"),xe.app.commandLine.appendSwitch("remote-debugging-port","19322")):process.env.FIGMA_TEST||xe.app.commandLine.removeSwitch("remote-debugging-port");',
    ),
]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_asar(path):
    data = path.read_bytes()
    marker, header_size, payload_size, json_size = struct.unpack("<4I", data[:16])
    if marker != 4 or payload_size + 4 != header_size:
        raise ValueError("Unexpected ASAR header")
    header = json.loads(data[16:16 + json_size])
    return header, data[8 + header_size:]


def entries(tree):
    for value in tree.get("files", {}).values():
        if "files" in value:
            yield from entries(value)
        else:
            yield value


def replace_main(header, payload, main):
    entry = header["files"]["main.js"]
    start, old_size = int(entry["offset"]), entry["size"]
    delta = len(main) - old_size
    payload = payload[:start] + main + payload[start + old_size:]
    for item in entries(header):
        if "offset" in item and int(item["offset"]) >= start + old_size:
            item["offset"] = str(int(item["offset"]) + delta)
    block_size = entry["integrity"]["blockSize"]
    entry["size"] = len(main)
    entry["integrity"] = {
        "algorithm": "SHA256", "hash": sha(main), "blockSize": block_size,
        "blocks": [sha(main[i:i + block_size]) for i in range(0, len(main), block_size)],
    }
    raw_json = json.dumps(header, separators=(",", ":"), ensure_ascii=False).encode()
    pickle_payload = struct.pack("<I", len(raw_json)) + raw_json
    pickle_payload += b"\0" * (-len(pickle_payload) % 4)
    header_pickle = struct.pack("<I", len(pickle_payload)) + pickle_payload
    archive = struct.pack("<2I", 4, len(header_pickle)) + header_pickle + payload
    # Figma's native loader checks an eight-byte, base-36 archive-length trailer.
    value, trailer = len(archive) - 8, ""
    while value:
        value, digit = divmod(value, 36)
        trailer = "0123456789abcdefghijklmnopqrstuvwxyz"[digit] + trailer
    encoded_size = trailer.rjust(8, "0").encode()
    if len(encoded_size) != 8:
        raise ValueError("Archive exceeds the trailer's supported length")
    archive = archive[:-8] + encoded_size
    return archive, sha(raw_json)


def verify_archive(path):
    data = path.read_bytes()
    if int(data[-8:], 36) != len(data) - 8:
        raise ValueError("Figma archive-length trailer does not match")
    header, payload = read_asar(path)
    checked = 0
    for item in entries(header):
        if item.get("unpacked") or item.get("size", -1) < 0 or "offset" not in item:
            continue
        offset, size = int(item["offset"]), item["size"]
        content = payload[offset:offset + size]
        if len(content) != size:
            raise ValueError("Truncated ASAR entry")
        integrity = item.get("integrity")
        if integrity:
            if sha(content) != integrity["hash"]:
                raise ValueError("ASAR integrity verification failed")
            blocks = [sha(content[i:i + integrity["blockSize"]]) for i in range(0, len(content), integrity["blockSize"])]
            if blocks != integrity["blocks"] and content:
                raise ValueError("ASAR block verification failed")
        checked += 1
    return checked


def run(*args):
    subprocess.run(args, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=pathlib.Path, default=pathlib.Path("/Applications/Figma.app"))
    parser.add_argument("--output", type=pathlib.Path, default=ROOT / ".local" / "Figma Plugin Experiment.app")
    parser.add_argument("--refresh", action="store_true", help="Rebuild only a previously recorded experiment")
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    if source == output or source in output.parents:
        parser.error("Output must be outside the source app")
    if output.exists():
        if not args.refresh:
            parser.error("Output exists; use --refresh only for a previously recorded experiment")
        previous = json.loads((ROOT / ".local" / "patch-report.json").read_text())
        existing_archive = output / "Contents/Resources/app.asar"
        if previous["output"] != str(output) or previous["patchedAsarSha256"] != sha(existing_archive.read_bytes()):
            parser.error("Existing output does not match the previous experiment report")
    plist_path = source / "Contents/Info.plist"
    info = plistlib.loads(plist_path.read_bytes())
    if info["CFBundleShortVersionString"] != VERSION:
        parser.error(f"Only Figma {VERSION} is supported; refusing to guess at another build")
    archive_path = source / "Contents/Resources/app.asar"
    original_archive_hash = sha(archive_path.read_bytes())
    header, payload = read_asar(archive_path)
    entry = header["files"]["main.js"]
    original_main = payload[int(entry["offset"]):int(entry["offset"]) + entry["size"]]
    if sha(original_main) != MAIN_HASH:
        parser.error("Source main.js does not match the inspected build")
    original_main_path = ROOT / ".local" / "original-main.js"
    original_main_path.parent.mkdir(parents=True, exist_ok=True)
    original_main_path.write_bytes(original_main)
    modified = original_main.decode()
    for old, new in PATCHES:
        if modified.count(old) != 1:
            raise ValueError("Patch target is missing or ambiguous")
        modified = modified.replace(old, new)
    hook_source = (ROOT / "scripts" / "plugin-access-hook.cjs").read_text()
    hook_install = "(()=>{const module={exports:{}};" + hook_source + ";module.exports.install()})();"
    if not modified.startswith('"use strict";'):
        raise ValueError("Unexpected main script prefix")
    modified = modified.replace('"use strict";', '"use strict";' + hook_install, 1)
    modified_bytes = modified.encode()
    archive, header_hash = replace_main(header, payload, modified_bytes)
    with tempfile.TemporaryDirectory(prefix="figma-plugin-patch-") as temp:
        temp = pathlib.Path(temp)
        js = temp / "main.js"
        js.write_bytes(modified_bytes)
        run("node", "--check", str(js))
        output.parent.mkdir(parents=True, exist_ok=True)
        if not output.exists():
            print(f"Copying {source} to {output}", flush=True)
            run("ditto", str(source), str(output))
        destination_archive = output / "Contents/Resources/app.asar"
        destination_archive.write_bytes(archive)
        checked = verify_archive(destination_archive)
        info["ElectronAsarIntegrity"]["Resources/app.asar"]["hash"] = header_hash
        info["CFBundleIdentifier"] = "local.figma.PluginExperiment"
        info["CFBundleDisplayName"] = "Figma Plugin Experiment"
        (output / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
        ent_result = subprocess.run(
            ["codesign", "-d", "--entitlements", ":-", str(source)],
            check=True, capture_output=True,
        )
        entitlements = plistlib.loads(ent_result.stdout)
        # Ad-hoc signatures cannot satisfy the vendor's library team identity.
        entitlements["com.apple.security.cs.disable-library-validation"] = True
        ent_path = temp / "entitlements.plist"
        ent_path.write_bytes(plistlib.dumps(entitlements))
        run("codesign", "--force", "--deep", "--sign", "-", "--options", "runtime", "--entitlements", str(ent_path), str(output))
        run("codesign", "--verify", "--deep", "--strict", str(output))
    if sha(archive_path.read_bytes()) != original_archive_hash:
        raise ValueError("The original app changed during this experiment")
    report = {
        "source": str(source), "output": str(output), "version": VERSION,
        "bundleIdentifier": info["CFBundleIdentifier"],
        "sourceAsarSha256": original_archive_hash,
        "patchedAsarSha256": sha(archive), "patchedMainSha256": sha(modified_bytes),
        "verifiedAsarEntries": checked, "patches": len(PATCHES), "revision": 2,
        "scope": "Desktop plugin menu and five frontend plugin selectors; document read-only state and server permissions remain unchanged.",
    }
    (ROOT / ".local" / "patch-report.json").write_text(json.dumps(report, indent=2) + "\n")
    (ROOT / ".local" / "patched-main.js").write_bytes(modified_bytes)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
