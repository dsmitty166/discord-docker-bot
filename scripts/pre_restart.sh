#!/bin/bash
# pre_restart.sh <container_name>
# Runs before a container restart to adjust the <game_name> line in XML.

CONTAINER="$1"
INI_FILE="$(dirname "$0")/../configs/container_map.ini"

if [ -z "$CONTAINER" ]; then
  echo "Usage: $0 <container_name>"
  exit 1
fi

if [ ! -f "$INI_FILE" ]; then
  echo "Mapping file not found: $INI_FILE"
  exit 1
fi

# --- Simple INI parser (no external dependencies) ---
read_ini_value() {
  local section="$1" key="$2"
  awk -v s="[$section]" -v k="$key" '
    BEGIN { FS="=" }
    $0==s { f=1; next }
    /^\[/ { f=0 }
    f && $1==k {
      sub(/^[^=]+= */, "")
      print $0
      exit
    }
  ' "$INI_FILE"
}

# --- Read mapping values ---
GAME_NAME=$(read_ini_value "$CONTAINER" "game_name")
NBSP_COUNT=$(read_ini_value "$CONTAINER" "nbsp_count")
SUFFIX=$(read_ini_value "$CONTAINER" "suffix")
XML_PATH=$(read_ini_value "$CONTAINER" "xml_path")

# --- Validate inputs ---
if [ -z "$GAME_NAME" ]; then
  echo "No mapping for $CONTAINER — skipping pre-restart."
  exit 0
fi

if [ ! -f "$XML_PATH" ]; then
  echo "⚠️  XML file not found: $XML_PATH"
  exit 1
fi

# --- Build Unicode pattern safely ---
: "${NBSP_COUNT:=0}"
: "${SUFFIX:=}"

UNICODE_PATTERN=""
for ((i=1; i<=NBSP_COUNT; i++)); do
  UNICODE_PATTERN+=$'\u3164'   # Append non-breaking space (U+00A0)
done
UNICODE_PATTERN+="$SUFFIX"

# --- Perform XML replacement ---
SED_REPLACEMENT=$'<game_name>'"$UNICODE_PATTERN"$'</game_name>'

echo "🔧 Updating <$GAME_NAME> in $XML_PATH for container $CONTAINER ..."
sed -i "s|^\([[:space:]]*\)<game_name>.*</game_name>|\1${SED_REPLACEMENT}|" "$XML_PATH"

# --- Verify success ---
if [ $? -eq 0 ]; then
  if command -v xxd &>/dev/null; then
    NBSPS_WRITTEN=$(xxd -p "$XML_PATH" | grep -o c2a0 | wc -l)
  else
    NBSPS_WRITTEN="(xxd not installed)"
  fi
  GAME_LINE=$(grep -o "<game_name>.*</game_name>" "$XML_PATH")

  echo "✅ Updated $XML_PATH successfully."
  echo "   → Confirmed $NBSPS_WRITTEN non-breaking spaces written."
  echo "   → Final line: $GAME_LINE"
  echo "WEBHOOK_NBSPS_WRITTEN:$NBSPS_WRITTEN"
  echo "WEBHOOK_GAME_LINE:$GAME_LINE"
else
  echo "❌ Failed to update $XML_PATH."
fi
