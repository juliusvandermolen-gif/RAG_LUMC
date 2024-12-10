#!/bin/bash

file="rat_data.txt.gz"

# Ensure the input file exists
if [ ! -f "./to_be_converted/${file}" ]; then
    echo "Error: File './to_be_converted/${file}' does not exist."
    exit 1
fi

zcat "./to_be_converted/${file}" | \
csvcut -c "Gene stable ID","Gene name","Gene description","Gene Synonym","NCBI gene (formerly Entrezgene) description","NCBI gene (formerly Entrezgene) ID" | \
gzip > "./to_be_converted/reordered_${file}"

echo "Reordered file saved as ./to_be_converted/reordered_${file}"
