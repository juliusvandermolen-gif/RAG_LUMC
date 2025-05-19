#!/usr/bin/env bash

# How many files per group, and how much to increment each time
FILES_PER_GROUP=3
INCREMENT=50
MAX_GENES=1000

# Loop through each subdirectory
for dir in */; do
  dir=${dir%/}   # strip trailing slash

  # gather all GSEA files here
  all_files=( $(ls -1tr "$dir"/*-GSEA-*.txt 2>/dev/null) )
  if [ ${#all_files[@]} -eq 0 ]; then
    echo "No GSEA files in $dir, skipping."
    continue
  fi

  # extract the list of distinct model prefixes
  models=$(printf "%s\n" "${all_files[@]}" \
           | xargs -n1 basename \
           | sed -E 's/^(.*)-GSEA-.*$/\1/' \
           | sort -u)

  for model in $models; do
    echo "→ Processing model '$model' in '$dir' …"

    # list all files for this model, old and new
    model_files=( $(ls -1tr "$dir/${model}-GSEA-"*.txt 2>/dev/null) )

    # build counts of already-processed files per genes‐group
    declare -A processed_counts
    unprocessed=()

    for f in "${model_files[@]}"; do
      base=$(basename "$f" .txt)
      suffix=${base#*-GSEA-}
      IFS='-' read -r tok1 tok2 tok3 <<< "$suffix"
      if [ -n "$tok3" ]; then
        # this is already renamed: tok1=genes
        genes=${tok1}
        processed_counts[$genes]=$(( processed_counts[$genes] + 1 ))
      else
        # still needs renaming
        unprocessed+=( "$f" )
      fi
    done

    # figure out where to start
    highest_genes=0
    for g in "${!processed_counts[@]}"; do
      (( g > highest_genes )) && highest_genes=$g
    done

    if (( highest_genes == 0 )); then
      # no processed files yet → start at 100
      current_genes=100
      current_count=0
    else
      cnt=${processed_counts[$highest_genes]:-0}
      if (( cnt < FILES_PER_GROUP )); then
        current_genes=$highest_genes
        current_count=$cnt
      else
        current_genes=$(( highest_genes + INCREMENT ))
        current_count=0
      fi
    fi

    # now rename each unprocessed file in mtime order
    for file in "${unprocessed[@]}"; do
      # if we’ve filled this group, bump to the next
      if (( current_count >= FILES_PER_GROUP )); then
        current_genes=$(( current_genes + INCREMENT ))
        current_count=0
      fi
      # clamp at MAX_GENES
      if (( current_genes > MAX_GENES )); then
        current_genes=$MAX_GENES
      fi

      # pull out the original num & time
      base=$(basename "$file" .txt)
      suffix=${base#*-GSEA-}
      IFS='-' read -r num time <<< "$suffix"

      newname="${model}-GSEA-${current_genes}-${num}-${time}.txt"
      echo "    mv '$file' → '$dir/$newname'"
      mv "$file" "$dir/$newname"

      current_count=$(( current_count + 1 ))
    done

    # clean up for next model
    unset processed_counts
    unset unprocessed
  done
done

echo "All done."
