#!/usr/bin/env bash

# Loop through all directories in the current directory
for dir in */; do
  # Remove trailing slash
  dir=${dir%/}

  # Find all matching GSEA files inside this directory
  all_files=( $(ls -1tr "$dir"/*-GSEA-*.txt 2>/dev/null) )
  if [ ${#all_files[@]} -eq 0 ]; then
    echo "No matching files found in $dir."
    continue
  fi

  # Determine unique model prefixes (everything before "-GSEA-")
  models=$(for f in "${all_files[@]}"; do
             basename "$f" | sed -E 's/^(.*)-GSEA-.*$/\1/'
           done | sort -u)

  for model in $models; do
    echo "Processing model group: $model in $dir"
    # Get all files for this model in the current directory, sorted by modification time
    files=( $(ls -1tr "$dir/${model}-GSEA-"*.txt 2>/dev/null) )
    index=1
    for file in "${files[@]}"; do
      base=$(basename "$file" .txt)
      suffix="${base#*-GSEA-}"
      IFS='-' read -r token1 token2 token3 extra <<< "$suffix"

      # Only process if token3 is empty (i.e., file not yet renamed)
      if [ -z "$token3" ]; then
        group=$(( (index - 1) / 10 ))
        genes=$(( 100 + 100 * group ))
        if [ $genes -gt 1000 ]; then
          genes=1000
        fi
        num="$token1"
        time="$token2"
        newfile="${model}-GSEA-${genes}-${num}-${time}.txt"
        echo "Renaming in $dir: $file -> $newfile"
        mv "$file" "$dir/$newfile"
      fi

      index=$(( index + 1 ))
    done
  done
done

echo "All directories processed."
