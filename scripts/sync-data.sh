#!/usr/bin/env bash
# Copy one municipality's decision history from a kuntapaatokset checkout into data/<muni>/.
#
#   scripts/sync-data.sh <kuntapaatokset checkout> <municipality slug>
#
# Copies what the bot reads: bodies.json, the rosters that party attribution uses, and
# meetings/. Leaves out candidates-*.json, persons.json and every other derived file.
# Meetings the bot crawled itself are overwritten by the dataset's version of the same id.
# Records the kuntapaatokset commit in data/<muni>/SOURCE, because the parser that wrote the
# data has to match the one the bot runs (a parser change means reparse there, then sync).
set -euo pipefail
kp=${1:?kuntapaatokset checkout}
muni=${2:?municipality slug}
src="$kp/data/$muni"
dst="$(cd "$(dirname "$0")/.." && pwd)/data/$muni"
[ -f "$src/bodies.json" ] || { echo "no $src/bodies.json" >&2; exit 1; }
mkdir -p "$dst/meetings"
cp "$src/bodies.json" "$dst/"
cp "$src"/roster-*.json "$dst/"
cp -r "$src/meetings/." "$dst/meetings/"
printf 'kuntapaatokset %s\nsynced %s\n' "$(git -C "$kp" rev-parse --short HEAD)" "$(date -u +%F)" > "$dst/SOURCE"
echo "$dst: $(find "$dst/meetings" -name '*.json' | wc -l) meetings"
