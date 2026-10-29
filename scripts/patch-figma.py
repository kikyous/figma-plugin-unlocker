#!/usr/bin/env python3
"""Build a reversible Figma copy using shared desktop patch rules."""
import argparse
import hashlib
import json
import pathlib
import plistlib
import re
import struct
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
RULES_PATH = ROOT / "patches" / "desktop-rules.json"


def load_rules(path=RULES_PATH):
    rules = json.loads(path.read_text())
    if not isinstance(rules, list) or not rules:
        raise ValueError("Missing desktop patch rules")
    names = set()
    for rule in rules:
        if (not isinstance(rule, dict) or not isinstance(rule.get("name"), str) or not rule["name"]
                or rule["name"] in names or not isinstance(rule.get("pattern"), str)
                or not rule["pattern"] or not isinstance(rule.get("replacement"), str)):
            raise ValueError("Invalid or duplicate desktop patch rule")
        try:
            pattern = re.compile(rule["pattern"])
        except re.error as error:
            raise ValueError(f"Invalid patch pattern: {rule['name']}") from error
        if pattern.match("") or any(int(n) < 1 or int(n) > pattern.groups
                                     for n in re.findall(r"\{(\d+)\}", rule["replacement"])):
            raise ValueError(f"Invalid patch replacement or empty match: {rule['name']}")
        names.add(rule["name"])
    return rules


def matches_for(text, rule):
    return list(re.finditer(rule["pattern"], text))


def replacement_for(match, rule):
    if rule["name"] == "plugin-action" and "__figmaPluginAction" in match.groups():
        raise ValueError("Plugin action identifier conflicts with patch")
    return re.sub(r"\{(\d+)\}", lambda m: match.group(int(m.group(1))), rule["replacement"])


def sha(data):
    return hashlib.sha256(data).hexdigest()


def patch_main(original, rules, hook_source):
    modified = original.decode("utf8")
    for patch in rules:
        matches = matches_for(modified, patch)
        if len(matches) != 1:
            raise ValueError(f"Patch target is missing or ambiguous: {patch['name']}")
        match = matches[0]
        modified = modified[:match.start()] + replacement_for(match, patch) + modified[match.end():]
    if not modified.startswith('"use strict";'):
        raise ValueError("Unexpected main script prefix")
    hook_install = "(()=>{const module={exports:{}};" + hook_source + ";module.exports.install()})();"
    return modified.replace('"use strict";', '"use strict";' + hook_install, 1).encode("utf8")


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


def inspect_source(source, rules):
    info = plistlib.loads((source / "Contents/Info.plist").read_bytes())
    version = info["CFBundleShortVersionString"]
    archive_path = source / "Contents/Resources/app.asar"
    header, payload = read_asar(archive_path)
    entry = header["files"].get("main.js")
    if not entry or entry.get("unpacked"):
        raise ValueError("Unsupported archive layout: expected app.asar with a packed main.js")
    original = payload[int(entry["offset"]):int(entry["offset"]) + entry["size"]]
    digest = sha(original)
    checks = [
        {"name": p["name"], "matches": len(matches_for(original.decode("utf8"), p))}
        for p in rules
    ]
    verified = verify_archive(archive_path)
    patch_error = None
    try:
        patch_main(original, rules, "")
    except ValueError as error:
        patch_error = str(error)
    report = {
        "source": str(source), "version": version, "sourceMainSha256": digest,
        "sourceAsarSha256": sha(archive_path.read_bytes()),
        "compatible": patch_error is None,
        "patchChecks": checks, "patchError": patch_error, "verifiedAsarEntries": verified,
        "frontend": "Checked separately against the pinned public web bundle at runtime.",
    }
    return report, info, header, payload, original


def artifact_directory(output):
    return ROOT / ".local" / "builds" / sha(str(output.resolve()).encode())[:16]


def verify_refresh(output, artifacts):
    report_path = artifacts / "patch-report.json"
    if not report_path.exists():
        # The first release wrote a single report. Accept it only for its exact output.
        report_path = ROOT / ".local" / "patch-report.json"
    previous = json.loads(report_path.read_text())
    existing_archive = output / "Contents/Resources/app.asar"
    if previous["output"] != str(output) or previous["patchedAsarSha256"] != sha(existing_archive.read_bytes()):
        raise ValueError("Existing output does not match the previous experiment report")


def run(*args):
    subprocess.run(args, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=pathlib.Path, default=pathlib.Path("/Applications/Figma.app"))
    parser.add_argument("--output", type=pathlib.Path, default=ROOT / ".local" / "Figma Plugin Experiment.app")
    parser.add_argument("--refresh", action="store_true", help="Rebuild only a previously recorded experiment")
    parser.add_argument("--check", action="store_true", help="Print a read-only desktop compatibility report; exit 2 if rules do not match")
    args = parser.parse_args()
    try:
        rules = load_rules()
        source, output = args.source.resolve(), args.output.resolve()
        compatibility, info, header, payload, original = inspect_source(source, rules)
        if args.check:
            print(json.dumps(compatibility, indent=2))
            if not compatibility["compatible"]:
                raise SystemExit(2)
            return
        if not compatibility["compatible"]:
            parser.error(
                f"Desktop patch rules do not match Figma {compatibility['version']}. "
                "Run --check to collect compatibility details."
            )
        if source == output or source in output.parents or output in source.parents:
            parser.error("Output must be outside the source app")
        artifacts = artifact_directory(output)
        if output.exists():
            if not args.refresh:
                parser.error("Output exists; use --refresh only for a previously recorded experiment")
            verify_refresh(output, artifacts)
        hook_source = (ROOT / "scripts" / "plugin-access-hook.cjs").read_text()
        modified = patch_main(original, rules, hook_source)
        archive, header_hash = replace_main(header, payload, modified)
        with tempfile.TemporaryDirectory(prefix="figma-plugin-patch-") as temp:
            temp = pathlib.Path(temp)
            js = temp / "main.js"
            js.write_bytes(modified)
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
            entitlements["com.apple.security.cs.disable-library-validation"] = True
            ent_path = temp / "entitlements.plist"
            ent_path.write_bytes(plistlib.dumps(entitlements))
            run("codesign", "--force", "--deep", "--sign", "-", "--options", "runtime", "--entitlements", str(ent_path), str(output))
            run("codesign", "--verify", "--deep", "--strict", str(output))
        if sha((source / "Contents/Resources/app.asar").read_bytes()) != compatibility["sourceAsarSha256"]:
            raise ValueError("The original app changed during this experiment")
        artifacts.mkdir(parents=True, exist_ok=True)
        (artifacts / "original-main.js").write_bytes(original)
        (artifacts / "patched-main.js").write_bytes(modified)
        report = {
            "source": str(source), "output": str(output), "version": compatibility["version"],
            "artifactDirectory": str(artifacts),
            "bundleIdentifier": info["CFBundleIdentifier"],
            "sourceAsarSha256": compatibility["sourceAsarSha256"],
            "sourceMainSha256": compatibility["sourceMainSha256"],
            "patchedAsarSha256": sha(archive), "patchedMainSha256": sha(modified),
            "verifiedAsarEntries": checked, "patches": len(rules), "revision": 3,
            "scope": "Desktop plugin menu and frontend plugin selectors; document read-only state and server permissions remain unchanged.",
        }
        report_text = json.dumps(report, indent=2) + "\n"
        (artifacts / "patch-report.json").write_text(report_text)
        if output == (ROOT / ".local" / "Figma Plugin Experiment.app").resolve():
            (ROOT / ".local" / "patch-report.json").write_text(report_text)
        print(report_text, end="")
    except (OSError, ValueError, KeyError, struct.error) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
