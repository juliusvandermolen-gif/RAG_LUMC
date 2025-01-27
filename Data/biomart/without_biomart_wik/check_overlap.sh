#!/bin/bash

# Decompress the rat_genes_consolidated.txt.gz file and extract the second column (gene names)
gunzip -c rat_genes_consolidated.txt.gz | cut -d, -f2 > rat_genes_cleaned.txt

# Convert gene_list.txt to Unix-style line endings (if needed)
dos2unix gene_list.txt

# Initialize counters
present_count=0
not_present_count=0

# Read each gene from gene_list.txt and check if it's present in rat_genes_cleaned.txt
while read -r gene; do
    # Remove any leading/trailing whitespace from the gene name
    gene=$(echo "$gene" | xargs)

    # Check if the gene is present in the cleaned rat genes file
    if grep -Fxq "$gene" rat_genes_cleaned.txt; then
        present_count=$((present_count + 1))
    else
        not_present_count=$((not_present_count + 1))
        echo "$gene : not present"
    fi
done < gene_list.txt

# Output the counts
echo "Genes present: $present_count"
echo "Genes not present: $not_present_count"

# Clean up the cleaned file (optional)
rm rat_genes_cleaned.txt
