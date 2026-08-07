#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="${SOURCE_DIR:-/home/pi/shared}"
DEST_DIR="${DEST_DIR:-/home/pi/shared/misure}"

usage() {
  printf 'Usage: %s [--dry-run]\n' "$0"
  printf 'Copies measurement files from %s to yearly folders under %s.\n' "$SOURCE_DIR" "$DEST_DIR"
}

dry_run=0
if [[ "${1:-}" == "--dry-run" ]]; then
  dry_run=1
elif [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
elif [[ $# -gt 0 ]]; then
  usage >&2
  exit 2
fi

if ! findmnt -rn --target "$DEST_DIR" >/dev/null; then
  printf 'ERROR: %s is not a mounted share.\n' "$DEST_DIR" >&2
  exit 1
fi

if [[ ! -d "$DEST_DIR" ]]; then
  printf 'ERROR: destination directory does not exist: %s\n' "$DEST_DIR" >&2
  exit 1
fi

if [[ ! -w "$DEST_DIR" ]]; then
  printf 'ERROR: destination directory is not writable: %s\n' "$DEST_DIR" >&2
  exit 1
fi

shopt -s nullglob
files=(
  "$SOURCE_DIR"/misure_*.csv
  "$SOURCE_DIR"/misure_*.xlsx
)
shopt -u nullglob

if [[ ${#files[@]} -eq 0 ]]; then
  printf 'No measurement files found in %s.\n' "$SOURCE_DIR"
  exit 0
fi

rsync_args=(-av)
if [[ "$dry_run" -eq 1 ]]; then
  rsync_args+=(--dry-run)
fi

for file in "${files[@]}"; do
  filename="$(basename "$file")"
  if [[ "$filename" =~ ^misure_([0-9]{4})-[0-9]{1,2}-[0-9]{1,2}\.(csv|xlsx)$ ]]; then
    year="${BASH_REMATCH[1]}"
  else
    printf 'Skipping file with unexpected name: %s\n' "$file" >&2
    continue
  fi

  year_dir="$DEST_DIR/$year"
  if [[ "$dry_run" -eq 0 ]]; then
    mkdir -p "$year_dir"
  fi

  rsync "${rsync_args[@]}" "$file" "$year_dir"/
done
