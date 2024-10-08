#!/bin/bash
zcat ./to_be_converted/mart_export_2.txt.gz | \
csvcut -c "Gene stable ID","Gene name","Gene description","Gene Synonym","NCBI gene (formerly Entrezgene) description","NCBI gene (formerly Entrezgene) ID" | \
gzip > ./to_be_converted/reordered_mart_export_2.txt.gz
