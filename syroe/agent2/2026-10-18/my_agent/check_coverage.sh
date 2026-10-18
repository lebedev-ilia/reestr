#!/usr/bin/env bash
# check_coverage.sh — проверить, что все картинки из разметки CVAT есть в датасете.
cd /home/cvat_share || exit 1
awk -F'\t' '{print $2}' /home/user2/ann_names.tsv | sort -u > /home/user2/_need.txt
tot=$(wc -l < /home/user2/_need.txt)
: > /home/user2/_missing.txt
ok=0
while IFS= read -r f; do
  if [ -f "$f" ]; then
    ok=$((ok+1))
  else
    echo "$f" >> /home/user2/_missing.txt
  fi
done < /home/user2/_need.txt
miss=$(wc -l < /home/user2/_missing.txt)
echo "всего уникальных в разметке: $tot"
echo "найдено на диске: $ok"
echo "отсутствует: $miss"
echo "--- примеры отсутствующих (до 10) ---"
head -10 /home/user2/_missing.txt
