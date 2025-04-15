#!/usr/bin/env bash

# List all files matching "*-GSEA-*.txt" in the current directory.
all_files=( $(ls -1tr *-GSEA-*.txt 2>/dev/null) )
if [ ${#all_files[@]} -eq 0 ]; then
  echo "No matching files found."
  exit 1
fi

# Determine unique model prefixes (everything before "-GSEA-").
models=$(for f in "${all_files[@]}"; do
           basename "$f" | sed -E 's/^(.*)-GSEA-.*$/\1/'
         done | sort -u)

for model in $models; do
  echo "Processing model group: $model"
  # Get all files for this model, sorted by modification time (oldest first)
  files=( $(ls -1tr "${model}"-GSEA-*.txt 2>/dev/null) )
  index=1
  for file in "${files[@]}"; do
    base=$(basename "$file" .txt)
    # Get the portion after the literal "-GSEA-"
    suffix="${base#*-GSEA-}"
    # Split the suffix by '-' into tokens.
    IFS='-' read -r token1 token2 token3 extra <<< "$suffix"
    # If token3 exists, then the file is already processed.
    if [ -n "$token3" ]; then
      echo "Skipping file (already processed): $file"
    else
      # Use the overall file order (index) for block assignment.
      group=$(( (index - 1) / 10 ))
      genes=$(( 100 + 50 * group ))
      if [ $genes -gt 1000 ]; then
        genes=1000
      fi
      num="$token1"
      time="$token2"
      newfile="${model}-GSEA-${genes}-${num}-${time}.txt"
      echo "Renaming: $file -> $newfile"
      mv "$file" "$newfile"
    fi
    index=$(( index + 1 ))
  done
done

echo "All files processed."
