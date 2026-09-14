#!/usr/bin/env bash
set -euo pipefail

ROOT="${GITHUB_WORKSPACE:-$(pwd)}"
DEST="$ROOT/.devbox/bin"
TMP="$ROOT/.devbox/tunnel-download"
mkdir -p "$DEST" "$TMP"

case "$(uname -s)" in
  Linux) os=linux ;;
  Darwin) os=darwin ;;
  *) echo "Unsupported OS: $(uname -s)" >&2; exit 1 ;;
esac

case "$(uname -m)" in
  x86_64|amd64) arch=amd64 ;;
  arm64|aarch64) arch=arm64 ;;
  *) echo "Unsupported architecture: $(uname -m)" >&2; exit 1 ;;
esac

release_json="$TMP/release.json"
curl -fsSL --retry 4 --retry-delay 2 \
  https://api.github.com/repos/openai/tunnel-client/releases/latest \
  -o "$release_json"

readarray_compat() {
  python3 - "$release_json" "$os" "$arch" <<'PY'
import json, sys
p, os_name, arch = sys.argv[1:]
data = json.load(open(p, encoding="utf-8"))
tag = data["tag_name"]
asset_name = f"tunnel-client-{tag}-{os_name}-{arch}.zip"
assets = {a["name"]: a["browser_download_url"] for a in data.get("assets", [])}
if asset_name not in assets:
    raise SystemExit(f"release asset not found: {asset_name}")
if "SHA256SUMS.txt" not in assets:
    raise SystemExit("SHA256SUMS.txt not found in latest release")
print(tag)
print(asset_name)
print(assets[asset_name])
print(assets["SHA256SUMS.txt"])
PY
}

meta="$(readarray_compat)"
tag="$(printf '%s\n' "$meta" | sed -n '1p')"
asset="$(printf '%s\n' "$meta" | sed -n '2p')"
asset_url="$(printf '%s\n' "$meta" | sed -n '3p')"
sums_url="$(printf '%s\n' "$meta" | sed -n '4p')"

curl -fsSL --retry 4 --retry-delay 2 "$asset_url" -o "$TMP/$asset"
curl -fsSL --retry 4 --retry-delay 2 "$sums_url" -o "$TMP/SHA256SUMS.txt"
expected="$(awk -v f="$asset" '$2 == f || $2 == "*" f {print $1; exit}' "$TMP/SHA256SUMS.txt")"
if [[ -z "$expected" ]]; then
  echo "No checksum found for $asset" >&2
  exit 1
fi
if command -v sha256sum >/dev/null 2>&1; then
  actual="$(sha256sum "$TMP/$asset" | awk '{print $1}')"
else
  actual="$(shasum -a 256 "$TMP/$asset" | awk '{print $1}')"
fi
[[ "$actual" == "$expected" ]] || { echo "SHA256 mismatch for $asset" >&2; exit 1; }

rm -rf "$TMP/unpacked"
mkdir -p "$TMP/unpacked"
unzip -q "$TMP/$asset" -d "$TMP/unpacked"
binary="$(find "$TMP/unpacked" -type f -name tunnel-client -print -quit)"
[[ -n "$binary" ]] || { echo "tunnel-client binary not found in $asset" >&2; exit 1; }
cp "$binary" "$DEST/tunnel-client"
chmod +x "$DEST/tunnel-client"

echo "$DEST" >> "${GITHUB_PATH:-/dev/null}"
echo "Installed tunnel-client $tag for $os-$arch"
"$DEST/tunnel-client" --version
