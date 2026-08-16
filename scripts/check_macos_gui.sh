#!/usr/bin/env bash
#
# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

set -euo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
project="$root/macos/Auralign.xcodeproj"
derived_parent="${HOME}/Library/Developer/Xcode/DerivedData"
derived_data="${HOME}/Library/Developer/Xcode/DerivedData/AuralignLocalGate"
lock_directory="${derived_parent}/.AuralignLocalGate.lock"
run_stamp="$(date -u '+%Y%m%dT%H%M%SZ')-$$"
evidence="${derived_data}/GateEvidence/${run_stamp}"
owner_pid=""
contender_pid=""

fail() {
  printf 'macOS GUI gate failed: %s\n' "$*" >&2
  exit 1
}

terminate_owned_process() {
  local process_identifier="${1:-}"
  [[ -n "$process_identifier" ]] || return 0
  kill -0 "$process_identifier" 2>/dev/null || return 0
  kill -TERM "$process_identifier" 2>/dev/null || true
  local attempt
  for attempt in {1..50}; do
    kill -0 "$process_identifier" 2>/dev/null || return 0
    sleep 0.1
  done
  kill -KILL "$process_identifier" 2>/dev/null || true
}

cleanup() {
  terminate_owned_process "$contender_pid"
  terminate_owned_process "$owner_pid"
  if [[ -f "$lock_directory/owner-pid" ]] \
    && [[ "$(<"$lock_directory/owner-pid")" == "$$" ]]; then
    rm -f "$lock_directory/owner-pid" "$lock_directory/started-at"
    rmdir "$lock_directory" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM HUP

mkdir -p "$derived_parent"
if ! mkdir "$lock_directory" 2>/dev/null; then
  existing_owner="unknown"
  if [[ -f "$lock_directory/owner-pid" ]]; then
    existing_owner="$(<"$lock_directory/owner-pid")"
  fi
  fail "another gate owns $lock_directory (recorded PID: $existing_owner)"
fi
printf '%s\n' "$$" > "$lock_directory/owner-pid"
date -u '+%Y-%m-%dT%H:%M:%SZ' > "$lock_directory/started-at"
mkdir -p "$evidence"
printf '%s\n' "$evidence" > "$derived_data/GateEvidence/LAST_ATTEMPT"

assert_no_active_gui_toolchain() {
  local phase="$1"
  local active
  active="$(
    ps -axo pid=,args= | awk '
      {
        executable = $2
        component_count = split(executable, components, "/")
        basename = components[component_count]
        if (basename ~ /^(xcodebuild|xctest|Auralign|AuralignTests-Runner|AuralignUITests-Runner)$/) {
          print
        }
      }
    '
  )"
  if [[ -n "$active" ]]; then
    printf '%s\n' "$active" > "$evidence/${phase}-active-processes.txt"
    fail "$phase found an active Xcode, Auralign, or test-runner process"
  fi
  local bridges
  bridges="$(pgrep -fl '[l]ocal_transcript_max.gui_bridge' || true)"
  if [[ -n "$bridges" ]]; then
    printf '%s\n' "$bridges" > "$evidence/${phase}-active-bridges.txt"
    fail "$phase found an active Auralign Python bridge"
  fi
}

write_source_state() {
  local destination="$1"
  uv run --python 3.11 python -B - "$root" "$destination" <<'PY'
import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
destination = Path(sys.argv[2])


def git_output(*arguments: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        stdout=subprocess.PIPE,
    ).stdout


status = git_output("status", "--porcelain=v1", "-z", "--untracked-files=all")
tracked_patch = git_output("diff", "--binary", "--no-ext-diff", "HEAD", "--")
untracked = git_output("ls-files", "--others", "--exclude-standard", "-z")
untracked_entries = []
for encoded_path in (item for item in untracked.split(b"\0") if item):
    relative = os.fsdecode(encoded_path)
    path = root / relative
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode):
        payload = os.fsencode(os.readlink(path))
        kind = "symlink"
    elif stat.S_ISREG(metadata.st_mode):
        payload = path.read_bytes()
        kind = "file"
    else:
        raise SystemExit(f"unsupported untracked source entry: {relative}")
    untracked_entries.append(
        {
            "bytes": len(payload),
            "kind": kind,
            "mode": stat.S_IMODE(metadata.st_mode),
            "path": relative,
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
    )

document = {
    "gitHead": git_output("rev-parse", "HEAD").decode().strip(),
    "statusSha256": hashlib.sha256(status).hexdigest(),
    "trackedPatchSha256": hashlib.sha256(tracked_patch).hexdigest(),
    "untracked": untracked_entries,
}
destination.write_text(
    json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY
}

run_xcode() {
  local label="$1"
  shift
  xcodebuild \
    -project "$project" \
    -scheme Auralign \
    -destination 'platform=macOS' \
    -derivedDataPath "$derived_data" \
    -jobs 1 \
    -resultBundlePath "$evidence/${label}.xcresult" \
    "$@" 2>&1 | tee "$evidence/${label}.log"
}

assert_no_active_gui_toolchain preflight

{
  printf 'run_stamp=%s\n' "$run_stamp"
  printf 'repository=%s\n' "$root"
  printf 'derived_data=%s\n' "$derived_data"
  printf 'evidence=%s\n' "$evidence"
  printf 'xcode=%s\n' "$(xcodebuild -version | tr '\n' ' ')"
  printf 'host=%s\n' "$(sw_vers -productVersion)"
  printf 'architecture=%s\n' "$(uname -m)"
  printf 'git_head=%s\n' "$(git -C "$root" rev-parse HEAD)"
} > "$evidence/environment.txt"
git -C "$root" status --short --untracked-files=all \
  > "$evidence/git-status-before.txt"
write_source_state "$evidence/source-state-before.json"

bash -n "$root/scripts/check_macos_gui.sh"
uv run --python 3.11 pytest -q "$root/tests/test_macos_gui_contract.py" \
  2>&1 | tee "$evidence/source-contract.log"

run_xcode signed-tests \
  -configuration Debug \
  -parallel-testing-enabled NO \
  test
xcrun xcresulttool get test-results summary \
  --path "$evidence/signed-tests.xcresult" \
  > "$evidence/signed-tests-summary.json"
xcrun xcresulttool get test-results tests \
  --path "$evidence/signed-tests.xcresult" \
  > "$evidence/signed-tests-tree.json"
uv run --python 3.11 python -B - \
  "$evidence/signed-tests-summary.json" \
  "$evidence/signed-tests-tree.json" \
  "$evidence/signed-tests-validation.json" <<'PY'
import json
import sys
from pathlib import Path

summary_path = Path(sys.argv[1])
tree_path = Path(sys.argv[2])
validation_path = Path(sys.argv[3])
summary = json.loads(summary_path.read_text(encoding="utf-8"))
tree = json.loads(tree_path.read_text(encoding="utf-8"))

required_suites = {
    "AuralignTests",
    "AuralignUITests",
    "AuralignFixtureUITests",
}
allowed_runtime_warnings = {
    "[Internal] Thread running at User-interactive quality-of-service class "
    "waiting on a lower QoS thread running at Default quality-of-service class. "
    "Investigate ways to avoid priority inversions",
}
observed_suites = set()
runtime_warnings = []


def visit(node: object) -> None:
    if isinstance(node, dict):
        node_type = node.get("nodeType")
        if node_type in {"Unit test bundle", "UI test bundle", "Test Suite"}:
            name = node.get("name")
            if isinstance(name, str):
                observed_suites.add(name)
        if node_type == "Runtime Warning":
            warning = node.get("name")
            runtime_warnings.append(str(warning))
        for value in node.values():
            visit(value)
    elif isinstance(node, list):
        for value in node:
            visit(value)


visit(tree)
errors = []
total = summary.get("totalTestCount")
passed = summary.get("passedTests")
if summary.get("result") != "Passed":
    errors.append(f"result={summary.get('result')!r}")
if summary.get("failedTests") != 0:
    errors.append(f"failedTests={summary.get('failedTests')!r}")
if summary.get("skippedTests") != 0:
    errors.append(f"skippedTests={summary.get('skippedTests')!r}")
if summary.get("expectedFailures") != 0:
    errors.append(f"expectedFailures={summary.get('expectedFailures')!r}")
if not isinstance(total, int) or total < 170:
    errors.append(f"totalTestCount={total!r}, expected at least 170")
if passed != total:
    errors.append(f"passedTests={passed!r}, totalTestCount={total!r}")
missing_suites = sorted(required_suites - observed_suites)
if missing_suites:
    errors.append("missing suites: " + ", ".join(missing_suites))
unexpected_runtime_warnings = [
    warning for warning in runtime_warnings if warning not in allowed_runtime_warnings
]
if unexpected_runtime_warnings:
    errors.append(
        f"{len(unexpected_runtime_warnings)} unexpected runtime warning(s)"
    )

validation = {
    "allowlistedRuntimeWarnings": sorted(allowed_runtime_warnings),
    "errors": errors,
    "minimumTestCount": 170,
    "observedSuites": sorted(observed_suites),
    "requiredSuites": sorted(required_suites),
    "runtimeWarnings": runtime_warnings,
    "unexpectedRuntimeWarnings": unexpected_runtime_warnings,
    "summary": {
        key: summary.get(key)
        for key in (
            "expectedFailures",
            "failedTests",
            "passedTests",
            "result",
            "skippedTests",
            "totalTestCount",
        )
    },
}
validation_path.write_text(
    json.dumps(validation, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
if errors:
    raise SystemExit("signed test validation failed: " + "; ".join(errors))
PY

assert_no_active_gui_toolchain after-tests
run_xcode analyze -configuration Debug analyze
assert_no_active_gui_toolchain after-analyze
run_xcode release-build -configuration Release build
assert_no_active_gui_toolchain after-release-build

uv run --python 3.11 python -B - \
  "$evidence/signed-tests.log" \
  "$evidence/analyze.log" \
  "$evidence/release-build.log" \
  "$evidence/build-warnings.json" <<'PY'
import json
import sys
from pathlib import Path

allowed = "warning: Metadata extraction skipped. No AppIntents.framework dependency found."
observed = []
unexpected = []
for raw_path in sys.argv[1:-1]:
    path = Path(raw_path)
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8", errors="replace").splitlines(),
        start=1,
    ):
        if "warning:" not in line:
            continue
        record = {
            "file": path.name,
            "line": line_number,
            "text": line.strip(),
        }
        observed.append(record)
        if allowed not in line:
            unexpected.append(record)

Path(sys.argv[-1]).write_text(
    json.dumps(
        {
            "allowlistedMessage": allowed,
            "observed": observed,
            "unexpected": unexpected,
        },
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)
if unexpected:
    raise SystemExit(f"unexpected Xcode warning(s): {len(unexpected)}")
PY

app="$derived_data/Build/Products/Release/Auralign.app"
executable="$app/Contents/MacOS/Auralign"
info="$app/Contents/Info.plist"
resources="$app/Contents/Resources"
asset_catalog="$resources/Assets.car"
compiled_icon="$resources/audio-waveform-icon.icns"

test -x "$executable" || fail "Release executable is missing"
test -f "$info" || fail "Release Info.plist is missing"
test -s "$asset_catalog" || fail "Release Assets.car is missing or empty"
test -s "$compiled_icon" || fail "Release compiled icon is missing or empty"
cmp -s \
  "$root/macos/Auralign/Resources/config_manifest.json" \
  "$resources/config_manifest.json" \
  || fail "bundled config manifest differs from the reviewed source"
cmp -s \
  "$root/macos/Auralign/Resources/FieldCatalog.json" \
  "$resources/FieldCatalog.json" \
  || fail "bundled field catalog differs from the reviewed source"

architectures="$(lipo -archs "$executable")"
[[ "$architectures" == "arm64" ]] \
  || fail "Release executable must be arm64-only; observed: $architectures"

engine_root="$(
  /usr/libexec/PlistBuddy -c 'Print :AuralignEngineRoot' "$info" 2>/dev/null \
    || true
)"
[[ -z "$engine_root" ]] \
  || fail "Release bundle embeds a source-checkout engine root"

multiple_instances="$(
  /usr/libexec/PlistBuddy \
    -c 'Print :LSMultipleInstancesProhibited' "$info" 2>/dev/null || true
)"
[[ "$multiple_instances" == "true" ]] \
  || fail "Release bundle must prohibit duplicate application instances"

bundle_identifier="$(
  /usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$info" 2>/dev/null \
    || true
)"
minimum_system="$(
  /usr/libexec/PlistBuddy -c 'Print :LSMinimumSystemVersion' "$info" 2>/dev/null \
    || true
)"
package_type="$(
  /usr/libexec/PlistBuddy -c 'Print :CFBundlePackageType' "$info" 2>/dev/null \
    || true
)"
short_version="$(
  /usr/libexec/PlistBuddy \
    -c 'Print :CFBundleShortVersionString' "$info" 2>/dev/null || true
)"
bundle_version="$(
  /usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "$info" 2>/dev/null \
    || true
)"
bundle_executable="$(
  /usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$info" 2>/dev/null \
    || true
)"
[[ "$bundle_identifier" == "com.auralign.studio" ]] \
  || fail "unexpected Release bundle identifier: $bundle_identifier"
[[ "$minimum_system" == "14.0" ]] \
  || fail "unexpected Release minimum system version: $minimum_system"
[[ "$package_type" == "APPL" ]] \
  || fail "unexpected Release package type: $package_type"
[[ "$short_version" == "0.3.1" ]] \
  || fail "unexpected Release short version: $short_version"
[[ "$bundle_version" == "1" ]] \
  || fail "unexpected Release bundle version: $bundle_version"
[[ "$bundle_executable" == "Auralign" ]] \
  || fail "unexpected Release executable name: $bundle_executable"

icon_name="$(
  /usr/libexec/PlistBuddy -c 'Print :CFBundleIconName' "$info" 2>/dev/null \
    || true
)"
icon_file="$(
  /usr/libexec/PlistBuddy -c 'Print :CFBundleIconFile' "$info" 2>/dev/null \
    || true
)"
[[ "$icon_name" == "audio-waveform-icon" ]] \
  || fail "CFBundleIconName does not select audio-waveform-icon"
[[ "$icon_file" == "audio-waveform-icon" ]] \
  || fail "CFBundleIconFile does not select audio-waveform-icon"

/usr/bin/codesign --verify --deep --strict --verbose=2 "$app" \
  > "$evidence/codesign-verify.log" 2>&1 \
  || fail "Release signature verification failed"
/usr/bin/codesign -dvvv "$app" \
  > "$evidence/codesign-details.txt" 2>&1
grep -q 'runtime' "$evidence/codesign-details.txt" \
  || fail "Release application signature lacks hardened runtime"
/usr/bin/codesign -d --entitlements :- "$app" \
  > "$evidence/entitlements.plist" 2> "$evidence/entitlements-extraction.log"
if [[ -s "$evidence/entitlements.plist" ]]; then
  plutil -lint "$evidence/entitlements.plist" \
    > "$evidence/entitlements-lint.txt"
else
  printf '%s\n' \
    'No entitlement payload was emitted; validating as an exact empty set.' \
    > "$evidence/entitlements-lint.txt"
fi

uv run --python 3.11 python -B - \
  "$evidence/entitlements.plist" \
  "$evidence/entitlements-validation.json" <<'PY'
import json
import plistlib
import sys
from pathlib import Path

path = Path(sys.argv[1])
validation_path = Path(sys.argv[2])
payload = path.read_bytes()
if payload:
    entitlements = plistlib.loads(payload)
    disposition = "explicit-empty-dictionary"
else:
    # A hardened binary with no entitlements causes codesign to exit
    # successfully while emitting no plist payload. That is semantically the
    # exact empty entitlement set required by this gate.
    entitlements = {}
    disposition = "no-entitlement-payload"
if not isinstance(entitlements, dict):
    raise SystemExit(
        "Release entitlement payload must be a dictionary; observed: "
        + type(entitlements).__name__
    )
reviewed_forbidden_examples = {
    "com.apple.security.get-task-allow",
    "com.apple.security.cs.disable-library-validation",
    "com.apple.security.cs.allow-unsigned-executable-memory",
    "com.apple.security.cs.allow-dyld-environment-variables",
    "com.apple.security.cs.dynamic-codesigning",
    "com.apple.security.application-groups",
}
validation = {
    "disposition": disposition,
    "entitlements": entitlements,
    "exactEmptyEntitlementDictionary": entitlements == {},
    "payloadBytes": len(payload),
}
validation_path.write_text(
    json.dumps(validation, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
if entitlements != {}:
    known = sorted(reviewed_forbidden_examples.intersection(entitlements))
    classification = f"; known forbidden keys: {', '.join(known)}" if known else ""
    raise SystemExit(
        "Release must have an exact empty entitlement dictionary; observed: "
        + ", ".join(sorted(entitlements))
        + classification
    )
PY

xcrun assetutil --info "$asset_catalog" \
  > "$evidence/assets-info.json"
iconset="$evidence/audio-waveform-icon.iconset"
xcrun iconutil -c iconset "$compiled_icon" -o "$iconset"

uv run --python 3.11 python -B - \
  "$root/macos/Auralign/audio-waveform-icon.icon" \
  "$root/macos/Auralign/Assets.xcassets/AppIcon.appiconset" \
  "$evidence/assets-info.json" \
  "$iconset" \
  "$evidence/icon-source-sha256.json" <<'PY'
import hashlib
import json
import struct
import sys
import zlib
from pathlib import Path

composer = Path(sys.argv[1])
fallback = Path(sys.argv[2])
asset_info_path = Path(sys.argv[3])
iconset = Path(sys.argv[4])
hash_manifest_path = Path(sys.argv[5])

source_hashes = {}
source_roots = (
    (composer, "macos/Auralign/audio-waveform-icon.icon"),
    (fallback, "macos/Auralign/Assets.xcassets/AppIcon.appiconset"),
)
for root, repository_prefix in source_roots:
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise SystemExit(f"icon source contains a symlink: {path}")
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            source_hashes[f"{repository_prefix}/{relative}"] = (
                hashlib.sha256(path.read_bytes()).hexdigest()
            )
hash_manifest_path.write_text(
    json.dumps(source_hashes, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)

items = json.loads(asset_info_path.read_text(encoding="utf-8"))
icon_images = {
    (item.get("PixelWidth"), item.get("PixelHeight"), item.get("Scale"))
    for item in items
    if item.get("AssetType") == "Icon Image"
    and item.get("Name") == "audio-waveform-icon"
}
expected_images = {
    (32, 32, 2),
    (64, 64, 2),
    (128, 128, 2),
    (256, 256, 2),
    (512, 512, 2),
    (1024, 1024, 2),
    (1024, 1024, 1),
}
if icon_images != expected_images:
    raise SystemExit(f"unexpected icon renditions: {sorted(icon_images)}")

expected_groups = {
    f"audio-waveform-icon/{name}"
    for name in (
        "Frame and Panel",
        "Inactive Controls",
        "Selected Controls",
        "Waveform",
    )
}
expected_appearances = {
    "NSAppearanceNameAqua",
    "NSAppearanceNameDarkAqua",
    "ISAppearanceTintable",
}
for group in expected_groups:
    appearances = {
        item.get("Appearance")
        for item in items
        if item.get("AssetType") == "IconGroup" and item.get("Name") == group
    }
    if appearances != expected_appearances:
        raise SystemExit(
            f"unexpected appearance renditions for {group}: {sorted(appearances)}"
        )

expected_iconset = {
    "icon_16x16.png": 16,
    "icon_16x16@2x.png": 32,
    "icon_32x32.png": 32,
    "icon_32x32@2x.png": 64,
    "icon_128x128.png": 128,
    "icon_128x128@2x.png": 256,
    "icon_256x256.png": 256,
    "icon_256x256@2x.png": 512,
    "icon_512x512.png": 512,
    "icon_512x512@2x.png": 1024,
}
observed_iconset = {path.name for path in iconset.iterdir() if path.is_file()}
if observed_iconset != set(expected_iconset):
    raise SystemExit(f"unexpected .icns round-trip files: {sorted(observed_iconset)}")
for filename, pixels in expected_iconset.items():
    path = iconset / filename
    data = path.read_bytes()
    if len(data) < 33 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise SystemExit(f"invalid .icns PNG signature: {filename}")
    chunk_length = struct.unpack(">I", data[8:12])[0]
    chunk_type = data[12:16]
    if chunk_length != 13 or chunk_type != b"IHDR":
        raise SystemExit(f"invalid .icns PNG IHDR: {filename}")
    chunk_data = data[16:29]
    expected_crc = struct.unpack(">I", data[29:33])[0]
    actual_crc = zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
    if actual_crc != expected_crc:
        raise SystemExit(f"invalid .icns PNG IHDR checksum: {filename}")
    width, height, bit_depth, color_type, compression, filtering, interlace = (
        struct.unpack(">IIBBBBB", chunk_data)
    )
    if (
        (width, height) != (pixels, pixels)
        or bit_depth != 8
        or color_type not in {2, 6}
        or (compression, filtering, interlace) != (0, 0, 0)
    ):
        raise SystemExit(f"invalid .icns rendition metadata: {filename}")
PY

symlinks="$evidence/release-symlinks.txt"
find -P "$app" -type l -print > "$symlinks"
[[ ! -s "$symlinks" ]] || fail "Release bundle contains symlinks"

[[ ! -e "$app/Contents/Frameworks" ]] \
  || fail "Release bundle unexpectedly contains Frameworks"
[[ ! -e "$app/Contents/PlugIns" ]] \
  || fail "Release bundle unexpectedly contains PlugIns"
[[ ! -e "$app/Contents/MacOS/Auralign.debug.dylib" ]] \
  || fail "Release bundle contains Auralign.debug.dylib"
[[ ! -e "$app/Contents/MacOS/__preview.dylib" ]] \
  || fail "Release bundle contains __preview.dylib"

expected_files="$evidence/release-files.expected"
actual_files="$evidence/release-files.actual"
cat > "$expected_files" <<'EOF'
Contents/Info.plist
Contents/MacOS/Auralign
Contents/PkgInfo
Contents/Resources/Apache-2.0.txt
Contents/Resources/Assets.car
Contents/Resources/CC-BY-4.0.txt
Contents/Resources/FieldCatalog.json
Contents/Resources/MIT.txt
Contents/Resources/audio-waveform-icon.icns
Contents/Resources/config_manifest.json
Contents/Resources/default.metallib
Contents/Resources/model_delivery_catalog.json
Contents/_CodeSignature/CodeResources
EOF
while IFS= read -r -d '' bundled_file; do
  printf '%s\n' "${bundled_file#"$app/"}"
done < <(find -P "$app" -type f -print0) | LC_ALL=C sort > "$actual_files"
LC_ALL=C sort "$expected_files" -o "$expected_files"
diff -u "$expected_files" "$actual_files" \
  > "$evidence/release-file-allowlist.diff" \
  || fail "Release bundle does not match the exact file allowlist"

quarantine="$evidence/release-quarantine.txt"
: > "$quarantine"
while IFS= read -r -d '' bundle_entry; do
  if /usr/bin/xattr -p com.apple.quarantine "$bundle_entry" >/dev/null 2>&1; then
    printf '%s\n' "${bundle_entry#"$app"/}" >> "$quarantine"
  fi
done < <(find -P "$app" -print0)
[[ ! -s "$quarantine" ]] || fail "Release bundle contains quarantine metadata"

/usr/bin/otool -L "$executable" > "$evidence/release-dependencies.txt"
while IFS= read -r dependency; do
  case "$dependency" in
    /System/Library/*|/usr/lib/*) ;;
    *) fail "Release executable has a non-system dependency: $dependency" ;;
  esac
done < <(awk 'NR > 1 {print $1}' "$evidence/release-dependencies.txt")

debug_only_markers=(
  'AURALIGN_ENABLE_UI_TEST_FIXTURES'
  '--ui-fixture'
  '/AURALIGN_SYNTHETIC/'
  'fixture.synthetic-evidence'
  'fixture.invalid-request'
  'selected-run-plan'
  'invalid-hidden-advanced'
  'settings-locked'
  'AURALIGN_PROTOTYPE_DIRECTION'
  'AURALIGN_PROTOTYPE_SCREEN'
  'AURALIGN_ENABLE_DEBUG_AUTOMATION'
  'AURALIGN_SNAPSHOT_DIR'
  'AURALIGN_SNAPSHOT_ON_LAUNCH'
  'AURALIGN_SNAPSHOT_SECTION'
  'AURALIGN_SNAPSHOT_APPEARANCE'
  'AURALIGN_SNAPSHOT_WINDOW_SIZE'
  'AURALIGN_SNAPSHOT_EXIT_AFTER_CAPTURE'
  'AURALIGN_SNAPSHOT_WINDOW_ROLE'
  'AURALIGN_SNAPSHOT_WINDOW_ACTIVITY'
  'AURALIGN_SNAPSHOT_SIDEBAR'
  'AURALIGN_SNAPSHOT_NONCE'
  'AURALIGN_PRESENTATION_REDUCE_TRANSPARENCY'
  'AURALIGN_PRESENTATION_INCREASE_CONTRAST'
  'AURALIGN_PRESENTATION_REDUCE_MOTION'
  'AURALIGN_PRESENTATION_DIFFERENTIATE_WITHOUT_COLOR'
  'com.auralign.studio.debug.command'
  'com.auralign.studio.synthetic-fixture.'
)

while IFS= read -r -d '' bundled_file; do
  relative="${bundled_file#"$app/"}"
  relative_lower="$(printf '%s' "$relative" | tr '[:upper:]' '[:lower:]')"
  basename_lower="$(basename "$relative_lower")"

  case "$relative_lower" in
    .git/*|*/.git/*|.codex-work/*|*/.codex-work/*|__pycache__/*|*/__pycache__/*|\
      .venv/*|*/.venv/*|*auraligntests*|*auralignuitests*)
      fail "Release bundle contains a development/private path: $relative"
      ;;
  esac

  case "$basename_lower" in
    .env|.env.*|.ds_store|.ltm-model.json|model_audit.json|validation-capsule.json|\
      package.resolved|project.pbxproj|*.swift|*.swiftinterface|*.swiftmodule|\
      *.swiftsourceinfo|*.py|*.pyc|*.pyo|*.aac|*.aif|*.aiff|*.avi|*.bin|*.ckpt|\
      *.flac|*.icon|*.m4a|*.m4v|*.mkv|*.mov|*.mp3|*.mp4|*.mxf|*.npz|*.ogg|\
      *.onnx|*.png|*.pt|*.pth|*.safetensors|*.wav|*.webm|*.wma)
      fail "Release bundle contains a prohibited source/private artifact: $relative"
      ;;
  esac

  private_user_marker="/"'Users/'
  private_temporary_marker="/private/"'var/folders/'
  for forbidden_path in "$private_user_marker" "$private_temporary_marker"; do
    if LC_ALL=C grep -a -F -q -- "$forbidden_path" "$bundled_file"; then
      fail "Release bundle contains a private machine-local path marker in $relative"
    fi
  done
  if LC_ALL=C grep -a -E -q \
    'gh[pousr]_[A-Za-z0-9]{30,}|hf_[A-Za-z0-9]{24,}|sk-(proj-)?[A-Za-z0-9_-]{24,}|AKIA[0-9A-Z]{16}' \
    "$bundled_file"; then
    fail "Release bundle contains a token-shaped secret in $relative"
  fi
  for private_key_marker in \
    'BEGIN OPENSSH PRIVATE'' KEY' \
    'BEGIN RSA PRIVATE'' KEY' \
    'BEGIN EC PRIVATE'' KEY'
  do
    if LC_ALL=C grep -a -F -q -- "$private_key_marker" "$bundled_file"; then
      fail "Release bundle contains private-key material in $relative"
    fi
  done
  for marker in "${debug_only_markers[@]}"; do
    if LC_ALL=C grep -a -F -q -- "$marker" "$bundled_file"; then
      fail "Release bundle contains Debug-only marker '$marker' in $relative"
    fi
  done
done < <(find -P "$app" -type f -print0)

if grep -E -q \
  'XCRemoteSwiftPackageReference|XCLocalSwiftPackageReference|packageReferences' \
  "$root/macos/Auralign.xcodeproj/project.pbxproj"; then
  fail "Xcode project contains an unreviewed package reference"
fi

uv run --python 3.11 python -B - "$app" "$evidence/release-sha256.json" <<'PY'
import hashlib
import json
import os
import sys
from pathlib import Path

bundle = Path(sys.argv[1])
destination = Path(sys.argv[2])
manifest = {}
for directory, directory_names, file_names in os.walk(bundle, followlinks=False):
    directory_names.sort()
    file_names.sort()
    for filename in file_names:
        path = Path(directory) / filename
        if path.is_symlink():
            raise SystemExit(f"bundle hash walk encountered symlink: {path}")
        relative = path.relative_to(bundle).as_posix()
        manifest[relative] = {
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
destination.write_text(
    json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY

debug_executable="$derived_data/Build/Products/Debug/Auralign.app/Contents/MacOS/Auralign"
test -x "$debug_executable" || fail "Debug executable is missing for single-instance test"
owner_tmp="$evidence/single-instance-owner-tmp"
contender_tmp="$evidence/single-instance-contender-tmp"
mkdir -p "$owner_tmp" "$contender_tmp"

TMPDIR="$owner_tmp/" \
  AURALIGN_ENABLE_UI_TEST_FIXTURES=1 \
  "$debug_executable" \
  -AppleLanguages '(en)' \
  -AppleLocale en_US \
  --ui-fixture no-input \
  > "$evidence/single-instance-owner.stdout" \
  2> "$evidence/single-instance-owner.stderr" &
owner_pid=$!
sleep 1
kill -0 "$owner_pid" 2>/dev/null \
  || fail "single-instance owner exited before the contention test"

TMPDIR="$contender_tmp/" \
  AURALIGN_ENABLE_UI_TEST_FIXTURES=1 \
  "$debug_executable" \
  -AppleLanguages '(en)' \
  -AppleLocale en_US \
  --ui-fixture no-input \
  > "$evidence/single-instance-contender.stdout" \
  2> "$evidence/single-instance-contender.stderr" &
contender_pid=$!

for attempt in {1..50}; do
  kill -0 "$contender_pid" 2>/dev/null || break
  sleep 0.1
done
if kill -0 "$contender_pid" 2>/dev/null; then
  fail "single-instance contender did not exit"
fi
set +e
wait "$contender_pid"
contender_status=$?
set -e
contender_pid=""
[[ "$contender_status" -eq 0 ]] \
  || fail "single-instance contender exited with status $contender_status"

ps -axo pid=,args= | awk -v executable="$debug_executable" '
  $2 == executable { print $1, $0 }
' > "$evidence/single-instance-processes.txt"
instance_count="$(
  awk 'NF { count += 1 } END { print count + 0 }' \
    "$evidence/single-instance-processes.txt"
)"
[[ "$instance_count" -eq 1 ]] \
  || fail "expected exactly one Auralign process; observed $instance_count"
grep -q "^ *$owner_pid " "$evidence/single-instance-processes.txt" \
  || fail "the surviving Auralign process is not the lock owner"

terminate_owned_process "$owner_pid"
set +e
wait "$owner_pid"
set -e
owner_pid=""

assert_no_active_gui_toolchain postflight
git -C "$root" status --short --untracked-files=all \
  > "$evidence/git-status-after.txt"
write_source_state "$evidence/source-state-after.json"
cmp -s \
  "$evidence/source-state-before.json" \
  "$evidence/source-state-after.json" \
  || fail "repository source state changed while the macOS GUI gate was active"
printf '%s\n' "$evidence" > "$derived_data/GateEvidence/LATEST_PASS"
printf '%s\n' "$evidence" > "$derived_data/GateEvidence/LATEST"

printf 'macOS GUI gate passed; preserved evidence: %s\n' "$evidence"
