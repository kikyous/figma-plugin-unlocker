import copy
import importlib.util
import json
import pathlib
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("patch_figma", ROOT / "scripts/patch-figma.py")
patch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(patch)


class DesktopRulesTest(unittest.TestCase):

    def test_load_rejects_invalid_patch_definitions(self):
        with tempfile.TemporaryDirectory() as folder:
            invalid = copy.deepcopy(patch.load_rules())
            invalid[0]["pattern"] = ""
            rule_file = pathlib.Path(folder) / "rules.json"
            rule_file.write_text(json.dumps(invalid))
            with self.assertRaises(ValueError):
                patch.load_rules(rule_file)

    def test_patch_rejects_missing_or_ambiguous_rules(self):
        for original in [b'"use strict";nothing;', b'"use strict";target;target;']:
            rules = [{"name": "menu", "pattern": "target", "replacement": "changed"}]
            with self.subTest(original=original), self.assertRaisesRegex(ValueError, "missing or ambiguous"):
                patch.patch_main(original, rules, "")

    def test_patch_accepts_different_scripts_matching_same_rules(self):
        rules = [{"name": "menu", "pattern": r"target_([a-z]+)", "replacement": "changed_{1}"}]
        for original, expected in [(b'"use strict";target_one;', b'changed_one'),
                                   (b'"use strict";target_two;', b'changed_two')]:
            with self.subTest(original=original):
                self.assertIn(expected, patch.patch_main(original, rules, ""))

    def test_inspect_accepts_unregistered_version_and_unpinned_script(self):
        import plistlib
        with tempfile.TemporaryDirectory() as folder:
            source = pathlib.Path(folder)
            resources = source / "Contents/Resources"
            resources.mkdir(parents=True)
            (source / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleShortVersionString": "999.0"}))
            (resources / "app.asar").write_bytes(b"archive")
            original = b'"use strict";target_changed;'
            header = {"files": {"main.js": {"offset": "0", "size": len(original)}}}
            rules = [{"name": "menu", "pattern": r"target_([a-z]+)", "replacement": "changed_{1}"}]
            with mock.patch.object(patch, "read_asar", return_value=(header, original)), \
                 mock.patch.object(patch, "verify_archive", return_value=1):
                report, *_ = patch.inspect_source(source, rules)
            self.assertTrue(report["compatible"])
            self.assertEqual(report["version"], "999.0")
            self.assertEqual(report["patchChecks"][0]["matches"], 1)

    def test_rules_match_renamed_identifiers(self):
        rules = {rule["name"]: rule for rule in patch.load_rules()}
        action = 'function renamed(x,y){let z=y&&win.fromBrowserWindow(y);x&&x.pluginMenuAction&&z&&z.postMessageToActiveWebBinding("handlePluginMenuAction",x.pluginMenuAction)}'
        matches = patch.matches_for(action, rules["plugin-action"])
        self.assertEqual(len(matches), 1)
        result = patch.replacement_for(matches[0], rules["plugin-action"])
        self.assertIn('function renamed(x,y)', result)
        self.assertIn('win.fromBrowserWindow(y)', result)
        self.assertIn('z.postMessageToActiveWebBinding', result)
        self.assertIn('delete __figmaPluginAction.showError', result)
        self.assertEqual(patch.matches_for(action.replace('y&&win', 'x&&win'), rules["plugin-action"]), [])

    def test_output_builds_keep_independent_reports(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(patch, "ROOT", pathlib.Path(folder)):
            first = pathlib.Path(folder) / "First.app"
            second = pathlib.Path(folder) / "Second.app"
            self.assertNotEqual(patch.artifact_directory(first), patch.artifact_directory(second))
            for output, content in [(first, b"first archive"), (second, b"second archive")]:
                archive = output / "Contents/Resources/app.asar"
                archive.parent.mkdir(parents=True)
                archive.write_bytes(content)
                artifacts = patch.artifact_directory(output)
                artifacts.mkdir(parents=True)
                (artifacts / "patch-report.json").write_text(json.dumps({
                    "output": str(output), "patchedAsarSha256": patch.sha(content),
                }))
            patch.verify_refresh(first, patch.artifact_directory(first))
            patch.verify_refresh(second, patch.artifact_directory(second))
            (first / "Contents/Resources/app.asar").write_bytes(b"modified externally")
            with self.assertRaisesRegex(ValueError, "does not match"):
                patch.verify_refresh(first, patch.artifact_directory(first))
            patch.verify_refresh(second, patch.artifact_directory(second))

    def test_refresh_accepts_legacy_report_only_for_its_recorded_output(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(patch, "ROOT", pathlib.Path(folder)):
            output = pathlib.Path(folder) / "Legacy.app"
            archive = output / "Contents/Resources/app.asar"
            archive.parent.mkdir(parents=True)
            archive.write_bytes(b"legacy archive")
            latest = pathlib.Path(folder) / ".local/patch-report.json"
            latest.parent.mkdir(parents=True)
            latest.write_text(json.dumps({"output": str(output), "patchedAsarSha256": patch.sha(archive.read_bytes())}))
            patch.verify_refresh(output, patch.artifact_directory(output))
            with self.assertRaisesRegex(ValueError, "does not match"):
                patch.verify_refresh(pathlib.Path(folder) / "Other.app", patch.artifact_directory(output))


if __name__ == "__main__":
    unittest.main()
